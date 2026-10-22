import os
from functools import lru_cache
from pathlib import Path
from typing import get_args

import torch
from transformers import BertForSequenceClassification, BertTokenizer

from ..common.constants import PROJECT_ROOT
from .intent_input import encode_intent_inputs
from .state import Intent

INTENTS = get_args(Intent)
MAX_LENGTH = 128
MODEL_PATH = PROJECT_ROOT / "checkpoint" / "intent_classify" / "bert_context_v3_continued"


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
        self.context_turns = getattr(self.model.config, "intent_context_turns", 0)
        self.max_length = getattr(self.model.config, "intent_max_length", MAX_LENGTH)
        self.device = torch.device("cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu")
        self.model.to(self.device)
        self.model.eval()

    def predict(self, text: str, history: str = "") -> dict:
        if not text.strip():
            raise ValueError("意图分类需要非空用户消息")
        inputs = encode_intent_inputs(
            self.tokenizer, [text], [history], self.context_turns, self.max_length,
        ).to(self.device)
        with torch.inference_mode():
            probabilities = self.model(**inputs).logits.softmax(dim=-1)[0]
            label_id = probabilities.argmax().item()
            confidence = probabilities[label_id].item()
        intent = self.model.config.id2label[label_id]
        context_used = bool(self.context_turns and (inputs["token_type_ids"][0] == 1).sum().item() > 1)
        return {
            "intent": intent, "intent_reason": "",
            "intent_confidence": confidence, "intent_context_used": context_used,
            "bert_intent": intent, "bert_confidence": confidence, "bert_context_used": context_used,
        }


@lru_cache(maxsize=1)
def get_intent_classifier() -> BertIntentClassifier:
    return BertIntentClassifier(os.getenv("INTENT_MODEL_PATH") or MODEL_PATH)
