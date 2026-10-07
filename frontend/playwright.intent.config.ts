import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: '../tests/e2e',
  testMatch: 'intent-demo.spec.ts',
  workers: 1,
  timeout: 60000,
  expect: { timeout: 10000 },
  fullyParallel: false,
  use: {
    baseURL: process.env.INTENT_DEMO_FRONTEND_URL ?? 'http://127.0.0.1:5174',
    viewport: { width: 1440, height: 1000 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  reporter: [['list']],
  outputDir: '../.runtime/member4-playwright',
})
