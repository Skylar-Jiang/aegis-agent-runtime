import type { ExperimentMode } from './contracts';

export interface ExperimentCase {
  case_id: string;
  description: string;
  objective: string;
  tool_name: string;
  arguments: Record<string, string>;
  expected_decision: string;
}

export interface ExperimentMetrics {
  sum_node_elapsed_ms: number;
  max_observed_concurrency: number;
  nodes_completed_during_approval: number;
  hidden_approval_wait_ms: number;
  affected_node_count: number;
  rolled_back_effect_count: number;
  preserved_node_count: number;
  preserved_effect_count: number;
}

export interface ExperimentResult {
  schema_version: string;
  run_id: string;
  case_id: string;
  repetition: number;
  mode: ExperimentMode;
  graph_id: string;
  task_id: string;
  started_at: string;
  finished_at: string;
  git_commit: string;
  python_version: string;
  node_version: string;
  os: string;
  environment_fingerprint: string;
  runner_command: string;
  fixture_id: string;
  objective_class: string;
  node_count: number;
  dependency_edge_count: number;
  max_parallelism: number;
  tool_sequence: string[];
  elapsed_ms: number;
  graph_elapsed_ms: number;
  critical_path_ms: number;
  parallel_saved_ms: number;
  approval_wait_ms: number;
  rollback_elapsed_ms: number;
  status: string;
  expected_status: string;
  safety_outcome: string;
  tool_executed_count: number;
  unsafe_tool_executed_count: number;
  blocked_count: number;
  risk_escalation_count: number;
  check_count: number;
  audit_event_count: number;
  approval_requested_count: number;
  approval_decision_count: number;
  manual_action_count: number;
  checkpoint_count: number;
  pending_effect_count: number;
  commit_count: number;
  rollback_count: number;
  selective_rollback_count: number;
  residual_effect_count: number;
  audit_digest?: string;
  raw_result_path?: string;
  error_code?: string;
  notes?: string;
  metrics?: ExperimentMetrics;
  error?: string;
}

export interface ModeStats {
  total: number;
  errors: number;
  blocked: number;
  success_rate: string;
  block_rate: string;
  avg_elapsed_ms: number;
}

export interface ModeComparison {
  modes: Record<string, ModeStats>;
  cross_mode: Record<string, unknown>;
}
