import { defineConfig } from '@playwright/test';

const backendPort = 18000;
const frontendPort = 15173;

export default defineConfig({
  testDir: './tests/e2e',
  outputDir: './test-results',
  timeout: 30000,
  retries: 1,
  use: {
    baseURL: `http://127.0.0.1:${frontendPort}`,
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
  },
  webServer: [
    {
      command: `py -3.11 -m uv run --project backend uvicorn ra_agent.main:app --host 127.0.0.1 --port ${backendPort}`,
      env: {
        RUNTIME_MODE: 'live-agent',
        DATABASE_URL: 'sqlite+aiosqlite:///./.runtime/playwright/ra_agent.db',
        WORKSPACE_ROOT: '.runtime/playwright/workspace',
        PENDING_ROOT: '.runtime/playwright/pending',
        CHECKPOINT_ROOT: '.runtime/playwright/checkpoints',
        QUARANTINE_ROOT: '.runtime/playwright/quarantine',
        ENABLE_DEMO_FIXTURES: 'true',
      },
      port: backendPort,
      reuseExistingServer: false,
    },
    {
      command: `corepack pnpm --dir frontend exec vite --host 127.0.0.1 --port ${frontendPort}`,
      env: {
        VITE_API_PROXY_TARGET: `http://127.0.0.1:${backendPort}`,
      },
      port: frontendPort,
      reuseExistingServer: false,
    },
  ],
});
