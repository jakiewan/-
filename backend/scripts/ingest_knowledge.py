"""将 JSONL 文档写入独立 Milvus 集合和 Redis 正文区；重复导入按主键更新。"""

import argparse
import json
from pathlib import Path

from pymilvus import DataType, MilvusClient

from ..rag.retriever import DOCUMENT_COLLECTION, DOCUMENT_PREFIX, EMBEDDING_SIGNATURE, FaqRetriever


def main():
    parser = argparse.ArgumentParser(description="导入短文档 JSONL，不删除现有集合或 Redis 键")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.path.read_text().splitlines() if line.strip()]
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
    retriever = FaqRetriever()
    try:
        client = retriever.milvus
        if not client.has_collection(DOCUMENT_COLLECTION):
            schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field("document_id", DataType.VARCHAR, is_primary=True, max_length=128)
            schema.add_field("version", DataType.VARCHAR, max_length=128)
            schema.add_field("product_model", DataType.VARCHAR, max_length=128)
            schema.add_field("vector", DataType.FLOAT_VECTOR, dim=1024)
            index = client.prepare_index_params()
            index.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
            client.create_collection(DOCUMENT_COLLECTION, schema=schema, index_params=index)
        existing = json.loads(retriever.redis.get(f"{DOCUMENT_PREFIX}:meta") or "{}")
        if existing and existing.get("embedding") != EMBEDDING_SIGNATURE:
            raise RuntimeError("已有文档集合使用其他模型，禁止覆盖")
        for start in range(0, len(rows), 16):
            batch = rows[start:start + 16]
            vectors = retriever.embeddings.embed_documents([row["page_content"] for row in batch])
            pipe = retriever.redis.pipeline(transaction=True)
            for row in batch:
                key = f"{DOCUMENT_PREFIX}:body:{row['id']}:{row['metadata']['version']}"
                pipe.set(key, json.dumps(row, ensure_ascii=False))
            pipe.execute()
            client.upsert(DOCUMENT_COLLECTION, [
                {"document_id": row["id"], "version": row["metadata"]["version"],
                 "product_model": row["metadata"]["product_model"], "vector": vector}
                for row, vector in zip(batch, vectors)
            ])
            print(f"已导入 {min(start + 16, len(rows))}/{len(rows)}", flush=True)
        client.flush(DOCUMENT_COLLECTION)
        client.load_collection(DOCUMENT_COLLECTION)
        retriever.redis.set(f"{DOCUMENT_PREFIX}:meta", json.dumps({
            "embedding": EMBEDDING_SIGNATURE,
            "models": sorted(set(existing.get("models", [])) | {r["metadata"]["product_model"] for r in rows}),
            "count": client.query(DOCUMENT_COLLECTION, filter="", output_fields=["count(*)"])[0]["count(*)"],
        }))
        print("文档向量与 Redis 正文导入完成")
    finally:
        retriever.close()


if __name__ == "__main__":
    main()
