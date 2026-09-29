import { useQueries, useQuery } from '@tanstack/react-query'

import {
  getCoreExperiments,
  getFinalEvidence,
  getResult,
  listResults,
} from '../api/experiments'
import { useI18n } from '../i18n'
import type { ExperimentResult } from '../types/experiments'

type FormalFamily = 'Safety' | 'Graph' | 'Rollback'

const families: Array<{
  label: FormalFamily
  matches: (name: string) => boolean
}> = [
  {
    label: 'Safety',
    matches: (name) => name.includes('v2-safety') && name.endsWith('.jsonl'),
  },
  {
    label: 'Graph',
    matches: (name) => name.includes('v2-graph') && name.endsWith('.jsonl'),
  },
  { label: 'Rollback', matches: (name) => name === 'rollback_benchmark.jsonl' },
]

function sum(rows: ExperimentResult[], field: keyof ExperimentResult) {
  return rows.reduce((total, row) => total + Number(row[field] ?? 0), 0)
}

function average(rows: ExperimentResult[], field: keyof ExperimentResult) {
  return rows.length ? Math.round(sum(rows, field) / rows.length) : 0
}

function metricSum(rows: ExperimentResult[], name: string) {
  return rows.reduce(
    (total, row) =>
      total +
      Number(
        row.metrics?.[name] ??
          row[`metrics_${name}` as keyof ExperimentResult] ??
          0,
      ),
    0,
  )
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded border border-slate-200 bg-white p-3">
      <p className="text-xs text-gray-500">{label}</p>
      <p className="mt-1 font-mono text-lg text-slate-800">{value}</p>
    </div>
  )
}

function FinalEvidence() {
  const { text } = useI18n()
  const query = useQuery({
    queryKey: ['final-evidence-summary'],
    queryFn: getFinalEvidence,
  })
  if (query.isLoading)
    return (
      <section className="rounded border border-slate-200 bg-white p-4 text-sm text-gray-500">
        {text('Loading frozen Final evidence…', '正在加载冻结的最终证据…')}
      </section>
    )
  if (query.isError || !query.data)
    return (
      <section className="rounded border border-red-900 bg-red-950/30 p-4 text-sm text-rose-700">
        {text(
          'Final evidence artifacts are unavailable.',
          '最终证据产物不可用。',
        )}
      </section>
    )
  const { runtime_boundary: boundary, scheduling, recovery } = query.data
  const reduction = scheduling.approval_only
    ? Math.round(
        (1 - scheduling.adaptive_runtime / scheduling.approval_only) * 100,
      )
    : 0
  return (
    <section className="rounded border border-cyan-900/70 bg-white p-4">
      <div className="mb-3">
        <h2 className="text-sm font-medium text-cyan-800">
          {text('Final evidence summary', '最终证据摘要')}
        </h2>
        <p className="mt-1 text-xs text-slate-500">
          {text(
            'Read-only computation from frozen Final raw and derived evidence. NO_PROPOSAL is excluded from conditional containment.',
            '根据冻结的最终原始数据和派生证据只读计算；条件阻断率不包含 NO_PROPOSAL。',
          )}
        </p>
      </div>
      <div className="grid gap-3 lg:grid-cols-3">
        <div className="rounded border border-slate-200 bg-white p-3">
          <p className="text-xs text-slate-500">
            {text('Runtime Boundary', '运行时边界')}
          </p>
          <p className="mt-1 font-mono text-sm text-slate-800">
            {boundary.contained_proposals}/{boundary.proposals}{' '}
            {text('conditional containment', '危险提案被条件阻断')}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            {boundary.unsafe_effects}/{boundary.unsafe_runs}{' '}
            {text(
              'unsafe effects in live dangerous runs',
              '次真实危险运行产生不安全副作用',
            )}
          </p>
        </div>
        <div className="rounded border border-slate-200 bg-white p-3">
          <p className="text-xs text-slate-500">{text('Scheduling', '调度')}</p>
          <p className="mt-1 font-mono text-sm text-slate-800">
            {scheduling.approval_only} → {scheduling.adaptive_runtime}{' '}
            {text('approvals', '次审批')}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            <span>
              {reduction}% {text('approval reduction', '审批量下降')}
            </span>{' '}
            ·{' '}
            <span>
              {scheduling.dependency_conflict_violations}{' '}
              {text('dependency/conflict violations', '次依赖或冲突违规')}
            </span>
          </p>
        </div>
        <div className="rounded border border-slate-200 bg-white p-3">
          <p className="text-xs text-slate-500">{text('Recovery', '恢复')}</p>
          <p className="mt-1 font-mono text-sm text-slate-800">
            {text('rollback scope', '回滚范围')} {recovery.approval_only_scope}{' '}
            → {recovery.aegis_scope}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            {text('preservation rate', '保留率')}{' '}
            {recovery.preservation_rate.toFixed(1)} ·{' '}
            {text('precision', '准确率')}{' '}
            {recovery.preservation_precision.toFixed(1)}
          </p>
        </div>
      </div>
    </section>
  )
}

function Dashboard({
  family,
  rows,
}: {
  family: FormalFamily
  rows: ExperimentResult[]
}) {
  const { text } = useI18n()
  const familyLabel =
    family === 'Safety'
      ? text('Safety', '安全')
      : family === 'Graph'
        ? text('Graph', '任务图')
        : text('Rollback', '回滚')
  const heading = text(
    `${family} formal dashboard`,
    `${familyLabel}正式实验面板`,
  )
  if (family === 'Safety') {
    const unsafeBlockedRate = rows.length
      ? `${((sum(rows, 'blocked_count') / rows.length) * 100).toFixed(1)}%`
      : '—'
    return (
      <section className="mt-0">
        <h2 className="mb-2 text-sm font-medium text-slate-600">{heading}</h2>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          <Metric
            label={text('Formal rows', '正式数据行')}
            value={rows.length}
          />
          <Metric
            label={text('Unsafe block rate', '危险请求阻断率')}
            value={unsafeBlockedRate}
          />
          <Metric
            label={text(
              'Unsafe requests reaching boundary',
              '到达执行边界的危险请求',
            )}
            value={sum(rows, 'unsafe_tool_executed_count')}
          />
          <Metric
            label={text('Approval requests', '审批请求')}
            value={sum(rows, 'approval_requested_count')}
          />
          <Metric
            label={text('Manual actions', '人工操作')}
            value={sum(rows, 'manual_action_count')}
          />
          <Metric
            label={text('False blocks', '误阻断')}
            value={sum(rows, 'false_block_count')}
          />
          <Metric
            label={text('Risk escalation', '风险升级')}
            value={sum(rows, 'risk_escalation_count')}
          />
        </div>
      </section>
    )
  }
  if (family === 'Graph') {
    return (
      <section className="mt-0">
        <h2 className="mb-2 text-sm font-medium text-slate-600">{heading}</h2>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          <Metric
            label={text('Formal rows', '正式数据行')}
            value={rows.length}
          />
          <Metric
            label={text('Mean graph elapsed (ms)', '任务图平均耗时（毫秒）')}
            value={average(rows, 'graph_elapsed_ms')}
          />
          <Metric
            label={text('Mean parallel saved (ms)', '并行平均节省（毫秒）')}
            value={average(rows, 'parallel_saved_ms')}
          />
          <Metric
            label={text('Max observed concurrency', '观测到的最大并发度')}
            value={Math.max(
              0,
              ...rows.map((row) =>
                Number(row.metrics?.max_observed_concurrency ?? 0),
              ),
            )}
          />
          <Metric
            label={text('Mean approval wait (ms)', '平均审批等待（毫秒）')}
            value={average(rows, 'approval_wait_ms')}
          />
          <Metric
            label={text(
              'Nodes completed during approval',
              '审批等待期间完成的节点',
            )}
            value={metricSum(rows, 'nodes_completed_during_approval')}
          />
        </div>
      </section>
    )
  }
  return (
    <section className="mt-0">
      <h2 className="mb-2 text-sm font-medium text-slate-600">{heading}</h2>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <Metric label={text('Formal rows', '正式数据行')} value={rows.length} />
        <Metric
          label={text('Rollback success rate', '回滚成功率')}
          value={
            rows.length
              ? `${((rows.filter((row) => row.residual_effect_count === 0).length / rows.length) * 100).toFixed(1)}%`
              : '—'
          }
        />
        <Metric
          label={text('Mean rollback elapsed (ms)', '平均回滚耗时（毫秒）')}
          value={average(rows, 'rollback_elapsed_ms')}
        />
        <Metric
          label={text('Rolled-back effects', '已回滚 Effect')}
          value={metricSum(rows, 'rolled_back_effect_count')}
        />
        <Metric
          label={text('Preserved effects', '已保留 Effect')}
          value={metricSum(rows, 'preserved_effect_count')}
        />
        <Metric
          label={text('Residual effects', '残留 Effect')}
          value={sum(rows, 'residual_effect_count')}
        />
        <Metric
          label={text('Selective rollback count', '选择性回滚次数')}
          value={sum(rows, 'selective_rollback_count')}
        />
      </div>
    </section>
  )
}

function CoreRecordedRuns() {
  const { text } = useI18n()
  const query = useQuery({
    queryKey: ['core-recorded-experiments'],
    queryFn: getCoreExperiments,
    retry: false,
  })
  return (
    <section className="mb-6 rounded border border-slate-200 bg-white p-4">
      <h2 className="text-sm font-medium text-slate-800">
        {text('Core cryptography · recorded runs', 'Core 密码学 · 历史记录')}
      </h2>
      <p className="mt-1 text-xs text-slate-500">
        {text(
          'Historical measurements from recorded evidence files. These values are not live telemetry or a new benchmark run.',
          '以下测量来自已记录的证据文件，并非实时指标或新执行的基准测试。',
        )}
      </p>
      {query.isLoading && (
        <p className="mt-3 text-sm text-slate-500">
          {text('Loading recorded runs…', '正在加载历史记录…')}
        </p>
      )}
      {query.isError && (
        <p role="alert" className="mt-3 text-sm text-rose-700">
          {text('Core evidence could not be loaded.', '无法加载 Core 证据。')}{' '}
          {(query.error as Error).message}
        </p>
      )}
      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        {query.data?.runs.map((run) => {
          const parameters =
            run.data.parameters &&
            typeof run.data.parameters === 'object' &&
            !Array.isArray(run.data.parameters)
              ? Object.entries(
                  run.data.parameters as Record<string, unknown>,
                ).filter(([key]) => key !== 'output')
              : []
          const measurements = Array.isArray(run.data.measurements)
            ? (run.data.measurements as Array<Record<string, unknown>>)
            : []
          const cases = Array.isArray(run.data.cases)
            ? (run.data.cases as Array<Record<string, unknown>>)
            : []
          const results = Array.isArray(run.data.results)
            ? (run.data.results as Array<Record<string, unknown>>)
            : []
          return (
            <article
              key={run.id}
              className="rounded border border-slate-200 p-3"
            >
              <h3 className="font-medium text-slate-800">{run.title}</h3>
              <p className="mt-1 text-xs text-slate-500">
                {text('Recorded at', '记录时间')}:{' '}
                <time dateTime={run.recorded_at}>{run.recorded_at}</time>
              </p>
              {parameters.length > 0 && (
                <p className="mt-2 text-xs text-slate-600">
                  {text('Configuration', '配置')}:{' '}
                  {parameters
                    .map(
                      ([key, value]) =>
                        `${key}: ${Array.isArray(value) ? value.join(', ') : String(value)}`,
                    )
                    .join(' · ')}
                </p>
              )}
              {measurements.length > 0 && (
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead>
                      <tr>
                        <th>{text('Metric', '指标')}</th>
                        <th>{text('History', '历史事件')}</th>
                        <th>p50</th>
                        <th>p95</th>
                      </tr>
                    </thead>
                    <tbody>
                      {measurements.map((measurement, index) => (
                        <tr
                          key={`${measurement.metric}-${index}`}
                          className="border-t border-slate-100"
                        >
                          <td>{String(measurement.metric)}</td>
                          <td>{String(measurement.history_events ?? '—')}</td>
                          <td>
                            {typeof measurement.p50_ms === 'number'
                              ? `${measurement.p50_ms} ms`
                              : '—'}
                          </td>
                          <td>
                            {typeof measurement.p95_ms === 'number'
                              ? `${measurement.p95_ms} ms`
                              : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {cases.length > 0 && (
                <details className="mt-2 text-xs text-slate-600">
                  <summary>
                    {text('Ablation case outcomes', '消融案例结果')}:{' '}
                    {cases.length}
                  </summary>
                  <div className="mt-2 overflow-x-auto">
                    <table className="w-full text-left text-xs">
                      <thead>
                        <tr>
                          <th>{text('Mode', '模式')}</th>
                          <th>{text('Scenario', '场景')}</th>
                          <th>{text('Outcome', '结果')}</th>
                          <th>
                            {text(
                              'Writes actual / expected',
                              '实际 / 预期写入',
                            )}
                          </th>
                          <th>{text('Tamper rejected', '篡改证据拒绝')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {cases.map((item, index) => (
                          <tr
                            key={`${item.mode}-${item.scenario}-${index}`}
                            className="border-t border-slate-100"
                          >
                            <td>{String(item.mode ?? '—')}</td>
                            <td>{String(item.scenario ?? '—')}</td>
                            <td>{String(item.final_status ?? '—')}</td>
                            <td>
                              {String(item.actual_writes ?? '—')} /{' '}
                              {String(item.expected_writes ?? '—')}
                            </td>
                            <td>
                              {item.tampered_evidence_rejected === true
                                ? text('Yes', '是')
                                : item.tampered_evidence_rejected === false
                                  ? text('No', '否')
                                  : '—'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </details>
              )}
              {results.length > 0 && (
                <p className="mt-2 text-xs text-slate-600">
                  {text('HTTP controls passed', 'HTTP 对照通过')}:{' '}
                  {results.filter((item) => item.passed === true).length}/
                  {results.length}
                </p>
              )}
              <p className="mt-3 break-all font-mono text-[10px] text-slate-500">
                {run.source}
                <br />
                SHA-256: {run.sha256}
              </p>
            </article>
          )
        })}
      </div>
      {query.data?.unavailable?.map((item) => (
        <p key={item.id} className="mt-2 text-xs text-amber-700">
          {item.id}: {item.error}
        </p>
      ))}
    </section>
  )
}

export function ExperimentsPage() {
  const { text } = useI18n()
  const filesQuery = useQuery({
    queryKey: ['experiment-results'],
    queryFn: listResults,
  })
  const formalFiles = families.map(
    ({ matches }) =>
      filesQuery.data?.find((file) => matches(file.name))?.name ?? null,
  )
  const resultQueries = useQueries({
    queries: formalFiles.map((filename, index) => ({
      queryKey: ['formal-experiment-result', index, filename],
      queryFn: () => getResult(filename!),
      enabled: filename !== null,
    })),
  })

  return (
    <div>
      <div className="mb-4">
        <h1 className="text-xl font-semibold">
          {text('Experiment dashboard', '实验仪表盘')}
        </h1>
        <p className="mt-1 text-sm text-gray-500">
          {text(
            'Read-only aggregation of committed formal raw JSONL; no experiment figure is embedded in the client.',
            '只读汇总已提交的正式 JSONL 原始数据，客户端不嵌入手工实验图。',
          )}
        </p>
      </div>
      <div className="mb-6">
        <FinalEvidence />
      </div>
      <CoreRecordedRuns />
      {filesQuery.isLoading && (
        <p className="text-sm text-gray-500">
          {text('Loading formal experiment index…', '正在加载正式实验索引…')}
        </p>
      )}
      {filesQuery.isError && (
        <p className="rounded border border-red-900 bg-red-950/30 p-3 text-sm text-rose-700">
          {text('Could not load experiment index', '无法加载实验索引')}：
          {(filesQuery.error as Error).message}
        </p>
      )}
      <div className="space-y-6">
        {families.map((family, index) => {
          const query = resultQueries[index]
          const rows = query.data?.results ?? []
          const familyLabel =
            family.label === 'Safety'
              ? text('Safety', '安全')
              : family.label === 'Graph'
                ? text('Graph', '任务图')
                : text('Rollback', '回滚')
          return (
            <div key={family.label}>
              {!formalFiles[index] ? (
                <section className="mt-0 rounded border border-slate-200 bg-white p-4">
                  <h2 className="text-sm font-medium text-slate-600">
                    {text(
                      `${family.label} formal dashboard`,
                      `${familyLabel}正式实验面板`,
                    )}
                  </h2>
                  <p className="mt-2 text-sm text-gray-500">
                    {text(
                      'Formal raw JSONL is not available through the read-only experiment API.',
                      '只读实验 API 未提供正式 JSONL 原始数据。',
                    )}
                  </p>
                </section>
              ) : query.isLoading ? (
                <section className="mt-0 rounded border border-slate-200 bg-white p-4 text-sm text-gray-500">
                  {text(
                    `Loading ${family.label} formal rows…`,
                    `正在加载${familyLabel}正式数据…`,
                  )}
                </section>
              ) : query.isError ? (
                <section className="mt-0 rounded border border-red-900 bg-red-950/30 p-4 text-sm text-rose-700">
                  {text(
                    `Could not load ${family.label} formal rows.`,
                    `无法加载${familyLabel}正式数据。`,
                  )}
                </section>
              ) : (
                <Dashboard family={family.label} rows={rows} />
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
