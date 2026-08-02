import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  outputDir: './docs/final-integration-assets',
  timeout: 30000,
  retries: 1,
  use: {
    baseURL: 'http://127.0.0.1:5173',
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
  },
  webServer: [
    {
      command: 'py -3.11 -m uv run --project backend uvicorn ra_agent.main:app --host 127.0.0.1 --port 8000',
      env: {
        RUNTIME_MODE: 'live-agent',
        DATABASE_URL: 'sqlite+aiosqlite:///./.runtime/playwright/ra_agent.db',
        WORKSPACE_ROOT: '.runtime/playwright/workspace',
        PENDING_ROOT: '.runtime/playwright/pending',
        CHECKPOINT_ROOT: '.runtime/playwright/checkpoints',
        QUARANTINE_ROOT: '.runtime/playwright/quarantine',
      },
      port: 8000,
      reuseExistingServer: true,
    },
    {
      command: 'corepack pnpm --dir frontend exec vite --host 127.0.0.1 --port 5173',
      port: 5173,
      reuseExistingServer: true,
    },
  ],
});
