import { get, post } from './client';
import type { AuditEvent, TaskContract, TaskResponse } from '../types/contracts';

export function createTask(objective: string, contract?: TaskContract): Promise<TaskResponse> {
  return post<TaskResponse>('/tasks', { objective, contract });
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

export function createTaskStreamUrl(taskId: string, afterSequence = 0): string {
  return `/api/tasks/${taskId}/stream?after_sequence=${afterSequence}`;
}

export function subscribeToTaskEvents(
  taskId: string,
  onEvent: (event: AuditEvent) => void,
  onError: () => void,
): (() => void) | null {
  if (typeof EventSource === 'undefined') return null;
  let source: EventSource | null = null;
  let stopped = false;
  let sequence = 0;
  let reconnectTimer: number | undefined;
  const connect = () => {
    source = new EventSource(createTaskStreamUrl(taskId, sequence));
    source.addEventListener('audit', (message) => {
      try {
        const event = JSON.parse((message as MessageEvent<string>).data) as AuditEvent;
        sequence = Math.max(sequence, event.sequence_number);
        onEvent(event);
      } catch {
        // Malformed events are not audit facts and must not enter the timeline.
      }
    });
    source.onerror = () => {
      source?.close();
      onError();
      if (!stopped) reconnectTimer = window.setTimeout(connect, 500);
    };
  };
  connect();
  return () => {
    stopped = true;
    source?.close();
    if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
  };
}
