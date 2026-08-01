import { useQueries, useQuery } from '@tanstack/react-query';

import { getResult, listResults } from '../api/experiments';
import type { ExperimentResult } from '../types/experiments';

type FormalFamily = 'Safety' | 'Graph' | 'Rollback';

const families: Array<{ label: FormalFamily; matches: (name: string) => boolean }> = [
  { label: 'Safety', matches: (name) => name.includes('v2-safety') && name.endsWith('.jsonl') },
  { label: 'Graph', matches: (name) => name.includes('v2-graph') && name.endsWith('.jsonl') },
  { label: 'Rollback', matches: (name) => name === 'rollback_benchmark.jsonl' },
];

function sum(rows: ExperimentResult[], field: keyof ExperimentResult) {
  return rows.reduce((total, row) => total + Number(row[field] ?? 0), 0);
}

function average(rows: ExperimentResult[], field: keyof ExperimentResult) {
  return rows.length ? Math.round(sum(rows, field) / rows.length) : 0;
}

function metricSum(rows: ExperimentResult[], name: string) {
  return rows.reduce((total, row) => total + Number(row.metrics?.[name] ?? row[`metrics_${name}` as keyof ExperimentResult] ?? 0), 0);
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="rounded border border-gray-800 bg-gray-900 p-3"><p className="text-xs text-gray-500">{label}</p><p className="mt-1 font-mono text-lg text-gray-100">{value}</p></div>;
}

function Dashboard({ family, rows }: { family: FormalFamily; rows: ExperimentResult[] }) {
  const heading = `${family} formal dashboard`;
  if (family === 'Safety') {
    const unsafeBlockedRate = rows.length ? `${((sum(rows, 'blocked_count') / rows.length) * 100).toFixed(1)}%` : '—';
    return <section className="mt-0"><h2 className="mb-2 text-sm font-medium text-gray-300">{heading}</h2><div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3"><Metric label="Formal rows" value={rows.length} /><Metric label="Unsafe block rate" value={unsafeBlockedRate} /><Metric label="Unsafe requests reaching boundary" value={sum(rows, 'unsafe_tool_executed_count')} /><Metric label="Approval requests" value={sum(rows, 'approval_requested_count')} /><Metric label="Manual actions" value={sum(rows, 'manual_action_count')} /><Metric label="False blocks" value={sum(rows, 'false_block_count')} /><Metric label="Risk escalation" value={sum(rows, 'risk_escalation_count')} /></div></section>;
  }
  if (family === 'Graph') {
    return <section className="mt-0"><h2 className="mb-2 text-sm font-medium text-gray-300">{heading}</h2><div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3"><Metric label="Formal rows" value={rows.length} /><Metric label="Mean graph elapsed (ms)" value={average(rows, 'graph_elapsed_ms')} /><Metric label="Mean parallel saved (ms)" value={average(rows, 'parallel_saved_ms')} /><Metric label="Max observed concurrency" value={Math.max(0, ...rows.map((row) => Number(row.metrics?.max_observed_concurrency ?? 0)))} /><Metric label="Mean approval wait (ms)" value={average(rows, 'approval_wait_ms')} /><Metric label="Nodes completed during approval" value={metricSum(rows, 'nodes_completed_during_approval')} /></div></section>;
  }
  return <section className="mt-0"><h2 className="mb-2 text-sm font-medium text-gray-300">{heading}</h2><div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3"><Metric label="Formal rows" value={rows.length} /><Metric label="Rollback success rate" value={rows.length ? `${(((rows.length - sum(rows, 'residual_effect_count')) / rows.length) * 100).toFixed(1)}%` : '—'} /><Metric label="Mean rollback elapsed (ms)" value={average(rows, 'rollback_elapsed_ms')} /><Metric label="Rolled-back effects" value={metricSum(rows, 'rolled_back_effect_count')} /><Metric label="Preserved effects" value={metricSum(rows, 'preserved_effect_count')} /><Metric label="Residual effects" value={sum(rows, 'residual_effect_count')} /><Metric label="Selective rollback count" value={sum(rows, 'selective_rollback_count')} /></div></section>;
}

export function ExperimentsPage() {
  const filesQuery = useQuery({ queryKey: ['experiment-results'], queryFn: listResults });
  const formalFiles = families.map(({ matches }) => filesQuery.data?.find((file) => matches(file.name))?.name ?? null);
  const resultQueries = useQueries({
    queries: formalFiles.map((filename, index) => ({ queryKey: ['formal-experiment-result', index, filename], queryFn: () => getResult(filename!), enabled: filename !== null })),
  });

  return <div><div className="mb-4"><h1 className="text-xl font-semibold">Experiment dashboard</h1><p className="mt-1 text-sm text-gray-500">Read-only aggregation of committed formal raw JSONL; no experiment figure is embedded in the client.</p></div>{filesQuery.isLoading && <p className="text-sm text-gray-500">Loading formal experiment index…</p>}{filesQuery.isError && <p className="rounded border border-red-900 bg-red-950/30 p-3 text-sm text-red-300">Could not load experiment index: {(filesQuery.error as Error).message}</p>}<div className="space-y-6">{families.map((family, index) => { const query = resultQueries[index]; const rows = query.data?.results ?? []; return <div key={family.label}>{!formalFiles[index] ? <section className="mt-0 rounded border border-gray-800 bg-gray-900 p-4"><h2 className="text-sm font-medium text-gray-300">{family.label} formal dashboard</h2><p className="mt-2 text-sm text-gray-500">Formal raw JSONL is not available through the read-only experiment API.</p></section> : query.isLoading ? <section className="mt-0 rounded border border-gray-800 bg-gray-900 p-4 text-sm text-gray-500">Loading {family.label} formal rows…</section> : query.isError ? <section className="mt-0 rounded border border-red-900 bg-red-950/30 p-4 text-sm text-red-300">Could not load {family.label} formal rows.</section> : <Dashboard family={family.label} rows={rows} />}</div>})}</div></div>;
}
