"""从现有 Markdown 的有效章节同步手机参数，不修改 Milvus、Redis 或其他项目图谱。"""

import argparse
import hashlib
import json
import re

from dotenv import load_dotenv

from ..common.constants import PROJECT_ROOT
from ..rag.phone_graph import PARAMETERS, PhoneGraph
from .rebuild_knowledge import CORPORA, load_knowledge

# shortcut: 仅提取已校验格式的参数；新增品牌或参数格式时扩充规则，不推断未写明的功能。
PATTERNS = {
    "电池容量": r"(\d+)\s*mAh\s*\(typ\)",
    "有线充电功率": r"(\d+)\s*W\s*小米澎湃有线秒充",
    "无线充电功率": r"(\d+)\s*W\s*小米澎湃无线秒充",
    "屏幕尺寸": r"(\d+\.\d+)\s*英寸",
    "屏幕刷新率": r"显示帧率[：:]\s*最高\s*(\d+)\s*Hz",
    "处理器": r"((?:第三代)?骁龙\s*®?\s*8(?:至尊版)?)\s*移动平台",
    "屏幕分辨率": r"分辨率[：:]\s*(\d+\s*[x×]\s*\d+)",
    "防护等级": r"(IP68)\s*级",
    "无线反充": r"(无线反充)",
    "NFC": r"(多功能\s*NFC)",
}


def load_phones():
    phones, file_ids, faq_ids = {}, set(), set()
    for config in CORPORA:
        if config["brand"] != "小米":
            continue
        documents, _ = load_knowledge(config, file_ids, faq_ids)
        for document in documents:
            meta = document["metadata"]
            if not meta["source_file"].endswith("参数.md") or meta["general"]:
                continue
            name = meta["product_model"]
            phone = phones.setdefault(name, {
                "id": "model:" + name, "name": name, "brand": meta["brand"],
                "source": meta["source"], "version": meta["version"],
                "file": meta["source_file"], "approved": meta["approved"], "parameters": {},
            })
            phone["parameters"]["品牌"] = {
                "id": "parameter:brand:" + meta["brand"], "key": "品牌", "value": meta["brand"],
                "number": None, "unit": "", "excerpt": f"资料元数据：品牌={meta['brand']}，型号={name}",
            }
            # 不匹配自动加上的型号/章节标题，避免把型号数字误当参数。
            content = document["page_content"].split("\n\n", 2)[-1]
            for key, pattern in PATTERNS.items():
                for match in re.finditer(pattern, content, re.I):
                    value = re.sub(r"\s+|®", "", match[1]).replace("×", "x")
                    number = float(value) if PARAMETERS[key] else None
                    value = f"{number:g}{PARAMETERS[key]}" if number is not None else value
                    if key in ("NFC", "无线反充"):
                        value = "支持"
                    previous = phone["parameters"].get(key)
                    if previous and previous["value"] != value:
                        raise ValueError(f"{name} 的 {key} 有冲突，停止同步")
                    start = content.rfind("\n", 0, match.start()) + 1
                    end = content.find("\n", match.end())
                    excerpt = content[start:end if end >= 0 else len(content)].strip()
                    phone["parameters"][key] = {
                        "id": "parameter:" + hashlib.sha256(f"{key}:{value}".encode()).hexdigest(),
                        "key": key, "value": value, "number": number, "unit": PARAMETERS[key],
                        "excerpt": excerpt,
                    }
    rows = [{**phone, "parameters": list(phone["parameters"].values())} for phone in phones.values()]
    if not rows or any(not row["parameters"] for row in rows):
        raise ValueError("没有有效的手机型号或参数，停止同步")
    return rows


def synchronize(graph, phones):
    with graph.driver.session(database=graph.database) as session:
        session.run("CREATE CONSTRAINT rag_fixed_phone_id IF NOT EXISTS FOR (n:RagFixedPhone) REQUIRE n.id IS UNIQUE").consume()

        def write(tx):
            # 专用标签内整批原子替换；解析和校验全部完成后才进入写事务。
            tx.run("MATCH (n:RagFixedPhone) DETACH DELETE n").consume()
            tx.run("""
                UNWIND $phones AS row
                CREATE (m:RagFixedPhone:RagFixedPhoneModel)
                SET m = {id:row.id, name:row.name, source:row.source, version:row.version,
                         file:row.file, approved:row.approved}
                MERGE (b:RagFixedPhone:RagFixedPhoneBrand {id:'brand:' + row.brand})
                SET b.name = row.brand
                CREATE (m)-[:MADE_BY]->(b)
                WITH m, row UNWIND row.parameters AS parameter
                MERGE (p:RagFixedPhone:RagFixedPhoneParameter {id:parameter.id})
                SET p.key = parameter.key, p.value = parameter.value,
                    p.number = parameter.number, p.unit = parameter.unit
                CREATE (m)-[:HAS_PARAMETER {excerpt:parameter.excerpt}]->(p)
            """, phones=phones).consume()
        session.execute_write(write)


def main():
    parser = argparse.ArgumentParser(description="同步手机型号与参数 Neo4j 图谱")
    parser.add_argument("--dry-run", action="store_true", help="只校验和显示参数，不连接数据库")
    args = parser.parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    phones = load_phones()
    print(json.dumps({row["name"]: {p["key"]: p["value"] for p in row["parameters"]}
                      for row in phones}, ensure_ascii=False, indent=2))
    if args.dry_run:
        return
    graph = PhoneGraph()
    try:
        synchronize(graph, phones)
    finally:
        graph.close()
    print(f"已同步 {len(phones)} 个型号、{sum(len(row['parameters']) for row in phones)} 条参数关系。")


if __name__ == "__main__":
    main()
