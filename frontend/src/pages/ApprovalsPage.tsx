import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import {
  grantApproval,
  denyApproval,
  listApprovals,
  type ApprovalListItem,
} from '../api/approvals'
import { StatusBadge } from '../components/StatusBadge'
import { localizeTool, useI18n } from '../i18n'

export function ApprovalsPage() {
  const { language, text } = useI18n()
  const [searchParams] = useSearchParams()
  const taskId = searchParams.get('task_id') ?? undefined
  const [decidedBy, setDecidedBy] = useState('')
  const [result, setResult] = useState<{
    status: string
    decided_by?: string
  } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [approvals, setApprovals] = useState<ApprovalListItem[]>([])
  const [loading, setLoading] = useState(false)

  const refreshApprovals = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setApprovals(await listApprovals(taskId, 'PENDING'))
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setLoading(false)
    }
  }, [taskId])

  useEffect(() => {
    void refreshApprovals()
  }, [refreshApprovals])

  async function handleAction(approvalId: string, action: 'grant' | 'deny') {
    if (!decidedBy.trim()) return
    setError(null)
    try {
      const fn = action === 'grant' ? grantApproval : denyApproval
      const decision = await fn(approvalId, decidedBy.trim())
      setResult({ status: decision.status, decided_by: decision.decided_by })
      await refreshApprovals()
    } catch (cause) {
      setError((cause as Error).message)
    }
  }

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">
            {text('Approvals', '审批中心')}
          </h1>
          <p className="mt-1 text-sm text-gray-500">
            {taskId
              ? text(
                  `Pending approvals for ${taskId}.`,
                  `任务 ${taskId} 的待处理审批。`,
                )
              : text('Global pending approvals.', '查看全部待处理审批。')}
          </p>
        </div>
        <button
          className="rounded border border-slate-200 px-3 py-2 text-sm text-slate-800 disabled:opacity-50"
          disabled={loading}
          onClick={() => void refreshApprovals()}
        >
          {loading ? text('Refreshing…', '刷新中…') : text('Refresh', '刷新')}
        </button>
      </div>
      <div className="mb-4">
        <label className="block text-xs text-gray-500" htmlFor="decided-by">
          {text('Approver identity', '审批人身份')}
        </label>
        <input
          id="decided-by"
          className="mt-1 w-full rounded border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800"
          placeholder={text('Approver identity...', '请输入审批人身份…')}
          value={decidedBy}
          onChange={(event) => setDecidedBy(event.target.value)}
        />
      </div>
      {error && (
        <p className="mb-4 rounded border border-red-900 bg-red-950/30 p-3 text-sm text-rose-700">
          {text('Error', '错误')}：{error}
        </p>
      )}
      {approvals.length === 0 && !loading ? (
        <p className="rounded border border-slate-200 bg-white p-4 text-sm text-gray-500">
          {text(
            'No pending approvals match this scope.',
            '当前范围内没有待处理审批。',
          )}
        </p>
      ) : (
        <div className="grid gap-3">
          {approvals.map((approval) => (
            <article
              key={approval.approval_id}
              className="rounded border border-slate-200 bg-white p-4"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-indigo-600">
                      {localizeTool(approval.tool_name, language)}{' '}
                      <span className="font-mono text-xs text-gray-500">
                        ({approval.tool_name})
                      </span>
                    </span>
                    <StatusBadge status={approval.status} />
                  </div>
                  <p className="mt-2 text-sm text-slate-600">
                    {approval.reason}
                  </p>
                  <p className="mt-2 font-mono text-xs text-gray-500">
                    {text('Task', '任务')} {approval.task_id}
                  </p>
                </div>
                <div className="flex gap-2">
                  <button
                    className="rounded bg-green-700 px-3 py-2 text-sm text-white disabled:opacity-50"
                    disabled={!decidedBy.trim() || loading}
                    onClick={() =>
                      void handleAction(approval.approval_id, 'grant')
                    }
                    aria-label={text(
                      `Grant ${approval.approval_id}`,
                      `批准 ${approval.approval_id}`,
                    )}
                  >
                    {text('Grant', '批准')}
                  </button>
                  <button
                    className="rounded bg-red-700 px-3 py-2 text-sm text-white disabled:opacity-50"
                    disabled={!decidedBy.trim() || loading}
                    onClick={() =>
                      void handleAction(approval.approval_id, 'deny')
                    }
                    aria-label={text(
                      `Deny ${approval.approval_id}`,
                      `拒绝 ${approval.approval_id}`,
                    )}
                  >
                    {text('Deny', '拒绝')}
                  </button>
                </div>
              </div>
              <div className="mt-3 flex gap-3 text-xs">
                <Link
                  aria-label={text(
                    `Open task ${approval.task_id}`,
                    `打开任务 ${approval.task_id}`,
                  )}
                  className="text-indigo-600 hover:text-indigo-600"
                  to={`/tasks?task_id=${encodeURIComponent(approval.task_id)}`}
                >
                  {text('Open task', '打开任务')}
                </Link>
                <Link
                  aria-label={text(
                    `Open runtime for ${approval.task_id}`,
                    `打开任务 ${approval.task_id} 的运行时`,
                  )}
                  className="text-indigo-600 hover:text-indigo-600"
                  to={`/runtime?task_id=${encodeURIComponent(approval.task_id)}`}
                >
                  {text('Open runtime', '打开运行时')}
                </Link>
              </div>
            </article>
          ))}
        </div>
      )}
      {result && (
        <div className="mt-4 rounded border border-slate-200 bg-white p-3 text-sm">
          <p>
            {text('Status', '状态')}：<StatusBadge status={result.status} />
          </p>
          {result.decided_by && (
            <p className="mt-1 text-slate-500">
              {text('By', '审批人')}：{result.decided_by}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
