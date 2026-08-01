import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { listApprovals, grantApproval, denyApproval } from '../api/approvals';
import { StatusBadge } from '../components/StatusBadge';
import type { ApprovalItem } from '../api/approvals';

export function ApprovalsPage() {
  const [decidedBy, setDecidedBy] = useState('');
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const { data: approvals, isLoading, error } = useQuery({
    queryKey: ['approvals', 'pending'],
    queryFn: () => listApprovals({ status: 'PENDING' }),
    refetchInterval: 5000,
  });

  const grantMutation = useMutation({
    mutationFn: ({ id, actor }: { id: string; actor: string }) =>
      grantApproval(id, actor),
    onSuccess: (data) => {
      setActionMessage(`Granted — ${data.approval_id}`);
      queryClient.invalidateQueries({ queryKey: ['approvals'] });
    },
    onError: (e) => setActionMessage((e as Error).message),
  });

  const denyMutation = useMutation({
    mutationFn: ({ id, actor }: { id: string; actor: string }) =>
      denyApproval(id, actor),
    onSuccess: (data) => {
      setActionMessage(`Denied — ${data.approval_id}`);
      queryClient.invalidateQueries({ queryKey: ['approvals'] });
    },
    onError: (e) => setActionMessage((e as Error).message),
  });

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold">Approvals</h1>

      <div className="mb-4 flex items-center gap-2">
        <input
          className="w-48 rounded border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200"
          placeholder="Your identity..."
          value={decidedBy}
          onChange={(e) => setDecidedBy(e.target.value)}
        />
        <span className="text-xs text-gray-600">Required for grant/deny</span>
      </div>

      {actionMessage && (
        <p className="mb-4 text-sm text-blue-400">{actionMessage}</p>
      )}
      {error && (
        <p className="mb-4 text-sm text-red-400">Error: {(error as Error).message}</p>
      )}

      {isLoading ? (
        <p className="text-sm text-gray-500">Loading pending approvals...</p>
      ) : (approvals ?? []).length === 0 ? (
        <p className="text-sm text-gray-500">No pending approvals.</p>
      ) : (
        <div className="flex flex-col gap-3">
          {(approvals ?? []).map((a: ApprovalItem) => (
            <div
              key={a.approval_id}
              className="rounded border border-gray-800 bg-gray-900 p-4"
            >
              <div className="mb-2 flex items-center gap-3">
                <span className="font-mono text-xs text-blue-400">
                  {a.approval_id.slice(0, 12)}…
                </span>
                <StatusBadge status={a.status} />
              </div>
              <div className="mb-2 text-sm text-gray-300">
                <span className="text-gray-500">Tool:</span> {a.tool_name}
              </div>
              <div className="mb-3 text-sm text-gray-400">{a.reason}</div>
              <div className="flex gap-2">
                <button
                  className="rounded bg-green-700 px-3 py-1 text-xs text-white hover:bg-green-600 disabled:opacity-50"
                  onClick={() =>
                    grantMutation.mutate({ id: a.approval_id, actor: decidedBy })
                  }
                  disabled={!decidedBy.trim() || grantMutation.isPending}
                >
                  Grant
                </button>
                <button
                  className="rounded bg-red-700 px-3 py-1 text-xs text-white hover:bg-red-600 disabled:opacity-50"
                  onClick={() =>
                    denyMutation.mutate({ id: a.approval_id, actor: decidedBy })
                  }
                  disabled={!decidedBy.trim() || denyMutation.isPending}
                >
                  Deny
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
