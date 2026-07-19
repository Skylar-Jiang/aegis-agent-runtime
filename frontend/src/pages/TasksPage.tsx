import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { createTask, getTaskEvents, cancelTask } from '../api/tasks';
import { StatusBadge } from '../components/StatusBadge';
import type { AuditEvent } from '../types/contracts';

export function TasksPage() {
  const [objective, setObjective] = useState('');
  const [createdId, setCreatedId] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const create = useMutation({
    mutationFn: () => createTask(objective),
    onSuccess: (data) => {
      setCreatedId(data.task_id);
      queryClient.invalidateQueries({ queryKey: ['task-events', data.task_id] });
    },
  });

  const { data: events, isLoading } = useQuery({
    queryKey: ['task-events', createdId],
    queryFn: () => getTaskEvents(createdId!),
    enabled: createdId !== null,
    refetchInterval: 3000,
  });

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold">Tasks</h1>
      <div className="mb-4 flex gap-2">
        <input
          className="flex-1 rounded border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200"
          placeholder="Enter task objective..."
          value={objective}
          onChange={(e) => setObjective(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && create.mutate()}
        />
        <button
          className="rounded bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-500 disabled:opacity-50"
          onClick={() => create.mutate()}
          disabled={!objective || create.isPending}
        >
          {create.isPending ? 'Creating...' : 'Create Task'}
        </button>
      </div>
      {create.isError && <p className="text-red-400 text-sm">Error: {(create.error as Error).message}</p>}
      {createdId && (
        <div className="rounded border border-gray-800 bg-gray-900 p-4">
          <h2 className="mb-2 text-sm font-medium">
            Task <span className="text-blue-400">{createdId}</span>
          </h2>
          <StatusBadge status="ACTIVE" />
          {isLoading && <p className="mt-2 text-sm text-gray-500">Loading events...</p>}
          {events && (
            <div className="mt-3 max-h-64 overflow-y-auto">
              <table className="w-full text-xs text-gray-400">
                <thead>
                  <tr className="text-left text-gray-500">
                    <th className="pb-1 pr-2">Seq</th><th className="pb-1 pr-2">Type</th><th className="pb-1">Summary</th>
                  </tr>
                </thead>
                <tbody>
                  {(events.events as AuditEvent[]).map((e) => (
                    <tr key={e.event_id} className="border-t border-gray-800">
                      <td className="py-1 pr-2">{e.sequence_number}</td>
                      <td className="py-1 pr-2 text-blue-400">{e.event_type}</td>
                      <td className="py-1">{e.summary}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <button
            className="mt-3 rounded border border-red-800 px-3 py-1 text-xs text-red-400 hover:bg-red-900/30"
            onClick={() => cancelTask(createdId)}
          >
            Cancel Task
          </button>
        </div>
      )}
    </div>
  );
}
