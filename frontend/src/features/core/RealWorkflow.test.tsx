import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { CoreWorkbench } from './CoreWorkbench'
import { useLanguageStore } from '../../i18n'
const contract = {
  contract: {
    contract_id: 'contract-1',
    task_id: 'task-1',
    session_id: 'session-1',
    user_id: 'core-ui',
    version: 1,
    goals: ['Create audit note'],
    allowed: [{ action: 'create_file', resource: '**' }],
    denied: [],
    limits: { max_affected_objects: 100 },
    policy_version: 'default:1',
    tool_manifest_digest: 'manifest-digest',
  },
  ref: {
    contract_id: 'contract-1',
    version: 1,
    digest: 'contract-digest',
    status: 'DRAFT',
  },
}
const event = {
  event_id: 'event-1',
  task_id: 'task-1',
  sequence: 1,
  type: 'CONTRACT_CREATED',
  state: 'DRAFT',
  actor: 'core-ui',
  source_ref: 'contract-1',
  object_digest: 'object-evidence-digest',
  result_digest: null,
  parent_event_id: null,
  occurred_at: '2026-09-23T00:00:00Z',
  decision: null,
}
const bundle = {
  task_id: 'task-1',
  checkpoint: {
    checkpoint_id: 'checkpoint-1',
    from_seq: 1,
    to_seq: 1,
    chain_head: 'chain-head',
    signer: 'demo-key',
  },
  entries: [{ event }],
  objects: [],
}
const ok = (data: unknown) =>
  Promise.resolve(
    new Response(JSON.stringify({ data }), {
      headers: { 'content-type': 'application/json' },
    }),
  )
const health = {
  status: 'ok',
  crypto_mode: 'sm2',
  signature_provider: 'OpenSSLSignatureProvider',
  public_keys: ['demo-key'],
  audit_exporter: 'AuditExporter',
  audit_verifier: 'AuditVerifier',
  limits: { audit_bundle_bytes: 67108864 },
}
beforeEach(() => useLanguageStore.setState({ language: 'en' }))
afterEach(() => {
  cleanup()
  window.history.replaceState({}, '', '/')
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  vi.useRealTimers()
})
function server(
  extra?: (
    path: string,
    body: Record<string, unknown>,
  ) => Promise<Response> | undefined,
) {
  const calls: Array<{ url: string; body: Record<string, unknown> }> = []
  vi.stubGlobal(
    'fetch',
    vi.fn((url, init) => {
      const path = String(url)
      const body = init?.body ? JSON.parse(init.body) : {}
      calls.push({ url: path, body })
      const response = extra?.(path, body)
      if (response) return response
      if (path.endsWith('/health')) return ok(health)
      if (path.endsWith('/sessions')) return ok({ session_id: 'session-1' })
      if (path.endsWith('/tasks'))
        return ok({
          task_id: 'task-1',
          session_id: 'session-1',
          status: 'DRAFT',
          contract,
        })
      if (path.endsWith('/confirm'))
        return ok({
          ...contract,
          ref: { ...contract.ref, status: 'CONFIRMED' },
        })
      if (path.endsWith('/evaluate'))
        return ok({
          decision: {
            decision: 'REQUIRE_CONFIRMATION',
            reason_code: 'CONFIRMATION_REQUIRED',
            confirmation_id: 'confirm-1',
          },
          effective_permission: { allowed: [] },
        })
      if (path.endsWith('/resolve'))
        return ok({
          decision: { decision: 'ALLOW', reason_code: 'ALLOWED' },
          effective_permission: { allowed: [] },
        })
      if (path.endsWith('/execute'))
        return ok({
          request_id: 'request-1',
          status: 'EXECUTED',
          result: { path: 'audit-note.txt' },
        })
      return ok([event])
    }),
  )
  return calls
}
async function createTask() {
  fireEvent.change(screen.getByLabelText('Task objective'), {
    target: { value: 'Create audit note' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Create task' }))
  return screen.findByRole('button', { name: 'Confirm contract' })
}
it.each(['session', 'task'])(
  'keeps the existing task and evidence when replacement %s creation fails',
  async (failureStep) => {
    let sessionCalls = 0
    let taskCalls = 0
    server((path) => {
      if (
        path.endsWith('/sessions') &&
        ++sessionCalls > 1 &&
        failureStep === 'session'
      )
        return Promise.resolve(
          new Response(JSON.stringify({ detail: 'creation unavailable' }), {
            status: 503,
          }),
        )
      if (path.endsWith('/tasks') && ++taskCalls > 1 && failureStep === 'task')
        return Promise.resolve(
          new Response(JSON.stringify({ detail: 'creation unavailable' }), {
            status: 503,
          }),
        )
    })
    render(<CoreWorkbench />)
    await createTask()
    await screen.findByRole('button', { name: /#1 CONTRACT_CREATED/ })
    fireEvent.click(screen.getByRole('button', { name: 'Create new task' }))
    expect(screen.getByTitle('task-1')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /#1 CONTRACT_CREATED/ }),
    ).toBeInTheDocument()
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'creation unavailable',
    )
    expect(
      screen.getByRole('button', { name: 'Confirm contract' }),
    ).toBeEnabled()
  },
)
it('restores a Core task from a deep link and reopens another by ID', async () => {
  window.history.replaceState({}, '', '/core?task_id=task-1')
  server((path) => {
    const id = path.match(/\/api\/v1\/tasks\/(task-[12])\/snapshot$/)?.[1]
    if (id)
      return ok({
        task: {
          task_id: id,
          session_id: 'session-1',
          status: 'DRAFT',
          contract: {
            ...contract,
            contract: { ...contract.contract, task_id: id },
          },
        },
        latest_request: null,
      })
  })
  render(<CoreWorkbench />)
  expect(await screen.findByTitle('task-1')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Reopen task ID'), {
    target: { value: 'task-2' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Reopen task' }))
  expect(await screen.findByTitle('task-2')).toBeInTheDocument()
  expect(window.location.search).toBe('?task_id=task-2')
})
it('shows an older request without offering execution under a newer contract', async () => {
  window.history.replaceState({}, '', '/core?task_id=task-1')
  server((path) => {
    if (path.endsWith('/tasks/task-1/snapshot'))
      return ok({
        task: {
          task_id: 'task-1',
          session_id: 'session-1',
          status: 'DRAFT',
          contract,
        },
        latest_request: {
          envelope: {
            request_id: 'old-request',
            task_id: 'task-1',
            session_id: 'session-1',
            contract_ref: {
              ...contract.ref,
              digest: 'old-digest',
              status: 'CONFIRMED',
            },
            skill_ref: 'core-ui',
            tool: 'create_file',
            action: 'create_file',
            canonical_args: { path: 'old.txt', content: 'old' },
            resource: 'old.txt',
            effect_class: 'WRITE',
          },
          evaluation: {
            decision: { decision: 'ALLOW', reason_code: 'ALLOWED' },
            effective_permission: { allowed: [] },
          },
          execution_state: 'PENDING',
          execution_result: null,
          confirmation_id: null,
        },
      })
  })
  render(<CoreWorkbench />)
  expect(await screen.findByText('ALLOWED')).toBeInTheDocument()
  expect(
    screen.queryByRole('button', { name: 'Execute tool' }),
  ).not.toBeInTheDocument()
})
it('does not offer replay when a restored request has an uncertain execution state', async () => {
  window.history.replaceState({}, '', '/core?task_id=task-1')
  const confirmed = {
    ...contract,
    ref: { ...contract.ref, status: 'CONFIRMED' },
  }
  server((path) => {
    if (path.endsWith('/tasks/task-1/snapshot'))
      return ok({
        task: {
          task_id: 'task-1',
          session_id: 'session-1',
          status: 'RUNNING',
          contract: confirmed,
        },
        latest_request: {
          envelope: {
            request_id: 'uncertain-request',
            task_id: 'task-1',
            session_id: 'session-1',
            contract_ref: confirmed.ref,
            skill_ref: 'core-ui',
            tool: 'create_file',
            action: 'create_file',
            canonical_args: { path: 'uncertain.txt', content: 'pending' },
            resource: 'uncertain.txt',
            effect_class: 'WRITE',
          },
          evaluation: {
            decision: { decision: 'ALLOW', reason_code: 'ALLOWED' },
            effective_permission: { allowed: [] },
          },
          execution_state: 'UNKNOWN',
          execution_result: null,
          confirmation_id: null,
        },
      })
  })
  render(<CoreWorkbench />)
  expect(await screen.findByText('ALLOWED')).toBeInTheDocument()
  expect(
    screen.queryByRole('button', { name: 'Execute tool' }),
  ).not.toBeInTheDocument()
})
it('loads the latest history task even while a new task creation is pending', async () => {
  window.history.replaceState({}, '', '/core?task_id=task-1')
  let finishSession!: (response: Response) => void
  let finishSnapshot!: (response: Response) => void
  server((path) => {
    const id = path.match(/\/api\/v1\/tasks\/(task-[12])\/snapshot$/)?.[1]
    if (id === 'task-2')
      return new Promise<Response>((resolve) => {
        finishSnapshot = resolve
      })
    if (id)
      return ok({
        task: {
          task_id: id,
          session_id: 'session-1',
          status: 'DRAFT',
          contract,
        },
        latest_request: null,
      })
    if (path.endsWith('/sessions'))
      return new Promise<Response>((resolve) => {
        finishSession = resolve
      })
  })
  render(<CoreWorkbench />)
  expect(await screen.findByTitle('task-1')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Create new task' }))
  await waitFor(() => expect(finishSession).toBeDefined())
  await act(async () => {
    window.history.pushState({}, '', '/core?task_id=task-2')
    window.dispatchEvent(new PopStateEvent('popstate'))
  })
  await waitFor(() => expect(finishSnapshot).toBeDefined())
  await act(async () =>
    finishSession(await ok({ session_id: 'session-created' })),
  )
  expect(screen.getByRole('status')).toHaveTextContent('Calling Core API')
  await act(async () =>
    finishSnapshot(
      await ok({
        task: {
          task_id: 'task-2',
          session_id: 'session-2',
          status: 'DRAFT',
          contract,
        },
        latest_request: null,
      }),
    ),
  )
  expect(screen.getByTitle('task-2')).toBeInTheDocument()
})
it('guides a real task through contract, server approval and execution without hand-entered IDs', async () => {
  const calls = server()
  render(<CoreWorkbench />)
  await screen.findByText('demo-key')
  expect(screen.queryByText('Verification passed')).not.toBeInTheDocument()
  fireEvent.click(await createTask())
  const evaluate = screen.getByRole('button', { name: 'Evaluate request' })
  await waitFor(() => expect(evaluate).toBeEnabled())
  fireEvent.click(evaluate)
  fireEvent.click(
    await screen.findByRole('button', { name: 'Approve and recheck' }),
  )
  fireEvent.click(await screen.findByRole('button', { name: 'Execute tool' }))
  await screen.findByText('EXECUTED')
  expect(
    calls.find((call) => call.url.endsWith('/evaluate'))!.body,
  ).toMatchObject({
    envelope: {
      task_id: 'task-1',
      session_id: 'session-1',
      contract_ref: {
        contract_id: 'contract-1',
        status: 'CONFIRMED',
        digest: 'contract-digest',
      },
      skill_ref: 'core-ui',
    },
    permissions: { user_grants: [], skill_grants: [], system_grants: [] },
  })
  expect(calls.find((call) => call.url.endsWith('/resolve'))!.body).toEqual({
    confirmed: true,
    resolved_by: 'core-ui',
  })
  expect(
    screen.getByRole('img', { name: 'Event activity' }),
  ).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /#1 CONTRACT_CREATED/ }))
  expect(screen.getByText('object-evidence-digest')).toBeInTheDocument()
})
it('keeps original evidence intact when verifying a tampered copy', async () => {
  const verified: Array<Record<string, unknown>> = []
  const verificationBindings: Array<Record<string, unknown>> = []
  server((path, body) => {
    if (path.endsWith('/export')) return ok(bundle)
    if (path.endsWith('/verify')) {
      verified.push(body.bundle as Record<string, unknown>)
      verificationBindings.push({
        task_id: body.task_id,
        trusted_checkpoint_id: body.trusted_checkpoint_id,
      })
      return ok({
        valid: verified.length !== 2,
        verified_events: 1,
        anchored_from_seq: 1,
        anchored_to_seq: 1,
        tail_complete: verified.length !== 2,
        errors:
          verified.length === 2
            ? [{ code: 'CHAIN_INVALID', message: 'Evidence changed' }]
            : [],
      })
    }
  })
  render(<CoreWorkbench />)
  await createTask()
  fireEvent.change(screen.getByLabelText('Checkpoint ID'), {
    target: { value: 'trusted-checkpoint-outside-bundle' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Export evidence' }))
  fireEvent.click(
    await screen.findByRole('button', { name: 'Verify original' }),
  )
  await screen.findByText('Verification passed')
  fireEvent.click(screen.getByRole('button', { name: 'Create tampered copy' }))
  const copyInput = screen.getByLabelText(
    'Verification copy JSON',
  ) as HTMLTextAreaElement
  const editedCopy = JSON.parse(copyInput.value)
  editedCopy.task_id = 'untrusted-task'
  editedCopy.checkpoint.checkpoint_id = 'untrusted-checkpoint'
  fireEvent.change(copyInput, { target: { value: JSON.stringify(editedCopy) } })
  fireEvent.click(screen.getByRole('button', { name: 'Verify copy' }))
  await screen.findByText(/CHAIN_INVALID/)
  fireEvent.click(screen.getByRole('button', { name: 'Verify original' }))
  await waitFor(() => expect(verified).toHaveLength(3))
  expect(verified[0]).toEqual(bundle)
  expect(verified[1]).not.toEqual(bundle)
  expect(verified[2]).toEqual(bundle)
  expect(verificationBindings).toEqual([
    {
      task_id: 'task-1',
      trusted_checkpoint_id: 'trusted-checkpoint-outside-bundle',
    },
    {
      task_id: 'task-1',
      trusted_checkpoint_id: 'trusted-checkpoint-outside-bundle',
    },
    {
      task_id: 'task-1',
      trusted_checkpoint_id: 'trusted-checkpoint-outside-bundle',
    },
  ])
})
it('labels fake crypto as unsigned without fabricating activity or successful verification', async () => {
  server((path) =>
    path.endsWith('/health')
      ? ok({
          ...health,
          crypto_mode: 'fake',
          public_keys: ['fake-test-key'],
          audit_exporter: null,
          audit_verifier: null,
        })
      : undefined,
  )
  render(<CoreWorkbench />)
  expect(await screen.findByText('Fake crypto · unsigned')).toBeInTheDocument()
  expect(
    screen.queryByRole('img', { name: 'Event activity' }),
  ).not.toBeInTheDocument()
  expect(screen.queryByText('Verification passed')).not.toBeInTheDocument()
})

it('polls current-task activity and discards the previous task when a new task is created', async () => {
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
  let nextEvents = [event]
  let taskCount = 0
  server((path) => {
    if (path.endsWith('/tasks')) {
      taskCount++
      return ok({
        task_id: `task-${taskCount}`,
        session_id: 'session-1',
        status: 'DRAFT',
        contract,
      })
    }
    if (path.includes('/task-2/events')) return ok([])
    if (path.includes('/events')) return ok(nextEvents)
  })
  render(<CoreWorkbench />)
  await createTask()
  await screen.findByRole('img', { name: 'Event activity' })
  expect(document.querySelector('.activity-chart rect')).toHaveAttribute(
    'data-count',
    '1',
  )
  nextEvents = [event, { ...event, event_id: 'event-2', sequence: 2 }]
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000)
  })
  expect(document.querySelector('.activity-chart rect')).toHaveAttribute(
    'data-count',
    '2',
  )
  fireEvent.click(screen.getByRole('button', { name: 'Create new task' }))
  await waitFor(() =>
    expect(
      screen.queryByRole('img', { name: 'Event activity' }),
    ).not.toBeInTheDocument(),
  )
  expect(screen.queryByText('object-evidence-digest')).not.toBeInTheDocument()
})

it('downloads compact JSON bytes without expanding the exported evidence bundle', async () => {
  let downloaded: Blob | undefined
  vi.stubGlobal(
    'URL',
    class extends URL {
      static createObjectURL(blob: Blob) {
        downloaded = blob
        return 'blob:download-test'
      }
      static revokeObjectURL() {}
    },
  )
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  const exported = {
    ...bundle,
    entries: [
      { event: { ...event, source_ref: 'spaces stay intact\nnext line' } },
    ],
  }
  server((path) => (path.endsWith('/export') ? ok(exported) : undefined))
  render(<CoreWorkbench />)
  await createTask()
  fireEvent.click(screen.getByRole('button', { name: 'Export evidence' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Download' }))
  expect(downloaded).toBeInstanceOf(Blob)
  const contents = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = reject
    reader.readAsText(downloaded!)
  })
  expect(contents).toBe(JSON.stringify(exported))
  expect(JSON.parse(contents)).toEqual(exported)
  expect(downloaded!.type).toBe('application/json')
})

it('labels repeated ALLOW decisions as event counts and pending approvals as request counts', async () => {
  server((path) =>
    path.includes('/events')
      ? ok([
          {
            ...event,
            event_id: 'evaluation',
            request_id: 'request-a',
            decision: 'ALLOW',
          },
          {
            ...event,
            event_id: 'execution',
            request_id: 'request-a',
            sequence: 2,
            decision: 'ALLOW',
          },
          {
            ...event,
            event_id: 'confirmation',
            request_id: 'request-b',
            sequence: 3,
            decision: 'REQUIRE_CONFIRMATION',
          },
        ])
      : undefined,
  )

  render(<CoreWorkbench />)
  await createTask()

  await waitFor(() => {
    expect(screen.getByText('ALLOW events')).toHaveTextContent(
      '2 ALLOW events',
    )
    expect(screen.getByText('DENY events')).toHaveTextContent(
      '0 DENY events',
    )
    expect(screen.getByText('Pending requests')).toHaveTextContent(
      '1 Pending requests',
    )
  })
})

it('loads more than 1000 events in bounded pages, merges overlaps and polls only the tail', async () => {
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
  const history = Array.from({ length: 1105 }, (_, index) => ({
    ...event,
    event_id: `event-${index + 1}`,
    sequence: index + 1,
  }))
  const cursors: number[] = []
  server((path) => {
    if (!path.includes('/events')) return
    const url = new URL(path, 'http://localhost')
    const cursor = Number(url.searchParams.get('after_sequence'))
    expect(url.searchParams.get('limit')).toBe('1000')
    cursors.push(cursor)
    if (cursor === 1000) return ok([history[999], ...history.slice(1000)])
    return ok(history.filter((row) => row.sequence > cursor).slice(0, 1000))
  })
  render(<CoreWorkbench />)
  await createTask()
  await waitFor(() =>
    expect(document.querySelectorAll('.core-events > li')).toHaveLength(1105),
  )
  expect(cursors).toEqual([0, 1000])
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000)
  })
  expect(cursors).toEqual([0, 1000, 1105])
  expect(document.querySelectorAll('.core-events > li')).toHaveLength(1105)
})

it('resets the event cursor and ignores a delayed page after switching tasks', async () => {
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
  let taskCount = 0
  let finishOldPage!: (response: Response) => void
  let oldRequests = 0
  const secondCursors: number[] = []
  server((path) => {
    if (path.endsWith('/tasks'))
      return ok({
        task_id: `task-${++taskCount}`,
        session_id: 'session-1',
        status: 'DRAFT',
        contract,
      })
    if (path.includes('/task-1/events')) {
      if (++oldRequests === 1) return ok([event])
      return new Promise<Response>((resolve) => {
        finishOldPage = resolve
      })
    }
    if (path.includes('/task-2/events')) {
      secondCursors.push(
        Number(
          new URL(path, 'http://localhost').searchParams.get('after_sequence'),
        ),
      )
      return ok([
        {
          ...event,
          event_id: 'second-task-event',
          task_id: 'task-2',
          type: 'SECOND_TASK',
        },
      ])
    }
  })
  render(<CoreWorkbench />)
  await createTask()
  await screen.findByRole('button', { name: /#1 CONTRACT_CREATED/ })
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000)
  })
  fireEvent.click(screen.getByRole('button', { name: 'Create new task' }))
  await screen.findByRole('button', { name: /#1 SECOND_TASK/ })
  await act(async () => {
    finishOldPage(await ok([{ ...event, sequence: 99, type: 'STALE_TASK' }]))
  })
  expect(secondCursors).toEqual([0])
  expect(
    screen.queryByRole('button', { name: /STALE_TASK/ }),
  ).not.toBeInTheDocument()
  expect(
    screen.queryByRole('button', { name: /CONTRACT_CREATED/ }),
  ).not.toBeInTheDocument()
})

it('uses a fresh automatic checkpoint after new execution and preserves the earlier export', async () => {
  const exported: Array<Record<string, unknown>> = []
  const verified: Array<Record<string, unknown>> = []
  let history = [event]
  const calls = server((path, body) => {
    if (path.includes('/events')) {
      const cursor = Number(
        new URL(path, 'http://localhost').searchParams.get('after_sequence'),
      )
      return ok(history.filter((row) => row.sequence > cursor))
    }
    if (path.endsWith('/export')) {
      const next = {
        ...bundle,
        checkpoint: {
          ...bundle.checkpoint,
          checkpoint_id: body.checkpoint_id,
          to_seq: history.length,
        },
        entries: history.map((row) => ({ event: row })),
      }
      exported.push(structuredClone(next))
      return ok(next)
    }
    if (path.endsWith('/execute')) {
      history = [
        ...history,
        {
          ...event,
          event_id: 'executed',
          sequence: 2,
          type: 'EXECUTION_FINISHED',
        },
      ]
      return ok({
        request_id: 'request-1',
        status: 'EXECUTED',
        result: {
          runtime: {
            commit_status: 'COMMITTED',
            checkpoint_id: 'runtime-checkpoint',
            effect_id: 'runtime-effect',
          },
        },
      })
    }
    if (path.endsWith('/verify')) {
      verified.push(body)
      return ok({
        valid: true,
        verified_events: 1,
        errors: [],
        anchored_from_seq: 1,
        anchored_to_seq: 1,
        tail_complete: true,
      })
    }
  })
  render(<CoreWorkbench />)
  fireEvent.click(await createTask())
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Export evidence' }),
    ).toBeEnabled(),
  )
  fireEvent.click(screen.getByRole('button', { name: 'Export evidence' }))
  await screen.findByRole('button', { name: 'Verify original' })
  const original = structuredClone(exported[0])
  const firstId = calls.find((item) => item.url.endsWith('/export'))!.body
    .checkpoint_id
  fireEvent.click(screen.getByRole('button', { name: 'Evaluate request' }))
  fireEvent.click(
    await screen.findByRole('button', { name: 'Approve and recheck' }),
  )
  fireEvent.click(await screen.findByRole('button', { name: 'Execute tool' }))
  await screen.findByText('EXECUTED')
  fireEvent.click(screen.getByRole('button', { name: 'Verify original' }))
  await screen.findByText('Verification passed')
  expect(verified[0].trusted_checkpoint_id).toBe(firstId)
  expect(verified[0].bundle).toEqual(original)
  fireEvent.click(screen.getByRole('button', { name: 'Export evidence' }))
  await waitFor(() => expect(exported).toHaveLength(2))
  const exports = calls.filter((item) => item.url.endsWith('/export'))
  expect(exports[1].body.checkpoint_id).not.toBe(firstId)
  expect(exported[0]).toEqual(original)
  expect(screen.getByLabelText('Checkpoint ID')).toHaveValue(
    String(exports[1].body.checkpoint_id),
  )
  expect(screen.getByText('runtime-checkpoint')).toBeInTheDocument()
})

it('keeps a manually selected checkpoint unchanged and retains original verification when history grows', async () => {
  let history = [event]
  const exports: Array<Record<string, unknown>> = []
  const verified: Array<Record<string, unknown>> = []
  server((path, body) => {
    if (path.includes('/events')) {
      const cursor = Number(
        new URL(path, 'http://localhost').searchParams.get('after_sequence'),
      )
      return ok(history.filter((row) => row.sequence > cursor))
    }
    if (path.endsWith('/export')) {
      exports.push(body)
      return ok(bundle)
    }
    if (path.endsWith('/verify')) {
      verified.push(body)
      return ok({
        valid: true,
        verified_events: 1,
        errors: [],
        anchored_from_seq: 1,
        anchored_to_seq: 1,
        tail_complete: true,
      })
    }
  })
  render(<CoreWorkbench />)
  await createTask()
  fireEvent.change(screen.getByLabelText('Checkpoint ID'), {
    target: { value: 'operator-anchor' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Export evidence' }))
  await screen.findByRole('button', { name: 'Verify original' })
  history = [...history, { ...event, event_id: 'new-event', sequence: 2 }]
  fireEvent.click(screen.getByRole('button', { name: 'Export evidence' }))
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Enter a new Checkpoint ID',
  )
  expect(exports).toHaveLength(1)
  expect(screen.getByLabelText('Checkpoint ID')).toHaveValue('operator-anchor')
  fireEvent.change(screen.getByLabelText('Checkpoint ID'), {
    target: { value: 'operator-next-anchor' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Verify original' }))
  await screen.findByText('Verification passed')
  expect(verified[0]).toMatchObject({
    trusted_checkpoint_id: 'operator-anchor',
    task_id: 'task-1',
    bundle,
  })
  fireEvent.click(screen.getByRole('button', { name: 'Export evidence' }))
  await waitFor(() => expect(exports).toHaveLength(2))
  expect(exports[1].checkpoint_id).toBe('operator-next-anchor')
})

it('retries a racing automatic checkpoint conflict with a fresh ID and preserves old evidence on failure', async () => {
  const exports: Array<Record<string, unknown>> = []
  const verified: Array<Record<string, unknown>> = []
  server((path, body) => {
    if (path.endsWith('/export')) {
      exports.push(body)
      if (exports.length === 1) return ok(bundle)
      return Promise.resolve(
        new Response(JSON.stringify({ detail: 'Export unavailable' }), {
          status: exports.length === 2 ? 409 : 503,
        }),
      )
    }
    if (path.endsWith('/verify')) {
      verified.push(body)
      return ok({
        valid: true,
        verified_events: 1,
        errors: [],
        anchored_from_seq: 1,
        anchored_to_seq: 1,
        tail_complete: true,
      })
    }
  })
  render(<CoreWorkbench />)
  await createTask()
  fireEvent.click(screen.getByRole('button', { name: 'Export evidence' }))
  await screen.findByRole('button', { name: 'Verify original' })
  const originalId = exports[0].checkpoint_id
  fireEvent.click(screen.getByRole('button', { name: 'Export evidence' }))
  await screen.findByRole('alert')
  expect(exports).toHaveLength(3)
  expect(exports[1].checkpoint_id).toBe(originalId)
  expect(exports[2].checkpoint_id).not.toBe(originalId)
  expect(screen.getByLabelText('Checkpoint ID')).toHaveValue(String(originalId))
  fireEvent.click(screen.getByRole('button', { name: 'Verify original' }))
  await screen.findByText('Verification passed')
  expect(verified[0]).toMatchObject({
    trusted_checkpoint_id: originalId,
    bundle,
  })
})
