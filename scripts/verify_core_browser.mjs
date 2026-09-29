// Exercise the real browser + HTTP + SM2 chain; no mocked network responses.
// Start scripts/start_core.py and the frontend before invoking this script.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const require = createRequire(path.join(root, 'frontend', 'package.json'))
const { chromium, expect } = require('@playwright/test')
const origin = process.env.AEGIS_BROWSER_URL || 'http://127.0.0.1:5173'
const workspace = process.env.AEGIS_BROWSER_WORKSPACE || path.join(root, '.runtime', 'core-demo', 'workspace')
const stamp = new Date().toISOString().replaceAll(/[:.]/g, '-')
const output = path.join(root, '.runtime', 'review-fixes', `browser-${stamp}`)
await mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true, channel: process.env.AEGIS_BROWSER_CHANNEL || 'msedge' })
const page = await browser.newPage({ viewport: { width: 1600, height: 1100 }, locale: 'zh-CN' })
const captured = []
const errors = []
const checks = []
page.on('pageerror', error => errors.push(String(error)))
page.on('response', async response => {
  if (response.url().includes('/api/v1/') && response.request().method() === 'POST') {
    try { captured.push({ path: new URL(response.url()).pathname, status: response.status(), request: response.request().postDataJSON(), body: await response.json() }) } catch { /* Non-JSON errors remain visible in UI. */ }
  }
})
async function api(method, endpoint, data) {
  const response = await page.request.fetch(origin + endpoint, { method, data })
  assert(response.ok(), `${method} ${endpoint}: ${response.status()} ${await response.text()}`)
  return (await response.json()).data
}
const health = await api('GET', '/api/v1/health')
assert.equal(health.crypto_mode, 'sm2', 'This experiment requires real SM2; fake mode is not accepted')
const original = await api('GET', '/api/security-profiles/default')
const fields = ['allowed_actions', 'resource_scopes', 'allow_egress', 'max_affected_objects', 'approval_policy']
const profile = Object.fromEntries(fields.map(key => [key, original[key]]))
let task, evaluated, execution, bundle, secondEvaluated, secondExecution, secondBundle
async function capturedData(predicate, afterIndex = 0) {
  await expect.poll(() => captured.slice(afterIndex).findLast(predicate), { timeout: 15000 }).toBeTruthy()
  return captured.slice(afterIndex).findLast(predicate).body.data
}
try {
  await api('PUT', '/api/security-profiles/default', { ...profile,
    approval_policy: { ...profile.approval_policy, required_actions: [...new Set([...profile.approval_policy.required_actions, 'create_file'])] } })
  await page.goto(origin)
  await expect(page.getByText('SM2 / SM3', { exact: true })).toBeVisible()
  await page.getByLabel('任务目标', { exact: true }).fill('在授权范围生成实验摘要，并验证 SM2 签名与 SM3 审计链')
  await page.getByRole('button', { name: '创建任务', exact: true }).click()
  await expect(page.getByRole('button', { name: '确认契约', exact: true })).toBeVisible()
  task = await capturedData(item => /\/sessions\/[^/]+\/tasks$/.test(item.path))
  await page.screenshot({ path: path.join(output, '01-contract.png'), fullPage: true })
  await page.getByRole('button', { name: '确认契约', exact: true }).click()
  const resource = `reports/browser-${stamp}.txt`
  const content = `Aegis Core 实验摘要\n真实 SM2/SM3，审批后生成。\n${stamp}`
  await expect(page.getByRole('button', { name: '评估请求', exact: true })).toBeEnabled()
  await page.getByLabel('资源路径', { exact: true }).fill(resource)
  await page.getByRole('textbox', { name: '内容', exact: true }).fill(content)
  await page.getByRole('button', { name: '评估请求', exact: true }).click()
  await expect(page.getByTestId('real-gateway-decision')).toHaveText('REQUIRE_CONFIRMATION')
  evaluated = await capturedData(item => item.path.endsWith('/evaluate'))
  await page.screenshot({ path: path.join(output, '02-confirmation.png'), fullPage: true })
  const file = path.join(workspace, resource)
  await assert.rejects(readFile(file), error => error.code === 'ENOENT', 'No file before approval')
  checks.push('no effect before approval')
  await page.getByRole('button', { name: '批准并重新检查', exact: true }).click()
  await expect(page.getByTestId('real-gateway-decision')).toHaveText('ALLOW')
  await page.getByRole('button', { name: '执行工具', exact: true }).click()
  await expect(page.locator('.execution-result')).toContainText('EXECUTED')
  execution = await capturedData(item => item.path.endsWith('/execute'))
  assert.equal(await readFile(file, 'utf8'), content)
  checks.push('real file after execution')
  assert.equal(execution.result.runtime.commit_status, 'COMMITTED')
  assert(execution.result.runtime.checkpoint_id && execution.result.runtime.effect_id)
  checks.push('first execution returns committed Runtime checkpoint and effect')
  await page.screenshot({ path: path.join(output, '03-executed.png'), fullPage: true })
  await page.getByRole('button', { name: '导出证据', exact: true }).click()
  await expect(page.getByRole('button', { name: '核验原件', exact: true })).toBeVisible()
  bundle = await capturedData(item => item.path.endsWith('/audit/export'))
  const originalBundleBytes = JSON.stringify(bundle)
  const originalCheckpoint = bundle.checkpoint.checkpoint_id
  await writeFile(path.join(output, 'bundle.json'), originalBundleBytes)
  await page.getByRole('button', { name: '核验原件', exact: true }).click()
  await expect(page.getByText('核验通过', { exact: true })).toBeVisible()
  const originalVerification = await capturedData(item => item.path.endsWith('/audit/verify'))
  assert.equal(originalVerification.valid, true)
  checks.push('original verifies')
  await page.locator('.core-evidence-panel').screenshot({ path: path.join(output, '04-verified.png') })
  await page.getByRole('button', { name: '制作篡改副本', exact: true }).click()
  await page.getByRole('button', { name: '核验副本', exact: true }).click()
  await expect(page.getByText('核验失败', { exact: true })).toBeVisible()
  const tamperedVerification = await capturedData(item => item.path.endsWith('/audit/verify') && item.body.data?.valid === false)
  assert.equal(tamperedVerification.valid, false)
  checks.push('tampered copy fails')
  await page.locator('.core-evidence-panel').screenshot({ path: path.join(output, '05-tampered.png') })
  const before = await api('GET', `/api/v1/tasks/${task.task_id}/events`)
  const retry = await api('POST', `/api/v1/tool-calls/${execution.request_id}/execute`)
  assert.deepEqual(retry, execution)
  const after = await api('GET', `/api/v1/tasks/${task.task_id}/events`)
  assert.equal(after.length, before.length, 'A successful retry does not append a second execution')
  checks.push('cached retry without duplicate events')
  const negative = { ...JSON.parse(await page.getByLabel('ToolEvaluationRequest JSON', { exact: true }).inputValue()) }
  negative.envelope.canonical_args.content = 'changed after approval'
  const rejected = await page.request.post(origin + '/api/v1/tool-calls/evaluate', { data: negative })
  assert.equal(rejected.status(), 409)
  assert.equal(await readFile(file, 'utf8'), content)
  checks.push('changed request ID payload rejected')

  // Keep the original signed package and its independently selected anchor while
  // exercising a second real call within exactly the same confirmed task.
  const secondResource = `reports/browser-${stamp}-second.txt`
  const secondContent = `Aegis Core 第二次真实执行\n同一任务，新的请求与恢复凭据。\n${stamp}`
  const secondFile = path.join(workspace, secondResource)
  await expect(page.getByLabel('资源路径', { exact: true })).toBeEnabled()
  await page.getByLabel('资源路径', { exact: true }).fill(secondResource)
  await page.getByRole('textbox', { name: '内容', exact: true }).fill(secondContent)
  const secondEvaluateStart = captured.length
  await page.getByRole('button', { name: '评估请求', exact: true }).click()
  await expect(page.getByTestId('real-gateway-decision')).toHaveText('REQUIRE_CONFIRMATION')
  secondEvaluated = await capturedData(item => item.path.endsWith('/evaluate'), secondEvaluateStart)
  const secondRequest = JSON.parse(await page.getByLabel('ToolEvaluationRequest JSON', { exact: true }).inputValue())
  assert.equal(secondRequest.envelope.task_id, task.task_id)
  assert.notEqual(secondRequest.envelope.request_id, execution.request_id)
  checks.push('second request uses the same task with a distinct request ID')
  await assert.rejects(readFile(secondFile), error => error.code === 'ENOENT', 'No second file before approval')
  checks.push('second request has no effect before approval')
  await page.getByRole('button', { name: '批准并重新检查', exact: true }).click()
  await expect(page.getByTestId('real-gateway-decision')).toHaveText('ALLOW')
  await page.getByRole('button', { name: '执行工具', exact: true }).click()
  await expect(page.locator('.execution-result')).toContainText('EXECUTED')
  secondExecution = await capturedData(item => item.path === `/api/v1/tool-calls/${secondRequest.envelope.request_id}/execute`)
  assert.equal(await readFile(secondFile, 'utf8'), secondContent)
  assert.equal(secondExecution.result.runtime.commit_status, 'COMMITTED')
  assert.notEqual(secondExecution.result.runtime.effect_id, execution.result.runtime.effect_id)
  checks.push('second real file commits with a distinct Runtime effect')
  assert.equal(await readFile(file, 'utf8'), content)
  checks.push('second execution preserves the first file')
  const afterSecond = await api('GET', `/api/v1/tasks/${task.task_id}/events`)
  assert(afterSecond.length > after.length, 'The same task history must grow after the second execution')
  for (const requestId of [execution.request_id, secondExecution.request_id])
    assert.equal(afterSecond.filter(item => item.type === 'EXECUTION_FINISHED' && item.request_id === requestId).length, 1)
  checks.push('same-task history grows with exactly one finish for each execution')
  await page.screenshot({ path: path.join(output, '06-second-executed.png'), fullPage: true })

  const oldVerifyStart = captured.length
  await page.getByRole('button', { name: '核验原件', exact: true }).click()
  await expect(page.getByText('核验通过', { exact: true })).toBeVisible()
  const oldVerificationAfterGrowth = await capturedData(item => item.path.endsWith('/audit/verify'), oldVerifyStart)
  const oldVerifyRequest = captured.slice(oldVerifyStart).findLast(item => item.path.endsWith('/audit/verify')).request
  assert.equal(oldVerificationAfterGrowth.valid, true)
  assert.equal(oldVerifyRequest.trusted_checkpoint_id, originalCheckpoint)
  assert.equal(JSON.stringify(oldVerifyRequest.bundle), originalBundleBytes)
  checks.push('old bundle verifies through the UI with its original anchor after history growth')

  const secondExportStart = captured.length
  await page.getByRole('button', { name: '导出证据', exact: true }).click()
  secondBundle = await capturedData(item => item.path.endsWith('/audit/export'), secondExportStart)
  const secondCheckpoint = secondBundle.checkpoint.checkpoint_id
  assert.notEqual(secondCheckpoint, originalCheckpoint, 'The second export must create a new checkpoint')
  assert(secondBundle.checkpoint.to_seq > bundle.checkpoint.to_seq, 'The new anchor must cover the additional history')
  await expect(page.getByLabel('检查点 ID', { exact: true })).toHaveValue(secondCheckpoint)
  checks.push('second export creates a new checkpoint covering the grown history')
  const secondVerifyStart = captured.length
  await page.getByRole('button', { name: '核验原件', exact: true }).click()
  await expect(page.getByText('核验通过', { exact: true })).toBeVisible()
  const secondVerification = await capturedData(item => item.path.endsWith('/audit/verify'), secondVerifyStart)
  const secondVerifyRequest = captured.slice(secondVerifyStart).findLast(item => item.path.endsWith('/audit/verify')).request
  assert.equal(secondVerification.valid, true)
  assert.equal(secondVerifyRequest.trusted_checkpoint_id, secondCheckpoint)
  assert.deepEqual(secondVerifyRequest.bundle, secondBundle)
  checks.push('new bundle verifies against its new checkpoint')
  await page.locator('.core-evidence-panel').screenshot({ path: path.join(output, '07-second-verified.png') })
  const retainedOriginalVerification = await api('POST', '/api/v1/audit/verify', {
    bundle, task_id: task.task_id, trusted_checkpoint_id: originalCheckpoint,
  })
  assert.equal(retainedOriginalVerification.valid, true)
  assert.equal(await readFile(path.join(output, 'bundle.json'), 'utf8'), originalBundleBytes)
  assert.equal(JSON.stringify(bundle), originalBundleBytes)
  checks.push('old package bytes and trusted anchor remain valid after the second export')

  // The ordinary audit page must display the same actual Core task history.
  const history = await api('GET', `/api/tasks/${task.task_id}/events?limit=1000&offset=0`)
  assert.deepEqual(history.events.map(item => item.event_id), afterSecond.map(item => item.event_id))
  await page.goto(`${origin}/audit?task_id=${encodeURIComponent(task.task_id)}`)
  await expect(page.getByRole('heading', { name: '审计时间线', exact: true })).toBeVisible()
  await expect(page.getByPlaceholder('任务 ID…', { exact: true })).toHaveValue(task.task_id)
  const timeline = page.locator('main ol')
  await expect(timeline.locator('li')).toHaveCount(history.events.length)
  await expect(timeline).toContainText('EXECUTION_FINISHED')
  await timeline.evaluate(element => { element.scrollTop = element.scrollHeight })
  await page.screenshot({ path: path.join(output, '08-task-audit-history.png'), fullPage: true })
  checks.push('real audit page displays the current task history matching Core event IDs')
  assert.deepEqual(errors, [], 'Browser should not produce uncaught errors')
  checks.push('browser has no uncaught errors')
  await writeFile(path.join(output, 'bundle-after-second.json'), JSON.stringify(secondBundle))
  await writeFile(path.join(output, 'task-history.json'), JSON.stringify(history, null, 2))
  await writeFile(path.join(output, 'result.json'), JSON.stringify({ health, task, evaluated, execution,
    secondEvaluated, secondExecution, captured, checks,
    originalCheckpoint, secondCheckpoint,
    verifications: { originalVerification, tamperedVerification, oldVerificationAfterGrowth,
      secondVerification, retainedOriginalVerification },
    event_count: afterSecond.length, event_count_before_second: after.length,
    verified_files: [resource, secondResource], audit_page: page.url(), browser_errors: errors }, null, 2))
  console.log(JSON.stringify({ passed: true, output, task_id: task.task_id,
    request_ids: [execution.request_id, secondExecution.request_id],
    checkpoints: [originalCheckpoint, secondCheckpoint], checks: checks.length, events: afterSecond.length }))
} catch (error) {
  await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true })
  await writeFile(path.join(output, 'failure.json'), JSON.stringify({ error: String(error), captured, errors }, null, 2))
  throw error
} finally {
  try { await api('PUT', '/api/security-profiles/default', profile) }
  finally { await browser.close() }
}
