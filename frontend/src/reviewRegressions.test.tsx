import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { AuditPage } from './pages/AuditPage'
import { SecuritySettingsPage } from './pages/SecuritySettingsPage'
import { CoreWorkbench } from './features/core/CoreWorkbench'
import { ConversationsPage } from './pages/ConversationsPage'
import { TasksPage } from './pages/TasksPage'
import { useUIStore } from './stores/ui'
import { MultiValueCombobox } from './components/MultiValueCombobox'
import { useLanguageStore } from './i18n'

const clients: QueryClient[] = []
const ok = (data: unknown) =>
  Promise.resolve(
    new Response(JSON.stringify({ data, error: null }), {
      headers: { 'content-type': 'application/json' },
    }),
  )
const profile = {
  profile_id: 'default',
  version: 1,
  created_at: '2026-01-01',
  allowed_actions: ['read_file'],
  resource_scopes: ['docs/**'],
  allow_egress: false,
  max_affected_objects: 100,
  approval_policy: { required_actions: [], bulk_action_threshold: 20 },
  denied_actions: [],
}
function wrap(node: React.ReactNode, route = '/') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  clients.push(client)
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>{node}</MemoryRouter>
    </QueryClientProvider>,
  )
}
beforeEach(() => {
  useLanguageStore.setState({ language: 'en' })
  useUIStore.setState({ selectedTaskId: null, taskHistory: [] })
  vi.stubGlobal(
    'EventSource',
    vi.fn(() => ({
      addEventListener: vi.fn(),
      close: vi.fn(),
      onopen: null,
      onerror: null,
    })),
  )
})
afterEach(() => {
  cleanup()
  clients.forEach((client) => client.clear())
  clients.length = 0
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

it('keeps an audit draft independent from the active URL task', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => ok({ events: [] })),
  )
  wrap(<AuditPage />, '/audit?task_id=task-A')
  const input = screen.getByPlaceholderText('Task ID...')
  fireEvent.change(input, { target: { value: 'task-B' } })
  await waitFor(() => expect(input).toHaveValue('task-B'))
})

it('retains newlines while editing scopes and normalizes only the saved payload', async () => {
  const saved: unknown[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((url, init) => {
      if (init?.method === 'PUT') saved.push(JSON.parse(init.body))
      return ok(String(url).includes('security-profiles') ? profile : [])
    }),
  )
  wrap(<SecuritySettingsPage />)
  const input = await screen.findByLabelText('Workspace resource scopes')
  fireEvent.change(input, { target: { value: 'docs/**\n' } })
  expect(input).toHaveValue('docs/**\n')
  fireEvent.change(input, { target: { value: 'docs/**\n notes/** \n' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save new version' }))
  await waitFor(() =>
    expect(saved[0]).toMatchObject({
      resource_scopes: ['docs/**', 'notes/**'],
    }),
  )
})

it('renders a profile load error instead of loading forever', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn((url) =>
      String(url).includes('security-profiles')
        ? Promise.resolve(
            new Response(JSON.stringify({ detail: 'unavailable' }), {
              status: 503,
            }),
          )
        : ok([]),
    ),
  )
  wrap(<SecuritySettingsPage />)
  expect(
    await screen.findByText('Security profile is unavailable.'),
  ).toBeInTheDocument()
  expect(
    screen.queryByText('Loading security profile…'),
  ).not.toBeInTheDocument()
})

it('invalidates an allowed request as soon as its draft changes and after failed evaluation', async () => {
  let evaluations = 0
  vi.stubGlobal(
    'fetch',
    vi.fn((url) => {
      if (String(url).endsWith('/evaluate')) {
        evaluations++
        return evaluations === 1
          ? ok({
              decision: { decision: 'ALLOW', reason_code: 'ALLOW' },
              effective_permission: {},
            })
          : Promise.resolve(
              new Response(JSON.stringify({ detail: 'B invalid' }), {
                status: 409,
              }),
            )
      }
      return ok([])
    }),
  )
  wrap(<CoreWorkbench />)
  const input = screen.getByLabelText('ToolEvaluationRequest JSON')
  fireEvent.change(input, {
    target: {
      value: JSON.stringify({
        envelope: { request_id: 'A', task_id: 'task-A' },
      }),
    },
  })
  fireEvent.click(screen.getByRole('button', { name: '真实 Evaluate' }))
  await waitFor(() =>
    expect(screen.getByRole('button', { name: '真实 Execute' })).toBeEnabled(),
  )
  fireEvent.change(input, {
    target: {
      value: JSON.stringify({
        envelope: { request_id: 'B', task_id: 'task-B' },
      }),
    },
  })
  expect(
    screen.queryByRole('button', { name: '真实 Execute' }),
  ).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '真实 Evaluate' }))
  await screen.findByRole('alert')
  expect(
    screen.queryByRole('button', { name: '真实 Execute' }),
  ).not.toBeInTheDocument()
})

it('does not send a conversation when Enter confirms IME composition', async () => {
  const writes: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((url, init) => {
      if (init?.method === 'POST') writes.push(String(url))
      if (String(url).includes('security-profiles')) return ok(profile)
      if (String(url).endsWith('/health')) return ok({ mode: 'live-agent' })
      return ok([])
    }),
  )
  wrap(<ConversationsPage />)
  const input = screen.getByPlaceholderText('Continue this conversation…')
  fireEvent.change(input, { target: { value: '正在输入' } })
  await act(async () => {
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true, keyCode: 229 })
  })
  expect(writes).toEqual([])
})

it('does not add an unfinished IME composition to combobox selections', () => {
  const onChange = vi.fn()
  render(
    <MultiValueCombobox
      label="Scopes"
      values={[]}
      options={[]}
      placeholder="scope"
      hint=""
      selectedLabel="Selected scopes"
      addLabel="Add scope"
      removeLabel="Remove scope"
      onChange={onChange}
    />,
  )
  const input = screen.getByLabelText('Scopes')
  fireEvent.change(input, { target: { value: '正在输入' } })
  fireEvent.keyDown(input, { key: 'Enter', isComposing: true, keyCode: 229 })
  expect(onChange).not.toHaveBeenCalled()
  expect(input).toHaveValue('正在输入')
})

it.each(['conversation', 'task'])(
  'resolves the current pending approval in %s directly even when history starts with an old approval',
  async (page) => {
    const writes: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn((url, init) => {
        const path = String(url)
        if (init?.method === 'POST') {
          writes.push(path)
          return Promise.resolve(
            new Response(JSON.stringify({ detail: 'approval unavailable' }), {
              status: 409,
            }),
          )
        }
        if (path.startsWith('/api/conversations?')) return ok([])
        if (path.includes('security-profiles')) return ok(profile)
        if (path.endsWith('/health')) return ok({ mode: 'live-agent' })
        if (path === '/api/conversations/conv-long')
          return ok({
            conversation_id: 'conv-long',
            title: 'Long',
            messages: [
              {
                message_id: 'msg',
                role: 'user',
                task_id: 'task-long',
                content: 'Long',
              },
            ],
          })
        if (path.startsWith('/api/tasks?'))
          return ok([
            {
              task_id: 'task-long',
              status: 'WAITING_APPROVAL',
              objective: 'Long',
              created_at: '2026-01-01',
            },
          ])
        if (path === '/api/tasks/task-long/steps') return ok({ steps: [] })
        if (path === '/api/tasks/task-long')
          return ok({ task_id: 'task-long', status: 'WAITING_APPROVAL' })
        if (path.includes('/events'))
          return ok({
            events: [
              {
                event_id: 'old-event',
                task_id: 'task-long',
                sequence_number: 1,
                event_type: 'APPROVAL_REQUESTED',
                status: 'WAITING_APPROVAL',
                timestamp: '2026-01-01',
                details: { approval_id: 'old-granted' },
              },
            ],
            count: 1,
          })
        if (path.startsWith('/api/approvals?'))
          return ok([
            {
              approval_id: 'new-pending',
              task_id: 'task-long',
              status: 'PENDING',
              tool_name: 'create_file',
              reason: 'approval',
              step_id: 'step',
              request_id: 'request',
            },
          ])
        return ok([])
      }),
    )
    wrap(
      page === 'conversation' ? <ConversationsPage /> : <TasksPage />,
      page === 'conversation'
        ? '/conversations?conversation_id=conv-long'
        : '/tasks?task_id=task-long',
    )
    fireEvent.click(
      await screen.findByRole('button', {
        name: page === 'conversation' ? 'Approve and resume' : 'Approve',
      }),
    )
    await waitFor(() => expect(writes).toHaveLength(1))
    expect(writes[0]).toContain('/approvals/new-pending/grant')
    expect(await screen.findByText('approval unavailable')).toBeInTheDocument()
  },
)

it('ignores an older audit response after switching tasks', async () => {
  let resolveOld!: (value: Response) => void
  vi.stubGlobal(
    'fetch',
    vi.fn((url) =>
      String(url).includes('task-old')
        ? new Promise<Response>((resolve) => {
            resolveOld = resolve
          })
        : ok({
            events: [
              {
                event_id: 'new',
                task_id: 'task-new',
                sequence_number: 1,
                event_type: 'TASK_FINISHED',
                status: 'COMPLETED',
                timestamp: '2026-01-01',
                summary: 'new task evidence',
                details: {},
              },
            ],
          }),
    ),
  )
  wrap(<AuditPage />, '/audit?task_id=task-old')
  fireEvent.change(screen.getByPlaceholderText('Task ID...'), {
    target: { value: 'task-new' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Load audit timeline' }))
  await screen.findByText(/new task evidence/)
  await act(async () =>
    resolveOld(
      await ok({
        events: [
          {
            event_id: 'old',
            task_id: 'task-old',
            sequence_number: 1,
            event_type: 'TASK_FINISHED',
            status: 'COMPLETED',
            timestamp: '2026-01-01',
            summary: 'old task evidence',
            details: {},
          },
        ],
      }),
    ),
  )
  expect(screen.queryByText(/old task evidence/)).not.toBeInTheDocument()
})
