import apiClient from './client'
import type {
  Conversation,
  CreateConversationPayload,
  Message,
  SendMessageResponse,
  UpdateConversationPayload,
} from './types'

export const conversationApi = {
  list(): Promise<Conversation[]> {
    return apiClient.get('/conversations')
  },

  get(conversationId: string): Promise<Conversation> {
    return apiClient.get(`/conversations/${conversationId}`)
  },

  create(payload: CreateConversationPayload = {}): Promise<Conversation> {
    return apiClient.post('/conversations', payload)
  },

  update(conversationId: string, payload: UpdateConversationPayload): Promise<Conversation> {
    return apiClient.patch(`/conversations/${conversationId}`, payload)
  },

  archive(conversationId: string): Promise<null> {
    return apiClient.delete(`/conversations/${conversationId}`)
  },

  messages(conversationId: string): Promise<Message[]> {
    return apiClient.get(`/conversations/${conversationId}/messages`)
  },

  sendMessage(conversationId: string, content: string): Promise<SendMessageResponse> {
    return apiClient.post(`/conversations/${conversationId}/messages`, { content })
  },
}
