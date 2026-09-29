import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useNavigate } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { ConversationsPage } from './ConversationsPage'
import { useLanguageStore } from '../i18n'

const ok = (data: unknown) =>
  Promise.resolve(
    new Response(JSON.stringify({ data }), {
      headers: { 'content-type': 'application/json' },
    }),
  )
let navigate!: ReturnType<typeof useNavigate>
function HistoryControl() {
  navigate = useNavigate()
  return null
}
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('binds task status and approval controls to the URL conversation after history navigation', async () => {
  useLanguageStore.setState({ language: 'en' })
  vi.stubGlobal(
    'fetch',
    vi.fn((url) => {
      const path = String(url)
      if (path.includes('security-profiles'))
        return ok({
          profile_id: 'default',
          allowed_actions: [],
          resource_scopes: [],
        })
      if (path.endsWith('/health')) return ok({ mode: 'live-agent' })
      if (path === '/api/conversations/conv-a')
        return ok({
          conversation_id: 'conv-a',
          title: 'Conversation A',
          messages: [
            { message_id: 'a', role: 'user', content: 'A', task_id: 'task-a' },
          ],
        })
      if (path === '/api/conversations/conv-b')
        return ok({
          conversation_id: 'conv-b',
          title: 'Conversation B',
          messages: [],
        })
      if (path === '/api/tasks/task-a')
        return ok({ task_id: 'task-a', status: 'WAITING_APPROVAL' })
      if (path.startsWith('/api/approvals'))
        return ok([
          { approval_id: 'approval-a', task_id: 'task-a', status: 'PENDING' },
        ])
      if (path.startsWith('/api/tasks/task-a/events'))
        return ok({ events: [], count: 0 })
      return ok([])
    }),
  )
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/conversations?conversation_id=conv-a']}>
        <HistoryControl />
        <ConversationsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  expect(
    await screen.findByRole('button', { name: 'Approve and resume' }),
  ).toBeInTheDocument()
  await act(async () => navigate('/conversations?conversation_id=conv-b'))
  expect(await screen.findByText('Conversation B')).toBeInTheDocument()
  expect(
    screen.queryByRole('button', { name: 'Approve and resume' }),
  ).not.toBeInTheDocument()
  await waitFor(() =>
    expect(
      screen.getByPlaceholderText('Continue this conversation…'),
    ).toBeEnabled(),
  )
  expect(
    screen.queryByText('Aegis is planning and executing through the Runtime…'),
  ).not.toBeInTheDocument()
  client.clear()
})
