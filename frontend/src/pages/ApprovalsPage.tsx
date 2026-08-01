import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { grantApproval, denyApproval, listApprovals, type ApprovalListItem } from '../api/approvals';
import { StatusBadge } from '../components/StatusBadge';

export function ApprovalsPage() {
  const [searchParams] = useSearchParams();
  const taskId = searchParams.get('task_id') ?? undefined;
  const [decidedBy, setDecidedBy] = useState('');
  const [result, setResult] = useState<{ status: string; decided_by?: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [approvals, setApprovals] = useState<ApprovalListItem[]>([]);
  const [loading, setLoading] = useState(false);

  async function refreshApprovals() {
    setLoading(true);
    setError(null);
    try {
      setApprovals(await listApprovals(taskId, 'PENDING'));
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void refreshApprovals(); }, [taskId]);

  async function handleAction(approvalId: string, action: 'grant' | 'deny') {
    if (!decidedBy.trim()) return;
    setError(null);
    try {
      const fn = action === 'grant' ? grantApproval : denyApproval;
      const decision = await fn(approvalId, decidedBy.trim());
      setResult({ status: decision.status, decided_by: decision.decided_by });
      await refreshApprovals();
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  return <div><div className="mb-4 flex flex-wrap items-center justify-between gap-3"><div><h1 className="text-xl font-semibold">Approvals</h1><p className="mt-1 text-sm text-gray-500">Global pending approvals{taskId ? ` for ${taskId}` : ''}.</p></div><button className="rounded border border-gray-700 px-3 py-2 text-sm text-gray-200 disabled:opacity-50" disabled={loading} onClick={() => void refreshApprovals()}>{loading ? 'Refreshing…' : 'Refresh'}</button></div><div className="mb-4"><label className="block text-xs text-gray-500" htmlFor="decided-by">Approver identity</label><input id="decided-by" className="mt-1 w-full rounded border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200" placeholder="Approver identity..." value={decidedBy} onChange={(event) => setDecidedBy(event.target.value)} /></div>{error && <p className="mb-4 rounded border border-red-900 bg-red-950/30 p-3 text-sm text-red-300">Error: {error}</p>}{approvals.length === 0 && !loading ? <p className="rounded border border-gray-800 bg-gray-900 p-4 text-sm text-gray-500">No pending approvals match this scope.</p> : <div className="grid gap-3">{approvals.map((approval) => <article key={approval.approval_id} className="rounded border border-gray-800 bg-gray-900 p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex items-center gap-2"><span className="font-mono text-blue-400">{approval.tool_name}</span><StatusBadge status={approval.status} /></div><p className="mt-2 text-sm text-gray-300">{approval.reason}</p><p className="mt-2 font-mono text-xs text-gray-500">Task {approval.task_id}</p></div><div className="flex gap-2"><button className="rounded bg-green-700 px-3 py-2 text-sm text-white disabled:opacity-50" disabled={!decidedBy.trim() || loading} onClick={() => void handleAction(approval.approval_id, 'grant')} aria-label={`Grant ${approval.approval_id}`}>Grant</button><button className="rounded bg-red-700 px-3 py-2 text-sm text-white disabled:opacity-50" disabled={!decidedBy.trim() || loading} onClick={() => void handleAction(approval.approval_id, 'deny')} aria-label={`Deny ${approval.approval_id}`}>Deny</button></div></div><div className="mt-3 flex gap-3 text-xs"><Link aria-label={`Open task ${approval.task_id}`} className="text-blue-400 hover:text-blue-300" to={`/?task_id=${encodeURIComponent(approval.task_id)}`}>Open task</Link><Link aria-label={`Open runtime for ${approval.task_id}`} className="text-blue-400 hover:text-blue-300" to={`/runtime?task_id=${encodeURIComponent(approval.task_id)}`}>Open runtime</Link></div></article>)}</div>}{result && <div className="mt-4 rounded border border-gray-800 bg-gray-900 p-3 text-sm"><p>Status: <StatusBadge status={result.status} /></p>{result.decided_by && <p className="mt-1 text-gray-400">By: {result.decided_by}</p>}</div>}</div>;
}
