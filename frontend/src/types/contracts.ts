// Mirror of backend contracts/ — frozen at v0.2

export type AuditEventType =
  | 'TASK_CREATED' | 'PLAN_CREATED' | 'TOOL_REQUESTED'
  | 'RISK_CLASSIFIED' | 'PERMISSION_CHECKED' | 'APPROVAL_REQUESTED'
  | 'APPROVAL_GRANTED' | 'APPROVAL_DENIED'
  | 'CHECKPOINT_CREATED' | 'EXECUTION_STARTED' | 'EXECUTION_FINISHED'
  | 'DEEP_CHECK_STARTED' | 'DEEP_CHECK_FINISHED'
  | 'COMMIT_STARTED' | 'COMMIT_FINISHED'
  | 'ROLLBACK_STARTED' | 'ROLLBACK_FINISHED'
  | 'TOOL_BLOCKED' | 'STEP_FAILED'
  | 'TASK_FINISHED' | 'TASK_CANCELLED';

export type ApprovalStatus = 'PENDING' | 'GRANTED' | 'DENIED' | 'EXPIRED';
export type RiskLevel = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL' | 'FORBIDDEN';
export type PolicyDecision = 'FAST_EXECUTE' | 'SANDBOX_CHECK' | 'REQUEST_APPROVAL' | 'BLOCK';

export interface AuditEvent {
  event_id: string;
  task_id: string;
  step_id?: string;
  request_id?: string;
  sequence_number: number;
  event_type: AuditEventType;
  timestamp: string;
  actor: string;
  status: string;
  risk_level?: RiskLevel;
  decision?: PolicyDecision;
  summary: string;
  details: Record<string, unknown>;
}

export interface ApprovalRequest {
  approval_id: string;
  task_id: string;
  step_id: string;
  request_id: string;
  tool_name: string;
  request_fingerprint: string;
  reason: string;
  requested_at: string;
  expires_at: string;
  status: ApprovalStatus;
}

export interface ApprovalDecision {
  approval_id: string;
  task_id: string;
  step_id: string;
  request_id: string;
  status: ApprovalStatus;
  decided_by: string;
  decided_at: string;
  reason: string;
}

export interface TaskResponse {
  task_id: string;
  objective: string;
  status: string;
  created_at: string;
  steps: TaskStep[];
}

export interface TaskStep {
  task_id: string;
  step_id: string;
  description: string;
  status: string;
}

export interface APIResponse<T> {
  data: T | null;
  error: { code: string; message: string; details?: Record<string, unknown> } | null;
}
