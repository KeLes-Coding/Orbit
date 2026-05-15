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
  thread_id?: string | null
  user_id: string
  llm_config_id?: string | null
  title?: string | null
  chat_mode: string
  summary?: string | null
  summary_updated_at?: string | null
  summary_message_count?: number
  has_active_run?: boolean
  next_message_sequence_no?: number
  active_leaf_message_id?: string | null
  forked_from_conversation_id?: string | null
  forked_from_message_id?: string | null
  summary_leaf_message_id?: string | null
  metadata: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface StreamEnvelope {
  stream_id: string
  seq: number
  event_id: string
}

export interface Message {
  id: string
  conversation_id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  reasoning_content?: string
  content_parts?: unknown[]
  paragraphs?: string[]
  status?: 'completed' | 'streaming' | 'failed' | 'partial' | 'cancelled'
  sequence_no?: number
  langgraph_message_id?: string | null
  parent_message_id?: string | null
  active_child_message_id?: string | null
  depth?: number
  source_message_id?: string | null
  revision_type?: 'normal' | 'edit' | 'regenerate' | 'fork_copy' | null
  sibling_index?: number
  sibling_count?: number
  previous_sibling_id?: string | null
  next_sibling_id?: string | null
  llm_config_id?: string | null
  provider?: string | null
  model?: string | null
  token_usage?: Record<string, unknown>
  response_metadata?: Record<string, unknown>
  token_count?: number
  created_at: string
  updated_at?: string
}

export interface ToolCallDelta {
  id?: string | null
  name?: string | null
  args?: unknown
  index?: number | null
  type?: string | null
}

export interface ToolResultDelta {
  tool_call_id?: string | null
  name: string
  args?: unknown
  output: string
  is_error?: boolean
}

export interface LlmConfig {
  id: string
  user_id: string
  name: string
  provider: string
  models: string[]
  base_url?: string | null
  has_api_key: boolean
  provider_options?: Record<string, unknown> | null
  is_default: boolean
  is_enabled: boolean
  supports_vision: boolean
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
  name?: string | null
  description?: string | null
  owned_by?: string | null
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

export interface CreateConversationMessagePayload {
  content: string
  llm_config_id?: string | null
  model?: string | null
  chat_mode?: string
  metadata?: Record<string, unknown>
  idempotency_key?: string | null
  file_ids?: string[]
}

export interface UpdateConversationPayload {
  title?: string
  llm_config_id?: string | null
  chat_mode?: string
  metadata?: Record<string, unknown>
}

export interface SendMessagePayload {
  content: string
  llm_config_id?: string | null
  parent_message_id?: string | null
  idempotency_key?: string | null
  model?: string | null
  chat_mode?: string | null
  file_ids?: string[]
}

export interface ConversationFile {
  id: string
  user_id: string
  conversation_id?: string | null
  original_name: string
  file_type: string
  file_size: number
  file_extension?: string | null
  storage_type: string
  extraction_status: 'pending' | 'processing' | 'success' | 'failed' | 'skipped'
  extraction_error?: string | null
  bind_status: 'pending' | 'bound' | 'deleted'
  created_at: string
  updated_at: string
}

export type ContentPart =
  | { type: 'text'; text: string }
  | {
      type: 'file'
      file_id: string
      name: string
      mime_type: string
      file_size: number
      extracted_text?: string | null
    }

export interface BranchSwitchResponse {
  active_leaf_message_id?: string | null
  messages: Message[]
}

export interface ForkConversationResponse {
  conversation: Conversation
  messages: Message[]
}

export interface ActiveStreamResponse {
  conversation_id: string
  message_id: string
  assistant_message_id: string
  stream_id: string
}

export type StreamMessageEvent =
  | {
      event: 'conversation.created'
      data: StreamEnvelope & {
        conversation: Conversation
      }
    }
  | {
      event: 'conversation.updated'
      data: StreamEnvelope & {
        conversation: Conversation
      }
    }
  | {
      event: 'conversation.run_state_changed'
      data: StreamEnvelope & {
        conversation_id: string
        has_active_run: boolean
      }
    }
  | {
      event: 'message.created'
      data: StreamEnvelope & {
        user_message?: Message
        assistant_message: Message
      }
    }
  | {
      event: 'message.delta'
      data: StreamEnvelope & {
        message_id: string
        delta: string
      }
    }
  | {
      event: 'message.reasoning_delta'
      data: StreamEnvelope & {
        message_id: string
        delta: string
      }
    }
  | {
      event: 'message.tool_call_delta'
      data: StreamEnvelope & {
        message_id: string
        tool_calls: ToolCallDelta[]
      }
    }
  | {
      event: 'message.tool_result'
      data: StreamEnvelope & {
        message_id: string
        tool_results: ToolResultDelta[]
      }
    }
  | {
      event: 'message.completed' | 'message.failed' | 'message.cancelled'
      data: StreamEnvelope & {
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
  models: string[]
  base_url?: string | null
  api_key?: string | null
  provider_options?: Record<string, unknown>
  is_default?: boolean
  supports_vision?: boolean
}

export interface UpdateLlmConfigPayload {
  name?: string
  provider?: string
  models?: string[]
  base_url?: string | null
  api_key?: string | null
  provider_options?: Record<string, unknown>
  is_default?: boolean
  supports_vision?: boolean
}
