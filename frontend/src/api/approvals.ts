import { post } from './client';
import type { ApprovalDecision } from '../types/contracts';

export function grantApproval(
  approvalId: string,
  decidedBy = 'admin',
  reason = 'approved',
): Promise<ApprovalDecision> {
  return post<ApprovalDecision>(
    `/approvals/${approvalId}/grant?decided_by=${encodeURIComponent(decidedBy)}&reason=${encodeURIComponent(reason)}`,
  );
}

export function denyApproval(
  approvalId: string,
  decidedBy = 'admin',
  reason = 'denied',
): Promise<ApprovalDecision> {
  return post<ApprovalDecision>(
    `/approvals/${approvalId}/deny?decided_by=${encodeURIComponent(decidedBy)}&reason=${encodeURIComponent(reason)}`,
  );
}
