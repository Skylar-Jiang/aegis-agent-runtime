import { get, post, put } from './client'
import type {
  Conversation,
  ConversationTurnResponse,
  SecurityProfile,
  SecurityProfileUpdate,
  ToolDefinition,
} from '../types/contracts'

export function getSecurityProfile(
  profileId = 'default',
): Promise<SecurityProfile> {
  return get(`/security-profiles/${encodeURIComponent(profileId)}`)
}

export function updateSecurityProfile(
  update: SecurityProfileUpdate,
  profileId = 'default',
): Promise<SecurityProfile> {
  return put(`/security-profiles/${encodeURIComponent(profileId)}`, update)
}

export function listToolDefinitions(): Promise<ToolDefinition[]> {
  return get('/tools')
}

export function createConversation(title?: string): Promise<Conversation> {
  return post('/conversations', {
    title: title || null,
    security_profile_id: 'default',
  })
}

export function listConversations(limit = 50): Promise<Conversation[]> {
  return get(`/conversations?limit=${limit}`)
}

export function getConversation(conversationId: string): Promise<Conversation> {
  return get(`/conversations/${encodeURIComponent(conversationId)}`)
}

export function sendConversationMessage(
  conversationId: string,
  content: string,
): Promise<ConversationTurnResponse> {
  return post(`/conversations/${encodeURIComponent(conversationId)}/messages`, {
    content,
  })
}
