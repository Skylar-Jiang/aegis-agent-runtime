import { get, post } from './client'
import type {
  BehaviorEvent as CoreEvent,
  GatewayDecision,
  TaskContractV2,
} from '../features/core/generated'
import type {
  BehaviorEvent,
  CorrectionPlan,
  DecisionResult,
  EffectCheck,
  IntentSpec,
  TaskContract,
} from '../features/intent/generated'

export interface IntentCase {
  case_id: string
  title: string
  original_task: string
  attack_input: unknown
  expected_state: Record<string, unknown>
}

export interface ObservedState {
  files: Record<string, string | null>
  memory: Record<string, unknown>
  endpoint_calls: Array<{
    request_id: string
    tool: string
    target: string
    payload_digest: string
    effect: string
    sent: false
  }>
  config_call_count: number
  send_call_count: number
}

export interface IntentAction {
  request_id: string
  behavior_event: BehaviorEvent
  decision_result: DecisionResult
  gateway_decision: GatewayDecision
  envelope: Record<string, unknown>
  execution_status: string
  result: Record<string, unknown> | null
}

// This is a view envelope, not another definition of the public Intent models.
export interface IntentRun extends IntentCase {
  run_id: string
  status: string
  mode: string
  schema_version: string
  workspace: string
  intent_history: IntentSpec[]
  contract_history: TaskContract[]
  core_contract_history: Array<{ contract: TaskContractV2; ref: unknown }>
  actions: IntentAction[]
  correction_plan: CorrectionPlan | null
  effect_checks: EffectCheck[]
  timeline: CoreEvent[]
  baseline: ObservedState
  observed: ObservedState
  acceptance: { passed: boolean; checks: Record<string, boolean> }
}

const prefix = '/intent-demo'
export const listIntentCases = () => get<IntentCase[]>(`${prefix}/cases`)
export const startIntentCase = (case_id: string) =>
  post<IntentRun>(`${prefix}/runs`, { case_id })
export const getIntentRun = (run_id: string) =>
  get<IntentRun>(`${prefix}/runs/${encodeURIComponent(run_id)}`)
export const controlIntentRun = (
  run_id: string,
  action: 'confirm' | 'replan' | 'stop',
) =>
  post<IntentRun>(`${prefix}/runs/${encodeURIComponent(run_id)}/control`, {
    action,
  })
export const resetIntentDemo = () =>
  post<{ run_count: number }>(`${prefix}/reset`)
