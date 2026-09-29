import { get, post } from './client'
import { apiUrl } from './url'
import type { AuditEvent, TaskContract, TaskResponse } from '../types/contracts'

export interface TaskSummary {
  task_id: string
  objective: string
  status: string
  created_at: string
  updated_at: string
  final_answer: string | null
  contract?: TaskContract | null
}

export interface RuntimeTaskStep {
  task_id: string
  step_id: string
  request_id: string
  description: string
  tool_name: string
  arguments: Record<string, unknown>
  status: string
  error?: string | null
  error_code?: string | null
}

export function createTask(
  objective: string,
  contract?: TaskContract,
): Promise<TaskResponse> {
  return post<TaskResponse>('/tasks', { objective, contract })
}

export function getTask(taskId: string): Promise<TaskSummary> {
  return get(`/tasks/${taskId}`)
}

export function listTasks(limit = 50): Promise<TaskSummary[]> {
  return get(`/tasks?limit=${limit}`)
}

export function getTaskSteps(taskId: string): Promise<{
  task_id: string
  steps: RuntimeTaskStep[]
}> {
  return get(`/tasks/${taskId}/steps`)
}

export async function getTaskEvents(
  taskId: string,
  limit?: number,
  offset = 0,
): Promise<{ task_id: string; events: unknown[]; count: number }> {
  const pageSize = limit ?? 100
  const events: unknown[] = []
  let cursor = offset
  while (true) {
    const page = await get<{ events: unknown[] }>(
      `/tasks/${encodeURIComponent(taskId)}/events?limit=${pageSize}&offset=${cursor}`,
    )
    events.push(...page.events)
    if (limit !== undefined || page.events.length < pageSize) break
    cursor += page.events.length
  }
  return { task_id: taskId, events, count: events.length }
}

export function cancelTask(
  taskId: string,
): Promise<{ task_id: string; status: string }> {
  return post(`/tasks/${taskId}/cancel`)
}

export function getTaskReport(taskId: string): Promise<{
  task_id: string
  total_events: number
  event_summary: Record<string, number>
}> {
  return get(`/tasks/${taskId}/report`)
}

export function createTaskStreamUrl(taskId: string, afterSequence = 0): string {
  return apiUrl(
    `/api/tasks/${encodeURIComponent(taskId)}/stream?after_sequence=${afterSequence}`,
  )
}

export function subscribeToTaskEvents(
  taskId: string,
  onEvent: (event: AuditEvent) => void,
  onError: () => void,
  onAssistantResponse?: (finalAnswer: string) => void,
): (() => void) | null {
  if (typeof EventSource === 'undefined') return null
  let source: EventSource | null = null
  let stopped = false
  let sequence = 0
  let reconnectTimer: number | undefined
  const connect = () => {
    source = new EventSource(createTaskStreamUrl(taskId, sequence))
    source.addEventListener('audit', (message) => {
      try {
        const event = JSON.parse(
          (message as MessageEvent<string>).data,
        ) as AuditEvent
        sequence = Math.max(sequence, event.sequence_number)
        onEvent(event)
      } catch {
        // Malformed events are not audit facts and must not enter the timeline.
      }
    })
    source.addEventListener('assistant', (message) => {
      try {
        const payload = JSON.parse((message as MessageEvent<string>).data) as {
          final_answer?: unknown
        }
        if (typeof payload.final_answer === 'string')
          onAssistantResponse?.(payload.final_answer)
      } catch {
        // Malformed assistant responses are not user-visible output.
      }
    })
    source.onerror = () => {
      source?.close()
      onError()
      if (!stopped) reconnectTimer = window.setTimeout(connect, 500)
    }
  }
  connect()
  return () => {
    stopped = true
    source?.close()
    if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer)
  }
}
