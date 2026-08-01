import type { ExperimentMode } from './contracts';

export interface ExperimentCase {
  case_id: string;
  description: string;
  objective: string;
  tool_name: string;
  arguments: Record<string, string>;
  expected_decision: string;
}

export interface ExperimentResult {
  case_id: string;
  mode: ExperimentMode;
  task_id: string;
  request_id: string;
  status: string;
  expected_status: string;
  started_at: string;
  finished_at: string;
  elapsed_ms: number;
  rollback_elapsed_ms?: number;
  tool_executed_count?: number;
  unsafe_tool_executed_count?: number;
  blocked_count?: number;
  false_block_count?: number;
  risk_escalation_count?: number;
  check_count?: number;
  audit_event_count?: number;
  approval_requested_count?: number;
  approval_decision_count?: number;
  manual_action_count?: number;
  checkpoint_count?: number;
  pending_effect_count?: number;
  commit_count?: number;
  rollback_count?: number;
  selective_rollback_count?: number;
  residual_effect_count?: number;
  metrics?: Record<string, number>;
  result_output?: string;
  error?: string;
  error_code?: string;
}

export interface ModeStats {
  total: number;
  errors: number;
  blocked: number;
  false_blocks: number;
  success_rate: string;
  block_rate: string;
  avg_elapsed_ms: number;
}

export interface ModeComparison {
  modes: Record<string, ModeStats>;
  cross_mode: Record<string, unknown>;
}
