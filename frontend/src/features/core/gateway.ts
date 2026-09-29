import type {
  BehaviorEvent,
  EffectivePermission,
  GatewayDecision,
  TaskContractV2,
} from './generated'

export const scenarios = [
  'allow',
  'deny',
  'confirm',
  'replan',
  'empty',
  'error',
] as const
export type Scenario = (typeof scenarios)[number]

export type CoreSnapshot = {
  demo: true
  task_id: string
  request_id: string
  contract: TaskContractV2
  effective_permission: EffectivePermission
  decision: GatewayDecision | null
  confirmation_status: 'WAITING_CONFIRMATION' | 'CONFIRMED' | 'REJECTED' | null
  events: BehaviorEvent[]
}

export interface CoreGatewayClient {
  load(scenario: Scenario): Promise<CoreSnapshot>
  resolveConfirmation(
    confirmationId: string,
    confirmed: boolean,
  ): Promise<CoreSnapshot>
}

/** Development-only HTTP mock boundary. No production tool endpoint is called. */
export class HttpMockToolGateway implements CoreGatewayClient {
  async load(scenario: Scenario): Promise<CoreSnapshot> {
    return this.request(`/__core_mock__/scenarios/${scenario}`)
  }

  async resolveConfirmation(
    confirmationId: string,
    confirmed: boolean,
  ): Promise<CoreSnapshot> {
    return this.request(
      `/__core_mock__/confirmations/${encodeURIComponent(confirmationId)}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirmed }),
      },
    )
  }

  private async request(
    path: string,
    init?: RequestInit,
  ): Promise<CoreSnapshot> {
    const response = await fetch(path, init)
    if (!response.headers.get('content-type')?.includes('application/json')) {
      throw new Error(
        '模拟服务不可用，请通过前端开发服务 pnpm dev 打开此页面。',
      )
    }
    if (!response.ok) {
      throw new Error(`模拟服务请求失败（HTTP ${response.status}），请重试。`)
    }
    const snapshot = (await response.json()) as CoreSnapshot
    if (snapshot.demo !== true || !Array.isArray(snapshot.events)) {
      throw new Error('模拟服务响应格式错误。')
    }
    return snapshot
  }
}

export type RealToolEvaluationRequest = {
  envelope: {
    request_id: string
    task_id: string
    session_id: string
    contract_ref: {
      contract_id: string
      version: number
      digest: string
      status: string
      confirmed_by?: string | null
      confirmed_at?: string | null
    }
    skill_ref: string
    tool: string
    action: string
    canonical_args: Record<string, unknown>
    resource: string
    effect_class: string
  }
  permissions: {
    user_grants: Array<Record<string, unknown>>
    skill_grants: Array<Record<string, unknown>>
    system_grants: Array<Record<string, unknown>>
  }
}

export type RealEvaluationResult = {
  decision: GatewayDecision
  effective_permission: EffectivePermission
}

export type RealExecutionResult = {
  request_id: string
  status: string
  result: Record<string, unknown>
}

type ApiEnvelope<T> = { data: T }

export type CoreHealth = {
  status: string
  crypto_mode: string
  signature_provider: string
  public_keys: string[]
  event_store?: string
  tool_executor?: string
  audit_exporter?: string | null
  audit_verifier?: string | null
  limits?: Record<string, number | string>
}

export type CoreContractRecord = {
  contract: TaskContractV2
  ref: RealToolEvaluationRequest['envelope']['contract_ref']
}

export type CoreTaskDraft = {
  task_id: string
  session_id: string
  status: string
  contract: CoreContractRecord
}

export type CoreTaskSnapshot = {
  task: CoreTaskDraft
  latest_request: {
    envelope: RealToolEvaluationRequest['envelope']
    evaluation: RealEvaluationResult | null
    execution_state: string
    execution_result: RealExecutionResult | null
    confirmation_id: string | null
  } | null
}

export type VerificationResult = {
  valid: boolean
  verified_events: number
  anchored_from_seq: number | null
  anchored_to_seq: number | null
  tail_complete: boolean
  errors: Array<{ code: string; message: string }>
}

export class CoreApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
    this.name = 'CoreApiError'
  }
}

/** HTTP client for the real Core PR2 backend. It never reimplements permission policy in UI. */
export class RealCoreGatewayClient {
  constructor(private readonly baseUrl?: string) {}

  health(): Promise<CoreHealth> {
    return this.request('/api/v1/health')
  }

  createSession(title: string): Promise<{ session_id: string }> {
    return this.post('/api/v1/sessions', {
      user_id: 'core-ui',
      title,
      security_profile_id: 'default',
    })
  }

  createTask(sessionId: string, objective: string): Promise<CoreTaskDraft> {
    return this.post(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/tasks`,
      { objective, completion_criteria: [] },
    )
  }

  taskSnapshot(taskId: string): Promise<CoreTaskSnapshot> {
    return this.request(`/api/v1/tasks/${encodeURIComponent(taskId)}/snapshot`)
  }

  confirmContract(
    contractId: string,
    version: number,
  ): Promise<CoreContractRecord> {
    return this.post(
      `/api/v1/contracts/${encodeURIComponent(contractId)}/confirm`,
      { version, confirmed_by: 'core-ui' },
    )
  }

  exportAudit(
    taskId: string,
    checkpointId: string,
  ): Promise<Record<string, unknown>> {
    return this.post('/api/v1/audit/export', {
      task_id: taskId,
      checkpoint_id: checkpointId,
    })
  }

  verifyAudit(
    bundle: Record<string, unknown>,
    taskId: string,
    checkpointId: string,
  ): Promise<VerificationResult> {
    return this.post('/api/v1/audit/verify', {
      bundle,
      task_id: taskId,
      trusted_checkpoint_id: checkpointId,
    })
  }

  private post<T>(path: string, body: unknown): Promise<T> {
    return this.request(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  }

  async evaluate(
    body: RealToolEvaluationRequest,
  ): Promise<RealEvaluationResult> {
    return this.request('/api/v1/tool-calls/evaluate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  }

  async resolveConfirmation(
    confirmationId: string,
    confirmed: boolean,
  ): Promise<RealEvaluationResult> {
    return this.request(
      `/api/v1/confirmations/${encodeURIComponent(confirmationId)}/resolve`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirmed, resolved_by: 'core-ui' }),
      },
    )
  }

  async execute(requestId: string): Promise<RealExecutionResult> {
    return this.request(
      `/api/v1/tool-calls/${encodeURIComponent(requestId)}/execute`,
      { method: 'POST' },
    )
  }

  async events(
    taskId: string,
    options?: { afterSequence?: number; limit?: number; signal?: AbortSignal },
  ): Promise<BehaviorEvent[]> {
    const query = new URLSearchParams()
    if (options?.afterSequence !== undefined)
      query.set('after_sequence', String(options.afterSequence))
    if (options?.limit !== undefined) query.set('limit', String(options.limit))
    const suffix = query.size ? `?${query}` : ''
    return this.request(
      `/api/v1/tasks/${encodeURIComponent(taskId)}/events${suffix}`,
      options?.signal ? { signal: options.signal } : undefined,
    )
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(apiUrl(path, this.baseUrl), init)
    let payload: unknown
    try {
      payload = await response.json()
    } catch {
      throw new Error(`Core API 返回非 JSON 响应（HTTP ${response.status}）`)
    }
    if (!response.ok) {
      const detail =
        typeof payload === 'object' && payload !== null && 'detail' in payload
          ? JSON.stringify((payload as { detail: unknown }).detail)
          : `HTTP ${response.status}`
      throw new CoreApiError(`Core API 请求失败：${detail}`, response.status)
    }
    if (
      typeof payload !== 'object' ||
      payload === null ||
      !('data' in payload)
    ) {
      throw new Error('Core API 响应缺少 data 字段。')
    }
    return (payload as ApiEnvelope<T>).data
  }
}
import { apiUrl } from '../../api/url'
