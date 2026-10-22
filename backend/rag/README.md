# 固定 RAG 流程

普通问题重写后固定先查 FAQ。明确有效命中返回标准答案；未明确命中（无匹配或问法语义接近）进入文档 RAG：原查询与 HyDE 两组 dense/sparse 召回 → RRF → 独立精排 → 生成。手机参数关系问题可先查 Neo4j，未覆盖或连接异常时继续原流程。缺少必要条件时澄清，服务异常明确失败。

检索算法复用 `04-rag` 的双向量编码、混合搜索、HyDE、RRF 和 Reranker 思路。没有检索规划 Agent、证据选择 Agent 或重查循环，Ragas 不进入在线请求。

## 手机参数关系（可选）

在项目根目录 `.env` 配置 `RAG_NEO4J_URI`、`RAG_NEO4J_USER`、`RAG_NEO4J_PASSWORD` 和可选的 `RAG_NEO4J_DATABASE`（默认 `neo4j`），安装新增的官方 `neo4j` 驱动，然后重启后端。未配置 URI 时原检索功能照常使用；只在关系查询时建立数据库连接。

```bash
python -m backend.scripts.sync_phone_graph --dry-run
python -m backend.scripts.sync_phone_graph
```

图谱从现有小米 Markdown 的有效章节提取，不读取历史 JSONL，不调用 LLM 生成事实。第一版覆盖小米14、15、15 Pro、15 Ultra，支持品牌、处理器、电池典型容量、有线/无线标称充电功率、屏幕尺寸/最高刷新率/分辨率、防护等级、明确列出的 NFC 和无线反充。其他品牌和未覆盖参数继续走文档检索。原文没写某功能时保留未知，不记为不支持；容量和标称功率不代表实际续航和充满时间。

关系包括型号到品牌的 `MADE_BY`、型号到参数的 `HAS_PARAMETER`，相同参数值共用节点。可查询型号参数比较、数值差、共同参数、与参照型号具有相同参数的其他手机，以及多个数值/文本条件的交集。查询计划复用问题重写的结构化输出，Cypher 由程序固定，模型不能执行任意查询；结果由程序形成表格，保留原文和版本证据，不再让模型重算数值。

写入只替换专用 `RagFixedPhone*` 标签的数据，整批写入使用单个事务，不操作现有商品图谱、订单、售后、Milvus 或 Redis。不要并发运行图谱同步。修改原始 Markdown 后，先按原入口更新文档/FAQ，再执行上述图谱同步命令；在线查询发现本地文件与入图版本不同会退回原 RAG，不使用旧参数。在线请求不写图谱。

例如：`小米15与小米15 Pro电池容量相差多少？`、`小米哪些手机电池至少6000mAh且无线充电至少50W？`、`哪些手机和小米15使用同款处理器？`。筛选只覆盖已入图的型号，不代表全部在售商品。政策、订单、售后申请和普通单型号问答继续走原有节点。

2026-10-22 验证：已写入四个型号、42 条参数关系；真实 Neo4j 查询、重复同步幂等、原有标签数据数量保留、数值上下界通过。DeepSeek + 真实数据库的流式接口回归 10/10（本地模型使用 CPU），覆盖五类图谱问题及原有保修、参数解释、寒暄、订单与售后路径；前端构建和现有五项浏览器测试通过。未知参数处理、结果标注和过期资料回退另做离线断言。验证后 Docker 容器出现 `Input/output error`，未替换原有运行中的后端；需恢复 Docker 环境并重启本项目后端加载新代码，不能把上述验证记录视为当前服务在线状态。

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

日常维护小米手机资料：编辑 `backend/data/knowledge/xiaomi-cn/原始资料/` 中的 Markdown，在项目根目录执行：

```bash
python -m backend.scripts.rebuild_knowledge --dry-run  # 可选：仅校验，不写库
python -m backend.scripts.rebuild_knowledge
```

该入口读取本地 Markdown、按标题/段落切块、编码并更新 Milvus 与 Redis，同时从正文同步 FAQ，清理过期的小米手机文档和问法。版本自动生成，保留 GC、其他品类和 v1 旧库，无需重启聊天服务。不要并发运行多个入库脚本；更新不是跨库原子事务，失败后修复问题再重跑。历史 JSONL 导入入口保留作兼容，不再作为本资料集的日常更新入口。

运行时默认读取上述独立官方 FAQ 索引和前缀，避免被旧模拟手机条款抢先命中。官方手机 FAQ 写入独立集合和 Redis 前缀，保留其他品类的原演示问答；不修改 `faq_question` 或 `gc:faq`。文档写入 `customer_graph_documents_fixed_v2` 与 `customer_graph:documents:fixed:v2`，保留 v1。可通过 `RAG_DOCUMENT_COLLECTION`、`RAG_DOCUMENT_PREFIX` 指定新目标，回退需同时恢复匹配的代码与配置。

## 数据与来源

本地源文档及编辑约定见 [小米国行手机知识资料](../data/knowledge/xiaomi-cn/README.md)。`原始资料/` 是 Markdown 同步入口的唯一知识源，`整理稿/` 和 JSONL 是历史参考。聊天界面不展示资料来源及版本，内部证据仍保留这些信息供核查。

采集、复核日期：2026-10-22。范围为小米中国大陆版手机，不把海外版数据混入国行。

- 当前 8 份 Markdown 默认生成 165 条文档切块，覆盖四款国行手机参数、三包、渠道、凭证及维修价格查询入口；冲突的旧 Q7 和无适用条件的宣传摘要不入库。
- 10 项政策 FAQ、31 个问法，答案直接引用同一份 Markdown 正文章节，不再单独维护 JSONL 答案。
- 历史 JSONL 中的 36 条参数、50 条政策文档由新的 Markdown 文档索引替换。现有 GC 演示资料保留并明确标注模拟，不冒充小米资料。

主要来源：[小米15参数](https://www.mi.com/prod/xiaomi-15/specs)、[15 Pro参数](https://www.mi.com/prod/xiaomi-15-pro/specs)、[15 Ultra参数](https://www.mi.com/prod/xiaomi-15-ultra/specs)、[小米14参数](https://www.mi.com/xiaomi-14/specs)、[服务保障政策](https://order.mi.com/service/security)、[售后渠道说明](https://www.mi.com/c/service/miphone/aftermarket)、[售后常见问题](https://order.mi.com/service/fhelp)。

政策页面本次读取未见更新日期，采集日期不是生效日期。旧附件条款不能直接套给现代机型的内置电池；具体订单权益仍须核对凭证和官方适用条款。未接入实时价格、库存、维修报价、最新 OTA 和海外版资料。

## 匹配与生成边界

FAQ 先召回问法，再用 Reranker 校验相关性，最后按 FAQ 条目去重；避免同一条目的正确问法被提前丢掉。当前 FAQ 是泛品类政策，不作为型号参数答案；语义不确定时继续查询完整文档。

文档每路默认召回20条，dense/sparse 各0.5，混合后保留10条。RRF 使用 `k=60`，原查询/HyDE 权重为1.0/0.7，按带版本的证据 ID 去重。精排沿用原始打分和分差截断，最多5条、正文预算8000字符；允许零条，不强制最少三条。比较多个型号时分别保留相关证据，缺项不补造。

`RAG_RERANK_MIN_SCORE=0`、`RAG_RERANK_GAP_ABS=0.5`、`RAG_RERANK_GAP_RATIO=0.25` 均为待持续校准的起点，不是正确率。HyDE 只扩展检索，不作为证据。比较回答只列有据参数，未覆盖项标未核实，不依据资料缺项判断功能多少或优劣。

## 验证

历史 JSONL 版本的验收见 [官方资料自测报告](官方资料自测报告.md)，不代表 Markdown 版本已覆盖全部相同问法。Markdown 同步验证以真实 Milvus/Redis 检查修改正文后 FAQ 与文档精排命中新内容、向量及版本变化、问法和切块删除、重复运行不累积，以及其他品类保留。没有新增测试框架或测试文件。

历史 JSONL 轮次的接口验收使用 MiniMax，曾遇到 `429/2062`，失败未计为通过。本次 Markdown 同步验收使用临时索引及内存会话，正式同步后以当前 DeepSeek 配置运行六条真实客服 Graph 问答，通过后再交付；前端构建和现有 5 项浏览器测试通过。详细结果见本地知识资料 README 的本次验证记录。
