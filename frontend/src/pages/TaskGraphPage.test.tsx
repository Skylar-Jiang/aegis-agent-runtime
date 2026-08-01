import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { getTaskApprovals, getTaskEffects, getTaskGraph } from '../api/taskGraphs';
import { TaskGraphPage } from './TaskGraphPage';

vi.mock('../api/taskGraphs', () => ({
  getTaskGraph: vi.fn().mockResolvedValue({
    graph_id: 'graph-1',
    task_id: 'task-1',
    status: 'WAITING_APPROVAL',
    nodes: [{ node_id: 'delete', status: 'BLOCKED', blocked_reason: 'WAITING_APPROVAL' }],
  }),
  getTaskEffects: vi.fn().mockResolvedValue([
    { effect_id: 'effect-1', kind: 'filesystem', target_ref: 'file:demo.txt', status: 'PENDING' },
  ]),
  getTaskApprovals: vi.fn().mockResolvedValue([
    { approval_id: 'approval-1', task_id: 'task-1', status: 'PENDING', tool_name: 'delete_file' },
  ]),
}));

afterEach(() => cleanup());

describe('TaskGraphPage', () => {
  it('loads the graph, approval, and redacted effect projections for a task', async () => {
    render(<TaskGraphPage />);

    fireEvent.change(screen.getByLabelText('Task ID'), { target: { value: 'task-1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Load runtime facts' }));

    await waitFor(() => expect(screen.getByText('graph-1')).toBeInTheDocument());
    expect(screen.getAllByText('WAITING_APPROVAL')).not.toHaveLength(0);
    expect(screen.getByText('file:demo.txt')).toBeInTheDocument();
    expect(screen.getByText('approval-1')).toBeInTheDocument();
  });

  it('does not request dependent projections when the graph is unknown', async () => {
    vi.mocked(getTaskGraph).mockRejectedValueOnce(new Error('Unknown task graph'));
    vi.mocked(getTaskEffects).mockClear();
    vi.mocked(getTaskApprovals).mockClear();
    render(<TaskGraphPage />);

    fireEvent.change(screen.getByLabelText('Task ID'), { target: { value: 'missing' } });
    fireEvent.click(screen.getByRole('button', { name: 'Load runtime facts' }));

    expect(await screen.findByText('Unknown task graph')).toBeInTheDocument();
    expect(getTaskEffects).not.toHaveBeenCalled();
    expect(getTaskApprovals).not.toHaveBeenCalled();
  });
});
