import { useState } from 'react';
import { grantApproval, denyApproval } from '../api/approvals';
import { StatusBadge } from '../components/StatusBadge';

export function ApprovalsPage() {
  const [approvalId, setApprovalId] = useState('');
  const [result, setResult] = useState<{ status: string; decided_by?: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleAction(action: 'grant' | 'deny') {
    if (!approvalId) return;
    setError(null);
    try {
      const fn = action === 'grant' ? grantApproval : denyApproval;
      const decision = await fn(approvalId);
      setResult({ status: decision.status, decided_by: decision.decided_by });
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
        <button
          className="rounded bg-green-700 px-4 py-2 text-sm text-white hover:bg-green-600 disabled:opacity-50"
          onClick={() => handleAction('grant')}
          disabled={!approvalId}
        >
          Grant
        </button>
        <button
          className="rounded bg-red-700 px-4 py-2 text-sm text-white hover:bg-red-600 disabled:opacity-50"
          onClick={() => handleAction('deny')}
          disabled={!approvalId}
        >
          Deny
        </button>
      </div>
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
