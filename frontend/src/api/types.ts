export interface ApiErrorDetail {
  detail?: string | Array<{ msg: string }>
  message?: string
}

export interface AuthToken {
  access_token: string
  user: User
}

export interface User {
  id: string
  email: string
  display_name?: string | null
  is_enabled: boolean
  created_at: string
  updated_at: string
}

export interface Conversation {
  id: string
  thread_id?: number | null
  user_id: string
  llm_config_id?: string | null
  title?: string | null
  chat_mode: string
  metadata: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface Message {
  id: string
  conversation_id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  content_parts?: unknown[]
  paragraphs?: string[]
  status?: 'completed' | 'streaming' | 'failed' | 'partial' | 'cancelled'
  sequence_no?: number
  langgraph_message_id?: string | null
  llm_config_id?: string | null
  provider?: string | null
  model?: string | null
  token_usage?: Record<string, unknown>
  response_metadata?: Record<string, unknown>
  token_count?: number
  created_at: string
  updated_at?: string
}

export interface LlmConfig {
  id: string
  user_id: string
  name: string
  provider: string
  model: string
  base_url?: string | null
  has_api_key: boolean
  provider_options?: Record<string, unknown> | null
  is_default: boolean
  is_enabled: boolean
  created_at: string
  updated_at: string
}

export interface LlmProvider {
  id: string
  name: string
  requires_api_key: boolean
  supports_custom_base_url: boolean
  supports_model_list: boolean
  default_base_url?: string | null
}

export interface LlmModel {
  id: string
}

export interface LoginPayload {
  email: string
  password: string
}

export interface RegisterPayload {
  email: string
  password: string
  display_name?: string
}

export interface CreateConversationPayload {
  title?: string | null
  chat_mode?: string
  metadata?: Record<string, unknown>
}

export interface UpdateConversationPayload {
  title?: string
  llm_config_id?: string | null
  chat_mode?: string
  metadata?: Record<string, unknown>
}

export interface SendMessagePayload {
  content: string
}

export interface SendMessageResponse {
  user_message: Message
  assistant_message: Message
}

export type StreamMessageEvent =
  | {
      event: 'message.created'
      data: {
        user_message: Message
        assistant_message: Message
      }
    }
  | {
      event: 'message.delta'
      data: {
        message_id: string
        delta: string
      }
    }
  | {
      event: 'message.completed' | 'message.failed' | 'message.cancelled'
      data: {
        message: Message
      }
    }

export interface ProbeModelsPayload {
  provider: string
  base_url?: string | null
  api_key?: string | null
  provider_options?: Record<string, unknown>
}

export interface CreateLlmConfigPayload {
  name: string
  provider: string
  model: string
  base_url?: string | null
  api_key?: string | null
  provider_options?: Record<string, unknown>
  is_default?: boolean
}

export interface UpdateLlmConfigPayload {
  name?: string
  provider?: string
  model?: string
  base_url?: string | null
  api_key?: string | null
  provider_options?: Record<string, unknown>
  is_default?: boolean
}
