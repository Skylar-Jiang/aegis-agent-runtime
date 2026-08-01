import { useState } from 'react';

import {
  getTaskApprovals,
  getTaskEffects,
  getTaskGraph,
  type ApprovalView,
  type EffectView,
  type TaskGraphSnapshot,
} from '../api/taskGraphs';
import { StatusBadge } from '../components/StatusBadge';

export function TaskGraphPage() {
  const [taskId, setTaskId] = useState('');
  const [graph, setGraph] = useState<TaskGraphSnapshot | null>(null);
  const [effects, setEffects] = useState<EffectView[]>([]);
  const [approvals, setApprovals] = useState<ApprovalView[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    if (!taskId.trim()) return;
    setError(null);
    try {
      const selectedTaskId = taskId.trim();
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
    }
  }

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold">TaskGraph Runtime</h1>
      <div className="mb-5 flex gap-2">
        <input
          aria-label="Task ID"
          className="flex-1 rounded border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200"
          placeholder="Task ID..."
          value={taskId}
          onChange={(event) => setTaskId(event.target.value)}
        />
        <button
          className="rounded bg-blue-700 px-4 py-2 text-sm text-white disabled:opacity-50"
          disabled={!taskId.trim()}
          onClick={() => void load()}
        >
          Load runtime facts
        </button>
      </div>
      {error && <p className="mb-4 text-sm text-red-400">{error}</p>}
      {graph && (
        <section className="mb-5 rounded border border-gray-800 bg-gray-900 p-4">
          <div className="mb-3 flex items-center gap-3"><h2 className="font-mono text-blue-400">{graph.graph_id}</h2><StatusBadge status={graph.status} /></div>
          <table className="w-full text-left text-xs text-gray-400"><thead className="text-gray-500"><tr><th className="pb-2">Node</th><th className="pb-2">Status</th><th className="pb-2">Reason</th></tr></thead><tbody>{graph.nodes.map((node) => <tr key={node.node_id} className="border-t border-gray-800"><td className="py-2 font-mono">{node.node_id}</td><td className="py-2"><StatusBadge status={node.status} /></td><td className="py-2">{node.blocked_reason ?? '-'}</td></tr>)}</tbody></table>
        </section>
      )}
      {graph && <section className="mb-5"><h2 className="mb-2 text-sm font-medium text-gray-400">Effects (redacted projection)</h2><div className="rounded border border-gray-800"><table className="w-full text-left text-xs text-gray-400"><thead className="bg-gray-900 text-gray-500"><tr><th className="p-2">Kind</th><th className="p-2">Target</th><th className="p-2">Status</th></tr></thead><tbody>{effects.map((effect) => <tr key={effect.effect_id} className="border-t border-gray-800"><td className="p-2">{effect.kind}</td><td className="p-2 font-mono">{effect.target_ref}</td><td className="p-2"><StatusBadge status={effect.status} /></td></tr>)}</tbody></table></div></section>}
      {graph && <section><h2 className="mb-2 text-sm font-medium text-gray-400">Approvals</h2><div className="rounded border border-gray-800"><table className="w-full text-left text-xs text-gray-400"><thead className="bg-gray-900 text-gray-500"><tr><th className="p-2">Approval</th><th className="p-2">Tool</th><th className="p-2">Status</th></tr></thead><tbody>{approvals.map((approval) => <tr key={approval.approval_id} className="border-t border-gray-800"><td className="p-2 font-mono">{approval.approval_id}</td><td className="p-2">{approval.tool_name}</td><td className="p-2"><StatusBadge status={approval.status} /></td></tr>)}</tbody></table></div></section>}
    </div>
  );
}
