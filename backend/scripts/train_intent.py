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


def load_data(path: Path) -> tuple[list[dict], list[dict]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    seen = set()
    for row in rows:
        if not row["text"].strip() or row["intent"] not in INTENTS or row["split"] not in ("train", "validation"):
            raise ValueError("CSV 需要非空 text、六类 intent 和 train/validation split")
        key = (row["text"].strip(), row.get("history", "").strip())
        if key in seen:
            raise ValueError(f"存在重复样本或训练/验证数据重叠：{key[0]}")
        seen.add(key)
    train = [row for row in rows if row["split"] == "train"]
    validation = [row for row in rows if row["split"] == "validation"]
    if any({row["intent"] for row in split} != set(INTENTS) for split in (train, validation)):
        raise ValueError("训练集和验证集都必须覆盖六类意图")
    return train, validation


def encode(rows: list[dict], tokenizer: BertTokenizer) -> TensorDataset:
    inputs = tokenizer(
        [row["text"] for row in rows], text_pair=[row.get("history", "") for row in rows],
        truncation=True, max_length=MAX_LENGTH, padding=True, return_tensors="pt",
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
    parser.add_argument("--base-model", type=Path, required=True, help="本地 bert-base-chinese 预训练目录")
    # shortcut: 默认使用人工演示样本，真实客服分流前需更换标注数据并独立评估。
    parser.add_argument("--data", type=Path, default=PROJECT_ROOT / "backend/data/intent_demo.csv")
    parser.add_argument("--output", type=Path, default=MODEL_PATH)
    parser.add_argument("--epochs", type=int, default=8)
    args = parser.parse_args()
    if not args.base_model.is_dir() or args.epochs < 1:
        parser.error("需要有效的本地底座目录，epochs 必须大于 0")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("输出目录非空，请指定新的 --output，避免覆盖已有模型")
    train, validation = load_data(args.data)
    set_seed(42)
    tokenizer = BertTokenizer.from_pretrained(args.base_model, local_files_only=True)
    model = BertForSequenceClassification.from_pretrained(
        args.base_model, local_files_only=True, num_labels=len(INTENTS),
        id2label=dict(enumerate(INTENTS)), label2id={label: i for i, label in enumerate(INTENTS)},
    )
    device = torch.device("cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu")
    model.to(device)
    train_loader = DataLoader(encode(train, tokenizer), batch_size=16, shuffle=True)
    validation_loader = DataLoader(encode(validation, tokenizer), batch_size=16)
    optimizer = torch.optim.AdamW(model.parameters(), lr=3e-5, weight_decay=0.01)
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
                "note": "人工编写的演示样本指标不代表真实客服准确率；验证集用于选模型，非独立测试集。",
            }
            (args.output / "evaluation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"已保存验证集 macro_f1 最优模型：{args.output}", flush=True)


if __name__ == "__main__":
    main()
