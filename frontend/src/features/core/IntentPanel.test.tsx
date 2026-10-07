import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { IntentPanel } from './IntentPanel'
import type { BehaviorEvent } from './generated'

afterEach(cleanup)

it('shows persisted stop, risk and upstream evidence, and links the actual event', () => {
  const select = vi.fn()
  const event: BehaviorEvent = {
    schema_version: '1.0',
    event_id: 'risk',
    sequence: 1,
    task_id: 'task',
    parent_event_id: null,
    type: 'INTENT_EVALUATED',
    actor: 'detector',
    source_ref: 'intent:request',
    object_digest: null,
    state: 'SAFE_STOP',
    decision: 'DENY',
    result_digest: null,
    occurred_at: '2026-10-07T00:00:00Z',
    intent: {
      enabled: true,
      risk_score: 1,
      trigger_dimensions: ['report_completion_mismatch'],
      evidence_refs: ['request:read'],
      policy_version: 'policy:1',
      contract_version: 1,
      contract_digest: 'digest',
      step_index: 2,
      disposition: 'SAFE_STOP',
      reason: 'mismatch',
    },
  }
  render(
    <IntentPanel
      events={[
        event,
        { ...event, event_id: 'stop', type: 'TASK_SAFE_STOPPED', sequence: 2 },
      ]}
      onSelect={select}
    />,
  )
  expect(
    screen.getByText('Task safely stopped; further execution is refused.'),
  ).toBeInTheDocument()
  expect(screen.getByText(/request:read/)).toBeInTheDocument()
  expect(
    screen.getByRole('img', { name: 'Risk by proposed step' }),
  ).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /Step 2/ }))
  expect(select).toHaveBeenCalledWith('risk')
})

it('keeps missing intent facts as missing, rather than inventing zero-risk points', () => {
  render(<IntentPanel events={[]} onSelect={vi.fn()} />)
  expect(
    screen.getByText('No intent checks recorded for this task.'),
  ).toBeInTheDocument()
  expect(screen.queryByRole('img')).not.toBeInTheDocument()
})
