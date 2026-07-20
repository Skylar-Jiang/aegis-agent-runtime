import { useState, useEffect, useCallback } from 'react';
import { getTaskEvents, createTaskStreamUrl } from '../api/tasks';
import { useUIStore } from '../stores/ui';
import { StatusBadge } from '../components/StatusBadge';
import type { AuditEvent } from '../types/contracts';

export function AuditPage() {
  const [taskId, setTaskId] = useState('');
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const filterType = useUIStore((s) => s.auditFilterType);

  const loadEvents = useCallback(async () => {
    if (!taskId) return;
    const data = await getTaskEvents(taskId);
    setEvents((data.events as AuditEvent[]) ?? []);
  }, [taskId]);

  useEffect(() => {
    if (!taskId) return;
    loadEvents();

    const url = createTaskStreamUrl(taskId);
    const es = new EventSource(url);

    es.addEventListener('audit', (e) => {
      try {
        const evt = JSON.parse(e.data) as AuditEvent;
        // 'connected' status events don't have event_id
        if (evt.status === 'connected') {
          setConnected(true);
          return;
        }
        if (evt.event_id) {
          setEvents((prev) => [...prev.slice(-499), evt]);
        }
      } catch { /* ignore parse errors */ }
    });

    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    // Don't call es.close() — let EventSource auto-reconnect

    return () => {
      setConnected(false);
      es.close();
    };
  }, [taskId, loadEvents]);

  const filtered = filterType === 'ALL' ? events : events.filter((e) => e.event_type === filterType);

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold">Audit Log</h1>
      <div className="mb-4 flex gap-2">
        <input
          className="flex-1 rounded border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200"
          placeholder="Task ID..."
          value={taskId}
          onChange={(e) => setTaskId(e.target.value)}
        />
        <span className={`self-center text-xs ${connected ? 'text-green-400' : 'text-gray-600'}`}>
          {connected ? '● Live' : '○ Offline'}
        </span>
      </div>
      {filtered.length > 0 && (
        <div className="max-h-96 overflow-y-auto rounded border border-gray-800">
          <table className="w-full text-xs text-gray-400">
            <thead className="sticky top-0 bg-gray-900 text-left text-gray-500">
              <tr>
                <th className="p-2">Seq</th><th className="p-2">Type</th>
                <th className="p-2">Status</th><th className="p-2">Summary</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((e) => (
                <tr key={e.event_id} className="border-t border-gray-800 hover:bg-gray-900/50">
                  <td className="p-2">{e.sequence_number}</td>
                  <td className="p-2 text-blue-400">{e.event_type}</td>
                  <td className="p-2"><StatusBadge status={e.status} /></td>
                  <td className="p-2">{e.summary}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
