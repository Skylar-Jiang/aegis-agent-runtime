import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ExperimentsPage } from './ExperimentsPage';

const api = vi.hoisted(() => ({
  listCases: vi.fn().mockResolvedValue([]),
  listResults: vi.fn().mockResolvedValue([
    { name: 'v2-safety-formal.jsonl', size: 1024, modified: 0 },
    { name: 'v2-graph-formal.jsonl', size: 1024, modified: 0 },
    { name: 'rollback_benchmark.jsonl', size: 1024, modified: 0 },
  ]),
  getResult: vi.fn((filename: string) => Promise.resolve({
    filename,
    results: [{
      case_id: 'S1', mode: 'ADAPTIVE_RUNTIME', task_id: 'task-1', request_id: 'request-1',
      status: 'BLOCKED', expected_status: 'BLOCKED', started_at: '2026-07-30T00:00:00Z',
      finished_at: '2026-07-30T00:00:00Z', elapsed_ms: 12, false_block_count: 1,
      unsafe_tool_executed_count: 0, approval_requested_count: 1, manual_action_count: 1,
      risk_escalation_count: 1, graph_elapsed_ms: 12, parallel_saved_ms: 3,
      approval_wait_ms: 4, metrics: { max_observed_concurrency: 2, nodes_completed_during_approval: 1 },
      rollback_elapsed_ms: 5, rollback_count: 1, selective_rollback_count: 1,
      residual_effect_count: 0, metrics_rolled_back_effect_count: 1, metrics_preserved_effect_count: 1,
    }],
  })),
}));

vi.mock('../api/experiments', () => api);

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}><ExperimentsPage /></QueryClientProvider>);
}

describe('ExperimentsPage', () => {
  it('builds all three dashboard sections from the formal raw result files', async () => {
    renderPage();

    expect(await screen.findByRole('heading', { name: 'Safety formal dashboard' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Graph formal dashboard' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Rollback formal dashboard' })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('False blocks')).toBeInTheDocument());
    expect(api.getResult).toHaveBeenCalledTimes(3);
  });
});
