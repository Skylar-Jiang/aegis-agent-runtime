import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { ApprovalsPage } from './ApprovalsPage';

import { vi } from 'vitest';

const api = vi.hoisted(() => ({
  listApprovals: vi.fn().mockResolvedValue([
    { approval_id: 'approval-1', task_id: 'task-graph', status: 'PENDING', tool_name: 'delete_file', reason: 'high risk' },
  ]),
  grantApproval: vi.fn(),
  denyApproval: vi.fn(),
}));

vi.mock('../api/approvals', () => api);

describe('ApprovalsPage', () => {
  it('requires an explicit approver identity before a decision can be submitted', () => {
    render(<ApprovalsPage />);

    fireEvent.change(screen.getByPlaceholderText('Approval ID...'), {
      target: { value: 'approval-1' },
    });

    expect(screen.getByRole('button', { name: 'Grant' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Deny' })).toBeDisabled();
  });

  it('lists global approvals with their task identity', async () => {
    render(<ApprovalsPage />);

    expect(await screen.findByText('task-graph')).toBeInTheDocument();
    expect(screen.getAllByText('delete_file')).not.toHaveLength(0);
  });
});
