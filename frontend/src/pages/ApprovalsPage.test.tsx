import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { afterEach, describe, expect, it } from 'vitest';

import { ApprovalsPage } from './ApprovalsPage';

import { vi } from 'vitest';

const api = vi.hoisted(() => ({
  listApprovals: vi.fn().mockResolvedValue([
    { approval_id: 'approval-1', task_id: 'task-graph', status: 'PENDING', tool_name: 'delete_file', reason: 'high risk' },
  ]),
  grantApproval: vi.fn().mockResolvedValue({ approval_id: 'approval-1', task_id: 'task-graph', status: 'GRANTED', decided_by: 'reviewer' }),
  denyApproval: vi.fn(),
}));

vi.mock('../api/approvals', () => api);

afterEach(() => {
  cleanup();
  api.listApprovals.mockClear();
  api.grantApproval.mockClear();
});

describe('ApprovalsPage', () => {
  it('submits a decision directly from the pending approval card without an approval-id field', async () => {
    render(<BrowserRouter><ApprovalsPage /></BrowserRouter>);

    await screen.findByRole('link', { name: 'Open runtime for task-graph' });
    expect(screen.queryByPlaceholderText('Approval ID...')).not.toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('Approver identity...'), { target: { value: 'reviewer' } });
    fireEvent.click(screen.getByRole('button', { name: 'Grant approval-1' }));

    await waitFor(() => expect(api.grantApproval).toHaveBeenCalledWith('approval-1', 'reviewer'));
    expect(api.listApprovals).toHaveBeenCalledTimes(2);
  });

  it('lists global approvals with their task identity', async () => {
    render(<BrowserRouter><ApprovalsPage /></BrowserRouter>);

    expect(await screen.findByRole('link', { name: 'Open runtime for task-graph' })).toBeInTheDocument();
    expect(screen.getAllByText('delete_file')).not.toHaveLength(0);
    expect(screen.getByRole('link', { name: 'Open runtime for task-graph' })).toHaveAttribute('href', '/runtime?task_id=task-graph');
  });
});
