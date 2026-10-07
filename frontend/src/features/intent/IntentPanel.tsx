import { useState } from 'react'
import type { IntentRun } from '../../api/intentDemo'
import './intent.css'

function JsonDetails({ title, value }: { title: string; value: unknown }) {
  return (
    <details className="intent-json">
      <summary>{title}</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  )
}

export function IntentPanel({ run }: { run: IntentRun }) {
  const [selected, setSelected] = useState<string | null>(null)
  const action =
    run.actions.find((a) => a.request_id === selected) ?? run.actions.at(-1)
  const intent = run.intent_history.at(-1)
  const contract = run.contract_history.at(-1)
  return (
    <div
      className="intent-panel"
      data-testid="intent-panel"
      data-run-id={run.run_id}
    >
      <section className="intent-section">
        <div className="intent-section-title">
          <h2>任务目标与可信版本</h2>
          <span data-testid="run-status" className="intent-state">
            {run.status}
          </span>
        </div>
        <p>{intent?.goal ?? run.original_task}</p>
        <dl className="intent-metadata">
          <div>
            <dt>IntentSpec</dt>
            <dd data-testid="intent-version">v{intent?.version}</dd>
          </div>
          <div>
            <dt>TaskContract</dt>
            <dd data-testid="contract-version">
              v{contract?.contract_version}
            </dd>
          </div>
          <div>
            <dt>可信确认</dt>
            <dd>{intent?.confirmed_by ?? '未确认'}</dd>
          </div>
          <div>
            <dt>运行编号</dt>
            <dd>{run.run_id}</dd>
          </div>
        </dl>
        <p className="intent-muted">
          {run.mode} · {run.schema_version}
        </p>
        <JsonDetails
          title="原始任务与攻击输入"
          value={{
            original_task: run.original_task,
            attack_input: run.attack_input,
          }}
        />
        <JsonDetails
          title="IntentSpec / TaskContract 完整版本历史"
          value={{
            intent: run.intent_history,
            contract: run.contract_history,
            core_contract: run.core_contract_history,
          }}
        />
      </section>

      {action && (
        <section className="intent-section">
          <div className="intent-section-title">
            <h2>当前动作与检测证据</h2>
            <span
              data-testid="current-decision"
              className="intent-decision"
              data-decision={action.decision_result.decision}
            >
              {action.decision_result.decision}
            </span>
          </div>
          <dl className="intent-metadata">
            <div>
              <dt>动作</dt>
              <dd>{action.behavior_event.tool}</dd>
            </div>
            <div>
              <dt>目标</dt>
              <dd>{action.behavior_event.target}</dd>
            </div>
            <div>
              <dt>来源</dt>
              <dd>
                {action.behavior_event.source_type} ·{' '}
                {action.behavior_event.source_ref}
              </dd>
            </div>
            <div>
              <dt>执行状态</dt>
              <dd data-testid="action-execution-status">
                {action.execution_status}
              </dd>
            </div>
          </dl>
          <p>{action.decision_result.reason_code}</p>
          <p className="intent-muted">
            风险标签 {action.decision_result.risk_score} · 维度{' '}
            {action.decision_result.trigger_dimensions.join(' / ') || '无'} ·
            Core 权限 {action.gateway_decision.decision}
          </p>
          <ul className="intent-evidence">
            {action.decision_result.evidence_refs.map((ref) => (
              <li key={ref}>{ref}</li>
            ))}
          </ul>
          <p className="intent-muted">
            {action.decision_result.detector_version} ·{' '}
            {action.decision_result.policy_version}
          </p>
          <JsonDetails
            title="BehaviorEvent / DecisionResult / 执行结果"
            value={action}
          />
          <div className="intent-table-wrap">
            <table aria-label="动作与处置">
              <thead>
                <tr>
                  <th>步骤</th>
                  <th>动作 / 目标</th>
                  <th>来源</th>
                  <th>处置</th>
                  <th>执行</th>
                </tr>
              </thead>
              <tbody>
                {run.actions.map((a) => (
                  <tr key={a.request_id}>
                    <td>
                      <button
                        className="intent-step"
                        aria-pressed={a.request_id === action.request_id}
                        onClick={() => setSelected(a.request_id)}
                      >
                        查看步骤 {a.behavior_event.step_index}
                      </button>
                    </td>
                    <td>
                      {a.behavior_event.tool}
                      <small>{a.behavior_event.target}</small>
                    </td>
                    <td>{a.behavior_event.source_ref}</td>
                    <td>{a.decision_result.decision}</td>
                    <td>{a.execution_status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="intent-section">
        <div className="intent-section-title">
          <h2>CorrectionPlan 与纠偏状态</h2>
          <span>{run.correction_plan?.status ?? '无需纠偏'}</span>
        </div>
        {run.correction_plan ? (
          <>
            <p>
              基于契约 v{run.correction_plan.base_contract_version} · 剩余重试{' '}
              {run.correction_plan.retry_budget} · 扩大范围{' '}
              {run.correction_plan.added_scope.length} 项
            </p>
            <JsonDetails
              title="CorrectionPlan 完整内容"
              value={run.correction_plan}
            />
          </>
        ) : (
          <p className="intent-muted">当前后端没有返回纠偏计划。</p>
        )}
      </section>

      <section className="intent-section">
        <div className="intent-section-title">
          <h2>实际文件、Memory 与模拟端点 effect</h2>
          <span data-testid="acceptance-result">
            {run.acceptance.passed ? '一致性 PASS' : '一致性 FAIL'}
          </span>
        </div>
        <div className="intent-counts">
          <span>
            配置端点调用{' '}
            <strong data-testid="config-call-count">
              {run.observed.config_call_count}
            </strong>
          </span>
          <span>
            报告外发端点调用{' '}
            <strong data-testid="send-call-count">
              {run.observed.send_call_count}
            </strong>
          </span>
        </div>
        <p className="intent-muted">{run.workspace}</p>
        <div className="intent-table-wrap">
          <table aria-label="文件摘要">
            <thead>
              <tr>
                <th>文件</th>
                <th>运行前 SHA-256</th>
                <th>当前 SHA-256</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(run.observed.files).map(([path, hash]) => (
                <tr key={path}>
                  <td>{path}</td>
                  <td>{run.baseline.files[path] ?? '不存在'}</td>
                  <td data-file={path}>{hash ?? '不存在'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <JsonDetails
          title="实际 Memory 与模拟端点调用记录"
          value={{
            memory: run.observed.memory,
            endpoint_calls: run.observed.endpoint_calls,
          }}
        />
        <div className="intent-table-wrap">
          <table aria-label="EffectCheck">
            <thead>
              <tr>
                <th>目标 / 工具</th>
                <th>状态</th>
                <th>前后快照 digest</th>
              </tr>
            </thead>
            <tbody>
              {run.effect_checks.map((check) => (
                <tr key={check.effect_id}>
                  <td>
                    {check.normalized_target}
                    <small>{check.tool}</small>
                  </td>
                  <td>{check.status}</td>
                  <td>
                    {check.before_digest}
                    <small>{check.after_digest}</small>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <JsonDetails
          title="验收检查与 expected state"
          value={{
            acceptance: run.acceptance,
            expected_state: run.expected_state,
          }}
        />
      </section>

      <section className="intent-section">
        <div className="intent-section-title">
          <h2>完整 timeline</h2>
          <span data-testid="timeline-count">
            {run.timeline.length} 个后端事件
          </span>
        </div>
        <p className="intent-muted">
          原始 Core 事件顺序；mock 检测处置使用 INTENT_DECIDED 事件，未伪装成
          Core 权限拒绝。
        </p>
        <div className="intent-table-wrap intent-timeline">
          <table aria-label="完整 timeline">
            <thead>
              <tr>
                <th>序号</th>
                <th>事件</th>
                <th>状态</th>
                <th>来源 / 请求</th>
                <th>UTC 时间</th>
              </tr>
            </thead>
            <tbody>
              {run.timeline.map((event) => (
                <tr key={event.event_id}>
                  <td>{event.sequence}</td>
                  <td>{event.type}</td>
                  <td>{event.state}</td>
                  <td>
                    {event.source_ref}
                    <small>{event.request_id}</small>
                  </td>
                  <td>{event.occurred_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <JsonDetails title="完整后端响应 JSON" value={run} />
      </section>
    </div>
  )
}
