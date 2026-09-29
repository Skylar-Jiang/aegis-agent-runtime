import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CoreWorkbench } from './CoreWorkbench'
import { FakeToolGateway } from './mock/FakeToolGateway'
import {
  HttpMockToolGateway,
  type CoreGatewayClient,
  type CoreSnapshot,
} from './gateway'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('Core mock workbench', () => {
  it('shows a clearly labeled simulation and confirmation recheck before execution', async () => {
    const client = new FakeToolGateway()
    const resolve = vi.spyOn(client, 'resolveConfirmation')
    render(<CoreWorkbench client={client} />)
    expect(screen.getByRole('note')).toHaveTextContent('不执行真实工具')
    expect(await screen.findByTestId('gateway-decision')).toHaveTextContent(
      'REQUIRE_CONFIRMATION',
    )
    fireEvent.click(screen.getByRole('button', { name: '模拟批准并重新检查' }))
    await waitFor(() =>
      expect(screen.getByTestId('confirmation-status')).toHaveTextContent(
        'CONFIRMED',
      ),
    )
    expect(resolve).toHaveBeenCalledWith('p2-confirmation-1', true)
    const rows = within(
      screen.getByRole('list', { name: '有序行为事件' }),
    ).getAllByRole('listitem')
    expect(rows.map((row) => row.dataset.sequence)).toEqual([
      '1',
      '2',
      '3',
      '4',
    ])
    expect(rows[2]).toHaveTextContent('GATEWAY_EVALUATED')
    expect(rows[3]).toHaveTextContent('EXECUTED')
    expect(
      screen.queryByRole('button', { name: '模拟批准并重新检查' }),
    ).not.toBeInTheDocument()
  })

  it('rejects a confirmation without showing an executed event', async () => {
    render(<CoreWorkbench client={new FakeToolGateway()} />)
    fireEvent.click(await screen.findByRole('button', { name: '模拟拒绝' }))
    await waitFor(() =>
      expect(screen.getByTestId('gateway-decision')).toHaveTextContent('DENY'),
    )
    expect(screen.getByTestId('confirmation-status')).toHaveTextContent(
      'REJECTED',
    )
    expect(screen.queryByText(/GATEWAY_EXECUTED/)).not.toBeInTheDocument()
  })

  it.each([
    ['allow', 'ALLOW'],
    ['deny', 'DENY'],
    ['replan', 'REQUIRE_REPLAN'],
  ])(
    'displays %s directly from the mock response',
    async (scenario, decision) => {
      render(<CoreWorkbench client={new FakeToolGateway()} />)
      await screen.findByTestId('gateway-decision')
      fireEvent.change(screen.getByLabelText('演示场景'), {
        target: { value: scenario },
      })
      await waitFor(() =>
        expect(screen.getByTestId('gateway-decision')).toHaveTextContent(
          decision,
        ),
      )
      expect(
        screen.queryByRole('button', { name: '模拟批准并重新检查' }),
      ).not.toBeInTheDocument()
    },
  )

  it('handles empty history and a failed load without retaining stale task facts', async () => {
    render(<CoreWorkbench client={new FakeToolGateway()} />)
    await screen.findByTestId('gateway-decision')
    fireEvent.change(screen.getByLabelText('演示场景'), {
      target: { value: 'empty' },
    })
    expect(await screen.findByText('此任务暂无行为事件。')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('演示场景'), {
      target: { value: 'error' },
    })
    expect(await screen.findByRole('alert')).toHaveTextContent(
      '模拟服务暂时不可用',
    )
    expect(screen.queryByTestId('gateway-decision')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重新载入' })).toBeEnabled()
  })

  it('preserves a pending confirmation when the service fails and allows retry', async () => {
    const client = new FakeToolGateway()
    vi.spyOn(client, 'resolveConfirmation').mockRejectedValueOnce(
      new Error('模拟网络中断'),
    )
    render(<CoreWorkbench client={client} />)
    fireEvent.click(
      await screen.findByRole('button', { name: '模拟批准并重新检查' }),
    )
    expect(await screen.findByRole('alert')).toHaveTextContent('模拟网络中断')
    expect(screen.getByTestId('confirmation-status')).toHaveTextContent(
      'WAITING_CONFIRMATION',
    )
    fireEvent.click(screen.getByRole('button', { name: '模拟批准并重新检查' }))
    await waitFor(() =>
      expect(screen.getByTestId('confirmation-status')).toHaveTextContent(
        'CONFIRMED',
      ),
    )
  })

  it('does not let an older response replace the selected scenario', async () => {
    const fake = new FakeToolGateway()
    const stale = await fake.load('confirm')
    let resolveOld!: (value: CoreSnapshot) => void
    const client: CoreGatewayClient = {
      load: vi
        .fn()
        .mockImplementationOnce(
          () =>
            new Promise<CoreSnapshot>((resolve) => {
              resolveOld = resolve
            }),
        )
        .mockImplementation(() => fake.load('deny')),
      resolveConfirmation: vi.fn(),
    }
    render(<CoreWorkbench client={client} />)
    fireEvent.change(screen.getByLabelText('演示场景'), {
      target: { value: 'deny' },
    })
    await waitFor(() =>
      expect(screen.getByTestId('gateway-decision')).toHaveTextContent('DENY'),
    )
    resolveOld(stale)
    await waitFor(() =>
      expect(screen.getByTestId('gateway-decision')).toHaveTextContent('DENY'),
    )
  })

  it('sorts server events by sequence and ignores other tasks', async () => {
    const fake = new FakeToolGateway()
    const snapshot = await fake.load('allow')
    snapshot.events.reverse()
    snapshot.events.push({
      ...snapshot.events[0],
      event_id: 'foreign',
      task_id: 'foreign',
    })
    render(
      <CoreWorkbench
        client={{ load: async () => snapshot, resolveConfirmation: vi.fn() }}
      />,
    )
    await screen.findByTestId('gateway-decision')
    const rows = within(
      screen.getByRole('list', { name: '有序行为事件' }),
    ).getAllByRole('listitem')
    expect(rows.map((row) => row.dataset.sequence)).toEqual(['1', '2'])
  })

  it('prevents double confirmation while a request is outstanding', async () => {
    const client = new FakeToolGateway()
    vi.spyOn(client, 'resolveConfirmation').mockImplementation(
      () => new Promise(() => {}),
    )
    render(<CoreWorkbench client={client} />)
    const button = await screen.findByRole('button', {
      name: '模拟批准并重新检查',
    })
    fireEvent.click(button)
    fireEvent.click(button)
    expect(client.resolveConfirmation).toHaveBeenCalledTimes(1)
    expect(button).toBeDisabled()
  })
})

describe('mock boundary', () => {
  it('rejects unknown and already resolved confirmation ids', async () => {
    const gateway = new FakeToolGateway()
    await gateway.load('confirm')
    await expect(gateway.resolveConfirmation('wrong', true)).rejects.toThrow(
      '不存在',
    )
    await gateway.resolveConfirmation('p2-confirmation-1', false)
    await expect(
      gateway.resolveConfirmation('p2-confirmation-1', true),
    ).rejects.toThrow('不存在')
  })

  it('detects production HTML fallback instead of presenting it as successful data', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response('<html/>', {
          headers: { 'Content-Type': 'text/html' },
        }),
      ),
    )
    await expect(new HttpMockToolGateway().load('allow')).rejects.toThrow(
      'pnpm dev',
    )
  })
})
