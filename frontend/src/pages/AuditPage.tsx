import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { createTaskStreamUrl, getTaskEvents } from '../api/tasks';
import { StatusBadge } from '../components/StatusBadge';
import { describeAuditEvent, mergeAuditEvents } from '../lib/taskEvents';
import { useUIStore } from '../stores/ui';
import type { AuditEvent } from '../types/contracts';

export function AuditPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [taskId, setTaskId] = useState(searchParams.get('task_id') ?? '');
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [polling, setPolling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const filterType = useUIStore((state) => state.auditFilterType);

  const loadEvents = useCallback(async (value = taskId) => {
    if (!value.trim()) return;
    try {
      const data = await getTaskEvents(value.trim());
      setEvents((current) => mergeAuditEvents(current, (data.events as AuditEvent[]) ?? []));
      setError(null);
    } catch (cause) {
      setError((cause as Error).message);
    }
  }, [taskId]);

  function submitLoad() {
    const selectedTaskId = taskId.trim();
    if (!selectedTaskId) return;
    setEvents([]);
    setSearchParams({ task_id: selectedTaskId });
    void loadEvents(selectedTaskId);
  }

  useEffect(() => {
    const queryTaskId = searchParams.get('task_id')?.trim();
    if (!queryTaskId) return;
    setTaskId(queryTaskId);
    void loadEvents(queryTaskId);
  }, [loadEvents, searchParams]);

  useEffect(() => {
    if (!taskId.trim()) return;
    void loadEvents();
    const source = new EventSource(createTaskStreamUrl(taskId.trim()));
    source.addEventListener('audit', (message) => {
      try {
        const event = JSON.parse((message as MessageEvent<string>).data) as AuditEvent;
        if (event.status === 'connected') {
          setConnected(true);
          setPolling(false);
        } else if (event.event_id) {
          setEvents((current) => mergeAuditEvents(current, [event]).slice(-500));
        }
      } catch {
        // Malformed data is not an audit fact and is intentionally discarded.
      }
    });
    source.onopen = () => { setConnected(true); setPolling(false); };
    source.onerror = () => { setConnected(false); setPolling(true); };
    return () => source.close();
  }, [loadEvents, taskId]);

  useEffect(() => {
    if (!polling || !taskId.trim()) return;
    const timer = window.setInterval(() => void loadEvents(), 3000);
    return () => window.clearInterval(timer);
  }, [loadEvents, polling, taskId]);

  const filtered = filterType === 'ALL' ? events : events.filter((event) => event.event_type === filterType);

  return <div><div className="mb-4 flex flex-wrap items-center justify-between gap-3"><h1 className="text-xl font-semibold">Audit timeline</h1><span className={`text-xs ${connected ? 'text-green-400' : polling ? 'text-amber-300' : 'text-gray-600'}`}>{connected ? '● Live' : polling ? '◐ Polling fallback' : '○ Offline'}</span></div><div className="mb-4 flex flex-wrap gap-2"><input className="min-w-56 flex-1 rounded border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200" placeholder="Task ID..." value={taskId} onChange={(event) => setTaskId(event.target.value)} /><button className="rounded bg-blue-700 px-4 py-2 text-sm text-white disabled:opacity-50" disabled={!taskId.trim()} onClick={submitLoad}>Load audit timeline</button></div>{error && <p className="mb-4 rounded border border-red-900 bg-red-950/30 p-3 text-sm text-red-300">{error}</p>}{taskId && filtered.length === 0 && !error && <p className="rounded border border-gray-800 bg-gray-900 p-4 text-sm text-gray-500">No audit events have been recorded for this task.</p>}{filtered.length > 0 && <ol className="m-0 max-h-[36rem] overflow-y-auto rounded border border-gray-800 p-0">{filtered.map((event) => { const view = describeAuditEvent(event); return <li key={event.event_id} className="m-0 border-b border-gray-800 border-l-0 px-4 py-3 last:border-b-0"><div className="flex flex-wrap items-center gap-2"><span className="font-medium text-gray-200">{view.stage}</span><StatusBadge status={event.status} /><time className="ml-auto text-xs text-gray-500">{new Date(event.timestamp).toLocaleString()}</time></div><p className="mt-1 text-sm text-gray-400">{view.detail}</p><p className="mt-1 font-mono text-[11px] text-gray-600">#{event.sequence_number} · {event.event_type}</p></li>})}</ol>}</div>;
}
