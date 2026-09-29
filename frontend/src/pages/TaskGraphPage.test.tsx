import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  cancelTaskGraph,
  getTaskApprovals,
  getTaskEffects,
  getTaskGraph,
  retryTaskGraphRecovery,
  type TaskGraphSnapshot,
} from '../api/taskGraphs'
import { grantApproval } from '../api/approvals'
import { previewDemoRecovery, resetDemoWorkspace } from '../api/demo'
import { TaskGraphPage } from './TaskGraphPage'
import { layoutGraphNodes } from './taskGraphLayout'

vi.mock('../api/taskGraphs', () => ({
  getTaskGraph: vi.fn().mockResolvedValue({
    graph_id: 'graph-1',
    task_id: 'task-1',
    status: 'WAITING_APPROVAL',
    started_at: '2026-08-02T01:00:00Z',
    finished_at: null,
    nodes: [
      {
        node_id: 'delete',
        dependencies: ['prepare'],
        request_id: 'request-1',
        tool_name: 'delete_file',
        status: 'WAITING_APPROVAL',
        blocked_reason: 'WAITING_APPROVAL',
        started_at: null,
        finished_at: null,
      },
    ],
  }),
  getTaskEffects: vi.fn().mockResolvedValue([
    {
      effect_id: 'effect-1',
      kind: 'filesystem',
      target_ref: 'file:demo.txt',
      status: 'PENDING',
      checkpoint_id: 'checkpoint-1',
      created_at: '2026-08-02T01:00:00Z',
    },
  ]),
  getTaskApprovals: vi.fn().mockResolvedValue([
    {
      approval_id: 'approval-1',
      task_id: 'task-1',
      request_id: 'request-1',
      status: 'PENDING',
      tool_name: 'delete_file',
    },
  ]),
  cancelTaskGraph: vi.fn().mockResolvedValue({
    graph_id: 'graph-1',
    task_id: 'task-1',
    status: 'FAILED',
    nodes: [],
  }),
  retryTaskGraphRecovery: vi.fn().mockResolvedValue({
    graph_id: 'graph-1',
    task_id: 'task-1',
    status: 'RUNNING',
    nodes: [],
    recovery_failures: {},
  }),
}))

vi.mock('../api/approvals', () => ({
  grantApproval: vi
    .fn()
    .mockResolvedValue({ status: 'GRANTED', decided_by: 'reviewer' }),
  denyApproval: vi.fn(),
}))

vi.mock('../api/tasks', () => ({
  getTaskEvents: vi.fn().mockResolvedValue({ events: [] }),
}))
vi.mock('../api/demo', () => ({
  resetDemoWorkspace: vi
    .fn()
    .mockResolvedValue({ workspace: 'demo_workspace' }),
  startDemoScenario: vi.fn(),
  recoverDemoScenario: vi.fn(),
  applyDemoUserModification: vi.fn(),
  previewDemoRecovery: vi.fn().mockResolvedValue({
    selected_effect_ids: ['derived'],
    affected_effect_ids: ['derived'],
    rollback_effect_ids: ['derived'],
    preserve_effect_ids: ['source'],
    conflict_effect_ids: [],
  }),
  getDemoRecovery: vi.fn().mockResolvedValue(null),
}))

afterEach(() => {
  cleanup()
  window.history.pushState({}, '', '/runtime')
})

describe('TaskGraphPage', () => {
  it('shows recovery failure reasons and retries only on an explicit click', async () => {
    vi.mocked(getTaskGraph).mockResolvedValueOnce({
      graph_id: 'graph-failed',
      task_id: 'task-1',
      status: 'FAILED',
      nodes: [],
      recovery_failures: { 'request-1': 'checkpoint unavailable' },
    })
    vi.mocked(getTaskGraph).mockResolvedValueOnce({
      graph_id: 'graph-failed',
      task_id: 'task-1',
      status: 'RUNNING',
      nodes: [],
      recovery_failures: {},
    })
    window.history.pushState({}, '', '/runtime?task_id=task-1')
    render(
      <BrowserRouter>
        <TaskGraphPage />
      </BrowserRouter>,
    )
    expect(
      await screen.findByText(/checkpoint unavailable/),
    ).toBeInTheDocument()
    expect(retryTaskGraphRecovery).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Retry recovery' }))
    await waitFor(() =>
      expect(retryTaskGraphRecovery).toHaveBeenCalledWith('graph-failed'),
    )
    await waitFor(() =>
      expect(
        screen.queryByText(/checkpoint unavailable/),
      ).not.toBeInTheDocument(),
    )
  })
  it('ignores every projection from a slow previous URL task and reports failed polling', async () => {
    let resolveOld!: (value: TaskGraphSnapshot) => void
    window.history.pushState({}, '', '/runtime?task_id=task-old')
    vi.mocked(getTaskGraph)
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveOld = resolve
          }),
      )
      .mockResolvedValueOnce({
        graph_id: 'graph-new',
        task_id: 'task-new',
        status: 'RUNNING',
        nodes: [],
      })
    render(
      <BrowserRouter>
        <TaskGraphPage />
      </BrowserRouter>,
    )
    await waitFor(() => expect(resolveOld).toBeDefined())
    await act(async () => {
      window.history.pushState({}, '', '/runtime?task_id=task-new')
      window.dispatchEvent(new PopStateEvent('popstate'))
    })
    expect(await screen.findByText('graph-new')).toBeInTheDocument()
    await act(async () => {
      resolveOld({
        graph_id: 'graph-old',
        task_id: 'task-old',
        status: 'COMPLETED',
        nodes: [],
      })
    })
    expect(screen.queryByText('graph-old')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Task ID')).toHaveValue('task-new')
    vi.mocked(getTaskGraph).mockRejectedValueOnce(new Error('network offline'))
    expect(
      await screen.findByRole('alert', {}, { timeout: 1800 }),
    ).toHaveTextContent('network offline')
    expect(screen.getByText('graph-new')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry now' })).toBeEnabled()
  })
  it('confirms that the real reset request completed', async () => {
    render(
      <BrowserRouter>
        <TaskGraphPage />
      </BrowserRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Reset workspace' }))

    await waitFor(() => expect(resetDemoWorkspace).toHaveBeenCalled())
    expect(await screen.findByText(/Workspace reset/)).toBeInTheDocument()
  })

  it('does not restore stale runtime facts after a reset', async () => {
    window.history.pushState({}, '', '/runtime?task_id=task-1')
    render(
      <BrowserRouter>
        <TaskGraphPage />
      </BrowserRouter>,
    )

    await screen.findByText('graph-1')
    let resolveStale: ((value: TaskGraphSnapshot) => void) | undefined
    vi.mocked(getTaskGraph).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveStale = resolve
        }),
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'Refresh runtime facts' }),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Reset workspace' }))
    resolveStale?.({
      graph_id: 'stale-graph',
      task_id: 'task-1',
      status: 'COMPLETED',
      nodes: [],
    })

    expect(await screen.findByText(/Workspace reset/)).toBeInTheDocument()
    await waitFor(() =>
      expect(screen.queryByText('stale-graph')).not.toBeInTheDocument(),
    )
    expect(screen.getByLabelText('Task ID')).toHaveValue('')
  })

  it('keeps downstream nodes in their parent order so dependency arrows do not cross', () => {
    const nodes: TaskGraphSnapshot['nodes'] = [
      { node_id: 'N1', dependencies: [], status: 'COMMITTED' },
      { node_id: 'N2', dependencies: ['N1'], status: 'COMMITTED' },
      { node_id: 'N3', dependencies: [], status: 'WAITING_APPROVAL' },
      { node_id: 'N4', dependencies: [], status: 'COMMITTED' },
      { node_id: 'N5', dependencies: ['N4'], status: 'COMMITTED' },
      { node_id: 'N6', dependencies: ['N3'], status: 'BLOCKED' },
    ]

    const layout = layoutGraphNodes(nodes)

    expect(layout.positions.get('N2')!.y).toBeLessThan(
      layout.positions.get('N6')!.y,
    )
    expect(layout.positions.get('N6')!.y).toBeLessThan(
      layout.positions.get('N5')!.y,
    )
  })

  it('keeps controls interactive while an automatic graph refresh is pending', async () => {
    window.history.pushState({}, '', '/runtime?task_id=task-1')
    render(
      <BrowserRouter>
        <TaskGraphPage />
      </BrowserRouter>,
    )

    await screen.findByText('graph-1')
    const loadsBeforePoll = vi.mocked(getTaskGraph).mock.calls.length
    let resolvePoll: ((value: TaskGraphSnapshot) => void) | undefined
    vi.mocked(getTaskGraph).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolvePoll = resolve
        }),
    )

    await waitFor(
      () =>
        expect(vi.mocked(getTaskGraph).mock.calls.length).toBeGreaterThan(
          loadsBeforePoll,
        ),
      { timeout: 1800 },
    )
    const stayedInteractive = screen
      .getByRole('button', { name: /Refresh runtime facts|Loading…/ })
      .matches(':enabled')
    resolvePoll?.({
      graph_id: 'graph-1',
      task_id: 'task-1',
      status: 'WAITING_APPROVAL',
      nodes: [
        {
          node_id: 'delete',
          dependencies: ['prepare'],
          request_id: 'request-1',
          tool_name: 'delete_file',
          status: 'WAITING_APPROVAL',
        },
      ],
    })

    expect(stayedInteractive).toBe(true)
  })

  it('previews a selected child rollback without selecting its parent', async () => {
    vi.mocked(getTaskGraph).mockResolvedValueOnce({
      graph_id: 'recovery-graph',
      task_id: 'demo-recovery-task-1',
      status: 'COMPLETED',
      nodes: [],
    })
    vi.mocked(getTaskEffects).mockResolvedValueOnce([
      {
        effect_id: 'source',
        kind: 'filesystem',
        target_ref: 'file:demo_workspace/recovery/source.txt',
        status: 'COMMITTED',
        parent_effect_ids: [],
      },
      {
        effect_id: 'derived',
        kind: 'filesystem',
        target_ref: 'file:demo_workspace/recovery/derived.txt',
        status: 'COMMITTED',
        parent_effect_ids: ['source'],
      },
      {
        effect_id: 'independent',
        kind: 'filesystem',
        target_ref: 'file:demo_workspace/recovery/independent.txt',
        status: 'COMMITTED',
        parent_effect_ids: [],
      },
    ])
    window.history.pushState({}, '', '/runtime?task_id=demo-recovery-task-1')
    render(
      <BrowserRouter>
        <TaskGraphPage />
      </BrowserRouter>,
    )

    fireEvent.click(
      await screen.findByRole('checkbox', { name: 'Select derived.txt' }),
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'Preview selected rollback' }),
    )

    await waitFor(() =>
      expect(previewDemoRecovery).toHaveBeenCalledWith('demo-recovery-task-1', [
        'derived',
      ]),
    )
    expect(await screen.findByText(/Affected closure 1/)).toBeInTheDocument()
  })

  it('loads the graph, approval, and redacted effect projections for a task', async () => {
    window.history.pushState({}, '', '/runtime?task_id=task-1')
    render(
      <BrowserRouter>
        <TaskGraphPage />
      </BrowserRouter>,
    )

    await waitFor(() => expect(screen.getByText('graph-1')).toBeInTheDocument())
    expect(screen.getAllByText('WAITING_APPROVAL')).not.toHaveLength(0)
    expect(screen.getByText('file:demo.txt')).toBeInTheDocument()
    expect(screen.getByText('approval-1')).toBeInTheDocument()
    expect(screen.getByText('prepare')).toBeInTheDocument()
    expect(screen.getByText('checkpoint-1')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Refresh runtime facts' }),
    ).toBeEnabled()
    expect(screen.getByText('Safety inspector')).toBeInTheDocument()
    expect(screen.getByText('Runtime TaskGraph')).toBeInTheDocument()
  })

  it('does not request dependent projections when the graph is unknown', async () => {
    vi.mocked(getTaskGraph).mockRejectedValueOnce(
      new Error('Unknown task graph'),
    )
    vi.mocked(getTaskEffects).mockClear()
    vi.mocked(getTaskApprovals).mockClear()
    window.history.pushState({}, '', '/runtime')
    render(
      <BrowserRouter>
        <TaskGraphPage />
      </BrowserRouter>,
    )

    fireEvent.change(screen.getByLabelText('Task ID'), {
      target: { value: 'missing' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Load runtime facts' }))

    expect(await screen.findByText('Unknown task graph')).toBeInTheDocument()
    expect(getTaskEffects).not.toHaveBeenCalled()
    expect(getTaskApprovals).not.toHaveBeenCalled()
  })

  it('cancels a waiting graph through the runtime API', async () => {
    window.history.pushState({}, '', '/runtime?task_id=task-1')
    render(
      <BrowserRouter>
        <TaskGraphPage />
      </BrowserRouter>,
    )

    await screen.findByText('graph-1')
    fireEvent.click(screen.getByRole('button', { name: 'Cancel graph' }))

    await waitFor(() => expect(cancelTaskGraph).toHaveBeenCalledWith('graph-1'))
  })

  it('grants the selected node approval without leaving the runtime view', async () => {
    window.history.pushState({}, '', '/runtime?task_id=task-1')
    render(
      <BrowserRouter>
        <TaskGraphPage />
      </BrowserRouter>,
    )

    await screen.findByText('graph-1')
    const loadsBeforeApproval = vi.mocked(getTaskGraph).mock.calls.length
    fireEvent.change(screen.getByLabelText('Approver identity'), {
      target: { value: 'reviewer' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Grant approval' }))

    await waitFor(() =>
      expect(grantApproval).toHaveBeenCalledWith('approval-1', 'reviewer'),
    )
    await waitFor(() =>
      expect(vi.mocked(getTaskGraph).mock.calls.length).toBeGreaterThan(
        loadsBeforeApproval,
      ),
    )
  })
})
