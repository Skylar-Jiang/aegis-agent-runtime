import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { getTaskApprovals, getTaskEffects, getTaskGraph } from '../api/taskGraphs';
import { TaskGraphPage } from './TaskGraphPage';

vi.mock('../api/taskGraphs', () => ({
  getTaskGraph: vi.fn().mockResolvedValue({
    graph_id: 'graph-1',
    task_id: 'task-1',
    status: 'WAITING_APPROVAL',
    started_at: '2026-08-02T01:00:00Z',
    finished_at: null,
    nodes: [{ node_id: 'delete', dependencies: ['prepare'], status: 'BLOCKED', blocked_reason: 'WAITING_APPROVAL', started_at: null, finished_at: null }],
  }),
  getTaskEffects: vi.fn().mockResolvedValue([
    { effect_id: 'effect-1', kind: 'filesystem', target_ref: 'file:demo.txt', status: 'PENDING', checkpoint_id: 'checkpoint-1', created_at: '2026-08-02T01:00:00Z' },
  ]),
  getTaskApprovals: vi.fn().mockResolvedValue([
    { approval_id: 'approval-1', task_id: 'task-1', status: 'PENDING', tool_name: 'delete_file' },
  ]),
}));

afterEach(() => {
  cleanup();
  window.history.pushState({}, '', '/runtime');
});

describe('TaskGraphPage', () => {
  it('loads the graph, approval, and redacted effect projections for a task', async () => {
    window.history.pushState({}, '', '/runtime?task_id=task-1');
    render(<BrowserRouter><TaskGraphPage /></BrowserRouter>);

    await waitFor(() => expect(screen.getByText('graph-1')).toBeInTheDocument());
    expect(screen.getAllByText('WAITING_APPROVAL')).not.toHaveLength(0);
    expect(screen.getByText('file:demo.txt')).toBeInTheDocument();
    expect(screen.getByText('approval-1')).toBeInTheDocument();
    expect(screen.getByText('prepare')).toBeInTheDocument();
    expect(screen.getByText('checkpoint-1')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Refresh runtime facts' })).toBeEnabled();
  });

  it('does not request dependent projections when the graph is unknown', async () => {
    vi.mocked(getTaskGraph).mockRejectedValueOnce(new Error('Unknown task graph'));
    vi.mocked(getTaskEffects).mockClear();
    vi.mocked(getTaskApprovals).mockClear();
    window.history.pushState({}, '', '/runtime');
    render(<BrowserRouter><TaskGraphPage /></BrowserRouter>);

    fireEvent.change(screen.getByLabelText('Task ID'), { target: { value: 'missing' } });
    fireEvent.click(screen.getByRole('button', { name: 'Load runtime facts' }));

    expect(await screen.findByText('Unknown task graph')).toBeInTheDocument();
    expect(getTaskEffects).not.toHaveBeenCalled();
    expect(getTaskApprovals).not.toHaveBeenCalled();
  });
});
