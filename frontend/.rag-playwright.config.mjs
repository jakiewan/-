import { defineConfig } from '@playwright/test'
export default defineConfig({
  testDir: './tests',
  use: { baseURL: 'http://127.0.0.1:15173' },
  webServer: {
    command: 'npm run dev -- --port 15173 --strictPort',
    url: 'http://127.0.0.1:15173',
    env: { VITE_API_TARGET: 'http://127.0.0.1:18080' },
  },
})
