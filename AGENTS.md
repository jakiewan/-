# Repository Guidelines

## Project Structure & Module Organization

The working application is a Vue 3 + Vite customer-support chat demo in `frontend/`.

- `frontend/src/App.vue`: chat UI, reactive state, and local simulated replies.
- `frontend/src/main.js`: application entry point; `src/style.css`: shared styles and responsive layout.
- `frontend/index.html`: HTML entry point. No dedicated asset directory exists yet.
- `frontend/tests/chat.spec.js`: Playwright browser tests.
- `frontend/vite.config.js` and `playwright.config.js`: development and test configuration.
- `技术方案.md`: proposed backend architecture, not implemented functionality.

## Build, Test, and Development Commands

Use Node.js 22.12+ or 20.x starting at 20.19. Run commands from `frontend/`:

```bash
npm install                     # Install dependencies
npm run dev                     # Start Vite on 127.0.0.1
npm run build                   # Build production files into dist/
npm run preview                 # Preview the production build
npx playwright install chromium # Install the browser before first test run
npm test                        # Run Playwright tests
```

Playwright starts Vite on port 5173 and can reuse an existing server outside CI.

## Coding Style & Naming Conventions

Follow existing two-space indentation, single-quoted JavaScript strings, and omitted semicolons. Use ES modules and Vue Composition API with `<script setup>`. Name Vue components in PascalCase, variables and functions in camelCase, and CSS classes in kebab-case. Preserve Chinese interface text, accessible labels, keyboard behavior, and responsive styles. No formatter or linter is configured; match surrounding code.

## Testing Guidelines

**项目长期规则：禁止生成过度测试、过度防御的代码或测试文件。** 尽量使用 curl 调接口传参数并结合打印的日志进行测试, 不要额外写测试代码

Place browser tests in `frontend/tests/*.spec.js` using `@playwright/test`. Prefer role-based locators and assertions on visible behavior. Existing checks cover blank input, button/Enter submission, Chinese input composition, simulated replies, reload reset, mobile overflow, and HTML displayed as text. Update relevant checks when behavior changes. Run `npm run build` and `npm test` before submitting application changes. No coverage threshold is configured.

## Commit & Pull Request Guidelines

History contains only `Initial commit`; no established message convention exists. Use concise imperative subjects, such as `Fix chat input validation`. PRs should describe the change, reason, scope, risks, and validation results; link relevant issues and include screenshots for visual changes.

## Change Scope & Security

Keep changes minimal and preserve unrelated behavior. Explain scope and risks before multi-file, interface, database, or core-flow changes; state uncertainty explicitly. Report unrelated bugs before fixing them. Obtain explicit authorization for refactoring, optimization, architecture changes, stack replacements, or removing old logic. Never commit secrets or generated dependencies/build/test artifacts. Chat currently uses local replies without API calls or persistence. State any checks that could not be completed.
