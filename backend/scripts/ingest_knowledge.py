"""将 JSONL 文档写入独立 Milvus 集合和 Redis 正文区；重复导入按主键更新。"""

import argparse
import json
from pathlib import Path

from pymilvus import DataType, MilvusClient

from ..rag.models import HYBRID_SIGNATURE, encode_hybrid
from ..rag.retriever import FaqRetriever


def main():
    parser = argparse.ArgumentParser(description="导入短文档 JSONL，不删除现有集合或 Redis 键")
    parser.add_argument("path", type=Path, nargs="?")
    parser.add_argument("--migrate-v1", action="store_true", help="只读旧文档集合和 Redis，导入新双向量索引")
    args = parser.parse_args()
    if bool(args.path) == args.migrate_v1:
        parser.error("请提供 JSONL 路径，或使用 --migrate-v1，不能同时使用")
    retriever = FaqRetriever()
    try:
        if args.migrate_v1:
            rows = []
            iterator = retriever.milvus.query_iterator(
                collection_name="customer_graph_documents_v1", batch_size=100,
                output_fields=["document_id", "version"],
            )
            try:
                while batch := iterator.next():
                    for item in batch:
                        raw = retriever.redis.get(
                            f"customer_graph:documents:v1:body:{item['document_id']}:{item['version']}"
                        )
                        row = json.loads(raw or "null")
                        if (not isinstance(row, dict) or row.get("id") != item["document_id"]
                                or row.get("metadata", {}).get("version") != item["version"]):
                            raise RuntimeError("旧文档正文缺失或版本不一致，停止迁移")
                        rows.append(row)
            finally:
                iterator.close()
        else:
            rows = [json.loads(line) for line in args.path.read_text().splitlines() if line.strip()]
        ingest(retriever, rows)
    finally:
        retriever.close()


def ingest(retriever, rows):
    ids = set()
    for row in rows:
        metadata = row["metadata"]
        if (not row["id"] or row["id"] in ids or not row["page_content"].strip()
                or len(row["page_content"]) > 1500 or len(row["id"].encode()) > 128
                or metadata.get("market") != "CN" or metadata.get("language") != "zh-CN"
                or not all(metadata.get(key) for key in ("version", "title", "product_model"))):
            raise ValueError(f"知识内容、ID 或适用范围无效：{row.get('id')}")
        ids.add(row["id"])
    if not rows:
        raise ValueError("知识文件为空")
    client = retriever.milvus
    collection, prefix = retriever.document_collection, retriever.document_prefix
    if collection == "customer_graph_documents_v1" or prefix == "customer_graph:documents:v1":
        raise ValueError("双向量导入必须使用新集合和新 Redis 前缀，禁止覆盖 v1")
    if not client.has_collection(collection):
        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("document_id", DataType.VARCHAR, is_primary=True, max_length=128)
        schema.add_field("version", DataType.VARCHAR, max_length=128)
        schema.add_field("product_model", DataType.VARCHAR, max_length=128)
        schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=1024)
        schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
        index = client.prepare_index_params()
        index.add_index(field_name="dense_vector", index_type="AUTOINDEX", metric_type="IP")
        index.add_index(field_name="sparse_vector", index_type="SPARSE_INVERTED_INDEX", metric_type="IP")
        client.create_collection(collection, schema=schema, index_params=index)
    existing = json.loads(retriever.redis.get(f"{prefix}:meta") or "{}")
    if existing and existing.get("embedding") != HYBRID_SIGNATURE:
        raise RuntimeError("已有文档集合使用其他模型，禁止覆盖")
    for start in range(0, len(rows), 16):
        batch = rows[start:start + 16]
        with retriever._embedding_lock:
            vectors = encode_hybrid([row["page_content"] for row in batch])
        pipe = retriever.redis.pipeline(transaction=True)
        for row in batch:
            key = f"{prefix}:body:{row['id']}:{row['metadata']['version']}"
            pipe.set(key, json.dumps(row, ensure_ascii=False))
        pipe.execute()
        client.upsert(collection, [
            {"document_id": row["id"], "version": row["metadata"]["version"],
             "product_model": row["metadata"]["product_model"],
             "dense_vector": vectors["dense"][i], "sparse_vector": vectors["sparse"][i]}
            for i, row in enumerate(batch)
        ])
        print(f"已导入 {min(start + 16, len(rows))}/{len(rows)}", flush=True)
    client.flush(collection)
    client.load_collection(collection)
    model_goodsids = dict(existing.get("model_goodsids", {}))
    model_brands = dict(existing.get("model_brands", {}))
    for row in rows:
        metadata = row["metadata"]
        model = metadata["product_model"]
        goodsid = metadata.get("goodsid") or ("65539" if metadata.get("product_type") == "phone" else "")
        brand = metadata.get("brand") or ("GC" if metadata.get("simulated") is True and model.startswith("GC ") else "")
        if goodsid:
            model_goodsids[model] = goodsid
        if brand:
            model_brands[model] = brand
    retriever.redis.set(f"{prefix}:meta", json.dumps({
        "embedding": HYBRID_SIGNATURE,
        "models": sorted(set(existing.get("models", [])) | {r["metadata"]["product_model"] for r in rows}),
        "model_goodsids": model_goodsids,
        "model_brands": model_brands,
        "general_models": {**existing.get("general_models", {}),
                           **{r["metadata"]["brand"]: r["metadata"]["product_model"]
                              for r in rows if r["metadata"].get("general")}},
        "count": client.query(collection, filter="", output_fields=["count(*)"])[0]["count(*)"],
    }))
    print("文档双向量与 Redis 正文导入完成")


if __name__ == "__main__":
    main()
