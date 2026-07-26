import type { AuditEvent } from '../types/contracts'

export type EventTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger'

const stageByEvent: Partial<Record<AuditEvent['event_type'], string>> = {
  TASK_CREATED: 'Task',
  PLAN_CREATED: 'Planner',
  TOOL_REQUESTED: 'Tool call',
  RISK_CLASSIFIED: 'Risk classification',
  PERMISSION_CHECKED: 'Permission',
  APPROVAL_REQUESTED: 'Approval',
  APPROVAL_GRANTED: 'Approval',
  APPROVAL_DENIED: 'Approval',
  DEEP_CHECK_STARTED: 'Deep check',
  DEEP_CHECK_FINISHED: 'Deep check',
  CHECKPOINT_CREATED: 'Checkpoint',
  PRE_CHECK_STARTED: 'Pre-check',
  PRE_CHECK_FINISHED: 'Pre-check',
  EXECUTION_STARTED: 'Execution',
  EXECUTION_FINISHED: 'Execution',
  EXECUTION_INTERRUPTED: 'Execution interrupted',
  POST_CHECK_STARTED: 'Post-check',
  POST_CHECK_FINISHED: 'Post-check',
  COMMIT_STARTED: 'Commit',
  COMMIT_FINISHED: 'Commit',
  ROLLBACK_STARTED: 'Rollback',
  ROLLBACK_FINISHED: 'Rollback',
  TOOL_BLOCKED: 'Safety block',
  STEP_FAILED: 'Execution failed',
  TASK_FINISHED: 'Task complete',
  TASK_CANCELLED: 'Task cancelled',
}

export function mergeAuditEvents(existing: AuditEvent[], incoming: AuditEvent[]): AuditEvent[] {
  const bySequence = new Map(existing.map((event) => [event.sequence_number, event]))
  for (const event of incoming) {
    if (!bySequence.has(event.sequence_number)) bySequence.set(event.sequence_number, event)
  }
  return [...bySequence.values()].sort((left, right) => left.sequence_number - right.sequence_number)
}

export function describeAuditEvent(event: AuditEvent): { stage: string; detail: string; tone: EventTone } {
  const toolName = typeof event.details.tool_name === 'string' ? event.details.tool_name : undefined
  const isDryRunEmail = toolName === 'send_email_dry_run'
  const terminalFailure = ['BLOCKED', 'FAILED', 'ROLLED_BACK', 'DENIED', 'INTERRUPTED'].includes(event.status)
  const tone: EventTone = terminalFailure
    ? 'danger'
    : event.status === 'WAITING_APPROVAL' || event.event_type === 'APPROVAL_REQUESTED'
      ? 'warning'
      : ['COMMITTED', 'GRANTED', 'FINISHED'].includes(event.status)
        ? 'success'
        : event.event_type === 'TOOL_REQUESTED' || event.event_type === 'RISK_CLASSIFIED'
          ? 'info'
          : 'neutral'

  return {
    stage: stageByEvent[event.event_type] ?? event.event_type,
    detail: isDryRunEmail ? `${event.summary} — Not actually sent.` : event.summary,
    tone,
  }
}

export function taskStatus(events: AuditEvent[], fallback = 'CREATED'): string {
  const latest = events.at(-1)
  if (!latest) return fallback
  if (latest.event_type === 'TASK_CANCELLED') return 'CANCELLED'
  if (latest.event_type === 'EXECUTION_INTERRUPTED') return 'INTERRUPTED'
  if (latest.event_type === 'ROLLBACK_FINISHED') return 'ROLLED_BACK'
  if (latest.event_type === 'TOOL_BLOCKED') return 'BLOCKED'
  if (latest.event_type === 'STEP_FAILED') return 'FAILED'
  if (latest.event_type === 'COMMIT_FINISHED') return 'COMMITTED'
  if (latest.event_type === 'APPROVAL_REQUESTED') return 'WAITING_APPROVAL'
  if (latest.event_type === 'APPROVAL_DENIED') return 'BLOCKED'
  return latest.status || fallback
}
