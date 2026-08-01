import { get, post } from './client';

export interface ApprovalItem {
  approval_id: string;
  status: string;
  tool_name: string;
  reason: string;
  step_id: string;
  request_id: string;
  task_id: string;
}

export function listApprovals(params?: {
  status?: string;
  task_id?: string;
}): Promise<ApprovalItem[]> {
  const query = new URLSearchParams();
  if (params?.status) query.set('status', params.status);
  if (params?.task_id) query.set('task_id', params.task_id);
  const qs = query.toString();
  return get(`/approvals${qs ? '?' + qs : ''}`);
}

export function grantApproval(
  approvalId: string,
  decidedBy: string,
  reason?: string,
): Promise<{ approval_id: string; status: string; decided_by: string; reason: string }> {
  const qs = new URLSearchParams({ decided_by: decidedBy });
  if (reason) qs.set('reason', reason);
  return post(`/approvals/${approvalId}/grant?${qs.toString()}`);
}

export function denyApproval(
  approvalId: string,
  decidedBy: string,
  reason?: string,
): Promise<{ approval_id: string; status: string; decided_by: string; reason: string }> {
  const qs = new URLSearchParams({ decided_by: decidedBy });
  if (reason) qs.set('reason', reason);
  return post(`/approvals/${approvalId}/deny?${qs.toString()}`);
}
