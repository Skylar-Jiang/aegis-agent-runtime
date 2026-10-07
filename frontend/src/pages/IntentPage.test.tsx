import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import App from '../App'

const run = {
  run_id: 'unit-test-run',
  case_id: 'retrieval_injection',
  title: 'Unit test fixture',
  original_task: 'Analyze synthetic router-a',
  attack_input: 'UNIT TEST injection',
  status: 'BLOCKED',
  mode: 'fixture/mock',
  schema_version: 'intent-demo:0.1',
  workspace: 'UNIT_TEST_ONLY',
  intent_history: [
    {
      goal: 'Analyze synthetic router-a',
      version: 1,
      confirmed_by: 'operator',
    },
  ],
  contract_history: [{ contract_version: 1, digest: 'unit-contract-digest' }],
  core_contract_history: [],
  actions: [
    {
      request_id: 'unit-request',
      execution_status: 'NOT_EXECUTED',
      behavior_event: {
        tool: 'sim_config_modify',
        target: 'devices/router-a.cfg',
        source_type: 'retrieval',
        source_ref: 'fixture:knowledge/injected.md',
        step_index: 1,
        subgoal: 'Untrusted proposal',
      },
      decision_result: {
        decision: 'BLOCK',
        reason_code: 'RETRIEVAL_GOAL_DEVIATION',
        risk_score: 0.95,
        trigger_dimensions: ['goal', 'source'],
        evidence_refs: ['fixture:knowledge/injected.md'],
        detector_version: 'fixture/mock:0.1',
        policy_version: 'demo-policy:0.1',
      },
      gateway_decision: { decision: 'ALLOW' },
      result: null,
    },
  ],
  correction_plan: null,
  effect_checks: [
    {
      effect_id: 'unit-effect',
      request_id: 'unit-request',
      tool: 'sim_config_modify',
      normalized_target: 'devices/router-a.cfg',
      before_digest: 'unit-before',
      after_digest: 'unit-before',
      status: 'UNCHANGED',
    },
  ],
  timeline: [
    {
      event_id: 'unit-event',
      sequence: 1,
      type: 'INTENT_DECIDED',
      state: 'BLOCK',
      source_ref: 'fixture:knowledge/injected.md',
      request_id: 'unit-request',
      occurred_at: '2026-10-02T00:00:00Z',
    },
  ],
  baseline: { files: { 'devices/router-a.cfg': 'unit-before' } },
  observed: {
    files: { 'devices/router-a.cfg': 'unit-before' },
    memory: {},
    endpoint_calls: [],
    config_call_count: 0,
    send_call_count: 0,
  },
  expected_state: {},
  acceptance: { passed: true, checks: { config_preserved: true } },
}

beforeEach(() => {
  window.history.pushState({}, '', '/intent')
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string, options?: RequestInit) => {
      if (input.endsWith('/cases'))
        return new Response(JSON.stringify({ data: [run] }))
      if (input.endsWith('/reset'))
        return new Response(JSON.stringify({ data: { run_count: 0 } }))
      if (input.endsWith('/control')) {
        const action = JSON.parse(options?.body as string).action
        return new Response(
          JSON.stringify({
            data: {
              ...run,
              status: action === 'stop' ? 'TERMINATED' : 'COMPLETED',
            },
          }),
        )
      }
      return new Response(JSON.stringify({ data: run }))
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('loads the Intent route and renders backend decisions, effects and complete timeline', async () => {
  render(<App />)
  expect(
    await screen.findByRole('heading', { name: '通信运维 Intent 演示' }),
  ).toBeInTheDocument()
  expect(
    await screen.findByRole('option', { name: /Unit test fixture/ }),
  ).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '运行场景' }))
  expect(await screen.findByTestId('run-status')).toHaveTextContent('BLOCKED')
  expect(screen.getByTestId('current-decision')).toHaveTextContent('BLOCK')
  expect(screen.getByTestId('config-call-count')).toHaveTextContent('0')
  expect(screen.getByTestId('send-call-count')).toHaveTextContent('0')
  expect(screen.getByText('INTENT_DECIDED')).toBeInTheDocument()
  expect(screen.getByText('RETRIEVAL_GOAL_DEVIATION')).toBeInTheDocument()
  expect(screen.getByTestId('intent-panel')).toHaveTextContent('fixture/mock')
  expect(screen.getByTestId('contract-version')).toHaveTextContent('1')
})

it('stops via the backend and reset clears the previous run instead of fabricating an empty effect', async () => {
  render(<App />)
  await screen.findByRole('option', { name: /Unit test fixture/ })
  fireEvent.click(screen.getByRole('button', { name: '运行场景' }))
  await screen.findByTestId('run-status')
  fireEvent.click(screen.getByRole('button', { name: '安全终止' }))
  await waitFor(() =>
    expect(screen.getByTestId('run-status')).toHaveTextContent('TERMINATED'),
  )
  fireEvent.click(screen.getByRole('button', { name: '重置演示' }))
  await waitFor(() =>
    expect(screen.queryByTestId('run-status')).not.toBeInTheDocument(),
  )
  expect(
    screen.getByText('选择场景并运行，查看后端事件与实际副作用。'),
  ).toBeInTheDocument()
})

it('reports backend failure without displaying static results', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: 'fixtures disabled' }), {
          status: 404,
        }),
    ),
  )
  render(<App />)
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'fixtures disabled',
  )
  expect(screen.queryByTestId('intent-panel')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '运行场景' })).toBeDisabled()
})
