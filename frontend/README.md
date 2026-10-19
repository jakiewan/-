# 客服对话页面

使用 Vue 3 + Vite，后端使用 FastAPI，前后端独立运行。支持输入问题、点击或回车发送、空白消息拦截、自动滚动和请求失败提示，适配手机宽度。

## 启动

日常开发时，在项目根目录执行一个命令即可启动或重启前后端：

```bash
python3 main.py
```

该入口适用于 macOS/Linux，使用当前 Python 环境和 npm，前后端日志直接显示在终端；保持终端运行，按 Ctrl+C 会同时停止前后端。再次执行会重启本项目占用的 8000、5173 端口；如果端口被其他项目占用，会提示错误并退出。后端开启热更新，仅监控 `backend/`。重启会清空内存登录会话，需要重新登录。

首次运行仍需按下方步骤安装依赖和配置 `.env`。

需要 Node.js 22.12+（或 20.19+）和 Python 3.10+。先将根目录 `.env.example` 复制为 `.env`，填入真实 `MINIMAX_API_KEY`；`.env` 已被 Git 忽略，密钥仅由后端读取。国内平台默认地址为 `https://api.minimax.cn/v1`，国际平台需将 `MINIMAX_BASE_URL` 改为 `https://api.minimax.io/v1`。

在项目根目录启动后端：

```bash
python3 -m pip install -r backend/requirements.txt
python3 -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

另开一个终端，在项目根目录启动前端：

```bash
cd frontend
npm install
npm run dev
```

打开终端输出的本地地址。

前端向 `/api/chat` 发送 POST 请求，Vite 开发代理将请求转发到 `http://127.0.0.1:8000`。开发环境无需浏览器跨域配置；生产部署时需要将 `/api` 转发到 FastAPI 服务。

请求体：`{"message":"你好"}`，响应体为 `{"reply":"模型回复"}`。后端通过 `MiniMaxClient` 和 LangChain `create_agent` 调用固定型号 `MiniMax-Text-01`。接口文档：`http://127.0.0.1:8000/docs`。

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

`../backend/minimax_client.py` 封装模型和 Agent；当前没有业务工具、知识库或多轮记忆，每次请求只提交本次问题，刷新会清空页面记录。

接口参考：[MiniMax OpenAI 兼容接口](https://platform.minimax.cn/docs/api-reference/text-openai-api)、[LangChain Agent](https://docs.langchain.com/oss/python/langchain/agents)。
