"""FAQ 标准答案与文档混合召回，共用版本化 Redis 正文校验。"""

import json
import logging
import os
from threading import Lock

import redis
from dotenv import load_dotenv
from pymilvus import AnnSearchRequest, MilvusClient, WeightedRanker

from ..common.constants import PROJECT_ROOT
from ..knowledge_service import review_status
from .models import HYBRID_SIGNATURE, encode_hybrid, relevance_scores, rerank_evidence

CATEGORIES = {"65539": "手机", "65540": "电脑", "65541": "空调", "65542": "冰箱", "65543": "洗衣机"}
CHANNELS = ("MALL", "DIRECT", "AUTHORIZED")
COLLECTION = "customer_graph_faq_official_v1"
FAQ_PREFIX = "customer_graph:faq:official:v1"
MODEL = "BAAI/bge-m3"
EMBEDDING_SIGNATURE = f"huggingface:{MODEL}:1024"
# shortcut: 沿用旧 FAQ 的初始阈值；扩充知识或出现误召回时重新校准，分数不是正确率。
MIN_SCORE = 0.62
MIN_MARGIN = 0.02
DOCUMENT_COLLECTION = "customer_graph_documents_fixed_v2"
DOCUMENT_PREFIX = "customer_graph:documents:fixed:v2"
logger = logging.getLogger(__name__)


class FaqRetriever:
    """每进程创建一次；异步节点通过 asyncio.to_thread 调用 search，结束时 close。"""

    def __init__(self):
        load_dotenv(PROJECT_ROOT / ".env")
        self._embedding_lock = Lock()
        self.database_url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
        self.faq_collection = os.getenv("RAG_FAQ_COLLECTION", COLLECTION)
        self.faq_prefix = os.getenv("RAG_FAQ_PREFIX", FAQ_PREFIX)
        self.document_collection = os.getenv("RAG_DOCUMENT_COLLECTION", DOCUMENT_COLLECTION)
        self.document_prefix = os.getenv("RAG_DOCUMENT_PREFIX", DOCUMENT_PREFIX)
        self.milvus = None
        self.phone_graph = None
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
            schema = self.milvus.describe_collection(self.faq_collection)
            fields = {field["name"]: field for field in schema["fields"]}
            if not {"faq_item_id", "text", "goodsids", "sales_channels", "vector"} <= fields.keys():
                raise RuntimeError("FAQ 集合字段不兼容")
            if int(fields["vector"]["params"]["dim"]) != 1024:
                raise RuntimeError("FAQ 集合向量维度与 BGE-M3 不一致")
            self.faq_has_brand = "brand" in fields
            indexes = [self.milvus.describe_index(self.faq_collection, name)
                       for name in self.milvus.list_indexes(self.faq_collection)]
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
        if os.getenv("RAG_NEO4J_URI"):
            try:
                from .phone_graph import PhoneGraph

                self.phone_graph = PhoneGraph()
            except Exception as exc:
                logger.warning("手机关系检索初始化失败，保留原知识检索：%s", type(exc).__name__)

    def _check_embedding_signature(self):
        meta = json.loads(self.redis.get(f"{self.faq_prefix}:meta") or "{}")
        if not isinstance(meta, dict) or meta.get("embedding") != EMBEDDING_SIGNATURE:
            raise RuntimeError("FAQ 数据缺失或入库模型与 BGE-M3 不一致")

    def search(self, query: str, *, goodsid: str = "", sales_channel: str = "MALL", brand: str = "") -> dict:
        """品类未知时先匹配候选；命中后补品类，不读取任意品类的答案。"""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("检索问题不能为空")
        if (goodsid and goodsid not in CATEGORIES) or sales_channel not in CHANNELS:
            raise ValueError("请提供支持的商品品类和销售渠道")
        self._check_embedding_signature()
        with self._embedding_lock:
            vector = self.embeddings.embed_query(query.strip())
        if len(vector) != 1024:
            raise RuntimeError("查询向量维度与 FAQ 集合不一致")
        # 过滤值只来自上面的白名单，不拼接用户问题。
        hits = self.milvus.search(
            collection_name=self.faq_collection, data=[vector], limit=20 if self.faq_has_brand else 10,
            filter=(f'ARRAY_CONTAINS(sales_channels, "{sales_channel}")'
                    + (f' and ARRAY_CONTAINS(goodsids, "{goodsid}")' if goodsid else "")
                    + (f' and (brand == "" or brand == {json.dumps(brand, ensure_ascii=False)})'
                       if brand and self.faq_has_brand else "")),
            output_fields=["faq_item_id", "text"],
            search_params={"metric_type": "COSINE"}, consistency_level="Strong", timeout=5,
        )[0]
        candidates = [{"faq_item_id": hit["entity"]["faq_item_id"], "question": hit["entity"]["text"],
                       "score": float(hit["distance"])} for hit in hits]
        if self.faq_has_brand:
            candidates = [item for item in candidates if item["score"] >= MIN_SCORE]
            with self._embedding_lock:
                scores = relevance_scores(query, [item["question"] for item in candidates])
            candidates = [{**item, "match_score": score} for item, score in zip(candidates, scores)]
        margin_key, margin = ("match_score", 0.5) if self.faq_has_brand else ("score", MIN_MARGIN)
        best = {}
        for item in candidates:
            item_id = item["faq_item_id"]
            if item_id not in best or item[margin_key] > best[item_id][margin_key]:
                best[item_id] = item
        ranked = sorted(best.values(), key=lambda item: item[margin_key], reverse=True)
        if not ranked or ranked[0]["score"] < MIN_SCORE:
            return {"status": "no_match", "evidence": [], "candidates": []}
        if self.faq_has_brand:
            logger.info("FAQ relevance ids=%s scores=%s", [item["faq_item_id"] for item in ranked],
                        [round(item["match_score"], 4) for item in ranked])
            if ranked[0]["match_score"] < 0:
                return {"status": "no_match", "evidence": [], "candidates": []}
        if not goodsid:
            return {"status": "needs_info", "missing_field": "goodsid", "evidence": [], "candidates": ranked[:1]}
        if len(ranked) > 1 and ranked[0][margin_key] - ranked[1][margin_key] < margin:
            return {"status": "ambiguous", "evidence": [], "candidates": ranked[:2]}

        match = ranked[0]
        item_id = match["faq_item_id"]
        prefix = f"{self.faq_prefix}:version:{item_id}:{goodsid}:{sales_channel}"
        version = self.redis.get(prefix)
        if not version:
            raise RuntimeError("命中 FAQ，但对应答案版本缺失")
        raw = self.redis.get(f"{self.faq_prefix}:answer:{item_id}:{goodsid}:{sales_channel}:{version}")
        answer = json.loads(raw or "null")
        expected = {"faq_item_id": item_id, "goodsid": goodsid, "sales_channel": sales_channel,
                    "market": "CN", "language": "zh-CN", "version": version}
        if (not isinstance(answer, dict)
                or any(answer.get(key) != value for key, value in expected.items())
                or not isinstance(answer.get("answer"), str) or not answer["answer"].strip()
                or not answer.get("title") or not answer.get("policy_id")):
            raise RuntimeError("FAQ 答案缺失或适用范围、版本不一致")
        status = review_status(answer, f"{self.faq_prefix}:answer:{item_id}:{goodsid}:{sales_channel}:{version}", self.database_url)
        if status not in ('approved', 'pending'):
            return {"status": "no_match", "evidence": [], "candidates": []}
        # 保留模拟标记；未知审批状态不能当作正式政策。
        evidence = {**answer, "score": round(match["score"], 4),
                    "evidence_id": f"faq:{answer['policy_id']}:{item_id}:{version}",
                    "excerpt": answer["answer"], "kind": "faq",
                    "simulated": answer.get("simulated") is not False,
                    "approved": status == 'approved', "review_status": status}
        logger.info("FAQ read collection=%s faq=%s redis_version=%s score=%.4f", self.faq_collection, item_id, version, match["score"])
        return {"status": "ok", "evidence": [evidence], "candidates": []}

    def search_documents(self, query: str, *, product_model: str | list[str]) -> dict:
        """沿用 04-rag 的 dense/sparse 混合召回，读取本项目的版本化正文。"""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("检索问题不能为空")
        meta = self.document_meta()
        if meta.get("embedding") != HYBRID_SIGNATURE:
            raise RuntimeError("文档知识尚未入库或模型不一致")
        requested = [product_model] if isinstance(product_model, str) else product_model
        if any(name not in meta.get("models", []) for name in requested):
            # 型号未接入是确定性结论，改写查询也不会命中，直接给出型号。
            return {"status": "no_model", "product_model": product_model, "evidence": [], "candidates": []}
        with self._embedding_lock:
            vectors = encode_hybrid([query.strip()])
        expr = f"product_model in {json.dumps(requested, ensure_ascii=False)}"
        top_k = int(os.getenv("RAG_SEARCH_TOP_K", "20"))
        requests = [
            AnnSearchRequest(data=[vectors[kind][0]], anns_field=f"{kind}_vector",
                             param={"metric_type": "IP"}, expr=expr, limit=top_k)
            for kind in ("dense", "sparse")
        ]
        hits = self.milvus.hybrid_search(
            collection_name=self.document_collection, reqs=requests,
            ranker=WeightedRanker(0.5, 0.5, norm_score=True),
            limit=int(os.getenv("RAG_HYBRID_TOP_K", "10")),
            output_fields=["document_id", "version", "product_model"],
            consistency_level="Strong", timeout=5,
        )[0]
        evidence = []
        for hit in hits:
            entity = hit["entity"]
            document_id, version = entity["document_id"], entity["version"]
            raw = self.redis.get(f"{self.document_prefix}:body:{document_id}:{version}")
            payload = json.loads(raw or "null")
            if not isinstance(payload, dict):
                raise RuntimeError("文档向量存在但 Redis 正文缺失")
            metadata = payload["metadata"]
            if (payload.get("id") != document_id or metadata.get("version") != version
                    or metadata.get("product_model") != entity["product_model"] or entity["product_model"] not in requested
                    or metadata.get("market") != "CN" or metadata.get("language") != "zh-CN"
                    or not payload.get("page_content")):
                raise RuntimeError("文档正文版本或适用范围不一致")
            status = review_status(payload, f"{self.document_prefix}:body:{document_id}:{version}", self.database_url)
            if status not in ('approved', 'pending'):
                continue
            evidence.append({
                "evidence_id": f"document:{document_id}:{version}", "kind": "document",
                "title": metadata["title"], "version": version, "source": metadata.get("source", document_id),
                "product_model": entity["product_model"], "excerpt": payload["page_content"],
                "hybrid_score": float(hit["distance"]),
                "simulated": metadata.get("simulated") is not False,
                "approved": status == 'approved', "review_status": status,
            })
        logger.info("Document hybrid collection=%s model=%s ids=%s", self.document_collection, product_model,
                    [item["evidence_id"] for item in evidence])
        return {"status": "ok" if evidence else "no_match", "evidence": evidence, "candidates": []}

    def document_meta(self):
        return json.loads(self.redis.get(f"{self.document_prefix}:meta") or "{}")

    def rerank(self, query, evidence, product_models=()):
        with self._embedding_lock:
            selected = rerank_evidence(query, evidence, product_models)
        logger.info("Document rerank candidates=%s selected=%s scores=%s", len(evidence),
                    [item["evidence_id"] for item in selected],
                    [round(item["rerank_score"], 4) for item in selected])
        return selected

    def close(self):
        try:
            if self.phone_graph is not None:
                self.phone_graph.close()
            if self.milvus is not None:
                self.milvus.close()
        finally:
            self.redis.close()
