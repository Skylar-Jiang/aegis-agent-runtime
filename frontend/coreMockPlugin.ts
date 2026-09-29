import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Plugin } from 'vite'
import { FakeToolGateway } from './src/features/core/mock/FakeToolGateway'
import { scenarios, type Scenario } from './src/features/core/gateway'

/** Installed only in the Vite dev server; never registers production API routes. */
export function coreMockPlugin(): Plugin {
  return {
    name: 'p2-core-fixture-server',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        if (!request.url?.startsWith('/__core_mock__/')) return next()
        void handleCoreMock(request, response)
      })
    },
  }
}

export async function handleCoreMock(
  request: IncomingMessage,
  response: ServerResponse,
) {
  const send = (status: number, body: unknown) => {
    response.writeHead(status, {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'no-store',
    })
    response.end(JSON.stringify(body))
  }
  try {
    const path = new URL(request.url ?? '/', 'http://localhost').pathname
    const gateway = new FakeToolGateway()
    const scenario = path.match(/^\/__core_mock__\/scenarios\/([a-z]+)$/)?.[1]
    if (
      request.method === 'GET' &&
      scenario &&
      scenarios.includes(scenario as Scenario)
    ) {
      if (scenario === 'error')
        return send(503, { error: 'FIXTURE_UNAVAILABLE' })
      return send(200, await gateway.load(scenario as Scenario))
    }
    if (
      request.method === 'POST' &&
      path === '/__core_mock__/confirmations/p2-confirmation-1'
    ) {
      let body = ''
      for await (const chunk of request) {
        body += chunk.toString()
        if (body.length > 8192) return send(413, { error: 'BODY_TOO_LARGE' })
      }
      const input: unknown = JSON.parse(body)
      if (
        !input ||
        typeof input !== 'object' ||
        !('confirmed' in input) ||
        typeof input.confirmed !== 'boolean' ||
        Object.keys(input).length !== 1
      ) {
        return send(400, { error: 'CONFIRMATION_INVALID' })
      }
      // Stateless fixture replay; repeated HTTP requests repeat a simulation only.
      await gateway.load('confirm')
      return send(
        200,
        await gateway.resolveConfirmation('p2-confirmation-1', input.confirmed),
      )
    }
    return send(404, { error: 'MOCK_ROUTE_NOT_FOUND' })
  } catch {
    return send(400, { error: 'MOCK_REQUEST_INVALID' })
  }
}
