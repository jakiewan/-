# 固定 RAG 流程

问题重写后固定先查 FAQ。FAQ 命中直接返回标准条款；未命中才执行原查询与 HyDE 两组 dense/sparse 检索、RRF 融合、BGE Reranker 精排，再交给原有回答出口。歧义或必要参数缺失时澄清，服务异常不会伪装成未命中。

算法迁自 `04-rag` 的 `bge_m3_embedding_utils.py`、`12-search_embedding.py`、`search_embedding_hyde.py`、`18-rrf.py`、`rerank.py`；适配本项目的 Redis 版本正文、FAQ 优先及 SSE。没有引入课程的全局会话或在线 Ragas 评分。

安装后端依赖，在项目根目录 `.env` 配置本地模型路径：

```dotenv
RAG_BGE_M3_MODEL_PATH=/path/to/bge-m3
RAG_RERANKER_MODEL_PATH=/path/to/bge-reranker-base
RAG_MODEL_DEVICE=cpu
```

模型仅从本地路径或已有 Hugging Face 缓存加载。Mac 可使用 `mps`；FAQ 保留原有 CPU 编码及版本校验。

在项目根目录迁移现有文档：

```bash
python -m backend.scripts.ingest_knowledge --migrate-v1
```

迁移只读 `customer_graph_documents_v1` 及其 Redis 正文，写入新的 `customer_graph_documents_fixed_v2` 和 `customer_graph:documents:fixed:v2` 前缀，保留旧数据。也可以继续传入现有格式的 JSONL 文件进行导入。迁移期间不要让其他进程更新源知识，完成后检查有效条数和版本对应关系。

可通过 `RAG_DOCUMENT_COLLECTION`、`RAG_DOCUMENT_PREFIX` 指定新索引。新旧代码必须使用各自兼容的索引与元数据；回退同时恢复旧代码和配置。

文档默认每路召回 20 条，dense/sparse 权重各 0.5，混合后保留 10 条。RRF 使用 `k=60`、原查询权重 1.0、HyDE 权重 0.7，并按含版本的证据 ID 去重。

精排沿用课程的原始分数和分差截断思路，默认最多 5 条、正文总计不超过 8000 字符，允许零条，不强制保留最少三条。`RAG_RERANK_MIN_SCORE=0`、`RAG_RERANK_GAP_ABS=0.5` 和 `RAG_RERANK_GAP_RATIO=0.25` 是初始参数，需根据业务题集校准。分数不代表答案正确率，HyDE 文本不能作为回答证据。

## 本轮验证（2026-10-22）

- 迁移 286 条有效文档，逐条核对 ID、版本及 Redis 正文与 v1 一致，旧集合保留。
- 10 轮真实接口及 SSE 断言通过：品类补参、FAQ 标准答案、话题切换、型号补参与纠正、未知型号、无资料、错误前提和寒暄。日志与节点事件确认实际执行了混合召回、HyDE、RRF 和精排。
- 固定顺序、FAQ 命中短路、异常不回落、RRF 去重、精排断崖和空结果自检通过。
- Python 语法检查、`npm run build`、现有 5 项 Playwright 测试通过；浏览器测试使用独立端口和原测试文件。

本轮修正了重写模型将“只提供型号”擅自扩成参数概览、将“保修多久”猜成手机的问题。品类增加原文依据校验，仍保留必要补参。结果仅覆盖上述用例，不代表已完成全量知识质量评估。
