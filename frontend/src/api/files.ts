import apiClient, { API_BASE_URL, getStoredToken } from './client'
import type { ConversationFile } from './types'

function fileUploadHeaders(): HeadersInit {
  const headers: HeadersInit = {}
  const token = getStoredToken()
  if (token) {
    headers.Authorization = `Bearer ${token}`
  }
  return headers
}

export const fileApi = {
  async uploadPending(file: File): Promise<ConversationFile> {
    const formData = new FormData()
    formData.append('file', file)
    const url = `${API_BASE_URL}/files/pending`
    const response = await fetch(url, {
      method: 'POST',
      headers: fileUploadHeaders(),
      body: formData,
    })
    if (!response.ok) {
      const detail = await response.json().catch(() => ({ detail: 'Upload failed' }))
      throw new Error(typeof detail.detail === 'string' ? detail.detail : 'Upload failed')
    }
    return response.json()
  },

  async uploadToConversation(conversationId: string, file: File): Promise<ConversationFile> {
    const formData = new FormData()
    formData.append('file', file)
    const url = `${API_BASE_URL}/conversations/${conversationId}/files`
    const response = await fetch(url, {
      method: 'POST',
      headers: fileUploadHeaders(),
      body: formData,
    })
    if (!response.ok) {
      const detail = await response.json().catch(() => ({ detail: 'Upload failed' }))
      throw new Error(typeof detail.detail === 'string' ? detail.detail : 'Upload failed')
    }
    return response.json()
  },

  getMetadata(fileId: string): Promise<ConversationFile> {
    return apiClient.get(`/files/${fileId}`)
  },

  getContentUrl(fileId: string): string {
    const base = `${API_BASE_URL}/files/${fileId}/content`
    const token = getStoredToken()
    return token ? `${base}?token=${encodeURIComponent(token)}` : base
  },

  listConversationFiles(conversationId: string): Promise<ConversationFile[]> {
    return apiClient.get(`/conversations/${conversationId}/files`)
  },
}
