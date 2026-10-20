# 客服对话页面

使用 Vue 3 + Vite，后端使用 FastAPI，前后端独立运行。支持输入问题、点击或回车发送、空白消息拦截、自动滚动和请求失败提示，适配手机宽度。

## 启动

日常开发时，在项目根目录执行一个命令即可启动或重启前后端：

```bash
python3 main.py
```

该入口适用于 macOS/Linux，使用当前 Python 环境和 npm，前后端日志直接显示在终端；保持终端运行，按 Ctrl+C 会同时停止前后端。再次执行会重启本项目占用的 8000、5173 端口；如果端口被其他项目占用，会提示错误并退出。后端开启热更新，仅监控 `backend/`。重启会清空内存登录会话，需要重新登录。

首次运行仍需按下方步骤安装依赖和配置 `.env`。

后端启动时需要可连接的 PostgreSQL 和有效的模型配置。根目录 `.env` 中设置 `DATABASE_URL=postgresql://用户名:密码@地址:端口/数据库名`；也兼容当前已有的 `postgresql+psycopg://` 格式。数据库必须已创建，账号需要在该数据库内创建 schema 和表的权限。

需要 Node.js 22.12+（或 20.19+）和 Python 3.10+。先将根目录 `.env.example` 复制为 `.env`，填入真实 `MINIMAX_API_KEY`；`.env` 已被 Git 忽略，密钥仅由后端读取。国内平台默认地址为 `https://api.minimax.cn/v1`，国际平台需将 `MINIMAX_BASE_URL` 改为 `https://api.minimax.io/v1`。

在项目根目录启动后端：

```bash
python3 -m pip install -r backend/requirements.txt
python3 -m backend.scripts.train_intent --base-model /path/to/bert-base-chinese
python3 -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

首次启动前需要微调本项目六类意图 BERT，默认数据仅为开发演示样本。训练步骤、数据格式和 `INTENT_MODEL_PATH` 配置见 [BERT 分类说明](../backend/graph/intent.md)。MiniMax 继续负责工具参数提取与回复生成。

另开一个终端，在项目根目录启动前端：

```bash
cd frontend
npm install
npm run dev
```

打开终端输出的本地地址。

前端向 `/api/chat` 发送 POST 请求，Vite 开发代理将请求转发到 `http://127.0.0.1:8000`。开发环境无需浏览器跨域配置；生产部署时需要将 `/api` 转发到 FastAPI 服务。

请求体：`{"message":"你好"}`，响应体为 `{"reply":"模型回复"}`。后端通过 `MiniMaxClient` 和 LangChain `create_agent` 调用固定型号 `MiniMax-Text-01`。接口文档：`http://127.0.0.1:8000/docs`。

## 和风天气工具

Agent 已注册 `get_weather`，可查询城市当前实时天气，例如“武汉现在天气怎么样”。天气意图仍归为闲聊，查询会经过真实的工具执行节点。暂不支持未来预报、历史天气或通用网页搜索。

若模型把 `functions.get_weather(...)` 写成正文或代码块，后端会暂缓显示该文本，并在本次请求内最多纠正一次调用协议。参数仍由模型通过结构化工具调用提供，不解析执行正文；纠正仍失败时回复“天气查询暂时未能完成，请稍后重试”。以代码块开头的正常回答会在该次模型调用结束后显示，普通文本仍按增量输出。

在根目录 `.env` 配置：

```dotenv
QWEATHER_API_HOST="控制台分配的专属域名，例如 abcxyz.qweatherapi.com"
QWEATHER_API_KEY="你的和风天气 API Key"
```

API Host 在和风控制台的设置中查看，API Key 在项目凭据中创建；修改后重启后端。凭据只由后端读取，不放入前端。未配置时天气工具会返回失败原因，不影响登录或普通聊天。

工具先通过 GeoAPI 解析城市，再请求实时天气 v1；同名地点会返回候选城市供用户确认。回答使用真实数据并附带和风天气来源、归因链接和查询时间；查询时间不是天气观测时间。

可在项目根目录直接验证：

```bash
python3 -c 'import asyncio, json; from backend.tools.weather import get_weather; print(json.dumps(asyncio.run(get_weather.ainvoke({"city": "武汉"})), ensure_ascii=False))'
```

接口参考：[城市搜索](https://dev.qweather.com/docs/api/geoapi/city-lookup/)、[实时天气 v1](https://dev.qweather.com/docs/api/weather/weather-current/)。

## 登录与账号切换

在根目录 `.env` 中用 JSON 配置账号密码，例如：

```dotenv
AUTH_USERS={"alice":"请设置密码","bob":"请设置另一个密码"}
```

修改账号配置后重启后端。页面支持账号密码登录，刷新后保留登录状态；点击「退出 / 切换账号」会注销当前会话并清空页面对话，再输入另一个账号登录。

登录接口为 `POST /api/auth/login`，请求体 `{"username":"账号","password":"密码"}`；`GET /api/auth/me` 查询当前账号，`POST /api/auth/logout` 注销。`/api/chat` 现在需要登录，未登录返回 401。Cookie 由浏览器自动发送，使用 HttpOnly 和 SameSite=Strict，HTTPS 访问时加上 Secure。

这是单进程本地演示：账号密码由后端 `.env` 配置，会话保存在内存中，有效期 8 小时，重启后需要重新登录。暂不提供注册、角色权限或服务端聊天记录；多进程或公开部署前需接入持久化账号、密码哈希和共享会话存储。

当前官方文档未列出 `MiniMax-Text-01`，需用账号实际验证该型号是否可用，不会自动切换模型。缺少密钥返回 503，上游调用失败返回 502。

## 验证

```bash
npm run build
npx playwright install chromium
npm test
```

测试会自动启动前后端服务（需要先安装后端依赖）。浏览器测试模拟登录状态和客服回复，不调用付费模型；未登录聊天请求会调用真实后端验证 401。模型集成测试在项目根目录运行：

```bash
python3 -m unittest backend.test_minimax
```

该测试仅验证 Agent 和 OpenAI 兼容请求，使用模拟 HTTP 响应。真实调用需配置有效密钥后，在页面提问，或执行：

```bash
curl -c /tmp/customer-chat-cookies http://127.0.0.1:5173/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"账号","password":"密码"}'

curl -b /tmp/customer-chat-cookies http://127.0.0.1:5173/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"请只回复：连接成功"}'
```

页面和发送逻辑位于 `src/App.vue`，样式位于 `src/style.css`，接口位于 `../backend/main.py`。

后端目录按职责组织：

```text
backend/
├── main.py                  # FastAPI 入口、生命周期与聊天接口
├── common/
│   ├── auth.py              # 登录与鉴权
│   └── constants.py         # 项目路径、Cookie 与登录会话常量
├── memory/
│   ├── context.py           # 用户运行上下文与记忆命名空间
│   └── prompts.py           # 读取长期档案的动态提示
├── tools/
│   └── memory.py            # Agent 可调用的记忆读写工具
├── graph/
│   ├── state.py             # 图状态定义
│   ├── nodes.py             # 六个业务节点占位函数
│   ├── workflow.py          # 路由、节点注册和图编译
│   └── workflow.md          # 中文流程图
├── rag/                     # 知识检索与向量数据库集成，待实现
└── utils/
    └── minimax_client.py    # MiniMax 工具类与 Agent 创建
```

在项目根目录运行 `python3 -m backend.graph.workflow` 可查看图骨架的 Mermaid 拓扑。

`../backend/utils/minimax_client.py` 封装模型和 Agent，使用 `AsyncPostgresSaver` 将线程状态持久化到 PostgreSQL。后端启动时创建连接池、初始化检查点表并创建 Agent，关闭时释放连接；本项目使用独立的 `customer_chat` schema，不使用已有的 `public` 检查点表。

每个账号固定一个对话：后端从登录态读取当前账号名作为 `user_id`，生成 `thread_id = "chat:" + user_id`，不接受前端指定的用户或线程 ID。前端继续只提交 `message`，历史由 Saver 自动恢复与保存；同一线程的请求通过 PostgreSQL advisory transaction lock 串行执行。当前账号名是记忆的归属标识，修改或将账号名分配给其他人会影响对应记忆的归属。

退出、切换账号和后端重启不会删除数据库中的对话记忆；重启后仍需重新登录同一个账号才能继续。刷新页面会清空界面显示，后端记忆保留，当前未提供历史展示或清空记忆接口。

检查点由框架按执行步骤保存，上游请求失败也可能保留输入或中间状态。当前保留完整对话状态，尚未加入长对话摘要、上下文 Token 裁剪或数据保留策略。

长期记忆使用 `AsyncPostgresStore`，复用现有连接池并在 `customer_chat` schema 下初始化框架存储表。命名空间为 `("user_memories",)`，key 为后端认证的账号标识，value 是用户档案 JSON；只支持 `name`（姓名）、`language`（语言）和 `response_style`（回答偏好）。

例如发送“请长期记住，我叫万佳恒，回答简短一点”，Agent 会调用工具保存；“把我的名字改为张三”可更正，“删除长期档案中的姓名”可删除对应字段。只有用户明确要求保存、更正或删除时才写入；每次模型调用前通过动态提示中间件读取档案。用户标识由服务端传入运行上下文，不由模型或前端指定。

长期档案与线程检查点独立，因此可以在新的线程中读取同一用户的档案，不需要增加新建会话页面。删除档案字段不会删除短期记忆中的原始消息，彻底清除聊天数据需要单独清理检查点。本轮未加入向量检索、自动提取、历史列表或新建会话功能。

接口参考：[MiniMax OpenAI 兼容接口](https://platform.minimax.cn/docs/api-reference/text-openai-api)、[LangChain Agent](https://docs.langchain.com/oss/python/langchain/agents)。
