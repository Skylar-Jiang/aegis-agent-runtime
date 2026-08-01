import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { listCases, listResults, getResult } from '../api/experiments';
import { StatusBadge } from '../components/StatusBadge';
import type { ExperimentResult, ModeStats } from '../types/experiments';

const MODES = ['BASELINE', 'FULL_GUARD', 'ADAPTIVE_RUNTIME'] as const;
const MODE_COLORS: Record<string, string> = {
  BASELINE: 'text-yellow-400',
  FULL_GUARD: 'text-red-400',
  ADAPTIVE_RUNTIME: 'text-green-400',
};

export function ExperimentsPage() {
  const [selectedFile, setSelectedFile] = useState<string | null>(null);

  const casesQuery = useQuery({
    queryKey: ['experiment-cases'],
    queryFn: listCases,
  });

  const filesQuery = useQuery({
    queryKey: ['experiment-results'],
    queryFn: listResults,
  });

  const resultQuery = useQuery({
    queryKey: ['experiment-result', selectedFile],
    queryFn: () => getResult(selectedFile!),
    enabled: selectedFile !== null,
  });

  const results: ExperimentResult[] = resultQuery.data?.results ?? [];

  const modeStats: Record<string, ModeStats> = {};
  for (const r of results) {
    const m = r.mode || '';
    if (!modeStats[m]) {
      modeStats[m] = {
        total: 0, errors: 0, blocked: 0, false_blocks: 0,
        success_rate: '0%', block_rate: '0%', avg_elapsed_ms: 0,
      };
    }
    const s = modeStats[m];
    s.total++;
    if (r.error) s.errors++;
    if (r.status === 'BLOCKED') s.blocked++;
    s.false_blocks += Number(r.false_block_count ?? 0);
    s.avg_elapsed_ms = Math.round(
      ((s.avg_elapsed_ms * (s.total - 1)) + (r.elapsed_ms || 0)) / s.total,
    );
    s.success_rate = ((s.total - s.errors) / s.total * 100).toFixed(1) + '%';
    s.block_rate = (s.blocked / s.total * 100).toFixed(1) + '%';
  }

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold">Experiments</h1>

      <section className="mb-6">
        <h2 className="mb-2 text-sm font-medium text-gray-400">Test Cases</h2>
        {casesQuery.isLoading ? (
          <p className="text-sm text-gray-500">Loading cases...</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {(casesQuery.data ?? []).map((c) => (
              <div
                key={c.case_id}
                className="rounded border border-gray-700 bg-gray-900 px-3 py-2 text-xs"
              >
                <div className="font-mono text-blue-400">{c.case_id}</div>
                <div className="mt-0.5 text-gray-400">{c.description}</div>
                <div className="mt-0.5 text-gray-600">
                  {c.tool_name} &rarr; {c.expected_decision}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="mb-6">
        <h2 className="mb-2 text-sm font-medium text-gray-400">Experiment Results</h2>
        {filesQuery.isLoading ? (
          <p className="text-sm text-gray-500">Loading...</p>
        ) : (filesQuery.data ?? []).length === 0 ? (
          <div className="rounded border border-gray-800 bg-gray-900 p-4 text-sm text-gray-500">
            No results yet. Run an experiment to generate results:
            <pre className="mt-2 rounded bg-gray-950 p-2 text-xs text-gray-400">
              {'py -3.11 -m uv run --project backend python'}<br />
              {'  experiments/runners/run_experiment.py --mode all'}
            </pre>
          </div>
        ) : (
          <div className="flex flex-wrap gap-2">
            {(filesQuery.data ?? []).map((f) => (
              <button
                key={f.name}
                className={`rounded border px-3 py-2 text-left text-xs ${
                  selectedFile === f.name
                    ? 'border-blue-500 bg-blue-900/30 text-blue-300'
                    : 'border-gray-700 bg-gray-900 text-gray-400 hover:border-gray-600'
                }`}
                onClick={() => setSelectedFile(f.name)}
              >
                <div className="font-mono">{f.name.replace('.json', '')}</div>
                <div className="mt-0.5 text-gray-600">
                  {(f.size / 1024).toFixed(1)} KB
                </div>
              </button>
            ))}
          </div>
        )}
      </section>

      {Object.keys(modeStats).length > 0 && (
        <section className="mb-6">
          <h2 className="mb-2 text-sm font-medium text-gray-400">Mode Comparison</h2>
          <div className="overflow-x-auto rounded border border-gray-800">
            <table className="w-full text-xs text-gray-400">
              <thead className="bg-gray-900 text-left text-gray-500">
                <tr>
                  <th className="p-2">Mode</th>
                  <th className="p-2">Total</th>
                  <th className="p-2">Errors</th>
                  <th className="p-2">Blocked</th>
                  <th className="p-2">False blocks</th>
                  <th className="p-2">Success Rate</th>
                  <th className="p-2">Block Rate</th>
                  <th className="p-2">Avg Time (ms)</th>
                </tr>
              </thead>
              <tbody>
                {MODES.filter((m) => modeStats[m]).map((mode) => {
                  const s = modeStats[mode]!;
                  return (
                    <tr key={mode} className="border-t border-gray-800 hover:bg-gray-900/50">
                      <td className={`p-2 font-medium ${MODE_COLORS[mode] || ''}`}>
                        {mode}
                      </td>
                      <td className="p-2">{s.total}</td>
                      <td className="p-2 text-red-400">{s.errors || '-'}</td>
                      <td className="p-2 text-orange-400">{s.blocked || '-'}</td>
                      <td aria-label={`False blocks for ${mode}`} className="p-2 text-orange-400">
                        {s.false_blocks || '-'}
                      </td>
                      <td className="p-2 text-green-400">{s.success_rate}</td>
                      <td className="p-2">{s.block_rate}</td>
                      <td className="p-2">{s.avg_elapsed_ms}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {results.length > 0 && (
        <section>
          <h2 className="mb-2 text-sm font-medium text-gray-400">Case Details</h2>
          <div className="max-h-96 overflow-y-auto rounded border border-gray-800">
            <table className="w-full text-xs text-gray-400">
              <thead className="sticky top-0 bg-gray-900 text-left text-gray-500">
                <tr>
                  <th className="p-2">Case</th>
                  <th className="p-2">Mode</th>
                  <th className="p-2">Status</th>
                  <th className="p-2">Expected</th>
                  <th className="p-2">Time (ms)</th>
                  <th className="p-2">Error</th>
                </tr>
              </thead>
              <tbody>
                {results.map((r, i) => (
                  <tr key={`${r.case_id}-${r.mode}-${i}`} className="border-t border-gray-800 hover:bg-gray-900/50">
                    <td className="p-2 font-mono text-blue-400">{r.case_id}</td>
                    <td className={`p-2 ${MODE_COLORS[r.mode] || ''}`}>{r.mode}</td>
                    <td className="p-2"><StatusBadge status={r.status} /></td>
                    <td aria-label={`Expected status ${r.expected_status}`} className="p-2 text-gray-600">
                      {r.expected_status}
                    </td>
                    <td className="p-2">{r.elapsed_ms}</td>
                    <td className="p-2 text-red-400 max-w-48 truncate">{r.error || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
