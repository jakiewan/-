"""从本地 Markdown 同步多品牌手机文档与 FAQ，不访问网页或调用 LLM。"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

import yaml

# 与工作流入口一致，兼容 IDE 直接运行文件。
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = "backend.scripts"

from ..common.constants import PROJECT_ROOT
from ..rag.retriever import CHANNELS, FaqRetriever
from .ingest_knowledge import ingest

KNOWLEDGE_DIR = PROJECT_ROOT / "backend/data/knowledge"
# 一个资料集对应一个品牌：目录、品牌、品类、问法前缀、来源域名和索引标记都在这里声明。
CORPORA = (
    {"dir": "xiaomi-cn", "brand": "小米", "goodsid": "65539", "faq_prefix": "xiaomi",
     "hosts": ("mi.com",), "corpus": "xiaomi-cn-markdown"},
    {"dir": "oppo-cn", "brand": "OPPO", "goodsid": "65539", "faq_prefix": "oppo",
     "hosts": ("oppo.com",), "corpus": "oppo-cn-markdown"},
    {"dir": "huawei-cn", "brand": "华为", "goodsid": "65539", "faq_prefix": "huawei",
     "hosts": ("huawei.com",), "corpus": "huawei-cn-markdown"},
)
BRANDS = tuple(config["brand"] for config in CORPORA)
GOODSIDS = tuple(dict.fromkeys(config["goodsid"] for config in CORPORA))
CORPUS_NAMES = frozenset(config["corpus"] for config in CORPORA)


def split_text(text, limit=1100):
    """优先保留段落和完整句子，保证加上标题后不超过现有入库长度。"""
    chunks, current = [], ""
    for paragraph in re.split(r"\n\s*\n", text.strip()):
        while len(paragraph) > limit:
            end = max(paragraph.rfind("\n", 0, limit), paragraph.rfind("。", 0, limit))
            end = end + 1 if end >= limit // 2 else limit
            if current:
                chunks.append(current)
                current = ""
            chunks.append(paragraph[:end].strip())
            paragraph = paragraph[end:].strip()
        if current and len(current) + len(paragraph) + 2 > limit:
            chunks.append(current)
            current = ""
        current = "\n\n".join(part for part in (current, paragraph) if part)
    if current:
        chunks.append(current)
    return chunks


def load_knowledge(config, file_ids, faq_ids):
    """读取一个品牌资料集的 Markdown，按章节切块并生成 FAQ。"""
    documents, faqs = [], []
    source_dir = KNOWLEDGE_DIR / config["dir"] / "原始资料"
    for path in sorted(source_dir.rglob("*.md")):
        if path.name == "README.md":
            continue
        text = path.read_text(encoding="utf-8")
        match = re.fullmatch(r"---\s*\n(.*?)\n---\s*\n(.*)", text, re.S)
        if not match:
            raise ValueError(f"{path.name} 缺少 YAML 元数据")
        meta, body = yaml.safe_load(match[1]), match[2]
        file_id = meta.get("knowledge_id", "") if isinstance(meta, dict) else ""
        models = meta.get("product_models", []) if isinstance(meta, dict) else []
        source = urlsplit(meta.get("source", "")) if isinstance(meta, dict) else urlsplit("")
        host = source.hostname or ""
        if (not re.fullmatch(r"[a-z0-9-]{1,64}", file_id) or file_id in file_ids
                or not isinstance(models, list) or not models
                or any(not isinstance(name, str) or not name.strip() or len(name.encode()) > 128 for name in models)
                or len(set(models)) != len(models)
                or meta.get("brand") != config["brand"] or str(meta.get("goodsid")) != config["goodsid"]
                or meta.get("market") != "CN" or meta.get("language") != "zh-CN"
                or source.scheme != "https"
                or not any(host == allowed or host.endswith("." + allowed) for allowed in config["hosts"])
                or (meta.get("general_model") and meta["general_model"] not in models)):
            raise ValueError(f"{path.name} 的知识 ID、型号或适用范围无效")
        file_ids.add(file_id)
        # 快照哈希仅用于追溯；每次都按当前 Markdown 生成版本，无需手工维护版本号。
        version = "md-" + hashlib.sha256(text.encode()).hexdigest()[:24]
        clean = re.sub(r"^# [^\n]*\n", "", body, flags=re.M)
        clean = re.sub(r"^> 本文件[^\n]*\n?", "", clean, flags=re.M)
        headings = list(re.finditer(r"^#{2,6} (.+)$", clean, re.M))
        sections = {"概览": clean[:headings[0].start()].strip() if headings else clean.strip()}
        for index, heading in enumerate(headings):
            title = heading[1].strip()
            if title in sections or len(title) > 100:
                raise ValueError(f"{path.name} 标题重复或过长：{title}")
            end = headings[index + 1].start() if index + 1 < len(headings) else len(clean)
            sections[title] = clean[heading.end():end].strip()
        excluded = meta.get("exclude_sections", [])
        if not isinstance(excluded, list) or any(title not in sections for title in excluded):
            raise ValueError(f"{path.name} 的 exclude_sections 与正文标题不一致")
        for title in excluded:
            del sections[title]
        metadata = {
            "version": version, "source": meta["source"], "market": "CN", "language": "zh-CN",
            "brand": config["brand"], "goodsid": config["goodsid"], "simulated": False,
            "approved": meta.get("review_status") == "approved", "knowledge_corpus": config["corpus"],
            "source_file": path.relative_to(KNOWLEDGE_DIR).as_posix(),
            "retrieved_at": str(meta.get("captured_on", "")),
        }
        for title, content in sections.items():
            for number, chunk in enumerate(split_text(content)):
                for model in models:
                    key = hashlib.sha256(f"{title}:{number}:{model}".encode()).hexdigest()[:24]
                    documents.append({
                        "id": f"md-{file_id}-{key}",
                        "page_content": f"# {model} · {title}\n\n适用范围：中国大陆{config['brand']}手机；型号：{model}。\n\n{chunk}",
                        "metadata": {**metadata, "title": f"{model}：{title}", "product_model": model,
                                     "general": model == meta.get("general_model")},
                    })
        for faq in meta.get("faqs", []):
            item_id, questions = faq.get("id", ""), faq.get("questions", [])
            channels, titles = faq.get("sales_channels", list(CHANNELS)), faq.get("sections", [])
            if (not re.fullmatch(rf"{config['faq_prefix']}_[a-z0-9_]{{1,64}}", item_id) or item_id in faq_ids
                    or not isinstance(questions, list) or not questions
                    or any(not isinstance(q, str) or not q.strip() or len(q.encode()) > 1024 for q in questions)
                    or len(set(questions)) != len(questions)
                    or not isinstance(channels, list) or not channels or any(c not in CHANNELS for c in channels)
                    or not isinstance(titles, list) or not titles or any(not sections.get(t) for t in titles)):
                raise ValueError(f"{path.name} 的 FAQ {item_id} 问法、渠道或正文标题无效")
            faq_ids.add(item_id)
            faqs.append({
                "faq_item_id": item_id, "policy_id": f"MD-{file_id}-{item_id}",
                "title": faq.get("title") or titles[0], "questions": questions, "sales_channels": channels,
                "answer": f"适用范围：中国大陆{config['brand']}手机。\n\n" + "\n\n".join(sections[t] for t in titles),
                **{key: value for key, value in metadata.items() if key not in ("knowledge_corpus",)},
            })
    if not documents:
        raise ValueError(f"{config['dir']} 没有有效 Markdown 正文，停止同步")
    return documents, faqs


def load_all():
    """按资料集顺序读取全部品牌，ID 在所有品牌间保持唯一。"""
    documents, faqs, file_ids, faq_ids = [], [], set(), set()
    for config in CORPORA:
        docs, items = load_knowledge(config, file_ids, faq_ids)
        documents += docs
        faqs += items
    if len({row["id"] for row in documents}) != len(documents):
        raise ValueError("文档 ID 重复，停止同步")
    if any(len(row["page_content"]) > 1500 or len(row["id"].encode()) > 128 for row in documents):
        raise ValueError("切块后的文档超出现有索引长度限制")
    return documents, faqs


def scan_records(client, collection, fields):
    iterator = client.query_iterator(collection_name=collection, batch_size=100,
                                     output_fields=fields, consistency_level="Strong")
    rows = []
    try:
        while batch := iterator.next():
            rows.extend(batch)
    finally:
        iterator.close()
    return rows


def synchronize(retriever, documents, faqs):
    """先写入并核对新数据，再删除本资料集的过期索引；保留历史正文用于追溯。"""
    client, redis = retriever.milvus, retriever.redis
    collection, prefix = retriever.document_collection, retriever.document_prefix
    faq_collection, faq_prefix = retriever.faq_collection, retriever.faq_prefix
    if collection == "customer_graph_documents_v1" or faq_collection == "faq_question" or faq_prefix == "gc:faq":
        raise ValueError("Markdown 同步必须使用当前独立索引，禁止覆盖旧库")
    old_documents = scan_records(client, collection, ["document_id", "version", "product_model"])
    managed_documents = set()
    for row in old_documents:
        raw = redis.get(f"{prefix}:body:{row['document_id']}:{row['version']}")
        if not raw:
            raise RuntimeError("当前文档正文缺失，停止同步以免误删")
        meta = json.loads(raw)["metadata"]
        if meta.get("knowledge_corpus") in CORPUS_NAMES or (meta.get("brand") in BRANDS
                                                           and str(meta.get("goodsid")) in GOODSIDS):
            managed_documents.add(row["document_id"])
    old_faqs = scan_records(client, faq_collection, ["id", "faq_item_id", "brand", "goodsids"])
    managed_faqs = {row["id"] for row in old_faqs
                    if row["brand"] in BRANDS and any(goodsid in row["goodsids"] for goodsid in GOODSIDS)}
    old_faq_ids = {row["faq_item_id"] for row in old_faqs if row["id"] in managed_faqs}

    questions = [(faq, question) for faq in faqs for question in faq["questions"]]
    vectors = retriever.embeddings.embed_documents([q for _, q in questions]) if questions else []
    faq_records = [
        {"id": hashlib.sha256((faq["faq_item_id"] + question).encode()).hexdigest(),
         "faq_item_id": faq["faq_item_id"], "text": question, "brand": faq["brand"],
         "goodsids": [faq["goodsid"]], "sales_channels": faq["sales_channels"], "vector": vector}
        for (faq, question), vector in zip(questions, vectors)
    ]
    if len(vectors) != len(questions) or any(len(v) != 1024 for v in vectors):
        raise RuntimeError("FAQ 编码数量或维度不一致")

    ingest(retriever, documents)
    expected = {row["id"]: row["metadata"]["version"] for row in documents}
    actual = client.get(collection, ids=list(expected), output_fields=["document_id", "version"], consistency_level="Strong")
    if {row["document_id"]: row["version"] for row in actual} != expected:
        raise RuntimeError("新文档索引核对失败，保留旧索引，修复后重新运行")
    pipe = redis.pipeline(transaction=True)
    for faq in faqs:
        for channel in faq["sales_channels"]:
            answer = {key: value for key, value in faq.items() if key not in ("questions", "sales_channels")}
            answer.update(sales_channel=channel)
            pipe.set(f"{faq_prefix}:answer:{faq['faq_item_id']}:{faq['goodsid']}:{channel}:{faq['version']}",
                     json.dumps(answer, ensure_ascii=False))
    pipe.execute()
    if faq_records:
        client.upsert(faq_collection, faq_records)
        client.flush(faq_collection)
        actual = client.get(faq_collection, ids=[row["id"] for row in faq_records], output_fields=["id", "text"], consistency_level="Strong")
        if {row["id"]: row["text"] for row in actual} != {row["id"]: row["text"] for row in faq_records}:
            raise RuntimeError("新 FAQ 索引核对失败，保留旧版本指针")
    pipe = redis.pipeline(transaction=True)
    for faq in faqs:
        for channel in faq["sales_channels"]:
            pipe.set(f"{faq_prefix}:version:{faq['faq_item_id']}:{faq['goodsid']}:{channel}", faq["version"])
    for item_id in old_faq_ids - {faq["faq_item_id"] for faq in faqs}:
        for goodsid in GOODSIDS:
            for key in redis.scan_iter(f"{faq_prefix}:version:{item_id}:{goodsid}:*"):
                pipe.delete(key)
    pipe.execute()

    stale_documents = managed_documents - expected.keys()
    stale_faqs = managed_faqs - {row["id"] for row in faq_records}
    if stale_documents:
        client.delete(collection, ids=list(stale_documents))
    if stale_faqs:
        client.delete(faq_collection, ids=list(stale_faqs))
    client.flush(collection)
    client.flush(faq_collection)
    remaining = scan_records(client, collection, ["document_id", "version", "product_model"])
    expected_ids = ({row["document_id"] for row in old_documents} - managed_documents) | expected.keys()
    remaining_faqs = scan_records(client, faq_collection, ["id"])
    expected_faq_ids = ({row["id"] for row in old_faqs} - managed_faqs) | {row["id"] for row in faq_records}
    if {row["document_id"] for row in remaining} != expected_ids or {row["id"] for row in remaining_faqs} != expected_faq_ids:
        raise RuntimeError("同步后的索引数量或旧内容清理核对失败，请重新运行")

    meta = retriever.document_meta()
    models, goodsids, brands, general = set(), {}, {}, {}
    for row in remaining:
        payload = json.loads(redis.get(f"{prefix}:body:{row['document_id']}:{row['version']}"))
        item, model = payload["metadata"], row["product_model"]
        models.add(model)
        goodsids[model] = item.get("goodsid") or meta.get("model_goodsids", {}).get(model, "")
        brands[model] = item.get("brand") or meta.get("model_brands", {}).get(model, "")
        if item.get("general"):
            general[brands[model]] = model
    meta.update(models=sorted(models), model_goodsids=goodsids, model_brands=brands,
                general_models=general, count=len(remaining), brands=list(BRANDS),
                markdown_corpora=sorted(CORPUS_NAMES))
    faq_meta = json.loads(redis.get(f"{faq_prefix}:meta") or "{}")
    faq_meta.update(official_phone_items=len(faqs), markdown_corpora=sorted(CORPUS_NAMES))
    pipe = redis.pipeline(transaction=True)
    pipe.set(f"{prefix}:meta", json.dumps(meta, ensure_ascii=False))
    pipe.set(f"{faq_prefix}:meta", json.dumps(faq_meta, ensure_ascii=False))
    pipe.execute()
    return {"documents": len(documents), "faqs": len(faqs), "questions": len(faq_records),
            "removed_documents": len(stale_documents), "removed_questions": len(stale_faqs),
            "total_documents": len(remaining)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="仅校验 Markdown、切块和 FAQ，不连接数据库")
    args = parser.parse_args()
    documents, faqs = load_all()
    print(f"Markdown 校验通过：{len(documents)} 条文档，{len(faqs)} 项 FAQ，{sum(len(f['questions']) for f in faqs)} 个问法", flush=True)
    if args.dry_run:
        return
    retriever = FaqRetriever()
    try:
        result = synchronize(retriever, documents, faqs)
        print("同步完成：" + json.dumps(result, ensure_ascii=False), flush=True)
        print("Milvus 索引、Redis 正文、FAQ 版本及型号目录已更新；无需重启聊天服务。", flush=True)
    finally:
        retriever.close()


if __name__ == "__main__":
    main()
