import type { BehaviorEvent } from './generated'
import { useI18n } from '../../i18n'

const reasons: Record<string, string> = {
  report_completion_mismatch: '报告不满足已确认的完成条件',
  repeated_read_without_new_input: '持续重复读取，未引入新输入',
  upstream_read_evidence: '已关联此前实际读取的资料',
  intent_criteria_invalid: '任务完成条件不完整',
  intent_check_unavailable: '检测不可用，已停止执行',
  task_safely_stopped: '任务已安全终止',
}

export function IntentPanel({
  events,
  onSelect,
}: {
  events: BehaviorEvent[]
  onSelect: (id: string) => void
}) {
  const { text } = useI18n()
  const points = events.filter(
    (event) => event.type === 'INTENT_EVALUATED' && event.intent,
  )
  const latest = points.at(-1)?.intent
  const stopIndex = events.reduce(
    (last, event, index) =>
      event.type === 'TASK_SAFE_STOPPED' ||
      event.type === 'CORRECTION_SAFE_STOPPED'
        ? index
        : last,
    -1,
  )
  const recoveredIndex = events.reduce(
    (last, event, index) =>
      event.type === 'CORRECTION_COMPLETED' ? index : last,
    -1,
  )
  const stopped = stopIndex >= 0 && stopIndex > recoveredIndex
  return (
    <section
      className="intent-panel"
      aria-label={text('Intent timeline', '意图风险时间线')}
    >
      <h3>{text('Intent timeline', '意图风险时间线')}</h3>
      <p aria-live="polite">
        {stopped
          ? text(
              'Task safely stopped; further execution is refused.',
              '任务已安全终止，后续执行由服务器拒绝。',
            )
          : recoveredIndex > stopIndex
            ? text('Bounded report recovery completed.', '有界报告恢复已完成。')
            : latest
              ? text(
                  'Checking approved report criteria.',
                  '正在按已确认的报告条件检查。',
                )
              : text(
                  'No intent checks recorded for this task.',
                  '此任务尚无意图检测记录。',
                )}
      </p>
      {points.length > 0 && (
        <>
          <svg
            role="img"
            aria-label={text('Risk by proposed step', '按动作步骤显示风险')}
            viewBox="0 0 360 110"
            style={{ width: '100%', maxHeight: 160 }}
          >
            <line x1="20" x2="340" y1="90" y2="90" stroke="currentColor" />
            <polyline
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              points={points
                .map(
                  (event, index) =>
                    `${20 + (index * 320) / Math.max(1, points.length - 1)},${90 - (event.intent?.risk_score ?? 0) * 70}`,
                )
                .join(' ')}
            />
            {points.map((event, index) => (
              <circle
                key={event.event_id}
                cx={20 + (index * 320) / Math.max(1, points.length - 1)}
                cy={90 - (event.intent?.risk_score ?? 0) * 70}
                r="5"
                fill={
                  event.intent?.disposition === 'SAFE_STOP'
                    ? '#dc2626'
                    : '#16856c'
                }
              >
                <title>{`step ${event.intent?.step_index}: ${event.intent?.risk_score}`}</title>
              </circle>
            ))}
          </svg>
          <ol>
            {points.map((event) => (
              <li key={event.event_id}>
                <button type="button" onClick={() => onSelect(event.event_id)}>
                  {text('Step', '步骤')} {event.intent?.step_index} ·{' '}
                  {event.intent?.risk_score.toFixed(2)} ·{' '}
                  {event.intent?.disposition}
                </button>
                <p>
                  {event.intent?.trigger_dimensions
                    ?.map((item) => reasons[item] ?? item)
                    .join('；') || text('Continue', '继续')}
                </p>
                <small>
                  v{event.intent?.contract_version} ·{' '}
                  {event.intent?.policy_version} ·{' '}
                  {event.intent?.detector_version}
                </small>
                {Boolean(event.intent?.evidence_refs?.length) && (
                  <p>
                    {text('Upstream references', '上游引用')}：
                    {event.intent?.evidence_refs?.join(' · ')}
                  </p>
                )}
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  )
}
