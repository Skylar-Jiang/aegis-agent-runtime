import { useEffect, useState } from 'react';
import { grantApproval, denyApproval, listApprovals, type ApprovalListItem } from '../api/approvals';
import { StatusBadge } from '../components/StatusBadge';

export function ApprovalsPage() {
  const [approvalId, setApprovalId] = useState('');
  const [decidedBy, setDecidedBy] = useState('');
  const [result, setResult] = useState<{ status: string; decided_by?: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [approvals, setApprovals] = useState<ApprovalListItem[]>([]);

  async function refreshApprovals() {
    try {
      setApprovals(await listApprovals());
    } catch (e) {
      setError((e as Error).message);
    }
  }

  useEffect(() => {
    void refreshApprovals();
  }, []);

  async function handleAction(action: 'grant' | 'deny') {
    if (!approvalId || !decidedBy.trim()) return;
    setError(null);
    try {
      const fn = action === 'grant' ? grantApproval : denyApproval;
      const decision = await fn(approvalId, decidedBy.trim());
      setResult({ status: decision.status, decided_by: decision.decided_by });
      await refreshApprovals();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold">Approvals</h1>
      <div className="mb-4 flex gap-2">
        <input
          className="flex-1 rounded border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200"
          placeholder="Approval ID..."
          value={approvalId}
          onChange={(e) => setApprovalId(e.target.value)}
        />
        <input
          className="flex-1 rounded border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200"
          placeholder="Approver identity..."
          value={decidedBy}
          onChange={(e) => setDecidedBy(e.target.value)}
        />
        <button
          className="rounded bg-green-700 px-4 py-2 text-sm text-white hover:bg-green-600 disabled:opacity-50"
          onClick={() => handleAction('grant')}
          disabled={!approvalId || !decidedBy.trim()}
        >
          Grant
        </button>
        <button
          className="rounded bg-red-700 px-4 py-2 text-sm text-white hover:bg-red-600 disabled:opacity-50"
          onClick={() => handleAction('deny')}
          disabled={!approvalId || !decidedBy.trim()}
        >
          Deny
        </button>
      </div>
      <section className="mb-4 overflow-x-auto rounded border border-gray-800">
        <table className="w-full text-left text-xs text-gray-400">
          <thead className="bg-gray-900 text-gray-500"><tr><th className="p-2">Task</th><th className="p-2">Tool</th><th className="p-2">Status</th><th className="p-2">Approval</th></tr></thead>
          <tbody>{approvals.map((approval) => <tr key={approval.approval_id} className="cursor-pointer border-t border-gray-800 hover:bg-gray-900/50" onClick={() => setApprovalId(approval.approval_id)}><td className="p-2 font-mono">{approval.task_id}</td><td className="p-2">{approval.tool_name}</td><td className="p-2"><StatusBadge status={approval.status} /></td><td className="p-2 font-mono">{approval.approval_id}</td></tr>)}</tbody>
        </table>
      </section>
      {error && <p className="text-red-400 text-sm">Error: {error}</p>}
      {result && (
        <div className="rounded border border-gray-800 bg-gray-900 p-3 text-sm">
          <p>Status: <StatusBadge status={result.status} /></p>
          {result.decided_by && <p className="mt-1 text-gray-400">By: {result.decided_by}</p>}
        </div>
      )}
    </div>
  );
}
