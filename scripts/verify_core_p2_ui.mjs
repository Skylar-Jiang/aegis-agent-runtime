// Start the frontend dev server before running. Uses the locally installed Chrome.
import { createRequire } from 'node:module'
import { mkdir } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const require = createRequire(path.join(root, 'frontend/package.json'))
const { chromium, expect } = require('@playwright/test')
const output = path.join(root, '.runtime/p2-ui')
await mkdir(output, { recursive: true })
const browser = await chromium.launch({ channel: process.env.P2_BROWSER_CHANNEL || 'chrome' })
try {
  const page = await browser.newPage({ viewport: { width: 1360, height: 1000 } })
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto(`${process.env.P2_UI_BASE_URL || 'http://127.0.0.1:15175'}/core`)
  await expect(page.getByTestId('gateway-decision')).toHaveText('REQUIRE_CONFIRMATION')
  await page.screenshot({ path: path.join(output, 'desktop.png'), fullPage: true })
  await page.getByRole('button', { name: '模拟批准并重新检查' }).click()
  await expect(page.getByTestId('confirmation-status')).toHaveText('CONFIRMED')
  await expect(page.locator('[data-sequence]')).toHaveCount(4)
  await page.getByLabel('演示场景').selectOption('deny')
  await expect(page.getByTestId('gateway-decision')).toHaveText('DENY')
  await page.getByLabel('演示场景').selectOption('replan')
  await expect(page.getByTestId('gateway-decision')).toHaveText('REQUIRE_REPLAN')
  await page.getByLabel('演示场景').selectOption('empty')
  await expect(page.getByText('此任务暂无行为事件。')).toBeVisible()
  await page.getByLabel('演示场景').selectOption('error')
  await expect(page.getByRole('alert')).toBeVisible()
  await page.getByLabel('演示场景').selectOption('confirm')
  await expect(page.getByTestId('gateway-decision')).toHaveText('REQUIRE_CONFIRMATION')
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({ path: path.join(output, 'mobile.png'), fullPage: true })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.getByRole('button', { name: '模拟拒绝' }).click()
  await expect(page.getByTestId('confirmation-status')).toHaveText('REJECTED')
  expect(errors).toEqual([])
  console.log('PASS: six mock scenarios, confirmation, rejection, mobile layout, no page errors')
} finally {
  await browser.close()
}
