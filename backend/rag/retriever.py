"""BGE-M3 → Milvus FAQ 召回 → Redis 版本化答案，只读现有知识。"""

import json
import logging
import os
from threading import Lock

import redis
from dotenv import load_dotenv
from pymilvus import MilvusClient

from ..common.constants import PROJECT_ROOT

CATEGORIES = {"65539": "手机", "65540": "电脑", "65541": "空调", "65542": "冰箱", "65543": "洗衣机"}
CHANNELS = ("MALL", "DIRECT", "AUTHORIZED")
COLLECTION = "faq_question"
MODEL = "BAAI/bge-m3"
EMBEDDING_SIGNATURE = f"huggingface:{MODEL}:1024"
# shortcut: 沿用旧 FAQ 的初始阈值；扩充知识或出现误召回时重新校准，分数不是正确率。
MIN_SCORE = 0.62
MIN_MARGIN = 0.02
DOCUMENT_COLLECTION = "customer_graph_documents_v1"
DOCUMENT_PREFIX = "customer_graph:documents:v1"
logger = logging.getLogger(__name__)


class FaqRetriever:
    """每进程创建一次；异步节点通过 asyncio.to_thread 调用 search，结束时 close。"""

    def __init__(self):
        load_dotenv(PROJECT_ROOT / ".env")
        self._embedding_lock = Lock()
        self.milvus = None
        self.redis = redis.Redis.from_url(
            os.getenv("RAG_REDIS_URL", "redis://127.0.0.1:56379/0"),
            decode_responses=True, socket_timeout=3, socket_connect_timeout=3,
        )
        try:
            self._check_embedding_signature()
            self.milvus = MilvusClient(
                uri=os.getenv("RAG_MILVUS_URI", "http://127.0.0.1:19530"),
                db_name=os.getenv("RAG_MILVUS_DB", "ai_customer_service"), timeout=5,
            )
            schema = self.milvus.describe_collection(COLLECTION)
            fields = {field["name"]: field for field in schema["fields"]}
            if not {"faq_item_id", "text", "goodsids", "sales_channels", "vector"} <= fields.keys():
                raise RuntimeError("FAQ 集合字段不兼容")
            if int(fields["vector"]["params"]["dim"]) != 1024:
                raise RuntimeError("FAQ 集合向量维度与 BGE-M3 不一致")
            indexes = [self.milvus.describe_index(COLLECTION, name)
                       for name in self.milvus.list_indexes(COLLECTION)]
            if not any(index.get("field_name") == "vector" and index.get("metric_type") == "COSINE"
                       for index in indexes):
                raise RuntimeError("FAQ 集合缺少 COSINE 向量索引")

            from langchain_huggingface import HuggingFaceEmbeddings

            # 仅使用已缓存的同一模型；不联网下载，不降级到无语义的 hash 向量。
            self.embeddings = HuggingFaceEmbeddings(
                model_name=MODEL,
                model_kwargs={"device": "cpu", "local_files_only": True},
                encode_kwargs={"normalize_embeddings": True},
            )
        except Exception:
            self.close()
            raise

    def _check_embedding_signature(self):
        meta = json.loads(self.redis.get("gc:faq:meta") or "{}")
        if not isinstance(meta, dict) or meta.get("embedding") != EMBEDDING_SIGNATURE:
            raise RuntimeError("FAQ 数据缺失或入库模型与 BGE-M3 不一致")

    def search(self, query: str, *, goodsid: str, sales_channel: str = "MALL") -> dict:
        """只需提供商品品类，渠道默认商城；返回 ok/no_match/ambiguous，故障抛异常。"""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("检索问题不能为空")
        if goodsid not in CATEGORIES or sales_channel not in CHANNELS:
            raise ValueError("请提供支持的商品品类和销售渠道")
        self._check_embedding_signature()
        with self._embedding_lock:
            vector = self.embeddings.embed_query(query.strip())
        if len(vector) != 1024:
            raise RuntimeError("查询向量维度与 FAQ 集合不一致")
        # 过滤值只来自上面的白名单，不拼接用户问题。
        hits = self.milvus.search(
            collection_name=COLLECTION, data=[vector], limit=10,
            filter=f'ARRAY_CONTAINS(goodsids, "{goodsid}") and ARRAY_CONTAINS(sales_channels, "{sales_channel}")',
            output_fields=["faq_item_id", "text"],
            search_params={"metric_type": "COSINE"}, timeout=5,
        )[0]
        best = {}
        for hit in hits:
            item_id = hit["entity"]["faq_item_id"]
            score = float(hit["distance"])
            if item_id not in best or score > best[item_id]["score"]:
                best[item_id] = {"faq_item_id": item_id, "question": hit["entity"]["text"], "score": score}
        ranked = sorted(best.values(), key=lambda item: item["score"], reverse=True)
        if not ranked or ranked[0]["score"] < MIN_SCORE:
            return {"status": "no_match", "evidence": [], "candidates": []}
        if len(ranked) > 1 and ranked[0]["score"] - ranked[1]["score"] < MIN_MARGIN:
            return {"status": "ambiguous", "evidence": [], "candidates": ranked[:2]}

        match = ranked[0]
        item_id = match["faq_item_id"]
        prefix = f"gc:faq:version:{item_id}:{goodsid}:{sales_channel}"
        version = self.redis.get(prefix)
        if not version:
            raise RuntimeError("命中 FAQ，但对应答案版本缺失")
        raw = self.redis.get(f"gc:faq:answer:{item_id}:{goodsid}:{sales_channel}:{version}")
        answer = json.loads(raw or "null")
        expected = {"faq_item_id": item_id, "goodsid": goodsid, "sales_channel": sales_channel,
                    "market": "CN", "language": "zh-CN", "version": version}
        if (not isinstance(answer, dict)
                or any(answer.get(key) != value for key, value in expected.items())
                or not isinstance(answer.get("answer"), str) or not answer["answer"].strip()
                or not answer.get("title") or not answer.get("policy_id")):
            raise RuntimeError("FAQ 答案缺失或适用范围、版本不一致")
        # 保留模拟标记；未知审批状态不能当作正式政策。
        evidence = {**answer, "score": round(match["score"], 4),
                    "evidence_id": f"faq:{answer['policy_id']}:{item_id}:{version}",
                    "excerpt": answer["answer"], "kind": "faq",
                    "simulated": answer.get("simulated") is not False,
                    "approved": answer.get("approved") is True}
        logger.info("FAQ read collection=%s faq=%s redis_version=%s score=%.4f", COLLECTION, item_id, version, match["score"])
        return {"status": "ok", "evidence": [evidence], "candidates": []}

    def search_documents(self, query: str, *, product_model: str) -> dict:
        """按知识目录校验型号，真实查询文档向量，再从 Redis 取对应版本正文。"""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("检索问题不能为空")
        meta = json.loads(self.redis.get(f"{DOCUMENT_PREFIX}:meta") or "{}")
        if meta.get("embedding") != EMBEDDING_SIGNATURE:
            raise RuntimeError("文档知识尚未入库或模型不一致")
        if product_model not in meta.get("models", []):
            # 型号未接入是确定性结论，改写查询也不会命中，直接给出型号。
            return {"status": "no_model", "product_model": product_model, "evidence": [], "candidates": []}
        with self._embedding_lock:
            vector = self.embeddings.embed_query(query.strip())
        hits = self.milvus.search(
            collection_name=DOCUMENT_COLLECTION, data=[vector], limit=3,
            # 当前 Milvus 不支持过滤模板；型号已从知识目录校验，再按字符串字面量转义。
            filter=f"product_model == {json.dumps(product_model, ensure_ascii=False)}",
            output_fields=["document_id", "version"],
            search_params={"metric_type": "COSINE"}, timeout=5,
        )[0]
        evidence = []
        for hit in hits:
            if float(hit["distance"]) < MIN_SCORE:
                continue
            entity = hit["entity"]
            document_id, version = entity["document_id"], entity["version"]
            raw = self.redis.get(f"{DOCUMENT_PREFIX}:body:{document_id}:{version}")
            payload = json.loads(raw or "null")
            if not isinstance(payload, dict):
                raise RuntimeError("文档向量存在但 Redis 正文缺失")
            metadata = payload["metadata"]
            if (payload.get("id") != document_id or metadata.get("version") != version
                    or metadata.get("product_model") != product_model
                    or metadata.get("market") != "CN" or metadata.get("language") != "zh-CN"
                    or not payload.get("page_content")):
                raise RuntimeError("文档正文版本或适用范围不一致")
            evidence.append({
                "evidence_id": f"document:{document_id}:{version}", "kind": "document",
                "title": metadata["title"], "version": version, "source": metadata.get("source", document_id),
                "product_model": product_model, "excerpt": payload["page_content"],
                "score": round(float(hit["distance"]), 4),
                "simulated": metadata.get("simulated") is not False,
                "approved": metadata.get("approved") is True,
            })
        logger.info("Document read collection=%s model=%s ids=%s", DOCUMENT_COLLECTION, product_model,
                    [item["evidence_id"] for item in evidence])
        return {"status": "ok" if evidence else "no_match", "evidence": evidence, "candidates": []}

    def close(self):
        try:
            if self.milvus is not None:
                self.milvus.close()
        finally:
            self.redis.close()
