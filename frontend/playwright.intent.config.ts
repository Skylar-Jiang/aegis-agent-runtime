import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: '../tests/e2e',
  testMatch: 'intent-local.spec.ts',
  timeout: 60000,
  workers: 1,
  outputDir: '../.runtime/intent-browser',
  reporter: [
    ['list'],
    ['json', { outputFile: '../.runtime/intent-browser-report.json' }],
  ],
  use: {
    baseURL: 'http://127.0.0.1:5174',
    locale: 'zh-CN',
    channel: 'chrome',
    viewport: { width: 1440, height: 1100 },
    trace: 'retain-on-failure',
    video: 'on',
  },
})
