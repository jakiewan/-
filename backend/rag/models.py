"""复用 04-rag 的 BGE-M3 双向量编码、RRF 和独立重排。"""

import os
from functools import lru_cache
from pathlib import Path

from huggingface_hub import snapshot_download

HYBRID_SIGNATURE = "flagembedding:BAAI/bge-m3:1024:dense+sparse:normalized:v1"


def local_model(path):
    return str(Path(path).resolve()) if Path(path).is_dir() else snapshot_download(path, local_files_only=True)


@lru_cache(maxsize=1)
def embedding_model():
    from FlagEmbedding import BGEM3FlagModel

    return BGEM3FlagModel(
        local_model(os.getenv("RAG_BGE_M3_MODEL_PATH", "BAAI/bge-m3")),
        devices=os.getenv("RAG_MODEL_DEVICE", "cpu"), use_fp16=False,
        normalize_embeddings=True,
    )


def encode_hybrid(texts):
    encoded = embedding_model().encode(
        texts, batch_size=8, max_length=2048,
        return_dense=True, return_sparse=True, return_colbert_vecs=False,
    )
    return {
        "dense": [vector.tolist() for vector in encoded["dense_vecs"]],
        "sparse": [{int(token): float(weight) for token, weight in weights.items()}
                   for weights in encoded["lexical_weights"]],
    }


@lru_cache(maxsize=1)
def reranker_model():
    from FlagEmbedding import FlagReranker

    return FlagReranker(
        local_model(os.getenv("RAG_RERANKER_MODEL_PATH", "BAAI/bge-reranker-base")),
        devices=os.getenv("RAG_MODEL_DEVICE", "cpu"), use_fp16=False,
    )


def fuse_evidence(original, hyde, *, k=60, limit=10):
    """04-rag/18-rrf.py 的带权排名融合；用含版本的证据 ID 去重。"""
    scores, items = {}, {}
    for ranked, weight in ((original, 1.0), (hyde, 0.7)):
        for rank, item in enumerate(ranked, 1):
            key = item["evidence_id"]
            scores[key] = scores.get(key, 0.0) + weight / (k + rank)
            items.setdefault(key, item)
    keys = sorted(scores, key=scores.get, reverse=True)[:limit]
    return [{**items[key], "rrf_score": scores[key]} for key in keys]


def relevance_scores(query, texts):
    if not texts:
        return []
    scores = reranker_model().compute_score(
        [[query, text] for text in texts], batch_size=8,
    )
    if isinstance(scores, (int, float)):
        scores = [scores]
    return [float(score) for score in scores]


def rerank_evidence(query, evidence, product_models=()):
    scores = relevance_scores(query, [item["excerpt"] for item in evidence])
    ranked = sorted(
        [{**item, "rerank_score": float(score)} for item, score in zip(evidence, scores)],
        key=lambda item: item["rerank_score"], reverse=True,
    )
    # shortcut: 沿用原始打分和分差起点，扩充题集时重新校准；分数不是正确率。
    minimum = float(os.getenv("RAG_RERANK_MIN_SCORE", "0"))
    limit = int(os.getenv("RAG_FINAL_TOP_K", "5"))
    gap_abs = float(os.getenv("RAG_RERANK_GAP_ABS", "0.5"))
    gap_ratio = float(os.getenv("RAG_RERANK_GAP_RATIO", "0.25"))
    groups = [[item for item in ranked if item["product_model"] == name] for name in product_models] if len(product_models) > 1 else [ranked]
    per_model = max(1, limit // len(groups))
    budget, selected = 8000, []
    for group in groups:
        kept = 0
        for index, item in enumerate(group):
            if item["rerank_score"] < minimum:
                break
            if len(item["excerpt"]) > budget:
                continue
            selected.append(item)
            budget -= len(item["excerpt"])
            kept += 1
            if kept >= per_model or len(selected) >= limit:
                break
            if index + 1 < len(group):
                gap = item["rerank_score"] - group[index + 1]["rerank_score"]
                if gap >= gap_abs or gap / (abs(item["rerank_score"]) + 1e-6) >= gap_ratio:
                    break
        if len(selected) >= limit:
            break
    return selected
