"""手机型号与参数关系查询；只执行固定 Cypher，不接收模型生成的查询语句。"""

import hashlib
import logging
import math
import os
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..common.constants import PROJECT_ROOT

logger = logging.getLogger(__name__)
PARAMETERS = {
    "电池容量": "mAh", "有线充电功率": "W", "无线充电功率": "W",
    "屏幕尺寸": "英寸", "屏幕刷新率": "Hz", "处理器": "",
    "屏幕分辨率": "", "防护等级": "", "无线反充": "", "NFC": "", "品牌": "",
}
ParameterName = Literal["电池容量", "有线充电功率", "无线充电功率", "屏幕尺寸", "屏幕刷新率",
                        "处理器", "屏幕分辨率", "防护等级", "无线反充", "NFC", "品牌"]


class ParameterFilter(BaseModel):
    key: ParameterName
    op: Literal["eq", "gt", "gte", "lt", "lte", "contains"]
    value: str | float

    @model_validator(mode="after")
    def validate_value(self):
        if PARAMETERS[self.key]:
            number = float(self.value)
            if not math.isfinite(number) or number < 0 or self.op == "contains":
                raise ValueError("数值条件无效")
        elif self.op not in ("eq", "contains") or not str(self.value).strip():
            raise ValueError("文本参数只支持相等或包含")
        return self


class PhoneGraphQuery(BaseModel):
    mode: Literal["compare", "shared", "filter", "related"]
    parameters: list[ParameterName] = Field(default_factory=list, max_length=11)
    filters: list[ParameterFilter] = Field(default_factory=list, max_length=6)


class PhoneGraph:
    def __init__(self):
        from neo4j import GraphDatabase

        self.driver = GraphDatabase.driver(
            os.environ["RAG_NEO4J_URI"],
            auth=(os.environ["RAG_NEO4J_USER"], os.environ["RAG_NEO4J_PASSWORD"]),
            connection_timeout=3, connection_acquisition_timeout=5, max_transaction_retry_time=0,
        )
        self.database = os.getenv("RAG_NEO4J_DATABASE", "neo4j")

    def close(self):
        self.driver.close()

    def search(self, plan: PhoneGraphQuery, *, models=(), brand=""):
        from neo4j import Query

        filters = [{"key": f.key, "op": f.op, "text": str(f.value).lower(),
                    "number": float(f.value) if PARAMETERS[f.key] else None} for f in plan.filters]
        with self.driver.session(database=self.database, default_access_mode="READ") as session:
            record = session.run(Query("""
                MATCH (m:RagFixedPhoneModel)-[:MADE_BY]->(b:RagFixedPhoneBrand)
                WHERE ($brand = '' OR b.name = $brand)
                MATCH (m)-[r:HAS_PARAMETER]->(p:RagFixedPhoneParameter)
                WITH m, collect({key:p.key, value:p.value, number:p.number, unit:p.unit,
                                 excerpt:r.excerpt}) AS facts
                ORDER BY m.name
                WITH collect({model:m.name, source:m.source, version:m.version,
                              file:m.file, approved:m.approved, facts:facts}) AS catalog
                RETURN catalog, [row IN catalog WHERE ($mode = 'related' OR $models = [] OR row.model IN $models)
                    AND all(c IN $filters WHERE any(f IN row.facts WHERE f.key = c.key AND
                        CASE c.op WHEN 'eq' THEN
                            CASE WHEN c.number IS NULL THEN toLower(f.value) = c.text ELSE f.number = c.number END
                        WHEN 'contains' THEN toLower(f.value) CONTAINS c.text
                        WHEN 'gt' THEN f.number > c.number WHEN 'gte' THEN f.number >= c.number
                        WHEN 'lt' THEN f.number < c.number WHEN 'lte' THEN f.number <= c.number
                        ELSE false END))] AS matches
            """, timeout=5), brand=brand, models=list(models), filters=filters, mode=plan.mode).single()
        catalog, matches = record["catalog"], record["matches"]
        if not catalog or set(models) - {row["model"] for row in catalog}:
            return None
        # 本地资料改动后不继续使用旧图谱；重新同步即可恢复关系查询。
        for row in catalog:
            path = PROJECT_ROOT / "backend/data/knowledge" / row["file"]
            if (not path.is_file() or row["version"] != "md-" + hashlib.sha256(path.read_bytes()).hexdigest()[:24]):
                logger.warning("Phone graph stale model=%s", row["model"])
                return None
        if plan.mode in ("compare", "shared") and len(models) < 2:
            return None
        if plan.mode == "related" and (len(models) != 1 or not plan.parameters):
            return None
        if plan.mode == "filter" and not filters:
            return None
        keys = list(dict.fromkeys([*plan.parameters, *(f.key for f in plan.filters)]))
        if not keys:
            keys = list(PARAMETERS)
        targets = [row for row in catalog if plan.mode == "related" or not models or row["model"] in models]
        known_keys = {f["key"] for row in targets for f in row["facts"]}
        if not set(keys) <= known_keys:
            return None
        facts = {row["model"]: {f["key"]: f for f in row["facts"]} for row in catalog}
        evidence_keys = keys[:]
        unknown = [row["model"] for row in targets
                   if any(key not in facts[row["model"]] for key in keys)]
        if plan.mode == "related":
            reference = facts[models[0]]
            if any(key not in reference for key in plan.parameters):
                return None
            matches = [row for row in matches if row["model"] != models[0]
                       and all(facts[row["model"]].get(key, {}).get("value") == reference[key]["value"]
                               for key in plan.parameters)]
        if plan.mode == "shared":
            keys = [key for key in keys if all(key in facts[row["model"]] for row in targets)
                    and len({facts[row["model"]][key]["value"] for row in targets}) == 1]
        scope = "当前已入图的中国大陆版手机"
        if not matches:
            answer = f"在{scope}中，未找到同时满足这些条件的{'其他' if plan.mode == 'related' else ''}型号。"
        elif not keys:
            answer = ("部分型号的相关参数未核实，无法判断这些参数是否相同。" if unknown else
                      "所比较型号在这些已核实参数中没有相同项。")
        else:
            titles = {"电池容量": "电池容量（典型值）", "屏幕刷新率": "屏幕刷新率（最高）",
                      "有线充电功率": "有线充电功率（标称）", "无线充电功率": "无线充电功率（标称）"}
            lines = ["| 型号 | " + " | ".join(titles.get(key, key) for key in keys) + " |",
                     "| --- | " + " | ".join("---" for _ in keys) + " |"]
            for row in matches:
                values = [facts[row["model"]].get(key, {}).get("value", "未核实") for key in keys]
                lines.append("| " + row["model"] + " | " + " | ".join(values) + " |")
            answer = "\n".join(lines)
            if plan.mode == "related":
                answer = f"以下型号与{models[0]}的{'、'.join(plan.parameters)}相同：\n\n" + answer
            if plan.mode == "compare" and len(matches) == 2:
                differences = []
                for key in keys:
                    a, b = (facts[row["model"]].get(key, {}) for row in matches)
                    if a.get("number") is not None and b.get("number") is not None and a["number"] != b["number"]:
                        differences.append(f"{key}相差 {abs(a['number'] - b['number']):g}{PARAMETERS[key]}")
                if differences:
                    answer += "\n\n" + "；".join(differences) + "。"
        if plan.mode in ("filter", "related"):
            answer += f"\n\n筛选范围仅限{scope}，不代表全部在售手机。"
            if unknown:
                answer += "以下型号的部分相关参数未核实：" + "、".join(unknown) + "；缺项不表示不支持。"
        elif plan.mode == "shared" and unknown and keys:
            answer += "\n\n部分比较参数未核实，未计入共同项：" + "、".join(unknown) + "。"
        evidence = []
        for row in targets:
            selected = [f for f in row["facts"] if f["key"] in evidence_keys]
            if not selected:
                continue
            evidence.append({
                "evidence_id": f"phone-graph:{row['model']}:{row['version']}", "kind": "phone_graph",
                "title": row["model"] + "参数关系", "product_model": row["model"],
                "source": row["source"], "version": row["version"], "simulated": False,
                "approved": row["approved"],
                "excerpt": "\n".join(f"{f['key']}：{f['value']}；原文：{f['excerpt']}" for f in selected),
            })
        logger.info("Phone graph mode=%s models=%s filters=%s matches=%s", plan.mode, list(models),
                    filters, [row["model"] for row in matches])
        return {"status": "ok", "evidence": evidence, "graph_answer": answer}
