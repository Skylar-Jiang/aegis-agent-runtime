import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 30000,
  retries: 1,
  use: {
    baseURL: 'http://127.0.0.1:5173',
    headless: true,
  },
  webServer: [
    {
      command: 'py -3.11 -m uv run --project backend uvicorn ra_agent.main:app --host 127.0.0.1 --port 8000',
      port: 8000,
      reuseExistingServer: true,
    },
    {
      command: 'cd frontend && corepack pnpm dev -- --host 127.0.0.1',
      port: 5173,
      reuseExistingServer: true,
    },
  ],
});
