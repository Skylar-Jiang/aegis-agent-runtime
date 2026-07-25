import { describe, expect, it } from 'vitest'

import type { AuditEvent } from '../types/contracts'
import { describeAuditEvent, mergeAuditEvents } from './taskEvents'

const event = (overrides: Partial<AuditEvent>): AuditEvent => ({
  event_id: 'event-1',
  task_id: 'task-1',
  sequence_number: 1,
  event_type: 'TOOL_REQUESTED',
  timestamp: '2026-07-25T00:00:00Z',
  actor: 'runtime',
  status: 'PENDING',
  summary: 'tool requested',
  details: { tool_name: 'send_email_dry_run' },
  ...overrides,
})

describe('mergeAuditEvents', () => {
  it('orders events and ignores an SSE replay with the same sequence number', () => {
    const merged = mergeAuditEvents(
      [event({ event_id: 'event-2', sequence_number: 2 })],
      [event({ event_id: 'event-1', sequence_number: 1 }), event({ event_id: 'replay-2', sequence_number: 2 })],
    )

    expect(merged.map((item) => item.sequence_number)).toEqual([1, 2])
    expect(merged[1].event_id).toBe('event-2')
  })
})

describe('describeAuditEvent', () => {
  it('labels dry-run egress as not actually sent', () => {
    const description = describeAuditEvent(event({
      event_type: 'EXECUTION_FINISHED',
      status: 'COMMITTED',
      details: { tool_name: 'send_email_dry_run' },
    }))

    expect(description.stage).toBe('Execution')
    expect(description.detail).toContain('Not actually sent')
  })

  it('marks a rollback as a non-success safety outcome', () => {
    const description = describeAuditEvent(event({
      event_type: 'ROLLBACK_FINISHED',
      status: 'ROLLED_BACK',
    }))

    expect(description.stage).toBe('Rollback')
    expect(description.tone).toBe('danger')
  })
})
