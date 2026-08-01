import { get, post } from './client';
import type { ApprovalDecision } from '../types/contracts';

export interface ApprovalListItem {
  approval_id: string;
  task_id: string;
  status: string;
  tool_name: string;
  reason: string;
  step_id: string;
  request_id: string;
}

export function listApprovals(taskId?: string): Promise<ApprovalListItem[]> {
  const query = taskId ? `?task_id=${encodeURIComponent(taskId)}` : '';
  return get(`/approvals${query}`);
}

export function grantApproval(
  approvalId: string,
  decidedBy: string,
  reason = 'approved',
): Promise<ApprovalDecision> {
  return post<ApprovalDecision>(
    `/approvals/${approvalId}/grant?decided_by=${encodeURIComponent(decidedBy)}&reason=${encodeURIComponent(reason)}`,
  );
}

export function denyApproval(
  approvalId: string,
  decidedBy: string,
  reason = 'denied',
): Promise<ApprovalDecision> {
  return post<ApprovalDecision>(
    `/approvals/${approvalId}/deny?decided_by=${encodeURIComponent(decidedBy)}&reason=${encodeURIComponent(reason)}`,
  );
}
