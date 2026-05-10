import { useMemo, useCallback, useEffect, useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { conversationApi } from '@/api/conversations'
import { fileApi } from '@/api/files'
import { streamManager } from '@/lib/streamManager'
import { useOrbitStore } from '@/stores/useOrbitStore'
import type { Conversation, Message, StreamMessageEvent } from '@/api/types'
import type { PendingFile } from '@/components/chat/FilePreviewItem'

interface UseConversationsOptions {
  enableStreamResume?: boolean
}

interface NormalizedMessage extends Message {
  paragraphs: string[]
}

interface ActiveStreamHandle {
  streamKey: string
  conversationId: string | null
  messageId: string | null
  streamId: string | null
  controller: AbortController
}

interface StreamMessageSnapshot {
  message: Message
  content: string
  reasoningContent: string
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
  let didUpdate = false
  const nextMessages = messages.map((message) => {
    if (message.id !== messageId) return message
    didUpdate = true
    return {
      ...message,
      content: `${message.content || ''}${delta}`,
      status: 'streaming' as const,
    }
  })
  return didUpdate ? nextMessages : messages
}

function appendMessageReasoningDelta(messages: Message[], messageId: string, delta: string): Message[] {
  let didUpdate = false
  const nextMessages = messages.map((message) => {
    if (message.id !== messageId) return message
    didUpdate = true
    return {
      ...message,
      reasoning_content: `${message.reasoning_content || ''}${delta}`,
      status: 'streaming' as const,
    }
  })
  return didUpdate ? nextMessages : messages
}

function isMessageVisible(messages: Message[], messageId: string | null | undefined): boolean {
  return Boolean(messageId && messages.some((message) => message.id === messageId))
}

function isUuid(value: string | null | undefined): value is string {
  return Boolean(
    value &&
      /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value),
  )
}

function hydrateStreamSnapshots(
  messages: Message[],
  snapshots: Record<string, StreamMessageSnapshot>,
): Message[] {
  let didUpdate = false
  const hydrated = messages.map((message) => {
    const snapshot = snapshots[message.id]
    if (!snapshot) return message
    const nextMessage = {
      ...message,
      ...snapshot.message,
      content: snapshot.content,
      reasoning_content: snapshot.reasoningContent,
    }
    if (
      nextMessage.content === message.content &&
      nextMessage.reasoning_content === message.reasoning_content &&
      nextMessage.status === message.status
    ) {
      return message
    }
    didUpdate = true
    return nextMessage
  })
  return didUpdate ? hydrated : messages
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
  const receivingConversationIds = useOrbitStore((s) => s.receivingConversationIds)
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
  const [isPendingNewConversationStream, setIsPendingNewConversationStream] = useState(false)
  const [pendingFiles, setPendingFiles] = useState<PendingFile[]>([])
  const [isUploadingFiles, setIsUploadingFiles] = useState(false)
  const pendingNewConversationStreamRef = useRef<ActiveStreamHandle | null>(null)
  const activeConversationIdRef = useRef(activeConversationId)
  const resumeAttemptRef = useRef<Record<string, boolean>>({})
  const streamMessageSnapshotsRef = useRef<Record<string, StreamMessageSnapshot>>({})

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
  const currentBranchIsStreaming = useMemo(
    () =>
      Boolean(
        activeConversation?.active_leaf_message_id &&
          rawMessages.some(
            (message) =>
              message.id === activeConversation.active_leaf_message_id &&
              message.role === 'assistant' &&
              message.status === 'streaming',
          ),
      ),
    [activeConversation?.active_leaf_message_id, rawMessages],
  )
  const currentBranchHasPendingLocalStream = useMemo(
    () =>
      Boolean(
        activeConversationId &&
          receivingConversationIds[activeConversationId] &&
          streamManager
            .getByConversation(activeConversationId)
            .some(
              (stream) =>
                stream.messageId === null &&
                stream.parentMessageId === (activeConversation?.active_leaf_message_id ?? null),
            ),
      ),
    [activeConversation?.active_leaf_message_id, activeConversationId, receivingConversationIds],
  )
  const isActiveConversationStreaming = activeConversationId
    ? currentBranchIsStreaming || currentBranchHasPendingLocalStream
    : isPendingNewConversationStream

  // 只有首次没有任何缓存消息时才显示 loading，切页返回时保留现有内容避免闪烁。
  const isLoadingMessages = rawMessages.length === 0 && (messagesQuery.isLoading || messagesQuery.isFetching)

  const formatConversationTitle = useCallback(
    (conversation: Conversation) =>
      conversation.title || 'Untitled chat',
    [],
  )

  const markConversationStreamState = useCallback(
    (
      conversationId: string,
      hasActiveRun?: boolean,
    ) => {
      if (typeof hasActiveRun !== 'boolean') return
      queryClient.setQueryData<Conversation[]>(['conversations'], (old = []) =>
        old.map((conversation) =>
          conversation.id === conversationId
            ? {
                ...conversation,
                has_active_run: hasActiveRun,
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

  const rememberStreamMessage = useCallback((message: Message) => {
    if (message.role !== 'assistant') return
    streamMessageSnapshotsRef.current[message.id] = {
      message,
      content: message.content || '',
      reasoningContent: message.reasoning_content || '',
    }
  }, [])

  const appendStreamSnapshotContent = useCallback((messageId: string, delta: string) => {
    const current = streamMessageSnapshotsRef.current[messageId]
    if (!current) return
    streamMessageSnapshotsRef.current[messageId] = {
      ...current,
      content: `${current.content}${delta}`,
      message: {
        ...current.message,
        content: `${current.content}${delta}`,
        status: 'streaming' as const,
      },
    }
  }, [])

  const appendStreamSnapshotReasoning = useCallback((messageId: string, delta: string) => {
    const current = streamMessageSnapshotsRef.current[messageId]
    if (!current) return
    streamMessageSnapshotsRef.current[messageId] = {
      ...current,
      reasoningContent: `${current.reasoningContent}${delta}`,
      message: {
        ...current.message,
        reasoning_content: `${current.reasoningContent}${delta}`,
        status: 'streaming' as const,
      },
    }
  }, [])

  const hydrateCachedStreamSnapshots = useCallback(
    (conversationId: string) => {
      queryClient.setQueryData<Message[]>(['messages', conversationId], (old = []) =>
        hydrateStreamSnapshots(old, streamMessageSnapshotsRef.current),
      )
    },
    [queryClient],
  )

  const applyStreamEvent = useCallback(
    (conversationId: string, streamKey: string, streamEvent: StreamMessageEvent, controller: AbortController) => {
      let nextStreamKey = streamKey
      streamManager.update(nextStreamKey, {
        streamId: streamEvent.data.stream_id,
        status: 'streaming',
      })

      if (streamEvent.event === 'conversation.created') {
        markConversationStreamState(streamEvent.data.conversation.id, true)
        upsertConversation(streamEvent.data.conversation)
        return nextStreamKey
      }

      if (streamEvent.event === 'conversation.updated') {
        upsertConversation(streamEvent.data.conversation)
        return nextStreamKey
      }

      if (streamEvent.event === 'conversation.run_state_changed') {
        // 会话级 has_active_run 只是 UI 缓存；真正停止动画要跟随后端显式广播。
        queryClient.setQueryData<Conversation[]>(['conversations'], (old = []) =>
          old.map((conversation) =>
            conversation.id === streamEvent.data.conversation_id
              ? {
                  ...conversation,
                  has_active_run: streamEvent.data.has_active_run,
                }
              : conversation,
          ),
        )
        if (!streamEvent.data.has_active_run) {
          markConversationStreamState(conversationId, false)
          if (streamManager.get(nextStreamKey)?.controller === controller) {
            streamManager.remove(nextStreamKey)
          }
        }
        return nextStreamKey
      }

      if (streamEvent.event === 'message.created') {
        rememberStreamMessage(streamEvent.data.assistant_message)
        nextStreamKey = streamManager.adoptMessageId(nextStreamKey, streamEvent.data.assistant_message.id)
        const streamHandle = streamManager.get(nextStreamKey)
        streamManager.set({
          streamKey: nextStreamKey,
          conversationId,
          messageId: streamEvent.data.assistant_message.id,
          parentMessageId: streamHandle?.parentMessageId ?? streamEvent.data.assistant_message.parent_message_id ?? null,
          streamId: streamEvent.data.stream_id,
          controller,
          source: streamHandle?.source ?? 'created',
          status: 'streaming',
        })
        markConversationStreamState(conversationId, true)
        let didApplyToVisiblePath = false
        queryClient.setQueryData<Message[]>(['messages', conversationId], (old = []) => {
          const parentMessageId =
            streamEvent.data.user_message?.parent_message_id ??
            streamEvent.data.assistant_message.parent_message_id
          if (old.length > 0 && parentMessageId && !isMessageVisible(old, parentMessageId)) {
            return old
          }
          didApplyToVisiblePath = true
          return replaceVisibleTail(
            old,
            streamEvent.data.user_message,
            streamEvent.data.assistant_message,
          )
        })
        if (didApplyToVisiblePath) {
          queryClient.setQueryData<Conversation[]>(['conversations'], (old = []) =>
            old.map((conversation) =>
              conversation.id === conversationId &&
              (conversation.active_leaf_message_id === (streamHandle?.parentMessageId ?? null) ||
                conversation.active_leaf_message_id === streamEvent.data.assistant_message.id)
                ? { ...conversation, active_leaf_message_id: streamEvent.data.assistant_message.id }
                : conversation,
            ),
          )
        }
        return nextStreamKey
      }

      if (streamEvent.event === 'message.delta') {
        appendStreamSnapshotContent(streamEvent.data.message_id, streamEvent.data.delta)
        queryClient.setQueryData<Message[]>(['messages', conversationId], (old = []) =>
          appendMessageDelta(old, streamEvent.data.message_id, streamEvent.data.delta),
        )
        return nextStreamKey
      }

      if (streamEvent.event === 'message.reasoning_delta') {
        appendStreamSnapshotReasoning(streamEvent.data.message_id, streamEvent.data.delta)
        queryClient.setQueryData<Message[]>(['messages', conversationId], (old = []) =>
          appendMessageReasoningDelta(old, streamEvent.data.message_id, streamEvent.data.delta),
        )
        return nextStreamKey
      }

      if (
        streamEvent.event === 'message.completed' ||
        streamEvent.event === 'message.failed' ||
        streamEvent.event === 'message.cancelled'
      ) {
        rememberStreamMessage(streamEvent.data.message)
        streamManager.update(nextStreamKey, {
          status:
            streamEvent.event === 'message.completed'
              ? 'completed'
              : streamEvent.event === 'message.failed'
                ? 'failed'
                : 'cancelled',
        })
        if (streamManager.get(nextStreamKey)?.controller === controller) {
          streamManager.remove(nextStreamKey)
        }
        if (streamEvent.event === 'message.completed' && activeConversationIdRef.current !== conversationId) {
          markConversationCompletedOffscreen(conversationId)
        }
      }

      queryClient.setQueryData<Message[]>(['messages', conversationId], (old = []) => {
        const messageStillVisible = old.some((message) => message.id === streamEvent.data.message.id)
        return messageStillVisible ? upsertMessage(old, streamEvent.data.message) : old
      })
      return nextStreamKey
    },
    [
      markConversationCompletedOffscreen,
      markConversationStreamState,
      appendStreamSnapshotContent,
      appendStreamSnapshotReasoning,
      queryClient,
      rememberStreamMessage,
      upsertConversation,
    ],
  )

  const selectConversation = useCallback(
    (conversationId: string) => {
      setActiveView('chat')
      setActiveConversationId(conversationId)
      setPendingConversationLlmConfigId(null)
      setPendingConversationLlmModel(null)
      clearConversationCompletionNotice(conversationId)
      setErrorMessage('')
    },
    [
      clearConversationCompletionNotice,
      setActiveView,
      setActiveConversationId,
      setPendingConversationLlmConfigId,
      setPendingConversationLlmModel,
      setErrorMessage,
    ],
  )

  const streamMessage = useCallback(
    async (
      conversationId: string,
      content: string,
      llmConfigId?: string | null,
      model?: string | null,
      fileIds?: string[],
    ) => {
      await queryClient.cancelQueries({ queryKey: ['messages', conversationId] })
      const previousMessages = queryClient.getQueryData<Message[]>(['messages', conversationId]) || []
      const conversation = queryClient
        .getQueryData<Conversation[]>(['conversations'])
        ?.find((item) => item.id === conversationId)
      const parentMessageId = isUuid(conversation?.active_leaf_message_id)
        ? conversation.active_leaf_message_id
        : null
      const idempotencyKey = createIdempotencyKey('msg')
      const localId = Date.now()
      const controller = new AbortController()
      let streamKey = streamManager.makePendingKey(conversationId, 'send')

      queryClient.setQueryData<Message[]>(['messages', conversationId], [
        ...previousMessages,
        {
          id: `local-user-${localId}`,
          conversation_id: conversationId,
          role: 'user',
          content,
          content_parts: [],
          status: 'completed',
          created_at: new Date().toISOString(),
        },
        {
          id: `local-assistant-${localId}`,
          conversation_id: conversationId,
          role: 'assistant',
          content: '',
          reasoning_content: '',
          content_parts: [],
          status: 'streaming',
          created_at: new Date().toISOString(),
        },
      ])

      streamManager.set({
        streamKey,
        conversationId,
        messageId: null,
        parentMessageId,
        streamId: null,
        controller,
        source: 'created',
      })

      try {
        for await (const streamEvent of conversationApi.streamMessage(
          conversationId,
          {
            content: content || '',
            llm_config_id: llmConfigId ?? null,
            parent_message_id: parentMessageId,
            idempotency_key: idempotencyKey,
            model: model ?? null,
            file_ids: fileIds?.length ? fileIds : undefined,
          },
          controller.signal,
        )) {
          streamKey = applyStreamEvent(conversationId, streamKey, streamEvent, controller)
        }
        queryClient.invalidateQueries({ queryKey: ['conversations'] })
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          return
        }
        queryClient.setQueryData(['messages', conversationId], previousMessages)
        setErrorMessage(error instanceof Error ? error.message : 'Streaming request failed.')
      } finally {
        if (streamManager.get(streamKey)?.controller === controller) {
          streamManager.remove(streamKey)
        }
      }
    },
    [applyStreamEvent, queryClient, setErrorMessage],
  )

  const streamNewConversationMessage = useCallback(
    async (content: string, llmConfigId: string | null, model?: string | null, fileIds?: string[]) => {
      const controller = new AbortController()
      let conversationId: string | null = null
      let streamKey: string | null = null

      setIsPendingNewConversationStream(true)
      setIsCreatingConversationTitle(true)
      pendingNewConversationStreamRef.current = {
        streamKey: streamManager.makePendingKey('new-conversation', 'new'),
        conversationId: null,
        messageId: null,
        streamId: null,
        controller,
      }

      try {
        for await (const streamEvent of conversationApi.streamNewConversationMessage(
          {
            content: content || '',
            llm_config_id: llmConfigId,
            chat_mode: 'chat',
            metadata: {},
            idempotency_key: createIdempotencyKey('new-chat'),
            model: model ?? null,
            file_ids: fileIds?.length ? fileIds : undefined,
          },
          controller.signal,
        )) {
          if (streamEvent.event === 'conversation.created') {
            const conversation = streamEvent.data.conversation
            conversationId = conversation.id
            streamKey = streamManager.makePendingKey(conversation.id, 'new')
            pendingNewConversationStreamRef.current = null
            setIsPendingNewConversationStream(false)
            streamManager.set({
              streamKey,
              conversationId,
              messageId: null,
              streamId: streamEvent.data.stream_id,
              controller,
              source: 'created',
              status: 'streaming',
            })
            setIsCreatingConversationTitle(false)
            queryClient.setQueryData<Conversation[]>(['conversations'], (old = []) => [
              conversation,
              ...old.filter((item) => item.id !== conversation.id),
            ])
            queryClient.setQueryData<Message[]>(['messages', conversation.id], [])
            setActiveConversationId(conversation.id)
            setPendingConversationLlmConfigId(null)
            setPendingConversationLlmModel(null)
            setActiveView('chat')
            continue
          }

          if (!conversationId) continue
          if (!streamKey) {
            streamKey = streamManager.makePendingKey(conversationId, 'new')
          }
          streamKey = applyStreamEvent(conversationId, streamKey, streamEvent, controller)
        }
        queryClient.invalidateQueries({ queryKey: ['conversations'] })
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          return
        }
        setErrorMessage(error instanceof Error ? error.message : 'Streaming request failed.')
      } finally {
        pendingNewConversationStreamRef.current = null
        if (streamKey && streamManager.get(streamKey)?.controller === controller) {
          streamManager.remove(streamKey)
        }
        setIsCreatingConversationTitle(false)
        setIsPendingNewConversationStream(false)
      }
    },
    [
      applyStreamEvent,
      queryClient,
      setActiveConversationId,
      setActiveView,
      setErrorMessage,
      setIsCreatingConversationTitle,
      setPendingConversationLlmConfigId,
      setPendingConversationLlmModel,
    ],
  )

  const addFiles = useCallback((files: File[]) => {
    const newFiles: PendingFile[] = files.map((file) => {
      const isImage = file.type.startsWith("image/")
      return {
        file,
        status: "pending" as const,
        preview: isImage ? URL.createObjectURL(file) : undefined,
      }
    })
    setPendingFiles((prev) => [...prev, ...newFiles])
  }, [])

  const removeFile = useCallback((index: number) => {
    setPendingFiles((prev) => {
      const next = [...prev]
      const removed = next[index]
      if (removed?.preview) URL.revokeObjectURL(removed.preview)
      next.splice(index, 1)
      return next
    })
  }, [])

  const uploadPendingFiles = useCallback(
    async (conversationId: string | null): Promise<string[]> => {
      const files = pendingFiles.filter((pf) => pf.status !== "ready")
      if (files.length === 0) {
        return pendingFiles
          .filter((pf) => pf.serverFile?.id)
          .map((pf) => pf.serverFile!.id)
      }

      setIsUploadingFiles(true)
      const fileIds: string[] = []
      const updated: PendingFile[] = [...pendingFiles]

      for (let i = 0; i < updated.length; i++) {
        const pf = updated[i]
        if (pf.status === "ready" && pf.serverFile?.id) {
          fileIds.push(pf.serverFile.id)
          continue
        }
        updated[i] = { ...pf, status: "uploading" }
        setPendingFiles([...updated])

        try {
          let serverFile
          if (conversationId) {
            serverFile = await fileApi.uploadToConversation(conversationId, pf.file)
          } else {
            serverFile = await fileApi.uploadPending(pf.file)
          }
          fileIds.push(serverFile.id)
          updated[i] = { ...pf, status: "ready", serverFile }
        } catch (err) {
          updated[i] = {
            ...pf,
            status: "error",
            error: err instanceof Error ? err.message : "Upload failed",
          }
        }
        setPendingFiles([...updated])
      }

      setIsUploadingFiles(false)
      return fileIds
    },
    [pendingFiles],
  )

  const sendMessage = useCallback((selectedLlmConfigId?: string | null, selectedModel?: string | null) => {
    const content = draft.trim()
    const hasFiles = pendingFiles.length > 0
    const isCurrentThreadStreaming = activeConversationId
      ? currentBranchIsStreaming || currentBranchHasPendingLocalStream
      : isPendingNewConversationStream
    if ((!content && !hasFiles) || isCurrentThreadStreaming || isUploadingFiles) return
    if (!hasUser) {
      setErrorMessage('Sign in before sending messages.')
      return
    }
    setDraft('')
    setErrorMessage('')

    const conversationId = activeConversationId
    // Upload files, then send with file_ids
    const doSend = async () => {
      const fileIds = await uploadPendingFiles(conversationId)
      if (!conversationId) {
        void streamNewConversationMessage(
          content,
          selectedLlmConfigId ?? pendingConversationLlmConfigId,
          selectedModel ?? pendingConversationLlmModel,
          fileIds,
        )
        return
      }
      void streamMessage(
        conversationId,
        content,
        selectedLlmConfigId ?? pendingConversationLlmConfigId,
        selectedModel ?? pendingConversationLlmModel,
        fileIds,
      )
    }
    void doSend().then(() => setPendingFiles([]))
  }, [
    draft,
    pendingFiles,
    isUploadingFiles,
    activeConversationId,
    pendingConversationLlmConfigId,
    pendingConversationLlmModel,
    hasUser,
    isPendingNewConversationStream,
    currentBranchIsStreaming,
    currentBranchHasPendingLocalStream,
    uploadPendingFiles,
    streamMessage,
    streamNewConversationMessage,
    setDraft,
    setErrorMessage,
  ])

  const regenerateAssistant = useCallback(
    async (messageId: string, llmConfigId?: string | null, model?: string | null) => {
      if (!activeConversationId || !isUuid(messageId)) return
      const controller = new AbortController()
      let streamKey = streamManager.makePendingKey(activeConversationId, 'regen')
      setErrorMessage('')
      streamManager.set({
        streamKey,
        conversationId: activeConversationId,
        messageId: null,
        parentMessageId: activeConversation?.active_leaf_message_id ?? null,
        streamId: null,
        controller,
        source: 'created',
      })

      try {
        const idempotencyKey = createIdempotencyKey('regen')
        for await (const streamEvent of conversationApi.streamRegenerateAssistant(
          activeConversationId,
          messageId,
          idempotencyKey,
          controller.signal,
          model ?? null,
          llmConfigId ?? null,
        )) {
          streamKey = applyStreamEvent(activeConversationId, streamKey, streamEvent, controller)
        }
        queryClient.invalidateQueries({ queryKey: ['conversations'] })
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          return
        }
        setErrorMessage(error instanceof Error ? error.message : 'Regenerate request failed.')
      } finally {
        if (streamManager.get(streamKey)?.controller === controller) {
          streamManager.remove(streamKey)
        }
      }
    },
    [
      activeConversationId,
      activeConversation?.active_leaf_message_id,
      applyStreamEvent,
      queryClient,
      setErrorMessage,
    ],
  )

  const editUserMessage = useCallback(
    async (messageId: string, content: string, llmConfigId?: string | null, model?: string | null) => {
      if (!activeConversationId || !isUuid(messageId)) return
      const controller = new AbortController()
      let streamKey = streamManager.makePendingKey(activeConversationId, 'edit')
      setErrorMessage('')
      streamManager.set({
        streamKey,
        conversationId: activeConversationId,
        messageId: null,
        parentMessageId: activeConversation?.active_leaf_message_id ?? null,
        streamId: null,
        controller,
        source: 'created',
      })

      try {
        for await (const streamEvent of conversationApi.streamEditUserMessage(
          activeConversationId,
          messageId,
          {
            content,
            llm_config_id: llmConfigId ?? null,
            idempotency_key: createIdempotencyKey('edit'),
            model: model ?? null,
          },
          controller.signal,
        )) {
          streamKey = applyStreamEvent(activeConversationId, streamKey, streamEvent, controller)
        }
        queryClient.invalidateQueries({ queryKey: ['conversations'] })
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          return
        }
        setErrorMessage(error instanceof Error ? error.message : 'Edit request failed.')
      } finally {
        if (streamManager.get(streamKey)?.controller === controller) {
          streamManager.remove(streamKey)
        }
      }
    },
    [
      activeConversationId,
      activeConversation?.active_leaf_message_id,
      applyStreamEvent,
      queryClient,
      setErrorMessage,
    ],
  )

  const switchBranch = useCallback(
    async (messageId: string) => {
      if (!activeConversationId || !isUuid(messageId)) return
      try {
        const response = await conversationApi.switchBranch(activeConversationId, messageId)
        queryClient.setQueryData<Message[]>(
          ['messages', activeConversationId],
          hydrateStreamSnapshots(response.messages, streamMessageSnapshotsRef.current),
        )
        setPendingConversationLlmConfigId(null)
        setPendingConversationLlmModel(null)
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
    [
      activeConversationId,
      queryClient,
      setErrorMessage,
      setPendingConversationLlmConfigId,
      setPendingConversationLlmModel,
    ],
  )

  const forkConversation = useCallback(
    async (messageId: string, title?: string | null) => {
      if (!activeConversationId || !isUuid(messageId)) return null
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
    ],
  )

  const stopGeneration = useCallback(async () => {
    const conversationStreams = activeConversationId ? streamManager.getByConversation(activeConversationId) : []
    const activeStream = activeConversationId
      ? streamManager.getByMessageId(activeConversation?.active_leaf_message_id) ??
        conversationStreams[conversationStreams.length - 1] ??
        null
      : pendingNewConversationStreamRef.current
    if (!activeStream) return
    if (!activeStream.messageId || !isUuid(activeStream.messageId)) {
      activeStream.controller.abort()
      if (activeStream.conversationId) {
        streamManager.remove(activeStream.streamKey)
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
        streamManager.remove(activeStream.streamKey)
      }
    }
  }, [
    activeConversationId,
    activeConversation?.active_leaf_message_id,
    queryClient,
    setErrorMessage,
    setIsCreatingConversationTitle,
  ])

  useEffect(() => {
    if (!enableStreamResume) return
    if (!hasUser || !activeConversationId || !activeConversation?.has_active_run) return
    if (messagesQuery.isLoading || messagesQuery.isFetching) return

    const streamingAssistant =
      [...rawMessages]
        .reverse()
        .find((message) => message.role === 'assistant' && message.status === 'streaming' && isUuid(message.id)) ?? null
    if (!streamingAssistant) return

    const resumeMessageId = streamingAssistant.id
    if (streamManager.getByMessageId(resumeMessageId)?.conversationId === activeConversationId) {
      hydrateCachedStreamSnapshots(activeConversationId)
      return
    }

    const attemptKey = `${activeConversationId}:${resumeMessageId}`
    if (resumeAttemptRef.current[attemptKey]) return
    resumeAttemptRef.current[attemptKey] = true

    void (async () => {
      const controller = new AbortController()
      let streamKey: string | null = null
      try {
        const activeStream = await conversationApi.getMessageActiveStream(
          activeConversationId,
          resumeMessageId,
        )
        streamKey = activeStream.assistant_message_id
        // 刷新页面、重新进入会话或切 branch 后，先锁定当前 visible branch，再恢复它对应的流。
        streamManager.set({
          streamKey,
          conversationId: activeConversationId,
          messageId: activeStream.assistant_message_id,
          streamId: activeStream.stream_id,
          controller,
          source: 'resumed',
          status: 'streaming',
        })

        for await (const streamEvent of conversationApi.resumeStreamById(
          activeConversationId,
          activeStream.stream_id,
          controller.signal,
        )) {
          streamKey = applyStreamEvent(activeConversationId, streamKey, streamEvent, controller)
        }
        queryClient.invalidateQueries({ queryKey: ['conversations'] })
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          return
        }
        const message = error instanceof Error ? error.message : 'Resume stream request failed.'
        if (message === '当前分支没有活跃流' || message === '流不存在或已过期') {
          queryClient.invalidateQueries({ queryKey: ['conversations'] })
          queryClient.invalidateQueries({ queryKey: ['messages', activeConversationId] })
        } else {
          setErrorMessage(message)
        }
      } finally {
        if (streamKey && streamManager.get(streamKey)?.controller === controller) {
          streamManager.remove(streamKey)
        }
        delete resumeAttemptRef.current[attemptKey]
      }
    })()
  }, [
    activeConversation?.has_active_run,
    activeConversationId,
    applyStreamEvent,
    enableStreamResume,
    hasUser,
    hydrateCachedStreamSnapshots,
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
    pendingFiles,
    isUploadingFiles,
    addFiles,
    removeFile,
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
      setPendingFiles([])
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
