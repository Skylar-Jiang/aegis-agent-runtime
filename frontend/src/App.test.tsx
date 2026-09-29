import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App'
import { useLanguageStore } from './i18n'

vi.mock('./api/health', () => ({
  getRuntimeHealth: vi.fn().mockResolvedValue({
    status: 'ok',
    phase: 'runtime-base-main-chain',
    mode: 'live-agent',
  }),
}))

vi.mock('./api/tasks', () => ({
  getTask: vi.fn(),
  getTaskEvents: vi
    .fn()
    .mockResolvedValue({ task_id: 'none', events: [], count: 0 }),
}))

vi.mock('./api/workbench', () => ({
  createConversation: vi.fn(),
  getConversation: vi
    .fn()
    .mockResolvedValue({
      conversation_id: 'legacy-conversation',
      title: 'Legacy',
      messages: [],
    }),
  listConversations: vi.fn().mockResolvedValue([]),
  sendConversationMessage: vi.fn(),
  getSecurityProfile: vi.fn().mockResolvedValue({
    profile_id: 'default',
    version: 3,
    created_at: '2026-09-11T00:00:00Z',
    allowed_actions: ['list_dir', 'read_file', 'create_file', 'write_file'],
    denied_actions: ['delete_file', 'run_shell'],
    resource_scopes: ['**'],
    allow_egress: false,
    max_affected_objects: 100,
    approval_policy: {
      required_actions: ['delete_file'],
      bulk_action_threshold: 20,
    },
  }),
  listToolDefinitions: vi.fn().mockResolvedValue([]),
  updateSecurityProfile: vi.fn(),
}))

vi.mock('./api/approvals', () => ({
  listApprovals: vi.fn().mockResolvedValue([]),
  denyApproval: vi.fn(),
  grantApproval: vi.fn(),
}))

beforeEach(() => {
  window.history.pushState({}, '', '/conversations')
  useLanguageStore.setState({ language: 'en' })
})

afterEach(() => {
  cleanup()
  window.localStorage.removeItem('aegis-language')
  useLanguageStore.setState({ language: 'en' })
})

describe('App conversation workbench', () => {
  it('preserves the continuous conversation route', async () => {
    render(<App />)
    expect(
      screen.getByRole('button', { name: 'New conversation' }),
    ).toBeInTheDocument()
    expect(
      screen.getByPlaceholderText('Continue this conversation…'),
    ).toBeInTheDocument()
    expect(screen.getByText('Active safety boundary')).toBeInTheDocument()
    expect(await screen.findByText('default · v3')).toBeInTheDocument()
  })

  it('keeps task history, security, approvals and audit as inspectable views', () => {
    render(<App />)
    expect(screen.getByRole('link', { name: 'Task history' })).toHaveAttribute(
      'href',
      '/tasks',
    )
    expect(
      screen.getByRole('link', { name: 'Security settings' }),
    ).toHaveAttribute('href', '/settings/security')
    expect(screen.getByRole('link', { name: 'Approvals' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Audit' })).toBeInTheDocument()
  })

  it('switches the conversation workbench between English and Chinese', () => {
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: '中文' }))
    expect(screen.getByRole('button', { name: '新建对话' })).toBeInTheDocument()
    expect(screen.getByPlaceholderText('继续这段对话……')).toBeInTheDocument()
    expect(screen.getByText('当前安全边界')).toBeInTheDocument()
  })
})

it('opens legacy conversation links from the new Core landing route', async () => {
  window.history.pushState({}, '', '/?conversation_id=legacy-conversation')
  render(<App />)
  expect(
    await screen.findByRole('button', { name: 'New conversation' }),
  ).toBeInTheDocument()
  expect(window.location.pathname).toBe('/conversations')
  expect(window.location.search).toBe('?conversation_id=legacy-conversation')
})
