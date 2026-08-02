import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { NavLink } from 'react-router-dom'

import { denyApproval, grantApproval } from '../api/approvals'
import { cancelTask, createTask, getTask, getTaskEvents, subscribeToTaskEvents } from '../api/tasks'
import { StatusBadge } from '../components/StatusBadge'
import { describeAuditEvent, mergeAuditEvents, taskStatus } from '../lib/taskEvents'
import { useUIStore } from '../stores/ui'
import type { ApprovalRequest, AuditEvent, TaskContract } from '../types/contracts'

const safeContract: TaskContract = {
  allowed_actions: ['list_dir', 'read_file', 'memory_read'],
  allowed_resources: ['README.md'],
  forbidden_actions: ['send_email', 'delete_file', 'run_shell'],
  max_affected_objects: 10,
  allow_egress: false,
  requires_reconfirmation: true,
}

function Detail({ label, value }: { label: string; value: unknown }) {
  return <div className="grid grid-cols-[112px_1fr] gap-3 border-b border-white/6 py-2 text-xs"><span className="text-slate-500">{label}</span><span className="break-words text-slate-200">{typeof value === 'string' ? value : JSON.stringify(value, null, 2)}</span></div>
}

export function TasksPage() {
  const [objective, setObjective] = useState('')
  const [advanced, setAdvanced] = useState(false)
  const [contract, setContract] = useState<TaskContract>(safeContract)
  const [eventsByTask, setEventsByTask] = useState<Record<string, AuditEvent[]>>({})
  const [finalAnswersByTask, setFinalAnswersByTask] = useState<Record<string, string>>({})
  const [usePolling, setUsePolling] = useState(false)
  const taskHistory = useUIStore((state) => state.taskHistory)
  const selectedTaskId = useUIStore((state) => state.selectedTaskId)
  const addTask = useUIStore((state) => state.addTask)
  const setSelectedTask = useUIStore((state) => state.setSelectedTask)
  const markCancelled = useUIStore((state) => state.markCancelled)
  const updateTaskStatus = useUIStore((state) => state.updateTaskStatus)

  const selectedTask = taskHistory.find((task) => task.taskId === selectedTaskId)
  const events = useMemo(
    () => (selectedTaskId ? eventsByTask[selectedTaskId] ?? [] : []),
    [eventsByTask, selectedTaskId],
  )
  const status = taskStatus(events, selectedTask?.status ?? 'CREATED')

  const eventsQuery = useQuery({
    queryKey: ['task-events', selectedTaskId],
    queryFn: () => getTaskEvents(selectedTaskId!),
    enabled: Boolean(selectedTaskId),
    refetchInterval: usePolling ? 3000 : false,
  })

  useEffect(() => {
    if (!selectedTaskId || !eventsQuery.data) return
    const incoming = eventsQuery.data.events as AuditEvent[]
    setEventsByTask((current) => ({ ...current, [selectedTaskId]: mergeAuditEvents(current[selectedTaskId] ?? [], incoming) }))
    if (incoming.some((event) => event.event_type === 'TASK_FINISHED')) {
      void getTask(selectedTaskId).then((task) => {
        const finalAnswer = task.final_answer
        if (finalAnswer) {
          setFinalAnswersByTask((current) => ({ ...current, [selectedTaskId]: finalAnswer }))
        }
      })
    }
  }, [eventsQuery.data, selectedTaskId])

  useEffect(() => {
    if (selectedTaskId) updateTaskStatus(selectedTaskId, taskStatus(events))
  }, [events, selectedTaskId, updateTaskStatus])

  useEffect(() => {
    if (!selectedTaskId) return
    setUsePolling(false)
    const stop = subscribeToTaskEvents(selectedTaskId, (event) => {
      setEventsByTask((current) => {
        const next = mergeAuditEvents(current[selectedTaskId] ?? [], [event])
        return { ...current, [selectedTaskId]: next }
      })
    }, () => setUsePolling(true), (finalAnswer) => {
      setFinalAnswersByTask((current) => ({ ...current, [selectedTaskId]: finalAnswer }))
    })
    if (!stop) setUsePolling(true)
    return stop ?? undefined
  }, [selectedTaskId, updateTaskStatus])

  const create = useMutation({
    mutationFn: () => createTask(objective.trim(), contract),
    onSuccess: (task) => {
      addTask({ taskId: task.task_id, objective: task.objective, createdAt: task.created_at, status: task.status, contract })
      setSelectedTask(task.task_id)
      setObjective('')
    },
  })
  const cancel = useMutation({ mutationFn: () => cancelTask(selectedTaskId!), onSuccess: () => selectedTaskId && markCancelled(selectedTaskId) })

  const pendingApproval = useMemo(() => {
    const requested = [...events].reverse().find((event) => event.event_type === 'APPROVAL_REQUESTED')
    if (!requested) return null
    const approvalId = requested?.details.approval_id
    if (typeof approvalId !== 'string') return null
    return { approval_id: approvalId, reason: requested.summary } as Pick<ApprovalRequest, 'approval_id' | 'reason'>
  }, [events])
  const approval = useMutation({ mutationFn: (decision: 'grant' | 'deny') => decision === 'grant' ? grantApproval(pendingApproval!.approval_id, 'workbench-user') : denyApproval(pendingApproval!.approval_id, 'workbench-user') })

  const submit = () => { if (objective.trim() && !create.isPending) create.mutate() }
  const latest = events.at(-1)
  const finalAnswer = selectedTaskId ? finalAnswersByTask[selectedTaskId] : undefined

  return <div className="grid min-h-[calc(100vh-2.75rem)] grid-cols-1 bg-[#0d0f12] lg:grid-cols-[260px_minmax(0,1fr)_320px]">
    <aside className="border-b border-white/8 p-3 lg:border-r lg:border-b-0">
      <button aria-label="New Task" className="mb-5 w-full rounded-md bg-slate-100 px-3 py-2 text-left text-sm font-medium text-slate-950 hover:bg-white" onClick={() => { setSelectedTask(null); setObjective('') }}>+ New Task</button>
      <p className="mb-2 px-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">Navigation</p>
      <nav className="mb-6 grid gap-1 text-sm"><NavLink to="/" className="rounded px-2 py-1.5 text-slate-100">Tasks</NavLink><NavLink to="/approvals" className="rounded px-2 py-1.5 text-slate-400 hover:bg-white/5">Approvals</NavLink><NavLink to="/audit" className="rounded px-2 py-1.5 text-slate-400 hover:bg-white/5">Audit</NavLink><NavLink to="/experiments" className="rounded px-2 py-1.5 text-slate-400 hover:bg-white/5">Experiments</NavLink>{selectedTaskId && <NavLink to={`/runtime?task_id=${encodeURIComponent(selectedTaskId)}`} className="rounded px-2 py-1.5 text-sky-300 hover:bg-white/5">Open selected Runtime</NavLink>}</nav>
      <div className="mb-2 flex items-center justify-between px-2"><p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">Recent tasks</p><span className="text-[10px] text-slate-600">{taskHistory.length}</span></div>
      <div className="grid gap-1">{taskHistory.length === 0 ? <p className="px-2 text-xs leading-5 text-slate-500">Create a task to start a safety-audited execution.</p> : taskHistory.map((task) => <button key={task.taskId} onClick={() => setSelectedTask(task.taskId)} className={`rounded-md p-2 text-left ${task.taskId === selectedTaskId ? 'bg-white/8' : 'hover:bg-white/4'}`}><p className="line-clamp-2 text-xs text-slate-200">{task.objective}</p><div className="mt-2 flex items-center justify-between gap-2"><span className="text-[10px] text-slate-500">{new Date(task.createdAt).toLocaleTimeString()}</span><StatusBadge status={task.status} /></div></button>)}</div>
      <div className="mt-6 border-t border-white/8 px-2 pt-3 text-[11px]"><span className="text-slate-500">Runtime mode</span><p className="mt-1 text-slate-300">Server-managed</p><p className="mt-1 text-slate-600">Mode endpoint unavailable</p></div>
    </aside>

    <main className="relative flex min-h-[660px] flex-col border-b border-white/8 lg:border-r lg:border-b-0">
      <div className="flex items-center justify-between border-b border-white/8 px-5 py-3"><div><p className="text-sm font-medium text-slate-100">{selectedTask ? selectedTask.objective : 'New task'}</p><p className="mt-1 text-xs text-slate-500">{selectedTaskId ? selectedTaskId : 'Describe the outcome; the runtime decides what is safe to do.'}</p></div>{selectedTaskId && <StatusBadge status={status} />}</div>
      <section className="flex-1 space-y-3 overflow-y-auto px-5 py-5 pb-44" aria-label="Task execution timeline">
        {!selectedTask ? <div className="mx-auto max-w-md pt-20 text-center"><p className="text-lg text-slate-200">What should the runtime do?</p><p className="mt-2 text-sm leading-6 text-slate-500">Submit a natural-language objective. You will see plans, tool calls and safety decisions here—never hidden model reasoning.</p></div> : <><div className="rounded-lg border border-white/8 bg-white/[0.03] p-4"><p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">You</p><p className="mt-2 text-sm leading-6 text-slate-100">{selectedTask.objective}</p></div>{eventsQuery.isLoading && <p className="text-xs text-slate-500">Loading audit history…</p>}{eventsQuery.isError && <p className="text-xs text-rose-300">Could not load audit history: {(eventsQuery.error as Error).message}</p>}{events.map((event) => { const view = describeAuditEvent(event); const errorCode = typeof event.details.error_code === 'string' ? event.details.error_code : null; const reason = typeof event.details.reason === 'string' ? event.details.reason : null; return <details key={event.sequence_number} className="group rounded-lg border border-white/8 bg-[#111419] p-3"><summary className="flex cursor-pointer list-none items-center gap-3"><span className={`h-2 w-2 rounded-full ${view.tone === 'danger' ? 'bg-rose-400' : view.tone === 'warning' ? 'bg-amber-300' : view.tone === 'success' ? 'bg-emerald-400' : 'bg-sky-400'}`} /><span className="flex-1 text-sm text-slate-200">{view.stage}</span><span className="text-[10px] text-slate-500">#{event.sequence_number}</span><StatusBadge status={event.status} /></summary><p className="ml-5 mt-3 text-xs leading-5 text-slate-400">{view.detail}</p>{errorCode && <p className="ml-5 mt-2 text-xs leading-5 text-rose-200">{errorCode}: {reason ?? 'No safe reason available'}</p>}<pre className="ml-5 mt-3 overflow-x-auto rounded bg-black/20 p-2 text-[11px] text-slate-500">{JSON.stringify(event.details, null, 2)}</pre></details> })}{status === 'COMPLETED' && finalAnswer && <article aria-label="Assistant response" className="rounded-lg border border-emerald-400/20 bg-emerald-400/5 p-4"><p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-emerald-300">Assistant</p><p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-100">{finalAnswer}</p></article>}</>}
      </section>
      {selectedTaskId && status === 'WAITING_APPROVAL' && <div className="mx-5 mb-3 rounded-md border border-amber-400/25 bg-amber-400/8 p-3 text-xs"><p className="text-amber-100">Waiting for explicit approval. No success is shown until the runtime emits a committed result.</p>{pendingApproval ? <div className="mt-2 flex gap-2"><button disabled={approval.isPending} onClick={() => approval.mutate('grant')} className="rounded bg-emerald-400 px-3 py-1.5 text-slate-950 disabled:opacity-50">Approve</button><button disabled={approval.isPending} onClick={() => approval.mutate('deny')} className="rounded border border-rose-400/40 px-3 py-1.5 text-rose-200 disabled:opacity-50">Reject</button></div> : <p className="mt-2 text-amber-200/70">The event did not include an approval ID; use the Approvals page.</p>}</div>}
      <div className="absolute inset-x-0 bottom-0 border-t border-white/8 bg-[#0d0f12]/95 p-4 backdrop-blur"><div className="mx-auto max-w-3xl rounded-lg border border-white/10 bg-[#161a20] p-2"><textarea value={objective} onChange={(event) => setObjective(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); submit() } }} placeholder="Describe a task for the runtime…" rows={2} className="w-full resize-none bg-transparent px-2 py-1 text-sm text-slate-100 outline-none placeholder:text-slate-600" /><div className="flex items-center justify-between px-2 pt-1"><button onClick={() => setAdvanced(!advanced)} className="text-xs text-slate-500 hover:text-slate-300">{advanced ? 'Hide' : 'Show'} safety settings</button><div className="flex gap-2">{selectedTaskId && !['COMMITTED', 'COMPLETED', 'BLOCKED', 'ROLLED_BACK', 'FAILED', 'CANCELLED', 'INTERRUPTED'].includes(status) && <button onClick={() => cancel.mutate()} disabled={cancel.isPending} className="rounded px-2 py-1 text-xs text-rose-300 hover:bg-rose-400/10">Cancel task</button>}<button onClick={submit} disabled={!objective.trim() || create.isPending} className="rounded bg-slate-100 px-3 py-1.5 text-xs font-medium text-slate-950 disabled:opacity-40">{create.isPending ? 'Creating…' : 'Send task'}</button></div></div>{advanced && <div className="mt-3 grid gap-2 border-t border-white/8 pt-3 text-xs text-slate-400 sm:grid-cols-2"><label>Allowed resources<input value={contract.allowed_resources.join(', ')} onChange={(event) => setContract({ ...contract, allowed_resources: event.target.value.split(',').map((value) => value.trim()).filter(Boolean) })} className="mt-1 w-full rounded border border-white/10 bg-black/20 p-1.5 text-slate-200" /></label><label>Maximum affected objects<input type="number" min="1" value={contract.max_affected_objects} onChange={(event) => setContract({ ...contract, max_affected_objects: Number(event.target.value) || 1 })} className="mt-1 w-full rounded border border-white/10 bg-black/20 p-1.5 text-slate-200" /></label><label className="flex items-center gap-2"><input type="checkbox" checked={contract.requires_reconfirmation} onChange={(event) => setContract({ ...contract, requires_reconfirmation: event.target.checked })} />Require reconfirmation</label><label className="flex items-center gap-2"><input type="checkbox" checked={contract.allow_egress} onChange={(event) => setContract({ ...contract, allow_egress: event.target.checked })} />Allow egress (off by default)</label></div>}</div>{create.isError && <p className="mt-2 text-center text-xs text-rose-300">Could not create task: {(create.error as Error).message}</p>}</div>
    </main>

    <aside className="bg-[#101318] p-4"><h2 className="text-sm font-medium text-slate-100">Safety inspector</h2>{!selectedTask ? <p className="mt-3 text-xs leading-5 text-slate-500">Select or create a task to inspect its Runtime-owned safety facts.</p> : <div className="mt-3"><Detail label="Planner model" value="Not exposed by API" /><Detail label="Task contract" value={selectedTask.contract ?? safeContract} /><Detail label="Risk level" value={latest?.risk_level ?? 'Not classified'} /><Detail label="Policy decision" value={latest?.decision ?? 'Not decided'} /><Detail label="Current tool" value={latest?.details.tool_name ?? 'None'} /><Detail label="Tool parameters" value={latest?.details.arguments ?? {}} /><Detail label="Allowed resources" value={(selectedTask.contract ?? safeContract).allowed_resources} /><Detail label="Forbidden actions" value={(selectedTask.contract ?? safeContract).forbidden_actions} /><Detail label="Pending changes" value={latest?.details.pending_changes ?? []} /><Detail label="Artifacts" value={latest?.details.artifacts ?? []} /><Detail label="Checkpoint" value={latest?.details.checkpoint_id ?? 'None'} /><div className="pt-3"><p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">Recent audit</p><div className="mt-2 space-y-1">{events.slice(-5).reverse().map((event) => <p key={event.sequence_number} className="truncate text-xs text-slate-400">#{event.sequence_number} {event.summary}</p>)}</div></div></div>}</aside>
  </div>
}
