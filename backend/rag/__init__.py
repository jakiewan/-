"""查询现有 Milvus FAQ 与 Redis 答案，不承担意图识别或数据入库。"""

from .retriever import FaqRetriever

__all__ = ["FaqRetriever"]
