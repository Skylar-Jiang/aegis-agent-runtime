import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ExperimentsPage } from './ExperimentsPage'

const api = vi.hoisted(() => ({
  listCases: vi.fn().mockResolvedValue([]),
  listResults: vi.fn().mockResolvedValue([
    { name: 'v2-safety-formal.jsonl', size: 1024, modified: 0 },
    { name: 'v2-graph-formal.jsonl', size: 1024, modified: 0 },
    { name: 'rollback_benchmark.jsonl', size: 1024, modified: 0 },
  ]),
  getResult: vi.fn((filename: string) =>
    Promise.resolve({
      filename,
      results: [
        {
          case_id: 'S1',
          mode: 'ADAPTIVE_RUNTIME',
          task_id: 'task-1',
          request_id: 'request-1',
          status: 'BLOCKED',
          expected_status: 'BLOCKED',
          started_at: '2026-07-30T00:00:00Z',
          finished_at: '2026-07-30T00:00:00Z',
          elapsed_ms: 12,
          false_block_count: 1,
          unsafe_tool_executed_count: 0,
          approval_requested_count: 1,
          manual_action_count: 1,
          risk_escalation_count: 1,
          graph_elapsed_ms: 12,
          parallel_saved_ms: 3,
          approval_wait_ms: 4,
          metrics: {
            max_observed_concurrency: 2,
            nodes_completed_during_approval: 1,
          },
          rollback_elapsed_ms: 5,
          rollback_count: 1,
          selective_rollback_count: 1,
          residual_effect_count: 0,
          metrics_rolled_back_effect_count: 1,
          metrics_preserved_effect_count: 1,
        },
      ],
    }),
  ),
  getFinalEvidence: vi.fn().mockResolvedValue({
    runtime_boundary: {
      contained_proposals: 12,
      proposals: 12,
      unsafe_runs: 20,
      unsafe_effects: 0,
    },
    scheduling: {
      approval_only: 700,
      adaptive_runtime: 210,
      dependency_conflict_violations: 0,
    },
    recovery: {
      approval_only_scope: 3,
      aegis_scope: 2,
      preservation_rate: 1,
      preservation_precision: 1,
    },
  }),
  getCoreExperiments: vi.fn().mockResolvedValue({
    recorded: true,
    runs: [
      {
        id: 'benchmark',
        title: 'Core benchmark',
        source: 'docs/evidence/core-completion/benchmark.json',
        recorded_at: '2026-09-23T12:45:42Z',
        sha256: 'abc123',
        data: {
          status: 'complete',
          parameters: { samples: 25, history_sizes: [100, 1000] },
          measurements: [
            {
              metric: 'sm2_sign',
              history_events: 100,
              samples: 25,
              p50_ms: 4.5,
              p95_ms: 6.2,
            },
          ],
        },
      },
      {
        id: 'ablations',
        title: 'Core ablations',
        source: 'docs/evidence/core-completion/ablations.json',
        recorded_at: '2026-09-23T12:55:46Z',
        sha256: 'def456',
        data: {
          cases: [
            {
              mode: 'full',
              scenario: 'dangerous_write',
              final_status: 'BLOCKED',
              actual_writes: 0,
              expected_writes: 0,
              tampered_evidence_rejected: true,
            },
          ],
        },
      },
    ],
    unavailable: [],
  }),
}))

vi.mock('../api/experiments', () => api)
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <ExperimentsPage />
    </QueryClientProvider>,
  )
}

describe('ExperimentsPage', () => {
  it('shows recorded ablation outcomes, not only a case count', async () => {
    renderPage()
    expect(await screen.findByText('dangerous_write')).toBeInTheDocument()
    expect(screen.getByText('BLOCKED')).toBeInTheDocument()
    expect(screen.getByText('0 / 0')).toBeInTheDocument()
  })
  it('shows historical Core measurements with time, configuration and source provenance', async () => {
    renderPage()
    expect(
      await screen.findByRole('heading', {
        name: 'Core cryptography · recorded runs',
      }),
    ).toBeInTheDocument()
    expect(await screen.findByText('sm2_sign')).toBeInTheDocument()
    expect(screen.getByText('4.5 ms')).toBeInTheDocument()
    expect(screen.getByText(/samples.*25/)).toBeInTheDocument()
    expect(
      screen.getByText(/docs\/evidence\/core-completion\/benchmark\.json/),
    ).toBeInTheDocument()
    expect(screen.getByText('2026-09-23T12:45:42Z')).toBeInTheDocument()
    expect(screen.getByText(/abc123/)).toBeInTheDocument()
  })
  it('builds all three dashboard sections from the formal raw result files', async () => {
    renderPage()

    expect(
      await screen.findByRole('heading', { name: 'Safety formal dashboard' }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('heading', { name: 'Graph formal dashboard' }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('heading', { name: 'Rollback formal dashboard' }),
    ).toBeInTheDocument()
    await waitFor(() =>
      expect(screen.getByText('False blocks')).toBeInTheDocument(),
    )
    expect(api.getResult).toHaveBeenCalledTimes(3)
  })

  it('shows final evidence values supplied by the read-only final evidence API', async () => {
    renderPage()

    expect(
      await screen.findByRole('heading', { name: 'Final evidence summary' }),
    ).toBeInTheDocument()
    expect(
      screen.getByText('12/12 conditional containment'),
    ).toBeInTheDocument()
    expect(
      screen.getByText('0/20 unsafe effects in live dangerous runs'),
    ).toBeInTheDocument()
    expect(screen.getByText('700 → 210 approvals')).toBeInTheDocument()
    expect(screen.getByText('70% approval reduction')).toBeInTheDocument()
    expect(screen.getByText('rollback scope 3 → 2')).toBeInTheDocument()

    // 等待页面内其余 React Query 请求完成，避免测试结束后继续更新 React。
    expect(await screen.findByText('False blocks')).toBeInTheDocument()
  })
})

it('counts rollback runs with zero residual effects rather than subtracting effects from runs', async () => {
  const base = (await api.getResult('fixture')).results[0]
  api.getResult.mockImplementationOnce(async (filename) => ({
    filename,
    results: [],
  }))
  api.getResult.mockImplementationOnce(async (filename) => ({
    filename,
    results: [],
  }))
  api.getResult.mockImplementationOnce(async (filename) => ({
    filename,
    results: [
      { ...base, residual_effect_count: 4 },
      { ...base, residual_effect_count: 0 },
    ],
  }))
  renderPage()
  const label = await screen.findByText('Rollback success rate')
  expect(within(label.parentElement!).getByText('50.0%')).toBeInTheDocument()
})
