# Errors

## ERR-20261019-001 — 本地预览浏览器选择

- Logged: 2026-10-19
- Priority: low
- Status: resolved
- Area: local-preview
- Summary: 当前环境没有 iab 浏览器，直接创建该浏览器标签失败。
- Details: 浏览器清单仅提供 Chrome；此前未先核对可用环境。
- Suggested Fix: 需要本地预览时先查询浏览器清单，再选择现有浏览器。
- Resolution: 已使用现有 Chrome 打开页面并检查外观，另有两项交互测试通过。
- Metadata: Source: tool-error; Related Files: frontend/; Tags: browser, preview

