import type { AuditEvent } from '../types/contracts'
import type { Language } from '../i18n'

export type EventTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger'

const stageByEvent: Partial<
  Record<AuditEvent['event_type'], [string, string]>
> = {
  TASK_CREATED: ['Task', '任务'],
  PLAN_CREATED: ['Planner', '规划器'],
  TOOL_REQUESTED: ['Tool call', '工具调用'],
  RISK_CLASSIFIED: ['Risk classification', '风险分类'],
  PERMISSION_CHECKED: ['Permission', '权限检查'],
  APPROVAL_REQUESTED: ['Approval', '审批'],
  APPROVAL_GRANTED: ['Approval', '审批'],
  APPROVAL_DENIED: ['Approval', '审批'],
  DEEP_CHECK_STARTED: ['Deep check', '深度检查'],
  DEEP_CHECK_FINISHED: ['Deep check', '深度检查'],
  CHECKPOINT_CREATED: ['Checkpoint', '检查点'],
  PRE_CHECK_STARTED: ['Pre-check', '执行前检查'],
  PRE_CHECK_FINISHED: ['Pre-check', '执行前检查'],
  EXECUTION_STARTED: ['Execution', '执行'],
  EXECUTION_FINISHED: ['Execution', '执行'],
  EXECUTION_INTERRUPTED: ['Execution interrupted', '执行已中断'],
  POST_CHECK_STARTED: ['Post-check', '执行后检查'],
  POST_CHECK_FINISHED: ['Post-check', '执行后检查'],
  COMMIT_STARTED: ['Commit', '提交'],
  COMMIT_FINISHED: ['Commit', '提交'],
  ROLLBACK_STARTED: ['Rollback', '回滚'],
  ROLLBACK_FINISHED: ['Rollback', '回滚'],
  TOOL_BLOCKED: ['Safety block', '安全阻断'],
  PLANNER_FAILED: ['Planner failed', '规划失败'],
  STEP_FAILED: ['Execution failed', '执行失败'],
  TASK_FINISHED: ['Task complete', '任务完成'],
  TASK_CANCELLED: ['Task cancelled', '任务已取消'],
}

const chineseDetailByEvent: Partial<Record<AuditEvent['event_type'], string>> =
  {
    TASK_CREATED: '任务已创建并进入安全运行时。',
    PLAN_CREATED: '规划器已生成下一步操作提案。',
    TOOL_REQUESTED: '模型提出了一个工具调用请求，尚未代表执行授权。',
    RISK_CLASSIFIED: '运行时已完成风险分类。',
    PERMISSION_CHECKED: '运行时已核验工具权限。',
    APPROVAL_REQUESTED: '该操作需要人工审批后才能继续。',
    APPROVAL_GRANTED: '人工审批已通过。',
    APPROVAL_DENIED: '人工审批已拒绝。',
    DEEP_CHECK_STARTED: '正在执行深度安全检查。',
    DEEP_CHECK_FINISHED: '深度安全检查已完成。',
    CHECKPOINT_CREATED: '已创建可恢复检查点。',
    PRE_CHECK_STARTED: '正在执行操作前安全检查。',
    PRE_CHECK_FINISHED: '操作前安全检查已完成。',
    EXECUTION_STARTED: '受控工具执行已经开始。',
    EXECUTION_FINISHED: '受控工具执行已经结束。',
    EXECUTION_INTERRUPTED: '受控工具执行已被中断。',
    POST_CHECK_STARTED: '正在检查执行结果。',
    POST_CHECK_FINISHED: '执行结果检查已完成。',
    COMMIT_STARTED: '正在提交已验证的结果。',
    COMMIT_FINISHED: '结果已安全提交。',
    ROLLBACK_STARTED: '正在回滚受影响的结果。',
    ROLLBACK_FINISHED: '受影响的结果已完成回滚。',
    TOOL_BLOCKED: '工具调用已在执行前被安全策略阻断。',
    PLANNER_FAILED: '模型规划失败，未执行新的工具操作。',
    STEP_FAILED: '当前执行步骤失败。',
    TASK_FINISHED: '任务已经完成。',
    TASK_CANCELLED: '任务已取消，运行时正在收敛状态。',
  }

export function mergeAuditEvents(
  existing: AuditEvent[],
  incoming: AuditEvent[],
): AuditEvent[] {
  const bySequence = new Map(
    existing.map((event) => [event.sequence_number, event]),
  )
  for (const event of incoming) {
    if (!bySequence.has(event.sequence_number))
      bySequence.set(event.sequence_number, event)
  }
  return [...bySequence.values()].sort(
    (left, right) => left.sequence_number - right.sequence_number,
  )
}

export function describeAuditEvent(
  event: AuditEvent,
  language: Language = 'en',
): { stage: string; detail: string; tone: EventTone } {
  const toolName =
    typeof event.details.tool_name === 'string'
      ? event.details.tool_name
      : undefined
  const isDryRunEmail = toolName === 'send_email_dry_run'
  const terminalFailure = [
    'BLOCKED',
    'FAILED',
    'ROLLED_BACK',
    'DENIED',
    'INTERRUPTED',
  ].includes(event.status)
  const tone: EventTone = terminalFailure
    ? 'danger'
    : event.status === 'WAITING_APPROVAL' ||
        event.event_type === 'APPROVAL_REQUESTED'
      ? 'warning'
      : ['COMMITTED', 'GRANTED', 'FINISHED'].includes(event.status)
        ? 'success'
        : event.event_type === 'TOOL_REQUESTED' ||
            event.event_type === 'RISK_CLASSIFIED'
          ? 'info'
          : 'neutral'

  const stage = stageByEvent[event.event_type]
  return {
    stage: stage ? stage[language === 'zh-CN' ? 1 : 0] : event.event_type,
    detail:
      language === 'zh-CN'
        ? isDryRunEmail
          ? '邮件发送预演已完成，没有实际发送邮件。'
          : (chineseDetailByEvent[event.event_type] ?? event.summary)
        : isDryRunEmail
          ? `${event.summary} — Not actually sent.`
          : event.summary,
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
  if (latest.event_type === 'PLANNER_FAILED') return 'FAILED'
  if (latest.event_type === 'STEP_FAILED') return 'FAILED'
  if (latest.event_type === 'COMMIT_FINISHED') return 'COMMITTED'
  if (latest.event_type === 'APPROVAL_REQUESTED') return 'WAITING_APPROVAL'
  if (latest.event_type === 'APPROVAL_GRANTED') return 'RUNNING'
  if (latest.event_type === 'APPROVAL_DENIED') return 'BLOCKED'
  return latest.status || fallback
}
