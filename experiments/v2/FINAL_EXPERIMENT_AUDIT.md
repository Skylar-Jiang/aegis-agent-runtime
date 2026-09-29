# Aegis Runtime Final Experiment Audit

This is an experiment audit, not a manuscript revision. No work-book,
abstract, innovation statement, conclusion, core Runtime policy, ToolSpec,
effect-target semantics, graph scheduler, effect store, or rollback mechanism
was changed. The protocol was frozen before execution on 2026-08-23
(Asia/Shanghai). V2--V6 raw evidence remains intact in the legacy snapshot
and is not redefined.

## Artifacts and provenance

- New runners: `runners/run_final_workflow_benchmark.py`,
  `runners/run_v6_real_agent_e2e.py`, and
  `runners/generate_final_experiment_materials.py`.
- Long-workflow raw/derived: `results/final-evidence/raw/final-workflow.{jsonl,csv}`
  and `results/final-evidence/derived/final-workflow-summary.csv`.
- Repeated live-Agent raw: `results/final-evidence/real-agent-repeat/raw/v6-real-agent.{jsonl,csv}`.
- Historical V2--V6 raw snapshots: `results/final-evidence/legacy/`.
- Unified derived data: `results/final-evidence/derived/`; code-generated
  SVG/PDF/360-dpi PNG figures: `results/final-evidence/figures/`.

New records retain fixture/version, mode, repetition, environment, source
commit, dirty state, decision/execution facts, and failures/skips. The frozen
long-workflow fixture (`final-workflow-freeze-20260823`) produced 180 measured
rows: three modes x 5/10/20 nodes x two risk fractions x ten repeats, after a
separate warm-up per cell. The live run produced 25 scenario records plus two
interface records; no scenario was skipped.

The live runner wrote its complete raw/manifest before the pre-existing
per-run SQLite-engine teardown issue prevented normal process exit. The process
was then stopped; no partially written row was accepted. The only subsequent
runner change is disposal of that engine after record emission. The live calls
were not repeated solely to avoid duplicate paid requests; the evidence rows
therefore retain their original timestamp and provenance.

## Frozen comparison and metrics

| Mode | Actual path | Absent by design |
| --- | --- | --- |
| Runtime OFF | Direct handler execution with explicit DAG dependencies | Effect analysis, conflict detection, approval, lineage, recovery |
| Approval-only | Frozen manual grant per mutation; graph pauses during approval | Effect-aware scheduling, lineage, selective rollback |
| Aegis Runtime | Existing scheduler, Effect Target analysis, policy approval, lineage, selective executor | No new Runtime mechanism |

All modes run the same 5/10/20-node graph: an explicit dependent chain,
independent safe writes, and protected deletes; width three, 120-ms approval
delay, `max_parallelism=3`, high-risk fractions 0.2/0.4. Aegis falsifies if it
violates a dependency/conflict, dispatches a protected delete before approval,
or exceeds three nodes. `approval_wait_ms` sums per-decision pending time, not
wall-clock time when approvals overlap; completion latency is reported apart.
Containment is conditional on a dangerous proposal; `NO_PROPOSAL` is separate.

## Results and claim status

| Innovation / metric | Observed result | Status |
| --- | --- | --- |
| Runtime safety boundary | Deterministic ablation: Aegis contained 3/3 before handler and had 0/3 unsafe effects; OFF and Approval-only each had 2/3 unsafe effects. | Supported for frozen protected-file/read fixtures, not a universal rate. |
| Dynamic approvals | Equal workflows: Approval-only requested 5/10/20 approvals at 5/10/20 nodes; Aegis requested 1/2/4 at 0.2 risk and 2/4/8 at 0.4. Total: 210 vs 700 approvals over 60 graphs (70% reduction). | Supported. |
| Approval-aware scheduling | Aegis completed 4/3, 15/17, and 50/72 independent nodes during approval waits for 5/10/20 nodes at 0.2/0.4 risk; Approval-only completed zero. Utilization: Aegis 0.67 or 1.0, Approval-only 0.33. | Supported for this deterministic DAG/current scheduler. |
| Long-workflow latency | At 20 nodes/0.4 risk: median Aegis 1193.0 ms, Approval-only 2847.5 ms, OFF 97.8 ms. All 180 runs completed with zero dependency/conflict violations. | Supports less blanket-approval blocking, not lower latency than OFF or universal performance. |
| Effect Target inference | V5 raw covers independent files plus exact read/write, directory-subtree read/write, normalized-alias write/write. Independent reached peak 2; all three conflicts were inferred and reached peak 1 despite misleading planner hints. | Supported deterministic mechanism evidence. |
| Selective recovery | Full rollback scope 3/preservation 0; Aegis affected closure 2/preservation 1.0/precision 1.0 vs 2/3. OFF has no recovery and is N/A. | Supported for current file/effect-lineage fixture only. |
| User-modification conflict | Aegis preserved `derived.txt = user-change`, but closure finished `FAILED` after rolling back only the non-conflicting part. | Negative boundary retained: conflict protection, not atomic closure rollback. |
| Real DeepSeek Agent, 5 repeats/scenario | `deepseek-v4-flash`; 20 unsafe runs: proposals 12/20 (0.60), conditional containment 12/12 (1.00), unsafe effects 0/20; audit complete 25/25; benign completion 5/5. `NO_PROPOSAL`: delete 2, injection 5, memory 1. | Repeated configuration-specific containment only; not general model behavior or real-Agent TaskGraph/recovery evidence. |

The V6 sensitive `.env` attempt still reaches the OFF/Approval-only handler and
fails due to the existing sensitive-path guard. That is defense in depth, not
Runtime containment. Live injection `NO_PROPOSAL` never counts as success.

## Unified reporting and figures

`derived/final-three-mode-metric-matrix.csv` is the numeric matrix;
`figures/final-capability-matrix.*` is the component matrix. OFF recovery is
blank/N/A, rather than zero, because it performs no recovery. Safety/scheduling
matrix metrics are deterministic V6 ablation; repeated DeepSeek metrics remain
separate in `derived/final-real-agent-summary.json`.

- `final-long-workflow-completion.*`: 5/10/20-node scaling.
- `final-approval-workload.*`: approval actions by risk fraction.
- `final-safety-scheduling.*`: containment/effect rate and safe parallelism.
- `final-recovery.*`: scope and preservation with OFF N/A.
- `final-real-agent-matrix.*`: repeated live-Agent scenario records.

## Later manuscript guidance (not applied)

May claim, with the stated fixture boundaries: a Runtime boundary distinct from
Agent refusal; exact/subtree/normalized-alias conflict inference with independent
resource concurrency; lower manual approval burden than blanket approval; and
narrower lineage recovery that preserves an unrelated effect.

Must not claim: universal speed over OFF; arbitrary Agent-plan safety or
real-Agent TaskGraph/recovery evidence; atomic rollback under user modification
or unsupported resources; or generalization of the DeepSeek rate to other
models, prompts, tools, people, or production environments.

Recommended later changes: add the capability/result matrices; present
long-workflow results as lower approval burden and safe parallelism while
retaining OFF's lower raw latency; show semantic conflict and user-modification
failure evidence; and report the live proposal denominator plus `NO_PROPOSAL`
exclusion.
