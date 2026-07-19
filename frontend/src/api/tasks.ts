import { get, post } from './client';
import type { TaskResponse } from '../types/contracts';

export function createTask(objective: string): Promise<TaskResponse> {
  return post<TaskResponse>('/tasks', { objective });
}

export function getTask(taskId: string): Promise<{ task_id: string; status: string }> {
  return get(`/tasks/${taskId}`);
}

export function getTaskEvents(
  taskId: string,
  limit = 100,
  offset = 0,
): Promise<{ task_id: string; events: unknown[]; count: number }> {
  return get(`/tasks/${taskId}/events?limit=${limit}&offset=${offset}`);
}

export function cancelTask(taskId: string): Promise<{ task_id: string; status: string }> {
  return post(`/tasks/${taskId}/cancel`);
}

export function getTaskReport(taskId: string): Promise<{
  task_id: string;
  total_events: number;
  event_summary: Record<string, number>;
}> {
  return get(`/tasks/${taskId}/report`);
}

export function createTaskStreamUrl(taskId: string): string {
  return `/api/tasks/${taskId}/stream`;
}
