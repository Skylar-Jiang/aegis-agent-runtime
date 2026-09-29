import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getTaskEvents, getTaskSteps, listTasks } from '../api/tasks'
import { useUIStore } from '../stores/ui'
import { TasksPage } from './TasksPage'

vi.mock('../api/health', () => ({
  getRuntimeHealth: vi.fn().mockResolvedValue({
    status: 'ok',
    phase: 'runtime-base-main-chain',
    mode: 'live-agent',
  }),
}))

vi.mock('../api/tasks', () => ({
  cancelTask: vi.fn(),
  createTask: vi.fn(),
  getTask: vi.fn(),
  getTaskEvents: vi.fn(),
  getTaskSteps: vi.fn(),
  listTasks: vi.fn(),
  subscribeToTaskEvents: vi.fn().mockReturnValue(null),
}))

vi.mock('../api/approvals', () => ({
  listApprovals: vi.fn().mockResolvedValue([]),
  denyApproval: vi.fn(),
  grantApproval: vi.fn(),
}))

beforeEach(() => {
  window.history.pushState({}, '', '/?task_id=task-server')
  useUIStore.setState({ selectedTaskId: null, taskHistory: [] })
  vi.mocked(listTasks).mockResolvedValue([
    {
      task_id: 'task-server',
      objective: 'Read the persisted project summary',
      status: 'COMPLETED',
      created_at: '2026-09-11T00:00:00Z',
      updated_at: '2026-09-11T00:00:01Z',
      final_answer: 'done',
      contract: null,
    },
  ])
  vi.mocked(getTaskEvents).mockResolvedValue({
    task_id: 'task-server',
    events: [],
    count: 0,
  })
  vi.mocked(getTaskSteps).mockResolvedValue({
    task_id: 'task-server',
    steps: [
      {
        task_id: 'task-server',
        step_id: 'step-read',
        request_id: 'request-read',
        description: 'Read project summary',
        tool_name: 'read_file',
        arguments: { path: 'README.md' },
        status: 'COMMITTED',
      },
    ],
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('TasksPage server-backed state', () => {
  it('restores a deep-linked task and displays its Runtime steps', async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <QueryClientProvider client={client}>
        <BrowserRouter>
          <TasksPage />
        </BrowserRouter>
      </QueryClientProvider>,
    )

    expect(
      await screen.findAllByText('Read the persisted project summary'),
    ).not.toHaveLength(0)
    expect(await screen.findByText(/step-read/)).toBeInTheDocument()
    expect(
      screen.getByRole('link', { name: 'Open selected Runtime' }),
    ).toHaveAttribute('href', '/runtime?task_id=task-server')
  })
})
