import { useState } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { createTask, getTaskEvents, cancelTask } from '../api/tasks';
import { useUIStore } from '../stores/ui';
import { StatusBadge } from '../components/StatusBadge';
import type { AuditEvent } from '../types/contracts';

export function TasksPage() {
  const [objective, setObjective] = useState('');
  const taskHistory = useUIStore((s) => s.taskHistory);
  const addTask = useUIStore((s) => s.addTask);
  const selectedTaskId = useUIStore((s) => s.selectedTaskId);
  const setSelectedTask = useUIStore((s) => s.setSelectedTask);
  const cancelledTaskIds = useUIStore((s) => s.cancelledTaskIds);
  const markCancelled = useUIStore((s) => s.markCancelled);

  const create = useMutation({
    mutationFn: () => createTask(objective),
    onSuccess: (data) => {
      addTask({
        taskId: data.task_id,
        objective: objective,
        createdAt: new Date().toISOString(),
        status: data.status,
      });
      setSelectedTask(data.task_id);
      setObjective('');
    },
  });

  // Events query for the selected task
  const { data: events, isLoading, error: eventsError } = useQuery({
    queryKey: ['task-events', selectedTaskId],
    queryFn: () => getTaskEvents(selectedTaskId!),
    enabled: selectedTaskId !== null,
    refetchInterval: 3000,
  });

  const cancel = useMutation({
    mutationFn: () => cancelTask(selectedTaskId!),
    onSuccess: () => {
      markCancelled(selectedTaskId!);
    },
  });

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold">Tasks</h1>

      {/* Create form */}
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
      {create.isError && (
        <p className="mb-4 text-sm text-red-400">Error: {(create.error as Error).message}</p>
      )}

      {/* Task history list */}
      {taskHistory.length > 0 && (
        <div className="mb-4">
          <h2 className="mb-2 text-sm font-medium text-gray-400">
            Task History ({taskHistory.length})
          </h2>
          <div className="flex flex-wrap gap-2">
            {taskHistory.map((t) => {
              const isSelected = t.taskId === selectedTaskId;
              const isCancelled = cancelledTaskIds.includes(t.taskId);
              return (
                <button
                  key={t.taskId}
                  className={`rounded border px-3 py-1.5 text-left text-xs transition-colors ${
                    isSelected
                      ? 'border-blue-500 bg-blue-900/30 text-blue-300'
                      : 'border-gray-700 bg-gray-900 text-gray-400 hover:border-gray-600'
                  }`}
                  onClick={() => setSelectedTask(t.taskId)}
                >
                  <div className="font-mono text-blue-400">{t.taskId.slice(0, 20)}…</div>
                  <div className="mt-0.5 truncate max-w-48">{t.objective}</div>
                  <StatusBadge status={isCancelled ? 'CANCELLED' : t.status} />
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Selected task detail */}
      {selectedTaskId && (
        <div className="rounded border border-gray-800 bg-gray-900 p-4">
          <h2 className="mb-2 text-sm font-medium">
            Task <span className="text-blue-400">{selectedTaskId}</span>
          </h2>
          <div className="flex items-center gap-2">
            <StatusBadge
              status={
                cancelledTaskIds.includes(selectedTaskId)
                  ? 'CANCELLED'
                  : taskHistory.find((task) => task.taskId === selectedTaskId)?.status ?? 'ACTIVE'
              }
            />
            {cancel.isPending && <span className="text-xs text-gray-500">cancelling...</span>}
            {cancel.isError && (
              <span className="text-xs text-red-400">Cancel failed</span>
            )}
          </div>

          {isLoading && (
            <p className="mt-2 text-sm text-gray-500">Loading events...</p>
          )}
          {eventsError && (
            <p className="mt-2 text-sm text-red-400">
              Events error: {(eventsError as Error).message}
            </p>
          )}

          {events && events.events && (events.events as AuditEvent[]).length > 0 && (
            <div className="mt-3 max-h-64 overflow-y-auto">
              <table className="w-full text-xs text-gray-400">
                <thead>
                  <tr className="text-left text-gray-500">
                    <th className="pb-1 pr-2">Seq</th>
                    <th className="pb-1 pr-2">Type</th>
                    <th className="pb-1">Summary</th>
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

          {!cancelledTaskIds.includes(selectedTaskId) && (
            <button
              className="mt-3 rounded border border-red-800 px-3 py-1 text-xs text-red-400 hover:bg-red-900/30 disabled:opacity-50"
              onClick={() => cancel.mutate()}
              disabled={cancel.isPending}
            >
              {cancel.isPending ? 'Cancelling...' : 'Cancel Task'}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
