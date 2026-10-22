"""导入官方手机 FAQ 到独立索引，保留旧库及其他品类的演示问答。"""

import argparse
import hashlib
import json
import os
from pathlib import Path

import redis
from dotenv import load_dotenv
from langchain_huggingface import HuggingFaceEmbeddings
from pymilvus import DataType, MilvusClient

from ..common.constants import PROJECT_ROOT
from ..rag.retriever import EMBEDDING_SIGNATURE, MODEL


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    collection = os.getenv("RAG_FAQ_COLLECTION", "customer_graph_faq_official_v1")
    prefix = os.getenv("RAG_FAQ_PREFIX", "customer_graph:faq:official:v1")
    if collection == "faq_question" or prefix == "gc:faq":
        raise ValueError("官方 FAQ 必须使用独立集合和前缀，禁止覆盖旧库")
    policies = [json.loads(line) for line in args.path.read_text().splitlines() if line.strip()]
    ids = set()
    for row in policies:
        if (row["faq_item_id"] in ids or not row["questions"] or not row["answer"].strip()
                or row["brand"] != "小米" or row["goodsid"] != "65539"
                or not row["source"].startswith("https://") or not row["version"]):
            raise ValueError("官方 FAQ 内容或适用范围无效")
        ids.add(row["faq_item_id"])
    if not policies:
        raise ValueError("FAQ 为空")
    r = redis.Redis.from_url(os.getenv("RAG_REDIS_URL", "redis://127.0.0.1:56379/0"), decode_responses=True)
    c = MilvusClient(uri=os.getenv("RAG_MILVUS_URI", "http://127.0.0.1:19530"),
                     db_name=os.getenv("RAG_MILVUS_DB", "ai_customer_service"))
    try:
        if json.loads(r.get("gc:faq:meta") or "{}").get("embedding") != EMBEDDING_SIGNATURE:
            raise RuntimeError("源 FAQ 模型不一致")
        if not c.has_collection(collection):
            schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=64)
            schema.add_field("faq_item_id", DataType.VARCHAR, max_length=64)
            schema.add_field("text", DataType.VARCHAR, max_length=1024)
            schema.add_field("brand", DataType.VARCHAR, max_length=64)
            for field, capacity in (("goodsids", 64), ("sales_channels", 8)):
                schema.add_field(field, DataType.ARRAY, element_type=DataType.VARCHAR,
                                 max_capacity=capacity, max_length=16)
            schema.add_field("vector", DataType.FLOAT_VECTOR, dim=1024)
            index = c.prepare_index_params()
            index.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
            c.create_collection(collection, schema=schema, index_params=index)
        old = c.query("faq_question", filter="", limit=1000,
                      output_fields=["id", "faq_item_id", "text", "goodsids", "sales_channels", "vector"])
        retained = []
        for row in old:
            row["goodsids"] = [value for value in row["goodsids"] if value != "65539"]
            if row["goodsids"]:
                retained.append({**row, "brand": ""})
        for key in r.scan_iter("gc:faq:answer:*"):
            value = json.loads(r.get(key))
            if value["goodsid"] != "65539":
                r.set(key.replace("gc:faq", prefix, 1), json.dumps(value, ensure_ascii=False))
                old_pointer = f"gc:faq:version:{value['faq_item_id']}:{value['goodsid']}:{value['sales_channel']}"
                current_version = r.get(old_pointer)
                if current_version:
                    r.set(old_pointer.replace("gc:faq", prefix, 1), current_version)
        encoder = HuggingFaceEmbeddings(model_name=MODEL,
            model_kwargs={"device": "cpu", "local_files_only": True},
            encode_kwargs={"normalize_embeddings": True})
        records = []
        for row in policies:
            vectors = encoder.embed_documents(row["questions"])
            for question, vector in zip(row["questions"], vectors):
                records.append({"id": hashlib.sha256((row["faq_item_id"] + question).encode()).hexdigest(),
                                "faq_item_id": row["faq_item_id"], "text": question,
                                "brand": row["brand"], "goodsids": [row["goodsid"]],
                                "sales_channels": row["sales_channels"], "vector": vector})
            for channel in row["sales_channels"]:
                answer = {**{k: v for k, v in row.items() if k not in ("questions", "sales_channels")},
                          "sales_channel": channel, "market": "CN", "language": "zh-CN",
                          "simulated": False, "approved": False}
                r.set(f"{prefix}:answer:{row['faq_item_id']}:65539:{channel}:{row['version']}", json.dumps(answer, ensure_ascii=False))
                r.set(f"{prefix}:version:{row['faq_item_id']}:65539:{channel}", row["version"])
        c.upsert(collection, retained + records)
        c.flush(collection)
        c.load_collection(collection)
        r.set(f"{prefix}:meta", json.dumps({"embedding": EMBEDDING_SIGNATURE,
              "official_phone_items": len(policies), "retained_demo_questions": len(retained)}))
        print(f"官方手机 FAQ {len(policies)} 项、{len(records)} 个问法；保留其他品类 {len(retained)} 个问法。旧库未修改。")
    finally:
        c.close()
        r.close()


if __name__ == "__main__":
    main()
