# 客服对话页面

使用 Vue 3 + Vite，后端使用 FastAPI，前后端独立运行。支持输入问题、点击或回车发送、空白消息拦截、自动滚动和请求失败提示，适配手机宽度。

## 启动

需要 Node.js 22.12+（或 20.19+）和 Python 3.10+。先在项目根目录启动后端：

```bash
python3 -m pip install -r backend/requirements.txt
python3 -m uvicorn main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

另开一个终端，在项目根目录启动前端：

```bash
cd frontend
npm install
npm run dev
```

打开终端输出的本地地址。

前端向 `/api/chat` 发送 POST 请求，Vite 开发代理将请求转发到 `http://127.0.0.1:8000`。开发环境无需浏览器跨域配置；生产部署时需要将 `/api` 转发到 FastAPI 服务。

请求体：`{"message":"你好"}`，响应体固定为 `{"reply":"helloword"}`。接口文档：`http://127.0.0.1:8000/docs`。

## 验证

```bash
npm run build
npx playwright install chromium
npm test
```

测试会自动启动前后端服务（需要先安装后端依赖），覆盖真实接口调用、输入校验、请求失败后的再次发送和原有页面交互。

页面和发送逻辑位于 `src/App.vue`，样式位于 `src/style.css`，接口位于 `../backend/main.py`。

目前后端只返回固定文本，没有接入智能问答或保存聊天记录，刷新页面会清空对话。
