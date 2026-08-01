import { get } from './client';

export interface TaskGraphSnapshot {
  graph_id: string;
  task_id: string;
  status: string;
  started_at?: string | null;
  finished_at?: string | null;
  nodes: Array<{
    node_id: string;
    dependencies: string[];
    status: string;
    blocked_reason?: string | null;
    started_at?: string | null;
    finished_at?: string | null;
  }>;
}

export interface EffectView {
  effect_id: string;
  kind: string;
  target_ref: string;
  status: string;
  checkpoint_id?: string | null;
  artifact_refs?: string[];
  created_at?: string | null;
}

export interface ApprovalView {
  approval_id: string;
  task_id: string;
  status: string;
  tool_name: string;
  reason?: string;
  step_id?: string;
  request_id?: string;
}

export function getTaskGraph(taskId: string): Promise<TaskGraphSnapshot> {
  return get(`/tasks/${encodeURIComponent(taskId)}/graph`);
}

export function getTaskEffects(taskId: string): Promise<EffectView[]> {
  return get(`/tasks/${encodeURIComponent(taskId)}/effects`);
}

export function getTaskApprovals(taskId: string): Promise<ApprovalView[]> {
  return get(`/tasks/${encodeURIComponent(taskId)}/approvals`);
}
