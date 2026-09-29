import { afterEach, expect, it, vi } from 'vitest'
import { RealCoreGatewayClient } from './gateway'

afterEach(() => vi.unstubAllGlobals())
it('creates a scoped Core session and uses only returned contract identity for confirmation', async () => {
  const sent: Array<{ url: string; body: unknown }> = []
  vi.stubGlobal(
    'fetch',
    vi.fn((url, init) => {
      sent.push({
        url: String(url),
        body: init?.body ? JSON.parse(init.body) : null,
      })
      return Promise.resolve(
        new Response(JSON.stringify({ data: { session_id: 'session-1' } }), {
          headers: { 'content-type': 'application/json' },
        }),
      )
    }),
  )
  const client = new RealCoreGatewayClient()
  const session = await client.createSession('Document review')
  await client.createTask(session.session_id, 'Read scoped file')
  await client.confirmContract('contract/a', 3)
  expect(sent).toEqual([
    {
      url: '/api/v1/sessions',
      body: {
        user_id: 'core-ui',
        title: 'Document review',
        security_profile_id: 'default',
      },
    },
    {
      url: '/api/v1/sessions/session-1/tasks',
      body: { objective: 'Read scoped file', completion_criteria: [] },
    },
    {
      url: '/api/v1/contracts/contract%2Fa/confirm',
      body: { version: 3, confirmed_by: 'core-ui' },
    },
  ])
})

it('verifies evidence using the independently selected task and checkpoint', async () => {
  const sent: unknown[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((_url, init) => {
      sent.push(JSON.parse(init.body))
      return Promise.resolve(
        new Response(
          JSON.stringify({
            data: { valid: false, errors: [{ code: 'CHAIN_INVALID' }] },
          }),
          { headers: { 'content-type': 'application/json' } },
        ),
      )
    }),
  )
  const client = new RealCoreGatewayClient()
  await client.exportAudit('task-1', 'trusted-checkpoint')
  const result = await client.verifyAudit(
    { task_id: 'tampered-task' },
    'task-1',
    'trusted-checkpoint',
  )
  expect(sent).toEqual([
    { task_id: 'task-1', checkpoint_id: 'trusted-checkpoint' },
    {
      bundle: { task_id: 'tampered-task' },
      task_id: 'task-1',
      trusted_checkpoint_id: 'trusted-checkpoint',
    },
  ])
  expect(result.valid).toBe(false)
})

it('requests bounded event pages after the selected cursor without changing legacy full reads', async () => {
  const fetch = vi
    .fn()
    .mockImplementation(() =>
      Promise.resolve(new Response(JSON.stringify({ data: [] }))),
    )
  vi.stubGlobal('fetch', fetch)
  const client = new RealCoreGatewayClient()
  const controller = new AbortController()
  await client.events('task/a', {
    afterSequence: 1000,
    limit: 1000,
    signal: controller.signal,
  })
  await client.events('task/a')
  expect(fetch.mock.calls[0]).toEqual([
    '/api/v1/tasks/task%2Fa/events?after_sequence=1000&limit=1000',
    { signal: controller.signal },
  ])
  expect(fetch.mock.calls[1][0]).toBe('/api/v1/tasks/task%2Fa/events')
})
