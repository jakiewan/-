import os
from functools import lru_cache
from pathlib import Path
from typing import get_args

import torch
from transformers import BertForSequenceClassification, BertTokenizer

from ..common.constants import PROJECT_ROOT
from .state import Intent

INTENTS = get_args(Intent)
MAX_LENGTH = 128
MODEL_PATH = PROJECT_ROOT / "checkpoint" / "intent_classify" / "best_model"


class BertIntentClassifier:
    def __init__(self, model_path: str | Path):
        model_path = Path(model_path)
        if not model_path.is_dir():
            raise ValueError("未找到六类意图 BERT 模型，请先按 backend/graph/intent.md 微调，或配置 INTENT_MODEL_PATH")
        self.tokenizer = BertTokenizer.from_pretrained(model_path, local_files_only=True)
        self.model = BertForSequenceClassification.from_pretrained(model_path, local_files_only=True)
        labels = self.model.config.id2label
        if set(labels) != set(range(len(INTENTS))) or set(labels.values()) != set(INTENTS):
            raise ValueError("BERT 模型标签必须是咨询、投诉、售后、订单查询、转人工、闲聊，不能直接使用 shop 商品查询权重")
        self.device = torch.device("cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu")
        self.model.to(self.device)
        self.model.eval()

    def predict(self, text: str, history: str = "") -> dict:
        if not text.strip():
            raise ValueError("意图分类需要非空用户消息")
        inputs = self.tokenizer(
            text, text_pair=history, truncation=True, max_length=MAX_LENGTH, return_tensors="pt",
        ).to(self.device)
        with torch.inference_mode():
            probabilities = self.model(**inputs).logits.softmax(dim=-1)[0]
            label_id = probabilities.argmax().item()
            confidence = probabilities[label_id].item()
        intent = self.model.config.id2label[label_id]
        reason = f"BERT 分类：{intent}，置信度 {confidence:.1%}"
        if confidence < 0.5:
            reason += "；低置信度，需结合上下文确认"
        return {"intent": intent, "intent_reason": reason, "intent_confidence": confidence}


@lru_cache(maxsize=1)
def get_intent_classifier() -> BertIntentClassifier:
    return BertIntentClassifier(os.getenv("INTENT_MODEL_PATH") or MODEL_PATH)
