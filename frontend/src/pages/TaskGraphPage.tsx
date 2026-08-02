import { useCallback, useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import {
  cancelTaskGraph,
  getTaskApprovals,
  getTaskEffects,
  getTaskGraph,
  type ApprovalView,
  type EffectView,
  type TaskGraphSnapshot,
} from '../api/taskGraphs';
import { StatusBadge } from '../components/StatusBadge';

function timestamp(value?: string | null) {
  return value ? new Date(value).toLocaleString() : '—';
}

function effectReason(effect: EffectView) {
  if (effect.status === 'ROLLED_BACK') return 'Selected by the scoped rollback plan.';
  if (effect.status === 'PRESERVED') return 'Independent committed effect outside the rollback scope.';
  if (effect.status === 'COMMITTED') return 'Commit checks completed.';
  return 'Awaiting commit or rollback decision.';
}

export function TaskGraphPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [taskId, setTaskId] = useState(searchParams.get('task_id') ?? '');
  const [graph, setGraph] = useState<TaskGraphSnapshot | null>(null);
  const [effects, setEffects] = useState<EffectView[]>([]);
  const [approvals, setApprovals] = useState<ApprovalView[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (value = taskId) => {
    const selectedTaskId = value.trim();
    if (!selectedTaskId) return;
    setLoading(true);
    setError(null);
    try {
      const nextGraph = await getTaskGraph(selectedTaskId);
      const [nextEffects, nextApprovals] = await Promise.all([
        getTaskEffects(selectedTaskId),
        getTaskApprovals(selectedTaskId),
      ]);
      setGraph(nextGraph);
      setEffects(nextEffects);
      setApprovals(nextApprovals);
    } catch (cause) {
      setGraph(null);
      setEffects([]);
      setApprovals([]);
      setError((cause as Error).message);
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    const queryTaskId = searchParams.get('task_id')?.trim();
    if (!queryTaskId) return;
    setTaskId(queryTaskId);
    void load(queryTaskId);
  }, [load, searchParams]);

  function submitLoad() {
    const selectedTaskId = taskId.trim();
    if (!selectedTaskId) return;
    setSearchParams({ task_id: selectedTaskId });
  }

  async function cancelGraph() {
    if (!graph) return;
    setLoading(true);
    setError(null);
    try {
      await cancelTaskGraph(graph.graph_id);
      await load(graph.task_id);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">TaskGraph Runtime</h1>
        <div className="flex items-center gap-2">{graph && <StatusBadge status={graph.status} />}{graph && ['RUNNING', 'WAITING_APPROVAL'].includes(graph.status) && <button className="rounded border border-rose-800 px-3 py-1.5 text-sm text-rose-200 disabled:opacity-50" disabled={loading} onClick={() => void cancelGraph()}>Cancel graph</button>}</div>
      </div>
      <div className="mb-5 flex flex-wrap gap-2">
        <input aria-label="Task ID" className="min-w-56 flex-1 rounded border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200" placeholder="Task ID..." value={taskId} onChange={(event) => setTaskId(event.target.value)} />
        <button className="rounded bg-blue-700 px-4 py-2 text-sm text-white disabled:opacity-50" disabled={!taskId.trim() || loading} onClick={submitLoad}>{loading ? 'Loading…' : graph ? 'Refresh runtime facts' : 'Load runtime facts'}</button>
      </div>
      {!graph && !loading && !error && <p className="rounded border border-gray-800 bg-gray-900 p-4 text-sm text-gray-500">Enter a task ID or open Runtime from a task or approval to inspect server-owned graph facts.</p>}
      {error && <p className="mb-4 rounded border border-red-900 bg-red-950/30 p-3 text-sm text-red-300">{error}</p>}
      {graph && <>
        <section className="mb-5 mt-0 rounded border border-gray-800 bg-gray-900 p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3"><h2 className="font-mono text-blue-400">{graph.graph_id}</h2><p className="text-xs text-gray-500">Started {timestamp(graph.started_at)} · Finished {timestamp(graph.finished_at)}</p></div>
          <div className="overflow-x-auto"><table className="w-full text-left text-xs text-gray-400"><thead className="text-gray-500"><tr><th className="pb-2">Node</th><th className="pb-2">Depends on</th><th className="pb-2">Status</th><th className="pb-2">Block reason</th><th className="pb-2">Start / end</th></tr></thead><tbody>{graph.nodes.map((node) => <tr key={node.node_id} className="border-t border-gray-800"><td className="py-2 font-mono">{node.node_id}</td><td className="py-2 font-mono">{node.dependencies.length ? node.dependencies.join(', ') : '—'}</td><td className="py-2"><StatusBadge status={node.status} /></td><td className="py-2">{node.blocked_reason ?? '—'}</td><td className="py-2">{timestamp(node.started_at)} / {timestamp(node.finished_at)}</td></tr>)}</tbody></table></div>
        </section>
        <section className="mb-5 mt-0"><h2 className="mb-2 text-sm font-medium text-gray-400">Effects (redacted projection)</h2>{effects.length === 0 ? <p className="rounded border border-gray-800 bg-gray-900 p-3 text-sm text-gray-500">No Effect facts have been recorded for this graph.</p> : <div className="overflow-x-auto rounded border border-gray-800"><table className="w-full text-left text-xs text-gray-400"><thead className="bg-gray-900 text-gray-500"><tr><th className="p-2">Kind</th><th className="p-2">Target</th><th className="p-2">Checkpoint</th><th className="p-2">Status</th><th className="p-2">Safe lifecycle reason</th></tr></thead><tbody>{effects.map((effect) => <tr key={effect.effect_id} className="border-t border-gray-800"><td className="p-2">{effect.kind}</td><td className="p-2 font-mono">{effect.target_ref}</td><td className="p-2 font-mono">{effect.checkpoint_id ?? '—'}</td><td className="p-2"><StatusBadge status={effect.status} /></td><td className="p-2">{effectReason(effect)}</td></tr>)}</tbody></table></div>}</section>
        <section className="mt-0"><div className="mb-2 flex items-center justify-between"><h2 className="text-sm font-medium text-gray-400">Approvals</h2><div className="flex gap-3 text-xs"><Link className="text-blue-400 hover:text-blue-300" to={`/approvals?task_id=${encodeURIComponent(graph.task_id)}`}>Open approvals</Link><Link className="text-blue-400 hover:text-blue-300" to={`/audit?task_id=${encodeURIComponent(graph.task_id)}`}>Open audit</Link></div></div>{approvals.length === 0 ? <p className="rounded border border-gray-800 bg-gray-900 p-3 text-sm text-gray-500">No approval facts have been recorded for this graph.</p> : <div className="overflow-x-auto rounded border border-gray-800"><table className="w-full text-left text-xs text-gray-400"><thead className="bg-gray-900 text-gray-500"><tr><th className="p-2">Approval</th><th className="p-2">Tool</th><th className="p-2">Status</th></tr></thead><tbody>{approvals.map((approval) => <tr key={approval.approval_id} className="border-t border-gray-800"><td className="p-2 font-mono">{approval.approval_id}</td><td className="p-2">{approval.tool_name}</td><td className="p-2"><StatusBadge status={approval.status} /></td></tr>)}</tbody></table></div>}</section>
      </>}
    </div>
  );
}
