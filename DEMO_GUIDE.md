# Aegis Runtime 5-minute Demo

## Before recording

Start the backend in `live-agent` Runtime mode with `ENABLE_DEMO_FIXTURES=true`. The
demo fixture never uses `.env`; it is recreated under the configured Runtime workspace
as `demo_workspace/`.

Open **TaskGraph Runtime** (`/runtime`) and click **Reset demo workspace**. This restores:

- `docs/project_brief.md` and `docs/security_notes.md`
- `protected/legacy_config.json`
- an empty `output/` directory
- `recovery/source.txt`, `recovery/derived.txt`, and `recovery/independent.txt`

Every scenario button also resets this workspace before submitting its real TaskGraph.

## Normal Conversation Agent write

Open **Security settings** once. Enable `create_file`, keep the resource scope as `**`
(the isolated Aegis Workspace), and save a new SecurityProfile version. There is no
per-call reconfirmation checkbox: ordinary authorized writes proceed directly, while
Runtime risk policy creates a real Approval only when required.

Return to **Agent**, create one Conversation, and submit:

> Use only `create_file` to create `agent_capability.md` containing exactly three bullet
> lines: Reads scoped files; Writes scoped files; Runtime audits every operation. After
> the creation commits, return a final answer and take no more tool actions.

Expected: the timeline records `CHECKPOINT_CREATED`, sandbox execution, deep check and
`COMMIT_FINISHED`, then ends at `COMPLETED`. Open the actual file at
`.runtime/workspace/agent_capability.md` to show its three lines. This proves the normal
natural-language Conversation Agent uses the same Runtime rather than a Demo UI shortcut.
Then send `Read the file you just created and summarize it.` in the same Conversation.
The second message creates a new auditable TaskRun but receives the prior user/Agent
context and SecurityProfile automatically. Repeating the exact creation is expected to
refuse overwrite; remove the file or choose a different target for another successful
creation demonstration.

Do not phrase a write objective as `do not modify ...`: that is intentionally treated as
a read-only instruction and the Runtime blocks the conflicting write. The Task contract,
not the prose alone, scopes the permitted file and tool.

## A — Runtime boundary

Click **A · Runtime boundary**. Select `D1` in the graph and inspect the right panel:
`delete_file`, the inferred delete Effect Target, `HIGH` risk, the Runtime decision,
approval state, and execution state all come from the server-side Runtime and Audit.

Expected: `D1` is `WAITING_APPROVAL` (or `BLOCKED` if configured policy blocks it), and
`protected/legacy_config.json` remains present until an approval is granted. Use the
**Approvals** and **Audit** links to show the same request and decision chain.

If it is not waiting or blocked, verify that the backend is running in live Runtime mode
and that the Demo fixture switch is enabled.

## B — Approval-aware scheduling

Click **B · Scheduling** and wait for the automatic refresh. The key frame is:

- `N3` — `WAITING_APPROVAL`
- `N4` and `N5` — `COMMITTED`
- `N6` — `BLOCKED`, because it depends on `N3`

Click `N3` to show its target, risk, decision and approval state. The same right-side
**Safety inspector** now shows the pending request, approver identity, and **Grant approval**
button. Grant it there; the graph refreshes in place to show `N3` and then `N6` committed.
The optional **Conflict** check uses a real exact read/write target
conflict; the dashed conflict fact identifies why the conflicting node was serialized.

If `N4`/`N5` do not complete, refresh once and inspect Audit for a Runtime failure. If
`N6` runs before `N3` is granted, stop recording: that is a dependency violation.

## C — Selective recovery

Click **C · Selective recovery** and wait for `R1`, `R2`, and `R3` to commit. In the
**Recovery / Effect lineage** panel, select the real Runtime effect(s) to use as recovery
roots, then click **Preview selected rollback**. The preview is the actual lineage plan:
selecting `source.txt` includes `derived.txt`; selecting `derived.txt` does not include
`source.txt`. `independent.txt` remains outside either closure.

For the main recording, select `source.txt`, verify the preview says two affected effects,
then click **Execute selected rollback**.

Expected: the closure contains `source.txt` and `derived.txt`, both display
`ROLLED_BACK`, and `independent.txt` displays `PRESERVED`. The Audit link records cancel,
rollback plan/start/finish, and preservation.

For the user-modification case, start C again, select `source.txt`, preview it, then click
**User change, then execute**.
The UI writes `derived.txt = user-change` as a user action before invoking the same real
rollback mechanism. Expected: `derived.txt` is `CONFLICT`, not a successful rollback;
its user content remains untouched.

If the preview does not have the expected closure, verify the selected file and its
parent/child label. If recovery is unavailable, make sure the backend has live effect,
checkpoint and rollback services. If a normal recovery has a conflict, reset the
workspace and rerun.

## Final evidence

Open **Experiments**. The Final evidence summary is read-only and calculated from the
frozen `experiments/v2/results/final-evidence` raw/derived files. It distinguishes
`NO_PROPOSAL` from conditional Runtime containment and presents the Final boundary,
scheduling and recovery values without client-side constants.
