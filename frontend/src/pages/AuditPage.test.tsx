import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { AuditPage } from './AuditPage';

const api = vi.hoisted(() => ({
  getTaskEvents: vi.fn().mockResolvedValue({ events: [{
    event_id: 'event-1', task_id: 'task-1', sequence_number: 1, event_type: 'APPROVAL_REQUESTED',
    timestamp: '2026-08-02T01:00:00Z', actor: 'runtime', status: 'WAITING_APPROVAL',
    summary: 'Approval required', details: {},
  }] }),
  createTaskStreamUrl: vi.fn().mockReturnValue('/api/tasks/task-1/stream'),
}));

vi.mock('../api/tasks', () => api);

describe('AuditPage', () => {
  it('renders the safe key-event timeline and falls back to polling after a stream error', async () => {
    const eventSource = { addEventListener: vi.fn(), close: vi.fn(), onopen: null as (() => void) | null, onerror: null as (() => void) | null };
    vi.stubGlobal('EventSource', vi.fn(() => eventSource));
    render(<BrowserRouter><AuditPage /></BrowserRouter>);

    fireEvent.change(screen.getByPlaceholderText('Task ID...'), { target: { value: 'task-1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Load audit timeline' }));
    await screen.findByText('Approval');
    eventSource.onerror?.();

    await waitFor(() => expect(screen.getByText(/Polling fallback/)).toBeInTheDocument());
  });
});
