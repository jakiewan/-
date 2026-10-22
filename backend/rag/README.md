# 固定 RAG 流程

问题重写后固定先查 FAQ。明确有效命中返回标准答案；未明确命中（无匹配或问法语义接近）进入文档 RAG：原查询与 HyDE 两组 dense/sparse 召回 → RRF → 独立精排 → 生成。缺少必要条件时澄清，服务异常明确失败。

检索算法复用 `04-rag` 的双向量编码、混合搜索、HyDE、RRF 和 Reranker 思路。没有检索规划 Agent、证据选择 Agent 或重查循环，Ragas 不进入在线请求。

## 配置与入库

安装 `backend/requirements.txt`，在项目根目录 `.env` 配置模型路径和独立 FAQ 索引：

```dotenv
RAG_BGE_M3_MODEL_PATH=/path/to/bge-m3
RAG_RERANKER_MODEL_PATH=/path/to/bge-reranker-base
RAG_MODEL_DEVICE=cpu
RAG_FAQ_COLLECTION=customer_graph_faq_official_v1
RAG_FAQ_PREFIX=customer_graph:faq:official:v1
```

模型只从本地路径或已有缓存加载；Mac 可以使用 `mps`。FAQ 继续使用原有 CPU dense 编码，匹配校验与文档精排复用同一个 Reranker。

在项目根目录依次执行，源服务需已有原 FAQ 与文档知识：

```bash
python -m backend.scripts.ingest_faq backend/data/xiaomi_phone_faq_cn.jsonl
python -m backend.scripts.ingest_knowledge --migrate-v1
python -m backend.scripts.ingest_knowledge backend/data/xiaomi_specs_cn.jsonl
python -m backend.scripts.ingest_knowledge backend/data/xiaomi_phone_policies_cn.jsonl
```

运行时默认读取上述独立官方 FAQ 索引和前缀，避免被旧模拟手机条款抢先命中。官方手机 FAQ 写入独立集合和 Redis 前缀，保留其他品类的原演示问答；不修改 `faq_question` 或 `gc:faq`。文档写入 `customer_graph_documents_fixed_v2` 与 `customer_graph:documents:fixed:v2`，保留 v1。可通过 `RAG_DOCUMENT_COLLECTION`、`RAG_DOCUMENT_PREFIX` 指定新目标，回退需同时恢复匹配的代码与配置。

## 数据与来源

采集、复核日期：2026-10-22。范围为小米中国大陆版手机，不把海外版数据混入国行。

- 36 条参数：小米14、小米15、小米15 Pro、小米15 Ultra，各9个主题；包含官方参数页实际加载的资源 URL 与 SHA-256。
- 10 项官方政策 FAQ、31 个问法；涵盖期限、质量退换修、无理由退货、进液摔损、凭证、维修价格边界、起算时间与配件期限。
- 政策以10条内容建立50条通用/型号适用索引记录，结合参数共86条官方文档。现有 GC 演示资料保留并明确标注模拟，不冒充小米资料。

主要来源：[小米15参数](https://www.mi.com/prod/xiaomi-15/specs)、[15 Pro参数](https://www.mi.com/prod/xiaomi-15-pro/specs)、[15 Ultra参数](https://www.mi.com/prod/xiaomi-15-ultra/specs)、[小米14参数](https://www.mi.com/xiaomi-14/specs)、[服务保障政策](https://order.mi.com/service/security)、[售后渠道说明](https://www.mi.com/c/service/miphone/aftermarket)、[售后常见问题](https://order.mi.com/service/fhelp)。

政策页面本次读取未见更新日期，采集日期不是生效日期。旧附件条款不能直接套给现代机型的内置电池；具体订单权益仍须核对凭证和官方适用条款。未接入实时价格、库存、维修报价、最新 OTA 和海外版资料。

## 匹配与生成边界

FAQ 先召回问法，再用 Reranker 校验相关性，最后按 FAQ 条目去重；避免同一条目的正确问法被提前丢掉。当前 FAQ 是泛品类政策，不作为型号参数答案；语义不确定时继续查询完整文档。

文档每路默认召回20条，dense/sparse 各0.5，混合后保留10条。RRF 使用 `k=60`，原查询/HyDE 权重为1.0/0.7，按带版本的证据 ID 去重。精排沿用原始打分和分差截断，最多5条、正文预算8000字符；允许零条，不强制最少三条。比较多个型号时分别保留相关证据，缺项不补造。

`RAG_RERANK_MIN_SCORE=0`、`RAG_RERANK_GAP_ABS=0.5`、`RAG_RERANK_GAP_RATIO=0.25` 均为待持续校准的起点，不是正确率。HyDE 只扩展检索，不作为证据。比较回答只列有据参数，未覆盖项标未核实，不依据资料缺项判断功能多少或优劣。

## 验证

见 [官方资料自测报告](官方资料自测报告.md)。验收覆盖50项常见场景及失败项的定向复测；Python 检查、前端构建和现有5项浏览器回归通过。没有新增测试框架。

本轮接口验收使用MiniMax。并行对话的售后、前端及模型切换改动保留，未纳入本次 RAG 提交。测试使用独立端口、账号与新索引。模型服务曾返回 `429/2062`；这些失败未计为通过，降速后重新验证，不以假答案掩盖服务失败。
