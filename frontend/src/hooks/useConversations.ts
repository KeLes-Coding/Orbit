import { useMemo, useCallback, useEffect, useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { conversationApi } from '@/api/conversations'
import { useOrbitStore } from '@/stores/useOrbitStore'
import type { Conversation, Message, StreamMessageEvent } from '@/api/types'

interface UseConversationsOptions {
  enableStreamResume?: boolean
}

interface NormalizedMessage extends Message {
  paragraphs: string[]
}

interface ActiveStreamHandle {
  conversationId: string | null
  messageId: string | null
  streamId: string | null
  lastSeq: number
  controller: AbortController
}

function normalizeMessage(message: Message | null | undefined): NormalizedMessage | null {
  if (!message) return null
  return {
    ...message,
    content: message.content || '',
    reasoning_content: message.reasoning_content || '',
    paragraphs: (message.content || '').split(/\n{2,}/).filter(Boolean),
  }
}

function sortMessages(messages: Message[]): Message[] {
  return [...messages].sort((a, b) => (a.sequence_no ?? 0) - (b.sequence_no ?? 0))
}

function replaceLocalExchange(
  messages: Message[],
  userMessage: Message,
  assistantMessage: Message,
): Message[] {
  const filtered = messages.filter((message) => !String(message.id).startsWith('local-'))
  return sortMessages([...filtered, userMessage, assistantMessage])
}

function upsertMessage(messages: Message[], nextMessage: Message): Message[] {
  const exists = messages.some((message) => message.id === nextMessage.id)
  if (!exists) return sortMessages([...messages, nextMessage])
  return sortMessages(
    messages.map((message) => (message.id === nextMessage.id ? { ...message, ...nextMessage } : message)),
  )
}

function replaceVisibleTail(
  messages: Message[],
  userMessage: Message | undefined,
  assistantMessage: Message,
): Message[] {
  const withoutLocal = messages.filter((message) => !String(message.id).startsWith('local-'))
  const parentId = userMessage?.parent_message_id ?? assistantMessage.parent_message_id
  const parentIndex = parentId
    ? withoutLocal.findIndex((message) => message.id === parentId)
    : -1
  const base = parentIndex >= 0 ? withoutLocal.slice(0, parentIndex + 1) : []
  if (userMessage) {
    return [...base, userMessage, assistantMessage]
  }

  const previousAssistant = parentId
    ? [...withoutLocal]
        .reverse()
        .find((message) => message.role === 'assistant' && message.parent_message_id === parentId)
    : null
  const siblingCount = Math.max(
    assistantMessage.sibling_count ?? 1,
    (previousAssistant?.sibling_count ?? 1) + (previousAssistant ? 1 : 0),
  )
  const nextAssistant = previousAssistant
    ? {
        ...assistantMessage,
        sibling_count: siblingCount,
        sibling_index: assistantMessage.sibling_index ?? siblingCount,
        previous_sibling_id: assistantMessage.previous_sibling_id ?? previousAssistant.id,
      }
    : assistantMessage

  return [...base, nextAssistant]
}

function appendMessageDelta(messages: Message[], messageId: string, delta: string): Message[] {
  return messages.map((message) =>
    message.id === messageId
      ? {
          ...message,
          content: `${message.content || ''}${delta}`,
          status: 'streaming',
        }
      : message,
  )
}

function appendMessageReasoningDelta(messages: Message[], messageId: string, delta: string): Message[] {
  return messages.map((message) =>
    message.id === messageId
      ? {
          ...message,
          reasoning_content: `${message.reasoning_content || ''}${delta}`,
          status: 'streaming',
        }
      : message,
  )
}

function createIdempotencyKey(prefix: string): string {
  // 前端每次显式生成幂等键，让重复点击/重试优先命中后端复用逻辑而不是重复落树。
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `${prefix}-${crypto.randomUUID()}`
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

export function useConversations(
  hasUser: boolean,
  options: UseConversationsOptions = {},
) {
  const { enableStreamResume = true } = options
  const activeConversationId = useOrbitStore((s) => s.activeConversationId)
  const pendingConversationLlmConfigId = useOrbitStore((s) => s.pendingConversationLlmConfigId)
  const pendingConversationLlmModel = useOrbitStore((s) => s.pendingConversationLlmModel)
  const draft = useOrbitStore((s) => s.draft)
  const isCreatingConversationTitle = useOrbitStore((s) => s.isCreatingConversationTitle)
  const setActiveConversationId = useOrbitStore((s) => s.setActiveConversationId)
  const setPendingConversationLlmConfigId = useOrbitStore((s) => s.setPendingConversationLlmConfigId)
  const setPendingConversationLlmModel = useOrbitStore((s) => s.setPendingConversationLlmModel)
  const setDraft = useOrbitStore((s) => s.setDraft)
  const setErrorMessage = useOrbitStore((s) => s.setErrorMessage)
  const setActiveView = useOrbitStore((s) => s.setActiveView)
  const setIsCreatingConversationTitle = useOrbitStore((s) => s.setIsCreatingConversationTitle)
  const markConversationCompletedOffscreen = useOrbitStore((s) => s.markConversationCompletedOffscreen)
  const clearConversationCompletionNotice = useOrbitStore((s) => s.clearConversationCompletionNotice)
  const queryClient = useQueryClient()
  const [streamingConversationIds, setStreamingConversationIds] = useState<Set<string>>(() => new Set())
  const [isPendingNewConversationStream, setIsPendingNewConversationStream] = useState(false)
  const activeStreamRefs = useRef<Record<string, ActiveStreamHandle>>({})
  const pendingNewConversationStreamRef = useRef<ActiveStreamHandle | null>(null)
  const streamCursorRef = useRef<Record<string, { streamId: string | null; lastSeq: number }>>({})
  const activeConversationIdRef = useRef(activeConversationId)

  useEffect(() => {
    activeConversationIdRef.current = activeConversationId
  }, [activeConversationId])

  const conversationsQuery = useQuery({
    queryKey: ['conversations'],
    queryFn: conversationApi.list,
    enabled: hasUser,
  })

  const messagesQuery = useQuery({
    queryKey: ['messages', activeConversationId],
    queryFn: () => conversationApi.messages(activeConversationId!),
    enabled: hasUser && !!activeConversationId,
  })

  const conversations = conversationsQuery.data || []
  const rawMessages = messagesQuery.data || []

  const messages = useMemo(
    () => rawMessages.map(normalizeMessage).filter(Boolean) as NormalizedMessage[],
    [rawMessages],
  )

  const sortedConversations = useMemo(() => {
    const sorted = [...conversations].sort(
      (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
    )
    if (!isCreatingConversationTitle) return sorted
    const now = new Date().toISOString()
    return [
      {
        id: 'local-pending-title',
        thread_id: null,
        user_id: 'local',
        llm_config_id: null,
        title: 'Generating title...',
        chat_mode: 'chat',
        metadata: { pendingTitle: true },
        created_at: now,
        updated_at: now,
      },
      ...sorted,
    ]
  }, [conversations, isCreatingConversationTitle])

  const activeConversation = useMemo(
    () => conversations.find((c) => c.id === activeConversationId),
    [conversations, activeConversationId],
  )
  const isActiveConversationStreaming = activeConversationId
    ? streamingConversationIds.has(activeConversationId)
    : isPendingNewConversationStream

  // 只有首次没有任何缓存消息时才显示 loading，切页返回时保留现有内容避免闪烁。
  const isLoadingMessages = rawMessages.length === 0 && (messagesQuery.isLoading || messagesQuery.isFetching)

  const formatConversationTitle = useCallback(
    (conversation: Conversation) =>
      conversation.title || 'Untitled chat',
    [],
  )

  const updateStreamCursor = useCallback((conversationId: string, streamId: string, seq: number) => {
    // 前端只需要记住每个会话最近收到的 seq，恢复时把它回传给后端即可。
    const current = streamCursorRef.current[conversationId]
    if (!current || current.streamId !== streamId || seq > current.lastSeq) {
      streamCursorRef.current[conversationId] = { streamId, lastSeq: seq }
    }
  }, [])

  const clearStreamCursor = useCallback((conversationId: string) => {
    delete streamCursorRef.current[conversationId]
  }, [])

  const markConversationStreaming = useCallback((conversationId: string, isStreaming: boolean) => {
    setStreamingConversationIds((current) => {
      const next = new Set(current)
      if (isStreaming) {
        next.add(conversationId)
      } else {
        next.delete(conversationId)
      }
      return next
    })
  }, [])

  const markConversationStreamState = useCallback(
    (
      conversationId: string,
      streamId: string | null,
      messageId: string | null,
      hasActiveRun?: boolean,
    ) => {
      queryClient.setQueryData<Conversation[]>(['conversations'], (old = []) =>
        old.map((conversation) =>
          conversation.id === conversationId
            ? {
                ...conversation,
                active_stream_id: streamId,
                active_stream_message_id: messageId,
                has_active_run:
                  typeof hasActiveRun === 'boolean'
                    ? hasActiveRun
                    : streamId
                      ? true
                      : conversation.has_active_run,
              }
            : conversation,
        ),
      )
    },
    [queryClient],
  )

  const upsertConversation = useCallback(
    (conversation: Conversation) => {
      queryClient.setQueryData<Conversation[]>(['conversations'], (old = []) => [
        conversation,
        ...old.filter((item) => item.id !== conversation.id),
      ])
    },
    [queryClient],
  )

  const applyStreamEvent = useCallback(
    (conversationId: string, streamEvent: StreamMessageEvent, controller: AbortController) => {
      updateStreamCursor(conversationId, streamEvent.data.stream_id, streamEvent.data.seq)

      if (streamEvent.event === 'conversation.created') {
        // New Chat 的首条事件里也带 stream 元信息，先把游标和会话状态记下来。
        markConversationStreamState(
          streamEvent.data.conversation.id,
          streamEvent.data.stream_id,
          streamEvent.data.conversation.active_stream_message_id ?? null,
        )
        upsertConversation(streamEvent.data.conversation)
        return
      }

      if (streamEvent.event === 'conversation.updated') {
        upsertConversation(streamEvent.data.conversation)
        return
      }

      if (streamEvent.event === 'conversation.run_state_changed') {
        // 会话级 has_active_run 只是 UI 缓存；真正停止动画要跟随后端显式广播。
        queryClient.setQueryData<Conversation[]>(['conversations'], (old = []) =>
          old.map((conversation) =>
            conversation.id === streamEvent.data.conversation_id
              ? {
                  ...conversation,
                  has_active_run: streamEvent.data.has_active_run,
                  active_stream_id: streamEvent.data.has_active_run ? conversation.active_stream_id : null,
                  active_stream_message_id: streamEvent.data.has_active_run
                    ? conversation.active_stream_message_id
                    : null,
                }
              : conversation,
          ),
        )
        if (!streamEvent.data.has_active_run) {
          markConversationStreamState(conversationId, null, null, false)
          markConversationStreaming(conversationId, false)
          if (activeStreamRefs.current[conversationId]?.controller === controller) {
            delete activeStreamRefs.current[conversationId]
          }
          clearStreamCursor(conversationId)
        }
        return
      }

      if (streamEvent.event === 'message.created') {
        activeStreamRefs.current[conversationId] = {
          conversationId,
          messageId: streamEvent.data.assistant_message.id,
          streamId: streamEvent.data.stream_id,
          lastSeq: streamEvent.data.seq,
          controller,
        }
        markConversationStreaming(conversationId, true)
        markConversationStreamState(
          conversationId,
          streamEvent.data.stream_id,
          streamEvent.data.assistant_message.id,
        )
        queryClient.setQueryData<Message[]>(['messages', conversationId], (old = []) => {
          return replaceVisibleTail(
            old,
            streamEvent.data.user_message,
            streamEvent.data.assistant_message,
          )
        })
        return
      }

      if (streamEvent.event === 'message.delta') {
        queryClient.setQueryData<Message[]>(['messages', conversationId], (old = []) =>
          appendMessageDelta(old, streamEvent.data.message_id, streamEvent.data.delta),
        )
        return
      }

      if (streamEvent.event === 'message.reasoning_delta') {
        queryClient.setQueryData<Message[]>(['messages', conversationId], (old = []) =>
          appendMessageReasoningDelta(old, streamEvent.data.message_id, streamEvent.data.delta),
        )
        return
      }

      if (
        streamEvent.event === 'message.completed' ||
        streamEvent.event === 'message.failed' ||
        streamEvent.event === 'message.cancelled'
      ) {
        markConversationStreamState(conversationId, null, null, false)
        markConversationStreaming(conversationId, false)
        if (activeStreamRefs.current[conversationId]?.controller === controller) {
          delete activeStreamRefs.current[conversationId]
        }
        clearStreamCursor(conversationId)
        if (
          streamEvent.event === 'message.completed' &&
          activeConversationIdRef.current !== conversationId
        ) {
          markConversationCompletedOffscreen(conversationId)
        }
      }

      queryClient.setQueryData<Message[]>(['messages', conversationId], (old = []) =>
        upsertMessage(old, streamEvent.data.message),
      )
    },
    [
      clearStreamCursor,
      markConversationCompletedOffscreen,
      markConversationStreamState,
      markConversationStreaming,
      queryClient,
      updateStreamCursor,
      upsertConversation,
    ],
  )

  const selectConversation = useCallback(
    (conversationId: string) => {
      setActiveView('chat')
      setActiveConversationId(conversationId)
      clearConversationCompletionNotice(conversationId)
      setErrorMessage('')
    },
    [
      clearConversationCompletionNotice,
      setActiveView,
      setActiveConversationId,
      setErrorMessage,
    ],
  )

  const streamMessage = useCallback(
    async (conversationId: string, content: string, model?: string | null) => {
      await queryClient.cancelQueries({ queryKey: ['messages', conversationId] })
      const previousMessages = queryClient.getQueryData<Message[]>(['messages', conversationId]) || []
      const conversation = queryClient
        .getQueryData<Conversation[]>(['conversations'])
        ?.find((item) => item.id === conversationId)
      // 普通发送不再默认绑定“唯一活跃流”，而是显式把当前可见 leaf 作为 base message 传给后端。
      const parentMessageId = conversation?.active_leaf_message_id ?? null
      const idempotencyKey = createIdempotencyKey('msg')
      const localId = Date.now()
      const controller = new AbortController()

      // 后端返回真实消息前先插入本地占位，保证发送后 UI 立即有反馈。
      queryClient.setQueryData<Message[]>(['messages', conversationId], [
        ...previousMessages,
        {
          id: `local-user-${localId}`,
          conversation_id: conversationId,
          role: 'user',
          content,
          status: 'completed',
          created_at: new Date().toISOString(),
        },
        {
          id: `local-assistant-${localId}`,
          conversation_id: conversationId,
          role: 'assistant',
          content: '',
          reasoning_content: '',
          status: 'streaming',
          created_at: new Date().toISOString(),
        },
      ])

      markConversationStreaming(conversationId, true)
      activeStreamRefs.current[conversationId] = {
        conversationId,
        messageId: null,
        streamId: null,
        lastSeq: 0,
        controller,
      }

      try {
        for await (const streamEvent of conversationApi.streamMessage(
          conversationId,
          {
            content,
            parent_message_id: parentMessageId,
            idempotency_key: idempotencyKey,
            model: model ?? null,
          },
          controller.signal,
        )) {
          applyStreamEvent(conversationId, streamEvent, controller)
        }
        queryClient.invalidateQueries({ queryKey: ['conversations'] })
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          return
        }
        queryClient.setQueryData(['messages', conversationId], previousMessages)
        setErrorMessage(error instanceof Error ? error.message : 'Streaming request failed.')
      } finally {
        if (activeStreamRefs.current[conversationId]?.controller === controller) {
          delete activeStreamRefs.current[conversationId]
        }
        markConversationStreaming(conversationId, false)
      }
    },
    [applyStreamEvent, markConversationStreaming, queryClient, setErrorMessage],
  )

  const streamNewConversationMessage = useCallback(
    async (content: string, llmConfigId: string | null, model?: string | null) => {
      const controller = new AbortController()
      let conversationId: string | null = null

      setIsPendingNewConversationStream(true)
      setIsCreatingConversationTitle(true)
      pendingNewConversationStreamRef.current = { conversationId: null, messageId: null, streamId: null, lastSeq: 0, controller }

      try {
        for await (const streamEvent of conversationApi.streamNewConversationMessage(
          {
            content,
            llm_config_id: llmConfigId,
            chat_mode: 'chat',
            metadata: {},
            idempotency_key: createIdempotencyKey('new-chat'),
            model: model ?? null,
          },
          controller.signal,
        )) {
          if (streamEvent.event === 'conversation.created') {
            const conversation = streamEvent.data.conversation
            conversationId = conversation.id
            pendingNewConversationStreamRef.current = null
            setIsPendingNewConversationStream(false)
            activeStreamRefs.current[conversation.id] = {
              conversationId,
              messageId: null,
              streamId: streamEvent.data.stream_id,
              lastSeq: streamEvent.data.seq,
              controller,
            }
            markConversationStreaming(conversation.id, true)
            setIsCreatingConversationTitle(false)
            queryClient.setQueryData<Conversation[]>(['conversations'], (old = []) => [
              conversation,
              ...old.filter((item) => item.id !== conversation.id),
            ])
            queryClient.setQueryData<Message[]>(['messages', conversation.id], [])
            setActiveConversationId(conversation.id)
            setPendingConversationLlmConfigId(null)
            setActiveView('chat')
            continue
          }

          if (!conversationId) continue
          applyStreamEvent(conversationId, streamEvent, controller)
        }
        queryClient.invalidateQueries({ queryKey: ['conversations'] })
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          return
        }
        setErrorMessage(error instanceof Error ? error.message : 'Streaming request failed.')
      } finally {
        pendingNewConversationStreamRef.current = null
        if (conversationId && activeStreamRefs.current[conversationId]?.controller === controller) {
          delete activeStreamRefs.current[conversationId]
          markConversationStreaming(conversationId, false)
        }
        setIsCreatingConversationTitle(false)
        setIsPendingNewConversationStream(false)
      }
    },
    [
      applyStreamEvent,
      markConversationStreaming,
      queryClient,
      setActiveConversationId,
      setActiveView,
      setErrorMessage,
      setIsCreatingConversationTitle,
      setPendingConversationLlmConfigId,
    ],
  )

  const sendMessage = useCallback(() => {
    const content = draft.trim()
    const isCurrentThreadStreaming = activeConversationId
      ? streamingConversationIds.has(activeConversationId)
      : isPendingNewConversationStream
    if (!content || isCurrentThreadStreaming) return
    if (!hasUser) {
      setErrorMessage('Sign in before sending messages.')
      return
    }
    setDraft('')
    setErrorMessage('')

    const conversationId = activeConversationId
    if (!conversationId) {
      void streamNewConversationMessage(content, pendingConversationLlmConfigId, pendingConversationLlmModel)
      return
    }

    void streamMessage(conversationId, content, pendingConversationLlmModel)
  }, [
    draft,
    activeConversationId,
    pendingConversationLlmConfigId,
    pendingConversationLlmModel,
    hasUser,
    isPendingNewConversationStream,
    streamingConversationIds,
    streamMessage,
    streamNewConversationMessage,
    setDraft,
    setErrorMessage,
  ])

  const regenerateAssistant = useCallback(
    async (messageId: string, model?: string | null) => {
      if (!activeConversationId || streamingConversationIds.has(activeConversationId)) return
      const controller = new AbortController()
      markConversationStreaming(activeConversationId, true)
      setErrorMessage('')
      activeStreamRefs.current[activeConversationId] = {
        conversationId: activeConversationId,
        messageId: null,
        streamId: null,
        lastSeq: 0,
        controller,
      }

      try {
        const idempotencyKey = createIdempotencyKey('regen')
        for await (const streamEvent of conversationApi.streamRegenerateAssistant(
          activeConversationId,
          messageId,
          idempotencyKey,
          controller.signal,
          model ?? null,
        )) {
          applyStreamEvent(activeConversationId, streamEvent, controller)
        }
        queryClient.invalidateQueries({ queryKey: ['conversations'] })
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          return
        }
        setErrorMessage(error instanceof Error ? error.message : 'Regenerate request failed.')
      } finally {
        if (activeStreamRefs.current[activeConversationId]?.controller === controller) {
          delete activeStreamRefs.current[activeConversationId]
        }
        markConversationStreaming(activeConversationId, false)
      }
    },
    [
      activeConversationId,
      applyStreamEvent,
      markConversationStreaming,
      queryClient,
      setErrorMessage,
      streamingConversationIds,
    ],
  )

  const editUserMessage = useCallback(
    async (messageId: string, content: string, model?: string | null) => {
      if (!activeConversationId || streamingConversationIds.has(activeConversationId)) return
      const controller = new AbortController()
      markConversationStreaming(activeConversationId, true)
      setErrorMessage('')
      activeStreamRefs.current[activeConversationId] = {
        conversationId: activeConversationId,
        messageId: null,
        streamId: null,
        lastSeq: 0,
        controller,
      }

      try {
        for await (const streamEvent of conversationApi.streamEditUserMessage(
          activeConversationId,
          messageId,
          {
            content,
            idempotency_key: createIdempotencyKey('edit'),
            model: model ?? null,
          },
          controller.signal,
        )) {
          applyStreamEvent(activeConversationId, streamEvent, controller)
        }
        queryClient.invalidateQueries({ queryKey: ['conversations'] })
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          return
        }
        setErrorMessage(error instanceof Error ? error.message : 'Edit request failed.')
      } finally {
        if (activeStreamRefs.current[activeConversationId]?.controller === controller) {
          delete activeStreamRefs.current[activeConversationId]
        }
        markConversationStreaming(activeConversationId, false)
      }
    },
    [
      activeConversationId,
      applyStreamEvent,
      markConversationStreaming,
      queryClient,
      setErrorMessage,
      streamingConversationIds,
    ],
  )

  const switchBranch = useCallback(
    async (messageId: string) => {
      if (!activeConversationId || streamingConversationIds.has(activeConversationId)) return
      try {
        const response = await conversationApi.switchBranch(activeConversationId, messageId)
        queryClient.setQueryData<Message[]>(['messages', activeConversationId], response.messages)
        queryClient.setQueryData<Conversation[]>(['conversations'], (old = []) =>
          old.map((conversation) =>
            conversation.id === activeConversationId
              ? { ...conversation, active_leaf_message_id: response.active_leaf_message_id ?? null }
              : conversation,
          ),
        )
      } catch (error) {
        setErrorMessage(error instanceof Error ? error.message : 'Failed to switch branch.')
      }
    },
    [activeConversationId, queryClient, setErrorMessage, streamingConversationIds],
  )

  const forkConversation = useCallback(
    async (messageId: string, title?: string | null) => {
      if (!activeConversationId || streamingConversationIds.has(activeConversationId)) return null
      try {
        const response = await conversationApi.forkConversation(activeConversationId, messageId, title)
        queryClient.setQueryData<Conversation[]>(['conversations'], (old = []) => [
          response.conversation,
          ...old.filter((conversation) => conversation.id !== response.conversation.id),
        ])
        queryClient.setQueryData<Message[]>(['messages', response.conversation.id], response.messages)
        setActiveConversationId(response.conversation.id)
        setActiveView('chat')
        queryClient.invalidateQueries({ queryKey: ['conversations'] })
        return response.conversation.id
      } catch (error) {
        setErrorMessage(error instanceof Error ? error.message : 'Failed to fork conversation.')
        return null
      }
    },
    [
      activeConversationId,
      queryClient,
      setActiveConversationId,
      setActiveView,
      setErrorMessage,
      streamingConversationIds,
    ],
  )

  const stopGeneration = useCallback(async () => {
    const activeStream = activeConversationId
      ? activeStreamRefs.current[activeConversationId]
      : pendingNewConversationStreamRef.current
    if (!activeStream) return
    if (!activeStream.messageId) {
      activeStream.controller.abort()
      if (activeStream.conversationId) {
        delete activeStreamRefs.current[activeStream.conversationId]
        markConversationStreaming(activeStream.conversationId, false)
      } else {
        pendingNewConversationStreamRef.current = null
        setIsPendingNewConversationStream(false)
      }
      setIsCreatingConversationTitle(false)
      return
    }
    if (!activeStream.conversationId) {
      activeStream.controller.abort()
      return
    }

    try {
      await conversationApi.cancelMessage(activeStream.conversationId, activeStream.messageId)
      // 取消接口可能会直接打断 SSE 任务，前端先用已有增量内容更新为 cancelled。
      queryClient.setQueryData<Message[]>(['messages', activeStream.conversationId], (old = []) =>
        old.map((message) =>
          message.id === activeStream.messageId ? { ...message, status: 'cancelled' } : message,
        ),
      )
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to stop generation.')
    } finally {
      if (activeStream.conversationId) {
        delete activeStreamRefs.current[activeStream.conversationId]
        markConversationStreaming(activeStream.conversationId, false)
      }
    }
  }, [
    activeConversationId,
    markConversationStreaming,
    queryClient,
    setErrorMessage,
    setIsCreatingConversationTitle,
  ])

  useEffect(() => {
    if (!enableStreamResume) return
    if (!hasUser || !activeConversationId || !activeConversation?.has_active_run) return
    if (messagesQuery.isLoading || messagesQuery.isFetching) return

    const currentStream = activeStreamRefs.current[activeConversationId]
    if (currentStream?.conversationId === activeConversationId) {
      return
    }

    const visibleLeafMessageId =
      activeConversation.active_leaf_message_id ?? rawMessages[rawMessages.length - 1]?.id ?? null
    if (!visibleLeafMessageId) return

    const controller = new AbortController()

    void (async () => {
      try {
        const activeStream = await conversationApi.getMessageActiveStream(
          activeConversationId,
          visibleLeafMessageId,
        )
        const savedCursor = streamCursorRef.current[activeConversationId]
        const lastSeq =
          savedCursor && savedCursor.streamId === activeStream.stream_id ? savedCursor.lastSeq : 0

        // 刷新页面、重新进入会话或切 branch 后，先锁定当前 visible branch，再恢复它对应的流。
        activeStreamRefs.current[activeConversationId] = {
          conversationId: activeConversationId,
          messageId: activeStream.assistant_message_id,
          streamId: activeStream.stream_id,
          lastSeq,
          controller,
        }
        markConversationStreaming(activeConversationId, true)

        for await (const streamEvent of conversationApi.resumeStreamById(
          activeConversationId,
          activeStream.stream_id,
          lastSeq,
          controller.signal,
        )) {
          applyStreamEvent(activeConversationId, streamEvent, controller)
        }
        queryClient.invalidateQueries({ queryKey: ['conversations'] })
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          return
        }
        const message = error instanceof Error ? error.message : 'Resume stream request failed.'
        if (message !== '当前分支没有活跃流') {
          setErrorMessage(message)
        }
      } finally {
        if (activeStreamRefs.current[activeConversationId]?.controller === controller) {
          delete activeStreamRefs.current[activeConversationId]
        }
        markConversationStreaming(activeConversationId, false)
      }
    })()

    return () => {
      controller.abort()
    }
  }, [
    activeConversation?.active_leaf_message_id,
    activeConversation?.has_active_run,
    activeConversationId,
    applyStreamEvent,
    enableStreamResume,
    hasUser,
    markConversationStreaming,
    messagesQuery.isFetching,
    messagesQuery.isLoading,
    queryClient,
    rawMessages,
    setErrorMessage,
  ])

  const renameConversation = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      conversationApi.update(id, { title }),
    onSuccess: (updated) => {
      queryClient.setQueryData<Conversation[]>(['conversations'], (old = []) =>
        old.map((c) => (c.id === updated.id ? { ...c, ...updated } : c)),
      )
    },
    onError: (error: Error) => {
      setErrorMessage(error.message)
    },
  })

  const archiveConversation = useMutation({
    mutationFn: (conversationId: string) => conversationApi.archive(conversationId),
    onSuccess: (_data, conversationId) => {
      queryClient.setQueryData<Conversation[]>(['conversations'], (old = []) =>
        old.filter((c) => c.id !== conversationId),
      )
      if (activeConversationId === conversationId) {
        setActiveConversationId(null)
        queryClient.setQueryData(['messages', conversationId], [])
      }
    },
    onError: (error: Error) => {
      setErrorMessage(error.message)
    },
  })

  const switchConversationLlm = useMutation({
    mutationFn: ({ conversationId, configId }: { conversationId: string; configId: string }) =>
      conversationApi.update(conversationId, { llm_config_id: configId }),
    onSuccess: (updated) => {
      queryClient.setQueryData<Conversation[]>(['conversations'], (old = []) =>
        old.map((c) => (c.id === updated.id ? { ...c, ...updated } : c)),
      )
    },
    onError: (error: Error) => {
      setErrorMessage(error.message)
    },
  })

  return {
    conversations,
    sortedConversations,
    activeConversation,
    activeConversationId,
    pendingConversationLlmConfigId,
    pendingConversationLlmModel,
    messages,
    isLoadingMessages,
    isSending: isActiveConversationStreaming,
    formatConversationTitle,
    selectConversation,
    createNewThread: () => {
      if (!hasUser) {
        setErrorMessage('Sign in before creating a conversation.')
        return
      }
      setActiveView('chat')
      setActiveConversationId(null)
      setPendingConversationLlmConfigId(null)
      setPendingConversationLlmModel(null)
      setIsCreatingConversationTitle(false)
      setErrorMessage('')
    },
    sendMessage,
    regenerateAssistant,
    editUserMessage,
    switchBranch,
    forkConversation,
    stopGeneration,
    renameConversation: (id: string, title: string) => renameConversation.mutate({ id, title }),
    archiveConversation: (id: string) => archiveConversation.mutate(id),
    switchConversationLlm: (conversationId: string, configId: string) =>
      switchConversationLlm.mutateAsync({ conversationId, configId }),
    selectPendingConversationLlm: (configId: string, model?: string | null) => {
      setPendingConversationLlmConfigId(configId)
      setPendingConversationLlmModel(model ?? null)
    },
  }
}
