import { afterEach, describe, expect, it, vi } from 'vitest'

import { subscribeToTaskEvents } from './tasks'

class FakeEventSource extends EventTarget {
  static current: FakeEventSource | null = null

  onerror: (() => void) | null = null

  constructor() {
    super()
    FakeEventSource.current = this
  }

  close() {}
}

const originalEventSource = globalThis.EventSource

afterEach(() => {
  globalThis.EventSource = originalEventSource
  FakeEventSource.current = null
})

describe('subscribeToTaskEvents', () => {
  it('delivers a separate assistant response event', () => {
    globalThis.EventSource = FakeEventSource as unknown as typeof EventSource
    const onAssistantResponse = vi.fn()

    const stop = subscribeToTaskEvents('task-1', vi.fn(), vi.fn(), onAssistantResponse)
    FakeEventSource.current?.dispatchEvent(
      new MessageEvent('assistant', {
        data: JSON.stringify({ task_id: 'task-1', final_answer: 'The README was read.' }),
      }),
    )

    expect(onAssistantResponse).toHaveBeenCalledWith('The README was read.')
    stop?.()
  })
})
