import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { NavLink, useSearchParams } from 'react-router-dom'

import { denyApproval, grantApproval, listApprovals } from '../api/approvals'
import { getRuntimeHealth } from '../api/health'
import {
  cancelTask,
  createTask,
  getTask,
  getTaskEvents,
  getTaskSteps,
  listTasks,
  subscribeToTaskEvents,
} from '../api/tasks'
import { MultiValueCombobox } from '../components/MultiValueCombobox'
import { StatusBadge } from '../components/StatusBadge'
import {
  describeAuditEvent,
  mergeAuditEvents,
  taskStatus,
} from '../lib/taskEvents'
import { useUIStore } from '../stores/ui'
import type { AuditEvent, TaskContract } from '../types/contracts'
import { localizeTool, useI18n } from '../i18n'

const safeContract: TaskContract = {
  allowed_actions: ['list_dir', 'read_file', 'memory_read'],
  allowed_resources: ['README.md'],
  forbidden_actions: [],
  max_affected_objects: 10,
  allow_egress: false,
  requires_reconfirmation: false,
}

const writingDemoObjective =
  'Use only create_file to create agent_capability.md containing exactly three bullet lines: Reads scoped files; Writes scoped files; Runtime audits every operation. After the creation commits, return a final answer and take no more tool actions.'
const writingDemoObjectiveZh =
  '仅使用 create_file 创建 agent_capability.md，文件内容必须正好包含三行项目符号：读取限定范围内的文件；写入限定范围内的文件；运行时审计每一次操作。创建提交后返回最终答复，不再执行其他工具操作。'

const writingDemoContract: TaskContract = {
  ...safeContract,
  allowed_actions: ['create_file'],
  allowed_resources: ['agent_capability.md'],
  requires_reconfirmation: false,
}

const supportedActions = [
  'list_dir',
  'read_file',
  'create_file',
  'write_file',
  'delete_file',
  'download_url',
  'memory_read',
  'memory_write',
  'send_email_dry_run',
  'run_shell',
]

const commonResources = [
  ['README.md', 'Project README', '项目 README'],
  ['agent_capability.md', 'Writing demo output', '写入示例输出文件'],
  ['demo_workspace/*', 'Entire demo workspace', '整个演示工作区'],
  ['demo_workspace/input/*', 'Demo input directory', '演示输入目录'],
  ['demo_workspace/output/*', 'Demo output directory', '演示输出目录'],
  [
    'demo_workspace/output/agent_capability.md',
    'Writing demo output',
    '写入示例输出文件',
  ],
] as const

function Detail({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="grid grid-cols-[112px_1fr] gap-3 border-b border-slate-200 py-2 text-xs">
      <span className="text-slate-500">{label}</span>
      <span className="break-words text-slate-800">
        {typeof value === 'string' ? value : JSON.stringify(value, null, 2)}
      </span>
    </div>
  )
}

export function TasksPage() {
  const { language, text } = useI18n()
  const [searchParams, setSearchParams] = useSearchParams()
  const [objective, setObjective] = useState('')
  const [advanced, setAdvanced] = useState(false)
  const [contract, setContract] = useState<TaskContract>(safeContract)
  const [eventsByTask, setEventsByTask] = useState<
    Record<string, AuditEvent[]>
  >({})
  const [finalAnswersByTask, setFinalAnswersByTask] = useState<
    Record<string, string>
  >({})
  const [usePolling, setUsePolling] = useState(false)
  const taskHistory = useUIStore((state) => state.taskHistory)
  const selectedTaskId = useUIStore((state) => state.selectedTaskId)
  const addTask = useUIStore((state) => state.addTask)
  const replaceTasks = useUIStore((state) => state.replaceTasks)
  const setSelectedTask = useUIStore((state) => state.setSelectedTask)
  const markCancelled = useUIStore((state) => state.markCancelled)
  const updateTaskStatus = useUIStore((state) => state.updateTaskStatus)

  const selectedTask = taskHistory.find(
    (task) => task.taskId === selectedTaskId,
  )
  const events = useMemo(
    () => (selectedTaskId ? (eventsByTask[selectedTaskId] ?? []) : []),
    [eventsByTask, selectedTaskId],
  )
  const status = taskStatus(events, selectedTask?.status ?? 'CREATED')

  const eventsQuery = useQuery({
    queryKey: ['task-events', selectedTaskId],
    queryFn: () => getTaskEvents(selectedTaskId!),
    enabled: Boolean(selectedTaskId),
    refetchInterval: usePolling ? 3000 : false,
  })
  const tasksQuery = useQuery({
    queryKey: ['tasks'],
    queryFn: () => listTasks(50),
    refetchInterval: 2000,
  })
  const stepsQuery = useQuery({
    queryKey: ['task-steps', selectedTaskId],
    queryFn: () => getTaskSteps(selectedTaskId!),
    enabled: Boolean(selectedTaskId),
    refetchInterval:
      selectedTaskId &&
      !['COMPLETED', 'BLOCKED', 'FAILED', 'CANCELLED', 'INTERRUPTED'].includes(
        status,
      )
        ? 2000
        : false,
  })
  const healthQuery = useQuery({
    queryKey: ['runtime-health'],
    queryFn: getRuntimeHealth,
    retry: false,
    staleTime: 30_000,
  })
  const runtimeMode =
    healthQuery.data?.mode === 'live-agent'
      ? text('Live agent', '真实智能体')
      : (healthQuery.data?.mode ?? text('Checking Runtime…', '正在检查运行时…'))
  const runtimeHealth = healthQuery.data
    ? text('Runtime health connected', '运行时服务已连接')
    : healthQuery.isError
      ? text('Runtime health unavailable', '运行时服务不可用')
      : text('Checking Runtime health…', '正在检查运行时服务…')

  useEffect(() => {
    if (!tasksQuery.data) return
    replaceTasks(
      tasksQuery.data.map((task) => ({
        taskId: task.task_id,
        objective: task.objective,
        createdAt: task.created_at,
        status: task.status,
        contract: task.contract ?? undefined,
      })),
    )
  }, [replaceTasks, tasksQuery.data])

  useEffect(() => {
    const queryTaskId = searchParams.get('task_id')?.trim()
    if (queryTaskId && queryTaskId !== selectedTaskId)
      setSelectedTask(queryTaskId)
  }, [searchParams, selectedTaskId, setSelectedTask])

  useEffect(() => {
    if (!selectedTaskId || !eventsQuery.data) return
    const incoming = eventsQuery.data.events as AuditEvent[]
    setEventsByTask((current) => ({
      ...current,
      [selectedTaskId]: mergeAuditEvents(
        current[selectedTaskId] ?? [],
        incoming,
      ),
    }))
    if (incoming.some((event) => event.event_type === 'TASK_FINISHED')) {
      void getTask(selectedTaskId).then((task) => {
        const finalAnswer = task.final_answer
        if (finalAnswer) {
          setFinalAnswersByTask((current) => ({
            ...current,
            [selectedTaskId]: finalAnswer,
          }))
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
    const stop = subscribeToTaskEvents(
      selectedTaskId,
      (event) => {
        setEventsByTask((current) => {
          const next = mergeAuditEvents(current[selectedTaskId] ?? [], [event])
          return { ...current, [selectedTaskId]: next }
        })
      },
      () => setUsePolling(true),
      (finalAnswer) => {
        setFinalAnswersByTask((current) => ({
          ...current,
          [selectedTaskId]: finalAnswer,
        }))
      },
    )
    if (!stop) setUsePolling(true)
    return stop ?? undefined
  }, [selectedTaskId, updateTaskStatus])

  const create = useMutation({
    mutationFn: () => createTask(objective.trim(), contract),
    onSuccess: (task) => {
      addTask({
        taskId: task.task_id,
        objective: task.objective,
        createdAt: task.created_at,
        status: task.status,
        contract,
      })
      setSelectedTask(task.task_id)
      setSearchParams({ task_id: task.task_id })
      setObjective('')
    },
  })
  const cancel = useMutation({
    mutationFn: () => cancelTask(selectedTaskId!),
    onSuccess: () => {
      if (selectedTaskId) markCancelled(selectedTaskId)
      void tasksQuery.refetch()
    },
  })

  const approvalsQuery = useQuery({
    queryKey: ['pending-approvals', selectedTaskId],
    queryFn: () => listApprovals(selectedTaskId!, 'PENDING'),
    enabled: Boolean(selectedTaskId) && status === 'WAITING_APPROVAL',
    refetchInterval: 1000,
  })
  const pendingApproval = approvalsQuery.data?.[0]
  const approval = useMutation({
    mutationFn: (decision: 'grant' | 'deny') =>
      decision === 'grant'
        ? grantApproval(pendingApproval!.approval_id, 'workbench-user')
        : denyApproval(pendingApproval!.approval_id, 'workbench-user'),
    onSuccess: () => {
      void eventsQuery.refetch()
      void tasksQuery.refetch()
    },
  })

  const submit = () => {
    if (objective.trim() && !create.isPending) create.mutate()
  }
  const loadWritingDemo = () => {
    setSelectedTask(null)
    setSearchParams({})
    setObjective(
      language === 'zh-CN' ? writingDemoObjectiveZh : writingDemoObjective,
    )
    setContract(writingDemoContract)
    setAdvanced(true)
  }
  const latest = events.at(-1)
  const steps = stepsQuery.data?.steps ?? []
  const finalAnswer = selectedTaskId
    ? finalAnswersByTask[selectedTaskId]
    : undefined
  const actionOptions = supportedActions.map((value) => ({
    value,
    label:
      language === 'zh-CN'
        ? `${localizeTool(value, language)} · ${value}`
        : value,
  }))
  const resourceOptions = commonResources.map(([value, english, chinese]) => ({
    value,
    label: `${text(english, chinese)} · ${value}`,
  }))

  return (
    <div className="grid min-h-[calc(100vh-2.75rem)] grid-cols-1 bg-white lg:grid-cols-[260px_minmax(0,1fr)_320px]">
      <aside className="border-b border-slate-200 p-3 lg:border-r lg:border-b-0">
        <button
          aria-label={text('New Task', '新建任务')}
          className="mb-5 w-full rounded-md bg-indigo-600 px-3 py-2 text-left text-sm font-medium text-white hover:bg-white"
          onClick={() => {
            setSelectedTask(null)
            setSearchParams({})
            setObjective('')
          }}
        >
          + {text('New Task', '新建任务')}
        </button>
        <p className="mb-2 px-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">
          {text('Navigation', '导航')}
        </p>
        <nav className="mb-6 grid gap-1 text-sm">
          <NavLink to="/tasks" className="rounded px-2 py-1.5 text-slate-800">
            {text('Task history', '任务历史')}
          </NavLink>
          <NavLink
            to="/approvals"
            className="rounded px-2 py-1.5 text-slate-500 hover:bg-slate-100"
          >
            {text('Approvals', '审批')}
          </NavLink>
          <NavLink
            to="/audit"
            className="rounded px-2 py-1.5 text-slate-500 hover:bg-slate-100"
          >
            {text('Audit', '审计')}
          </NavLink>
          <NavLink
            to="/experiments"
            className="rounded px-2 py-1.5 text-slate-500 hover:bg-slate-100"
          >
            {text('Experiments', '实验')}
          </NavLink>
          {selectedTaskId && (
            <NavLink
              to={`/runtime?task_id=${encodeURIComponent(selectedTaskId)}`}
              className="rounded px-2 py-1.5 text-indigo-600 hover:bg-slate-100"
            >
              {text('Open selected Runtime', '打开当前任务运行时')}
            </NavLink>
          )}
        </nav>
        <div className="mb-2 flex items-center justify-between px-2">
          <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">
            {text('Recent tasks', '最近任务')}
          </p>
          <span className="text-[10px] text-slate-600">
            {taskHistory.length}
          </span>
        </div>
        <div className="grid gap-1">
          {taskHistory.length === 0 ? (
            <p className="px-2 text-xs leading-5 text-slate-500">
              {text(
                'Create a task to start a safety-audited execution.',
                '创建任务以开始一次带安全审计的执行。',
              )}
            </p>
          ) : (
            taskHistory.map((task) => (
              <button
                key={task.taskId}
                onClick={() => {
                  setSelectedTask(task.taskId)
                  setSearchParams({ task_id: task.taskId })
                }}
                className={`rounded-md p-2 text-left ${task.taskId === selectedTaskId ? 'bg-slate-100' : 'hover:bg-slate-100'}`}
              >
                <p className="line-clamp-2 text-xs text-slate-800">
                  {task.objective}
                </p>
                <div className="mt-2 flex items-center justify-between gap-2">
                  <span className="text-[10px] text-slate-500">
                    {new Date(task.createdAt).toLocaleTimeString(language)}
                  </span>
                  <StatusBadge status={task.status} />
                </div>
              </button>
            ))
          )}
        </div>
        <div className="mt-6 border-t border-slate-200 px-2 pt-3 text-[11px]">
          <span className="text-slate-500">
            {text('Runtime mode', '运行模式')}
          </span>
          <p className="mt-1 text-slate-600">{runtimeMode}</p>
          <p className="mt-1 text-slate-600">{runtimeHealth}</p>
        </div>
      </aside>

      <main className="relative flex min-h-[660px] flex-col border-b border-slate-200 lg:border-r lg:border-b-0">
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3">
          <div>
            <p className="text-sm font-medium text-slate-800">
              {selectedTask
                ? selectedTask.objective
                : text('New task', '新任务')}
            </p>
            <p className="mt-1 text-xs text-slate-500">
              {selectedTaskId
                ? selectedTaskId
                : text(
                    'Describe the outcome; the runtime decides what is safe to do.',
                    '描述你想要的结果，运行时会判断哪些操作可以安全执行。',
                  )}
            </p>
          </div>
          {selectedTaskId && <StatusBadge status={status} />}
        </div>
        <section
          className="flex-1 space-y-3 overflow-y-auto px-5 py-5 pb-44"
          aria-label={text('Task execution timeline', '任务执行时间线')}
        >
          {!selectedTask ? (
            <div className="mx-auto max-w-md pt-20 text-center">
              <p className="text-lg text-slate-800">
                {text('What should the runtime do?', '你希望运行时完成什么？')}
              </p>
              <p className="mt-2 text-sm leading-6 text-slate-500">
                {text(
                  'Submit a natural-language objective. You will see plans, tool calls and safety decisions here—never hidden model reasoning.',
                  '提交一个自然语言目标。这里会展示规划、工具调用和安全决策，但不会显示模型隐藏推理。',
                )}
              </p>
            </div>
          ) : (
            <>
              <div className="rounded-lg border border-slate-200 bg-white/[0.03] p-4">
                <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">
                  {text('You', '你')}
                </p>
                <p className="mt-2 text-sm leading-6 text-slate-800">
                  {selectedTask.objective}
                </p>
              </div>
              {eventsQuery.isLoading && (
                <p className="text-xs text-slate-500">
                  {text('Loading audit history…', '正在加载审计记录…')}
                </p>
              )}
              {eventsQuery.isError && (
                <p className="text-xs text-rose-700">
                  {text('Could not load audit history', '无法加载审计记录')}：
                  {(eventsQuery.error as Error).message}
                </p>
              )}
              {events.map((event) => {
                const view = describeAuditEvent(event, language)
                const errorCode =
                  typeof event.details.error_code === 'string'
                    ? event.details.error_code
                    : null
                const reason =
                  typeof event.details.reason === 'string'
                    ? event.details.reason
                    : null
                return (
                  <details
                    key={event.sequence_number}
                    className="group rounded-lg border border-slate-200 bg-white p-3"
                  >
                    <summary className="flex cursor-pointer list-none items-center gap-3">
                      <span
                        className={`h-2 w-2 rounded-full ${view.tone === 'danger' ? 'bg-rose-400' : view.tone === 'warning' ? 'bg-amber-300' : view.tone === 'success' ? 'bg-emerald-700' : 'bg-sky-400'}`}
                      />
                      <span className="flex-1 text-sm text-slate-800">
                        {view.stage}
                      </span>
                      <span className="text-[10px] text-slate-500">
                        #{event.sequence_number}
                      </span>
                      <StatusBadge status={event.status} />
                    </summary>
                    <p className="ml-5 mt-3 text-xs leading-5 text-slate-500">
                      {view.detail}
                    </p>
                    {errorCode && (
                      <p className="ml-5 mt-2 text-xs leading-5 text-rose-700">
                        {errorCode}:{' '}
                        {reason ??
                          text(
                            'No safe reason available',
                            '没有可安全显示的原因',
                          )}
                      </p>
                    )}
                    <pre className="ml-5 mt-3 overflow-x-auto rounded bg-slate-50 p-2 text-[11px] text-slate-500">
                      {JSON.stringify(event.details, null, 2)}
                    </pre>
                  </details>
                )
              })}
              {status === 'COMPLETED' && finalAnswer && (
                <article
                  aria-label={text('Assistant response', '助手回复')}
                  className="rounded-lg border border-emerald-400/20 bg-emerald-400/5 p-4"
                >
                  <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-emerald-700">
                    {text('Assistant', '助手')}
                  </p>
                  <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-800">
                    {finalAnswer}
                  </p>
                </article>
              )}
            </>
          )}
        </section>
        {(approval.isError || approvalsQuery.isError || cancel.isError) && (
          <p role="alert" className="px-5 text-sm text-rose-700">
            {(approval.error ?? approvalsQuery.error ?? cancel.error)?.message}
          </p>
        )}
        {selectedTaskId && status === 'WAITING_APPROVAL' && (
          <div className="mx-5 mb-3 rounded-md border border-amber-400/25 bg-amber-400/8 p-3 text-xs">
            <p className="text-amber-700">
              {text(
                'Waiting for explicit approval. No success is shown until the runtime emits a committed result.',
                '正在等待明确审批。只有运行时提交结果后，界面才会显示成功。',
              )}
            </p>
            {pendingApproval ? (
              <div className="mt-2 flex gap-2">
                <button
                  disabled={approval.isPending}
                  onClick={() => approval.mutate('grant')}
                  className="rounded bg-emerald-700 px-3 py-1.5 text-white disabled:opacity-50"
                >
                  {text('Approve', '批准')}
                </button>
                <button
                  disabled={approval.isPending}
                  onClick={() => approval.mutate('deny')}
                  className="rounded border border-rose-400/40 px-3 py-1.5 text-rose-700 disabled:opacity-50"
                >
                  {text('Reject', '拒绝')}
                </button>
              </div>
            ) : (
              <p className="mt-2 text-amber-700/70">
                {text(
                  'No pending approval is available; refresh or open Approvals.',
                  '暂未取得待审批项，请刷新或前往审批页面处理。',
                )}
              </p>
            )}
          </div>
        )}
        <div className="absolute inset-x-0 bottom-0 border-t border-slate-200 bg-slate-100 p-4 backdrop-blur">
          <div className="mx-auto max-w-3xl rounded-lg border border-slate-200 bg-white p-2">
            <textarea
              value={objective}
              onChange={(event) => setObjective(event.target.value)}
              onKeyDown={(event) => {
                if (event.nativeEvent.isComposing || event.keyCode === 229)
                  return
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault()
                  submit()
                }
              }}
              placeholder={text(
                'Describe a task for the runtime…',
                '描述一个需要运行时完成的任务…',
              )}
              rows={2}
              className="w-full resize-none bg-transparent px-2 py-1 text-sm text-slate-800 outline-none placeholder:text-slate-600"
            />
            <div className="flex items-center justify-between px-2 pt-1">
              <div className="flex gap-3">
                <button
                  aria-label={text('Show safety settings', '显示安全设置')}
                  onClick={() => setAdvanced(!advanced)}
                  className="text-xs text-slate-500 hover:text-slate-600"
                >
                  {advanced
                    ? text('Hide safety settings', '隐藏安全设置')
                    : text('Show safety settings', '显示安全设置')}
                </button>
                <button
                  onClick={loadWritingDemo}
                  className="text-xs text-indigo-600 hover:text-indigo-600"
                >
                  {text('Load writing demo', '载入写入示例')}
                </button>
              </div>
              <div className="flex gap-2">
                {selectedTaskId &&
                  ![
                    'COMMITTED',
                    'COMPLETED',
                    'BLOCKED',
                    'ROLLED_BACK',
                    'FAILED',
                    'CANCELLED',
                    'INTERRUPTED',
                  ].includes(status) && (
                    <button
                      onClick={() => cancel.mutate()}
                      disabled={cancel.isPending}
                      className="rounded px-2 py-1 text-xs text-rose-700 hover:bg-rose-400/10"
                    >
                      {text('Cancel task', '取消任务')}
                    </button>
                  )}
                <button
                  onClick={submit}
                  disabled={!objective.trim() || create.isPending}
                  className="rounded bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40"
                >
                  {create.isPending
                    ? text('Creating…', '创建中…')
                    : text('Send task', '发送任务')}
                </button>
              </div>
            </div>
            {advanced && (
              <div className="mt-3 grid gap-2 border-t border-slate-200 pt-3 text-xs text-slate-500 sm:grid-cols-2">
                <MultiValueCombobox
                  label={text('Allowed actions', '允许的操作')}
                  values={contract.allowed_actions}
                  options={actionOptions}
                  placeholder={text(
                    'Search or enter an action…',
                    '搜索或输入操作…',
                  )}
                  hint={text(
                    'create_file only creates a missing file and refuses overwrite; write_file performs a general file write. Parent directories must already exist.',
                    'create_file 只能新建不存在的文件并拒绝覆盖；write_file 执行常规文件写入。两者都要求父目录已存在。',
                  )}
                  selectedLabel={text(
                    'Selected allowed actions',
                    '已选择的允许操作',
                  )}
                  addLabel={text('Add allowed action', '添加允许的操作')}
                  removeLabel={text('Remove allowed action', '删除允许的操作')}
                  onChange={(allowedActions) =>
                    setContract({
                      ...contract,
                      allowed_actions: allowedActions,
                    })
                  }
                />
                <MultiValueCombobox
                  label={text('Allowed resources', '允许的资源')}
                  values={contract.allowed_resources}
                  options={resourceOptions}
                  placeholder={text(
                    'Search or enter a path pattern…',
                    '搜索或输入路径模式…',
                  )}
                  hint={text(
                    'Exact paths and wildcard patterns are supported, for example demo_workspace/output/*.',
                    '支持精确路径和通配模式，例如 demo_workspace/output/*。',
                  )}
                  selectedLabel={text(
                    'Selected allowed resources',
                    '已选择的允许资源',
                  )}
                  addLabel={text('Add allowed resource', '添加允许的资源')}
                  removeLabel={text(
                    'Remove allowed resource',
                    '删除允许的资源',
                  )}
                  onChange={(allowedResources) =>
                    setContract({
                      ...contract,
                      allowed_resources: allowedResources,
                    })
                  }
                />
                <label>
                  {text('Maximum affected objects', '最大影响对象数')}
                  <input
                    type="number"
                    min="1"
                    value={contract.max_affected_objects}
                    onChange={(event) =>
                      setContract({
                        ...contract,
                        max_affected_objects: Number(event.target.value) || 1,
                      })
                    }
                    className="mt-1 w-full rounded border border-slate-200 bg-slate-50 p-1.5 text-slate-800"
                  />
                </label>
                <p className="rounded border border-sky-400/15 bg-sky-400/5 p-2 text-indigo-600 sm:col-span-2">
                  {text(
                    'Approval is risk-driven. Ordinary operations inside your persistent profile do not ask again.',
                    '审批由风险策略触发。长期安全配置范围内的普通操作不会重复询问。',
                  )}
                </p>
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={contract.allow_egress}
                    onChange={(event) =>
                      setContract({
                        ...contract,
                        allow_egress: event.target.checked,
                      })
                    }
                  />
                  {text(
                    'Allow egress (off by default)',
                    '允许数据外发（默认关闭）',
                  )}
                </label>
              </div>
            )}
          </div>
          {create.isError && (
            <p className="mt-2 text-center text-xs text-rose-700">
              {text('Could not create task', '无法创建任务')}：
              {(create.error as Error).message}
            </p>
          )}
        </div>
      </main>

      <aside className="bg-white p-4">
        <h2 className="text-sm font-medium text-slate-800">
          {text('Safety inspector', '安全检查器')}
        </h2>
        {!selectedTask ? (
          <p className="mt-3 text-xs leading-5 text-slate-500">
            {text(
              'Select or create a task to inspect its Runtime-owned safety facts.',
              '选择或创建任务，以查看由运行时记录的安全事实。',
            )}
          </p>
        ) : (
          <div className="mt-3">
            <Detail
              label={text('Planner model', '规划模型')}
              value={text('Not exposed by API', 'API 未公开')}
            />
            <Detail
              label={text('Task contract', '任务契约')}
              value={selectedTask.contract ?? safeContract}
            />
            <Detail
              label={text('Runtime steps', '运行步骤')}
              value={steps.map((step) => ({
                step: step.step_id,
                tool: step.tool_name,
                status: step.status,
              }))}
            />
            <Detail
              label={text('Risk level', '风险等级')}
              value={latest?.risk_level ?? text('Not classified', '尚未分类')}
            />
            <Detail
              label={text('Policy decision', '策略决定')}
              value={latest?.decision ?? text('Not decided', '尚未决定')}
            />
            <Detail
              label={text('Current tool', '当前工具')}
              value={localizeTool(
                typeof latest?.details.tool_name === 'string'
                  ? latest.details.tool_name
                  : null,
                language,
              )}
            />
            <Detail
              label={text('Tool parameters', '工具参数')}
              value={latest?.details.arguments ?? {}}
            />
            <Detail
              label={text('Allowed resources', '允许的资源')}
              value={(selectedTask.contract ?? safeContract).allowed_resources}
            />
            <Detail
              label={text('Forbidden actions', '禁止的操作')}
              value={(selectedTask.contract ?? safeContract).forbidden_actions}
            />
            <Detail
              label={text('Pending changes', '待提交变更')}
              value={latest?.details.pending_changes ?? []}
            />
            <Detail
              label={text('Artifacts', '产物')}
              value={latest?.details.artifacts ?? []}
            />
            <Detail
              label={text('Checkpoint', '检查点')}
              value={latest?.details.checkpoint_id ?? text('None', '无')}
            />
            <div className="pt-3">
              <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">
                {text('Recent audit', '最近审计')}
              </p>
              <div className="mt-2 space-y-1">
                {events
                  .slice(-5)
                  .reverse()
                  .map((event) => (
                    <p
                      key={event.sequence_number}
                      className="truncate text-xs text-slate-500"
                    >
                      #{event.sequence_number}{' '}
                      {describeAuditEvent(event, language).detail}
                    </p>
                  ))}
              </div>
            </div>
          </div>
        )}
      </aside>
    </div>
  )
}
