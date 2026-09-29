// Mirror of backend contracts/ — v0.3

export type AuditEventType =
  | 'TASK_CREATED'
  | 'PLAN_CREATED'
  | 'TOOL_REQUESTED'
  | 'RISK_CLASSIFIED'
  | 'PERMISSION_CHECKED'
  | 'APPROVAL_REQUESTED'
  | 'APPROVAL_GRANTED'
  | 'APPROVAL_DENIED'
  | 'CHECKPOINT_CREATED'
  | 'EXECUTION_STARTED'
  | 'EXECUTION_FINISHED'
  | 'EXECUTION_INTERRUPTED'
  | 'PRE_CHECK_STARTED'
  | 'PRE_CHECK_FINISHED'
  | 'POST_CHECK_STARTED'
  | 'POST_CHECK_FINISHED'
  | 'DEEP_CHECK_STARTED'
  | 'DEEP_CHECK_FINISHED'
  | 'COMMIT_STARTED'
  | 'COMMIT_FINISHED'
  | 'ROLLBACK_STARTED'
  | 'ROLLBACK_FINISHED'
  | 'TOOL_BLOCKED'
  | 'PLANNER_FAILED'
  | 'STEP_FAILED'
  | 'TASK_FINISHED'
  | 'TASK_CANCELLED'

export type ApprovalStatus = 'PENDING' | 'GRANTED' | 'DENIED' | 'EXPIRED'
export type RiskLevel = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL' | 'FORBIDDEN'
export type PolicyDecision =
  'FAST_EXECUTE' | 'SANDBOX_CHECK' | 'REQUEST_APPROVAL' | 'BLOCK'
export type ExperimentMode = 'BASELINE' | 'FULL_GUARD' | 'ADAPTIVE_RUNTIME'

export interface AuditEvent {
  event_id: string
  task_id: string
  step_id?: string
  request_id?: string
  sequence_number: number
  event_type: AuditEventType
  timestamp: string
  actor: string
  status: string
  risk_level?: RiskLevel
  decision?: PolicyDecision
  summary: string
  details: Record<string, unknown>
}

export interface ApprovalRequest {
  approval_id: string
  task_id: string
  step_id: string
  request_id: string
  tool_name: string
  request_fingerprint: string
  reason: string
  requested_at: string
  expires_at: string
  status: ApprovalStatus
}

export interface ApprovalDecision {
  approval_id: string
  task_id: string
  step_id: string
  request_id: string
  status: ApprovalStatus
  decided_by: string
  decided_at: string
  reason: string
}

export interface TaskResponse {
  task_id: string
  objective: string
  status: string
  created_at: string
  steps: TaskStep[]
}

export interface TaskContract {
  allowed_actions: string[]
  allowed_resources: string[]
  forbidden_actions: string[]
  max_affected_objects: number
  allow_egress: boolean
  requires_reconfirmation: boolean
  approval_required_actions?: string[]
  bulk_approval_threshold?: number
  security_profile_id?: string | null
  security_profile_version?: number | null
}

export interface ApprovalPolicy {
  required_actions: string[]
  bulk_action_threshold: number
}

export interface SecurityProfileUpdate {
  allowed_actions: string[]
  resource_scopes: string[]
  allow_egress: boolean
  max_affected_objects: number
  approval_policy: ApprovalPolicy
}

export interface SecurityProfile extends SecurityProfileUpdate {
  profile_id: string
  version: number
  created_at: string
  denied_actions: string[]
}

export interface ToolDefinition {
  name: string
  description: string
  input_schema: Record<string, unknown>
  base_risk?: RiskLevel
  side_effect_type?: string
  required_permissions?: string[]
}

export interface ConversationMessage {
  message_id: string
  conversation_id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  task_id?: string | null
  created_at: string
  sequence_number: number
}

export interface Conversation {
  conversation_id: string
  title: string
  security_profile_id: string
  context_summary: string
  created_at: string
  updated_at: string
  messages: ConversationMessage[]
}

export interface ConversationTurnResponse {
  conversation_id: string
  message: ConversationMessage
  task_id: string
  status: string
}

export interface TaskStep {
  task_id: string
  step_id: string
  description: string
  status: string
}

export interface APIResponse<T> {
  data: T | null
  error: {
    code: string
    message: string
    details?: Record<string, unknown>
  } | null
}
