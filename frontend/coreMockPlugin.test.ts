// @vitest-environment node
import { createServer, type Server } from 'node:http'
import type { AddressInfo } from 'node:net'
import { afterAll, beforeAll, expect, it } from 'vitest'
import { handleCoreMock } from './coreMockPlugin'

let server: Server
let base: string
beforeAll(async () => {
  server = createServer((request, response) => {
    void handleCoreMock(request, response)
  })
  await new Promise<void>((resolve) => {
    server.listen(0, '127.0.0.1', resolve)
  })
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
})
afterAll(async () => {
  await new Promise<void>((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()))
  })
})

it('serves fixture events and confirmation over the actual HTTP boundary', async () => {
  const response = await fetch(`${base}/__core_mock__/scenarios/confirm`)
  expect(response.status).toBe(200)
  const initial = await response.json()
  expect(initial.decision.decision).toBe('REQUIRE_CONFIRMATION')
  const resolved = await fetch(
    `${base}/__core_mock__/confirmations/p2-confirmation-1`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirmed: true }),
    },
  )
  const result = await resolved.json()
  expect(result.demo).toBe(true)
  expect(
    result.events.map((event: { sequence: number }) => event.sequence),
  ).toEqual([1, 2, 3, 4])
})

it('handles unavailable, unknown and invalid mock requests', async () => {
  expect((await fetch(`${base}/__core_mock__/scenarios/error`)).status).toBe(
    503,
  )
  expect((await fetch(`${base}/__core_mock__/scenarios/unknown`)).status).toBe(
    404,
  )
  expect(
    (
      await fetch(`${base}/__core_mock__/confirmations/p2-confirmation-1`, {
        method: 'POST',
        body: '{"confirmed":"yes"}',
      })
    ).status,
  ).toBe(400)
})
