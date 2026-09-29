import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'

import { denyApproval, grantApproval, listApprovals } from '../api/approvals'
import { getRuntimeHealth } from '../api/health'
import { getTask, getTaskEvents } from '../api/tasks'
import {
  createConversation,
  getConversation,
  getSecurityProfile,
  listConversations,
  sendConversationMessage,
} from '../api/workbench'
import { StatusBadge } from '../components/StatusBadge'
import { useI18n } from '../i18n'

const terminal = new Set([
  'COMPLETED',
  'BLOCKED',
  'FAILED',
  'CANCELLED',
  'INTERRUPTED',
])

export function ConversationsPage() {
  const { text } = useI18n()
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const [draft, setDraft] = useState('')
  const [activeTask, setActiveTask] = useState<{
    conversationId: string
    taskId: string
  } | null>(null)
  const selectedId = params.get('conversation_id')
  const selectedIdRef = useRef(selectedId)
  selectedIdRef.current = selectedId
  const sendingConversationId = useRef<string | null>(null)
  const creatingFromConversationId = useRef<string | null>(null)
  const activeTaskId =
    activeTask?.conversationId === selectedId ? activeTask.taskId : null
  const conversations = useQuery({
    queryKey: ['conversations'],
    queryFn: () => listConversations(),
    refetchInterval: 2500,
  })
  const conversation = useQuery({
    queryKey: ['conversation', selectedId],
    queryFn: () => getConversation(selectedId!),
    enabled: Boolean(selectedId),
    refetchInterval: activeTaskId ? 1200 : false,
  })
  const profile = useQuery({
    queryKey: ['security-profile', 'default'],
    queryFn: () => getSecurityProfile(),
  })
  const health = useQuery({
    queryKey: ['runtime-health'],
    queryFn: getRuntimeHealth,
    retry: false,
  })
  const task = useQuery({
    queryKey: ['task', activeTaskId],
    queryFn: () => getTask(activeTaskId!),
    enabled: Boolean(activeTaskId),
    refetchInterval: (query) =>
      terminal.has(String(query.state.data?.status)) ? false : 1000,
  })
  const events = useQuery({
    queryKey: ['task-events', activeTaskId],
    queryFn: () => getTaskEvents(activeTaskId!),
    enabled: Boolean(activeTaskId),
    refetchInterval: 1000,
  })
  const pendingApprovals = useQuery({
    queryKey: ['pending-approvals', activeTaskId],
    queryFn: () => listApprovals(activeTaskId!, 'PENDING'),
    enabled: Boolean(activeTaskId) && task.data?.status === 'WAITING_APPROVAL',
    refetchInterval: 1000,
  })

  useEffect(() => {
    if (!selectedId && conversations.data?.length)
      setParams({ conversation_id: conversations.data[0].conversation_id })
  }, [conversations.data, selectedId, setParams])
  useEffect(() => {
    if (task.data && terminal.has(task.data.status)) {
      void queryClient.invalidateQueries({
        queryKey: ['conversation', selectedId],
      })
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
    }
  }, [queryClient, selectedId, task.data])
  useEffect(() => {
    if (
      activeTaskId ||
      conversation.data?.conversation_id !== selectedId ||
      !conversation.data?.messages.length
    )
      return
    const latestWithTask = [...conversation.data.messages]
      .reverse()
      .find((message) => message.task_id)
    if (latestWithTask?.task_id)
      setActiveTask({
        conversationId: selectedId!,
        taskId: latestWithTask.task_id,
      })
  }, [activeTaskId, conversation.data, selectedId])
  useEffect(() => {
    setDraft('')
  }, [selectedId])

  const newConversation = useMutation({
    mutationFn: () => createConversation(),
    onSuccess: (created) => {
      if (selectedIdRef.current === creatingFromConversationId.current) {
        setParams({ conversation_id: created.conversation_id })
        setActiveTask(null)
      }
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
    },
  })
  const send = useMutation({
    mutationFn: async (content: string) => {
      let conversationId = selectedId
      if (!conversationId) {
        const created = await createConversation(content.slice(0, 60))
        conversationId = created.conversation_id
        if (selectedIdRef.current === null)
          setParams({ conversation_id: conversationId })
      }
      return sendConversationMessage(conversationId, content)
    },
    onSuccess: (turn) => {
      if (
        selectedIdRef.current === turn.conversation_id ||
        selectedIdRef.current === null
      ) {
        setDraft('')
        setActiveTask({
          conversationId: turn.conversation_id,
          taskId: turn.task_id,
        })
      }
      void queryClient.invalidateQueries({
        queryKey: ['conversation', turn.conversation_id],
      })
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
    },
  })
  const pendingApproval = pendingApprovals.data?.[0]
  const approval = useMutation({
    mutationFn: (decision: 'grant' | 'deny') => {
      const id = pendingApproval?.approval_id
      if (!id) throw new Error(text('No pending approval.', '没有待处理审批。'))
      return decision === 'grant'
        ? grantApproval(id, 'workbench-user')
        : denyApproval(id, 'workbench-user')
    },
    onSuccess: () => {
      void task.refetch()
      void events.refetch()
      void pendingApprovals.refetch()
    },
  })
  const busy =
    Boolean(task.data && !terminal.has(task.data.status)) ||
    (send.isPending && sendingConversationId.current === selectedId)
  const submit = () => {
    if (draft.trim() && !busy) {
      sendingConversationId.current = selectedId
      send.mutate(draft.trim())
    }
  }

  return (
    <div className="grid min-h-[calc(100vh-7rem)] grid-cols-1 overflow-hidden rounded-xl border border-slate-200 bg-white lg:grid-cols-[260px_minmax(0,1fr)_290px]">
      <aside className="border-b border-slate-200 p-3 lg:border-r lg:border-b-0">
        <button
          aria-label={text('New conversation', '新建对话')}
          onClick={() => {
            creatingFromConversationId.current = selectedId
            newConversation.mutate()
          }}
          disabled={newConversation.isPending}
          className="mb-5 w-full rounded-md bg-indigo-600 px-3 py-2 text-left text-sm font-medium text-white"
        >
          + {text('New conversation', '新建对话')}
        </button>
        <p className="mb-2 px-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">
          {text('Conversations', '连续对话')}
        </p>
        <div className="grid gap-1">
          {conversations.data?.map((item) => (
            <button
              key={item.conversation_id}
              onClick={() => {
                setParams({ conversation_id: item.conversation_id })
                setActiveTask(null)
              }}
              className={`rounded-md p-2 text-left ${selectedId === item.conversation_id ? 'bg-slate-100' : 'hover:bg-slate-100'}`}
            >
              <p className="truncate text-xs text-slate-800">{item.title}</p>
              <p className="mt-1 text-[10px] text-slate-600">
                {new Date(item.updated_at).toLocaleString()}
              </p>
            </button>
          ))}
        </div>
      </aside>

      <main className="relative flex min-h-[660px] flex-col border-b border-slate-200 lg:border-r lg:border-b-0">
        <header className="flex items-center justify-between border-b border-slate-200 px-5 py-3">
          <div>
            <p className="text-sm font-medium text-slate-800">
              {conversation.data?.title ??
                text('Aegis conversation', 'Aegis 连续对话')}
            </p>
            <p className="mt-1 text-xs text-slate-500">
              {selectedId ??
                text('Start by sending a message', '发送消息即可开始')}
            </p>
          </div>
          {task.data && <StatusBadge status={task.data.status} />}
        </header>
        <section className="flex-1 space-y-3 overflow-y-auto px-5 py-5 pb-40">
          {!conversation.data?.messages.length && (
            <div className="mx-auto max-w-lg pt-20 text-center">
              <p className="text-xl text-slate-800">
                {text(
                  'A conversation above auditable TaskRuns',
                  '建立在可审计 TaskRun 之上的连续对话',
                )}
              </p>
              <p className="mt-3 text-sm leading-6 text-slate-500">
                {text(
                  'Follow-up messages retain recent context, a bounded summary, the active security profile and Runtime state.',
                  '后续消息会携带近期上下文、受限摘要、当前安全配置和 Runtime 状态。',
                )}
              </p>
            </div>
          )}
          {conversation.data?.messages.map((message) => (
            <article
              key={message.message_id}
              className={`max-w-3xl rounded-lg border p-4 ${message.role === 'user' ? 'ml-auto border-sky-400/20 bg-sky-400/5' : 'mr-auto border-emerald-400/20 bg-emerald-400/5'}`}
            >
              <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">
                {message.role === 'user' ? text('You', '你') : 'Aegis'}
              </p>
              <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-800">
                {message.content}
              </p>
              {message.task_id && (
                <Link
                  to={`/runtime?task_id=${encodeURIComponent(message.task_id)}`}
                  className="mt-2 inline-block text-[11px] text-indigo-600"
                >
                  {text('Inspect Runtime', '查看 Runtime')} · {message.task_id}
                </Link>
              )}
            </article>
          ))}
          {task.data && !terminal.has(task.data.status) && (
            <div className="rounded-lg border border-amber-400/20 bg-amber-400/5 p-4 text-sm text-amber-700">
              {task.data.status === 'WAITING_APPROVAL'
                ? text(
                    'This TaskRun is waiting for a risk-based approval.',
                    '本次 TaskRun 正在等待风险审批。',
                  )
                : text(
                    'Aegis is planning and executing through the Runtime…',
                    'Aegis 正在通过 Runtime 规划并执行……',
                  )}
              {task.data.status === 'WAITING_APPROVAL' && pendingApproval && (
                <div className="mt-3 flex gap-2">
                  <button
                    onClick={() => approval.mutate('grant')}
                    disabled={approval.isPending}
                    className="rounded bg-emerald-700 px-3 py-1.5 text-xs font-medium text-white"
                  >
                    {text('Approve and resume', '批准并继续')}
                  </button>
                  <button
                    onClick={() => approval.mutate('deny')}
                    disabled={approval.isPending}
                    className="rounded border border-rose-400/40 px-3 py-1.5 text-xs text-rose-700"
                  >
                    {text('Reject', '拒绝')}
                  </button>
                </div>
              )}
            </div>
          )}
        </section>
        <div className="absolute inset-x-0 bottom-0 border-t border-slate-200 bg-slate-100 p-4 backdrop-blur">
          <div className="mx-auto max-w-3xl rounded-lg border border-slate-200 bg-white p-2">
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (
                  event.key === 'Enter' &&
                  !event.shiftKey &&
                  !event.nativeEvent.isComposing &&
                  event.keyCode !== 229
                ) {
                  event.preventDefault()
                  submit()
                }
              }}
              placeholder={text(
                'Continue this conversation…',
                '继续这段对话……',
              )}
              rows={3}
              disabled={busy}
              className="w-full resize-none bg-transparent px-2 py-1 text-sm text-slate-800 outline-none placeholder:text-slate-600 disabled:opacity-50"
            />
            <div className="flex items-center justify-between px-2 pt-1">
              <span className="text-[11px] text-slate-500">
                {text(
                  'Enter to send · Shift+Enter for newline',
                  'Enter 发送 · Shift+Enter 换行',
                )}
              </span>
              <button
                onClick={submit}
                disabled={!draft.trim() || busy}
                className="rounded bg-indigo-600 px-4 py-1.5 text-xs font-medium text-white disabled:opacity-40"
              >
                {busy ? text('Running…', '执行中……') : text('Send', '发送')}
              </button>
            </div>
          </div>
          {send.isError && (
            <p className="mt-2 text-center text-xs text-rose-700">
              {(send.error as Error).message}
            </p>
          )}
          {(newConversation.isError ||
            approval.isError ||
            pendingApprovals.isError) && (
            <p role="alert" className="mt-2 text-center text-xs text-rose-700">
              {
                (
                  newConversation.error ??
                  approval.error ??
                  pendingApprovals.error
                )?.message
              }
            </p>
          )}
        </div>
      </main>

      <aside className="p-4">
        <h2 className="text-sm font-medium text-slate-800">
          {text('Active safety boundary', '当前安全边界')}
        </h2>
        <div className="mt-4 space-y-3 text-xs">
          <div className="rounded border border-slate-200 p-3">
            <p className="text-slate-500">SecurityProfile</p>
            <p className="mt-1 text-slate-800">
              default · v{profile.data?.version ?? '—'}
            </p>
          </div>
          <div className="rounded border border-slate-200 p-3">
            <p className="text-slate-500">
              {text('Continuous permissions', '持续权限')}
            </p>
            <p className="mt-2 leading-5 text-slate-600">
              {profile.data?.allowed_actions.join(', ') || '—'}
            </p>
          </div>
          <div className="rounded border border-slate-200 p-3">
            <p className="text-slate-500">
              {text('Resource scopes', '资源范围')}
            </p>
            <p className="mt-2 font-mono text-slate-600">
              {profile.data?.resource_scopes.join(', ') || '—'}
            </p>
          </div>
          <div className="rounded border border-slate-200 p-3">
            <p className="text-slate-500">{text('Runtime mode', '运行模式')}</p>
            <p className="mt-1 text-slate-800">{health.data?.mode ?? '—'}</p>
          </div>
          {activeTaskId && (
            <Link
              to={`/runtime?task_id=${encodeURIComponent(activeTaskId)}`}
              className="block rounded border border-sky-400/20 p-3 text-indigo-600"
            >
              {text('Open current Runtime evidence', '打开当前 Runtime 证据')}
            </Link>
          )}
          <Link
            to="/settings/security"
            className="block rounded bg-slate-100 p-3 text-slate-800 hover:bg-slate-100"
          >
            {text('Edit persistent security settings', '修改长期安全设置')}
          </Link>
        </div>
      </aside>
    </div>
  )
}
