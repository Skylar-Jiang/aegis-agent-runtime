import { afterEach, expect, it, vi } from 'vitest'
import { getTaskEvents, createTaskStreamUrl } from './tasks'
import { RealCoreGatewayClient } from '../features/core/gateway'
import { getRuntimeHealth } from './health'

const ok = (data: unknown) =>
  Promise.resolve(
    new Response(JSON.stringify({ data }), {
      headers: { 'content-type': 'application/json' },
    }),
  )
afterEach(() => {
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
  vi.resetModules()
})

it('loads every event page so event 101 is visible to task consumers', async () => {
  const seen: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((url) => {
      seen.push(String(url))
      const offset = Number(
        new URL(String(url), 'http://localhost').searchParams.get('offset'),
      )
      const events =
        offset === 0
          ? Array.from({ length: 100 }, (_, index) => ({
              event_id: `event-${index + 1}`,
            }))
          : [{ event_id: 'event-101' }]
      return ok({ task_id: 'long-task', events, count: events.length })
    }),
  )
  const result = await getTaskEvents('long-task')
  expect(result.events).toHaveLength(101)
  expect(result.events[100]).toEqual({ event_id: 'event-101' })
  expect(seen[1]).toBe('/api/tasks/long-task/events?limit=100&offset=100')
})

it('encodes task IDs consistently in event stream URLs', () => {
  expect(createTaskStreamUrl('task/a b', 5)).toBe(
    '/api/tasks/task%2Fa%20b/stream?after_sequence=5',
  )
})

it.each([
  'https://example.test',
  'https://example.test/api/',
  'https://example.test/api/v1/',
])(
  'normalizes %s across legacy, Core, health and SSE clients',
  async (base) => {
    vi.stubEnv('VITE_API_BASE_URL', base)
    vi.resetModules()
    const { getTask, createTaskStreamUrl: stream } = await import('./tasks')
    const { RealCoreGatewayClient: Core } =
      await import('../features/core/gateway')
    const { getRuntimeHealth: health } = await import('./health')
    const seen: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn((url) => {
        seen.push(String(url))
        return ok({ status: 'ok' })
      }),
    )
    await getTask('task')
    await new Core().events('task')
    await health()
    expect(seen).toEqual([
      'https://example.test/api/tasks/task',
      'https://example.test/api/v1/tasks/task/events',
      'https://example.test/health',
    ])
    expect(stream('task')).toBe(
      'https://example.test/api/tasks/task/stream?after_sequence=0',
    )
  },
)

it('uses the default same-origin URLs when no base URL is configured', async () => {
  const seen: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((url) => {
      seen.push(String(url))
      return ok({ status: 'ok' })
    }),
  )
  await new RealCoreGatewayClient().events('task')
  await getRuntimeHealth()
  expect(seen).toEqual(['/api/v1/tasks/task/events', '/health'])
})
