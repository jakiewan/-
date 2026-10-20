"""从本地中文 BERT 微调六类客服意图，演示 CSV 不代表生产数据。"""

import argparse
import csv
import json
from pathlib import Path

import torch
from torch.utils.data import DataLoader, TensorDataset
from transformers import BertForSequenceClassification, BertTokenizer, set_seed

from ..common.constants import PROJECT_ROOT
from ..graph.intent_classifier import INTENTS, MAX_LENGTH, MODEL_PATH
from ..graph.intent_input import encode_intent_inputs


def load_data(path: Path) -> tuple[list[dict], list[dict], list[dict]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    seen = set()
    dialogue_splits = {}
    for row in rows:
        if not row["text"].strip() or row["intent"] not in INTENTS or row["split"] not in ("train", "validation", "test"):
            raise ValueError("CSV 需要非空 text、六类 intent 和 train/validation/test split")
        key = (row["text"].strip(), row.get("history", "").strip())
        if key in seen:
            raise ValueError(f"存在重复样本或训练/验证数据重叠：{key[0]}")
        seen.add(key)
        dialogue = row.get("dialogue_id")
        if dialogue:
            if dialogue_splits.setdefault(dialogue, row["split"]) != row["split"]:
                raise ValueError(f"同一会话被划入不同集合：{dialogue}")
    train = [row for row in rows if row["split"] == "train"]
    validation = [row for row in rows if row["split"] == "validation"]
    test = [row for row in rows if row["split"] == "test"]
    if any({row["intent"] for row in split} != set(INTENTS) for split in (train, validation)):
        raise ValueError("训练集和验证集都必须覆盖六类意图")
    if test and {row["intent"] for row in test} != set(INTENTS):
        raise ValueError("独立测试集必须覆盖六类意图")
    return train, validation, test


def encode(rows: list[dict], tokenizer: BertTokenizer, context_turns: int = 0, max_length: int = MAX_LENGTH) -> TensorDataset:
    inputs = encode_intent_inputs(
        tokenizer, [row["text"] for row in rows], [row.get("history", "") for row in rows], context_turns, max_length,
    )
    return TensorDataset(
        inputs["input_ids"], inputs["attention_mask"], inputs["token_type_ids"],
        torch.tensor([INTENTS.index(row["intent"]) for row in rows]),
    )


def evaluate(model, loader, device) -> dict:
    confusion = [[0] * len(INTENTS) for _ in INTENTS]
    model.eval()
    with torch.inference_mode():
        for ids, mask, types, labels in loader:
            predicted = model(
                input_ids=ids.to(device), attention_mask=mask.to(device), token_type_ids=types.to(device),
            ).logits.argmax(dim=-1).cpu().tolist()
            for actual, prediction in zip(labels.tolist(), predicted):
                confusion[actual][prediction] += 1
    per_class = {}
    for i, intent in enumerate(INTENTS):
        tp = confusion[i][i]
        actual = sum(confusion[i])
        predicted = sum(row[i] for row in confusion)
        per_class[intent] = {"count": actual, "f1": 2 * tp / (actual + predicted) if actual + predicted else 0}
    return {
        "accuracy": sum(confusion[i][i] for i in range(len(INTENTS))) / len(loader.dataset),
        "macro_f1": sum(item["f1"] for item in per_class.values()) / len(INTENTS),
        "per_class": per_class,
        "confusion_matrix": confusion,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-model", type=Path, required=True, help="本地中文 BERT 底座或已微调分类模型目录")
    # shortcut: 默认使用人工演示样本，真实客服分流前需更换标注数据并独立评估。
    parser.add_argument("--data", type=Path, default=PROJECT_ROOT / "backend/data/intent_context_v3.csv")
    parser.add_argument("--output", type=Path, default=MODEL_PATH)
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=1e-5)
    parser.add_argument("--context-turns", type=int, choices=(0, 1), default=1)
    parser.add_argument("--max-length", type=int, default=256)
    args = parser.parse_args()
    if not args.base_model.is_dir() or args.epochs < 1 or args.learning_rate <= 0:
        parser.error("需要有效的本地底座目录，epochs 和 learning-rate 必须大于 0")
    if not 16 <= args.max_length <= 512:
        parser.error("max-length 需要在 16–512 之间")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("输出目录非空，请指定新的 --output，避免覆盖已有模型")
    train, validation, test = load_data(args.data)
    if args.context_turns and any(row.get("history") and not row.get("dialogue_id") for row in train + validation + test):
        parser.error("上下文样本需要 dialogue_id，以检查同一会话没有跨集合")
    set_seed(42)
    tokenizer = BertTokenizer.from_pretrained(args.base_model, local_files_only=True)
    model = BertForSequenceClassification.from_pretrained(
        args.base_model, local_files_only=True, num_labels=len(INTENTS),
        id2label=dict(enumerate(INTENTS)), label2id={label: i for i, label in enumerate(INTENTS)},
    )
    model.config.intent_context_turns = args.context_turns
    model.config.intent_max_length = args.max_length
    device = torch.device("cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu")
    model.to(device)
    train_loader = DataLoader(encode(train, tokenizer, args.context_turns, args.max_length), batch_size=16, shuffle=True)
    validation_loader = DataLoader(encode(validation, tokenizer, args.context_turns, args.max_length), batch_size=16)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=0.01)
    best_f1 = -1
    print(f"训练 {len(train)} 条，验证 {len(validation)} 条；device={device}；演示数据指标仅供开发参考", flush=True)
    for epoch in range(1, args.epochs + 1):
        model.train()
        loss_total = 0
        for ids, mask, types, labels in train_loader:
            optimizer.zero_grad(set_to_none=True)
            loss = model(
                input_ids=ids.to(device), attention_mask=mask.to(device),
                token_type_ids=types.to(device), labels=labels.to(device),
            ).loss
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            loss_total += loss.item()
        metrics = evaluate(model, validation_loader, device)
        print(f"epoch={epoch} loss={loss_total / len(train_loader):.4f} accuracy={metrics['accuracy']:.4f} macro_f1={metrics['macro_f1']:.4f}", flush=True)
        if metrics["macro_f1"] > best_f1:
            best_f1 = metrics["macro_f1"]
            model.save_pretrained(args.output)
            tokenizer.save_pretrained(args.output)
            report = {
                "data": str(args.data.resolve()), "base_model": str(args.base_model.resolve()),
                "seed": 42, "epoch": epoch, "train_count": len(train), "validation_count": len(validation),
                "labels": list(INTENTS), "metrics": metrics,
                "context_turns": args.context_turns, "max_length": args.max_length,
                "learning_rate": args.learning_rate,
                "note": "人工编写的演示样本指标不代表真实客服准确率；验证集用于选模型，非独立测试集。",
            }
            (args.output / "evaluation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"已保存验证集 macro_f1 最优模型：{args.output}", flush=True)
    if test:
        # 测试集只在模型选定后评估，不参与训练或选轮次。
        best_model = BertForSequenceClassification.from_pretrained(args.output, local_files_only=True).to(device)
        test_metrics = evaluate(best_model, DataLoader(encode(test, tokenizer, args.context_turns, args.max_length), batch_size=16), device)
        report_path = args.output / "evaluation.json"
        report = json.loads(report_path.read_text())
        report.update(test_count=len(test), test_metrics=test_metrics)
        report["note"] = "人工开发语料；测试仅在选定模型后评估，不代表真实业务分布，调参用过的测试题应视为回归题。"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(f"测试集 {len(test)} 条 accuracy={test_metrics['accuracy']:.4f} macro_f1={test_metrics['macro_f1']:.4f}", flush=True)


if __name__ == "__main__":
    main()
