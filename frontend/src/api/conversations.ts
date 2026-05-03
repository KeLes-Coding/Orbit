import apiClient, { API_BASE_URL, clearStoredToken, getStoredToken } from './client'
import type {
  Conversation,
  BranchSwitchResponse,
  CreateConversationMessagePayload,
  CreateConversationPayload,
  ForkConversationResponse,
  Message,
  SendMessageResponse,
  StreamMessageEvent,
  UpdateConversationPayload,
} from './types'

async function parseStreamError(response: Response): Promise<string> {
  const fallback = `Request failed with status ${response.status}`
  try {
    const body = await response.json()
    const detail = body?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      return detail.map((item) => item?.msg).filter(Boolean).join(', ') || fallback
    }
    return body?.message || fallback
  } catch {
    return fallback
  }
}

function createHeaders(): HeadersInit {
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
  }
  const token = getStoredToken()
  if (token) {
    headers.Authorization = `Bearer ${token}`
  }
  return headers
}

function resolveApiUrl(path: string): string {
  return `${API_BASE_URL}${path}`
}

async function* parseSseStream(body: ReadableStream<Uint8Array>): AsyncGenerator<StreamMessageEvent> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let eventName = ''
  let dataLines: string[] = []

  const consumeBlock = function* (): Generator<StreamMessageEvent> {
    if (!eventName && dataLines.length === 0) return
    const rawData = dataLines.join('\n')
    const event = eventName
    eventName = ''
    dataLines = []
    if (!event || !rawData) return
    yield {
      event,
      data: JSON.parse(rawData),
    } as StreamMessageEvent
  }

  try {
    while (true) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value, { stream: !done })

      let newlineIndex = buffer.indexOf('\n')
      while (newlineIndex >= 0) {
        const rawLine = buffer.slice(0, newlineIndex)
        buffer = buffer.slice(newlineIndex + 1)
        const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine

        if (line === '') {
          yield* consumeBlock()
        } else if (line.startsWith('event:')) {
          eventName = line.slice(6).trim()
        } else if (line.startsWith('data:')) {
          dataLines.push(line.slice(5).trimStart())
        }

        newlineIndex = buffer.indexOf('\n')
      }

      if (done) break
    }

    if (buffer.trim() || eventName || dataLines.length > 0) {
      if (buffer.startsWith('data:')) {
        dataLines.push(buffer.slice(5).trimStart())
      }
      yield* consumeBlock()
    }
  } finally {
    reader.releaseLock()
  }
}

async function* fetchSse(
  input: string,
  init: RequestInit,
): AsyncGenerator<StreamMessageEvent> {
  // 所有 SSE 接口都走同一层 fetch 封装，避免重复处理鉴权和错误解析。
  const response = await fetch(resolveApiUrl(input), init)

  if (response.status === 401) {
    clearStoredToken()
  }
  if (!response.ok) {
    throw new Error(await parseStreamError(response))
  }
  if (!response.body) {
    throw new Error('Streaming response is not readable')
  }

  yield* parseSseStream(response.body)
}

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

  async *streamMessage(
    conversationId: string,
    content: string,
    signal?: AbortSignal,
  ): AsyncGenerator<StreamMessageEvent> {
    // 流式接口不能走 axios 的 JSON 解析，直接使用 fetch 读取 SSE 字节流。
    yield* fetchSse(`/conversations/${conversationId}/messages/stream`, {
      method: 'POST',
      headers: createHeaders(),
      body: JSON.stringify({ content }),
      signal,
    })
  },

  async *streamRegenerateAssistant(
    conversationId: string,
    messageId: string,
    signal?: AbortSignal,
  ): AsyncGenerator<StreamMessageEvent> {
    yield* fetchSse(`/conversations/${conversationId}/messages/${messageId}/regenerate/stream`, {
      method: 'POST',
      headers: createHeaders(),
      signal,
    })
  },

  async *streamEditUserMessage(
    conversationId: string,
    messageId: string,
    content: string,
    signal?: AbortSignal,
  ): AsyncGenerator<StreamMessageEvent> {
    yield* fetchSse(`/conversations/${conversationId}/messages/${messageId}/edit/stream`, {
      method: 'POST',
      headers: createHeaders(),
      body: JSON.stringify({ content }),
      signal,
    })
  },

  async *streamNewConversationMessage(
    payload: CreateConversationMessagePayload,
    signal?: AbortSignal,
  ): AsyncGenerator<StreamMessageEvent> {
    yield* fetchSse('/conversations/messages/stream', {
      method: 'POST',
      headers: createHeaders(),
      body: JSON.stringify(payload),
      signal,
    })
  },

  async *resumeStream(
    conversationId: string,
    lastSeq: number,
    signal?: AbortSignal,
  ): AsyncGenerator<StreamMessageEvent> {
    // 恢复订阅接口只关心会话和 last_seq；具体 active stream 由后端按会话指针解析。
    const query = new URLSearchParams({ last_seq: String(lastSeq) })
    yield* fetchSse(`/conversations/${conversationId}/stream?${query.toString()}`, {
      method: 'GET',
      headers: createHeaders(),
      signal,
    })
  },

  async cancelMessage(conversationId: string, messageId: string): Promise<Message> {
    // 取消接口返回的是当前消息快照；最终状态仍以后端流协程落库为准。
    return apiClient.post(`/conversations/${conversationId}/messages/${messageId}/cancel`)
  },

  switchBranch(conversationId: string, messageId: string): Promise<BranchSwitchResponse> {
    return apiClient.post(`/conversations/${conversationId}/messages/${messageId}/switch`)
  },

  forkConversation(
    conversationId: string,
    messageId: string,
    title?: string | null,
  ): Promise<ForkConversationResponse> {
    return apiClient.post(`/conversations/${conversationId}/messages/${messageId}/fork`, { title })
  },
}
