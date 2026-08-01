import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ExperimentsPage } from './ExperimentsPage';

const api = vi.hoisted(() => ({
  listCases: vi.fn().mockResolvedValue([]),
  listResults: vi.fn().mockResolvedValue([{ name: 'formal.jsonl', size: 1024, modified: 0 }]),
  getResult: vi.fn().mockResolvedValue({
    filename: 'formal.jsonl',
    results: [{
      case_id: 'S1', mode: 'ADAPTIVE_RUNTIME', task_id: 'task-1', request_id: 'request-1',
      status: 'BLOCKED', expected_status: 'BLOCKED', started_at: '2026-07-30T00:00:00Z',
      finished_at: '2026-07-30T00:00:00Z', elapsed_ms: 12, false_block_count: 1,
    }],
  }),
}));

vi.mock('../api/experiments', () => api);

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}><ExperimentsPage /></QueryClientProvider>);
}

describe('ExperimentsPage', () => {
  it('shows frozen false-block counts for loaded formal results', async () => {
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /formal/i }));

    expect(await screen.findByRole('columnheader', { name: 'False blocks' })).toBeInTheDocument();
    expect(screen.getByRole('cell', { name: 'False blocks for ADAPTIVE_RUNTIME' })).toHaveTextContent('1');
    expect(screen.getByRole('cell', { name: 'Expected status BLOCKED' })).toBeInTheDocument();
  });
});
