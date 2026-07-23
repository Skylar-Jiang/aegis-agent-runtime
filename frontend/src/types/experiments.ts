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
  expected_decision: string;
  started_at: string;
  finished_at: string;
  elapsed_ms: number;
  result_output?: string;
  error?: string;
  error_code?: string;
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
