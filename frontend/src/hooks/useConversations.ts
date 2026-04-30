import { useMemo, useCallback, useEffect, useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { conversationApi } from '@/api/conversations'
import { useOrbitStore } from '@/stores/useOrbitStore'
import type { Conversation, Message } from '@/api/types'

interface NormalizedMessage extends Message {
  paragraphs: string[]
}

function normalizeMessage(message: Message | null | undefined): NormalizedMessage | null {
  if (!message) return null
  return {
    ...message,
    content: message.content || '',
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

export function useConversations(hasUser: boolean) {
  const activeConversationId = useOrbitStore((s) => s.activeConversationId)
  const draft = useOrbitStore((s) => s.draft)
  const setActiveConversationId = useOrbitStore((s) => s.setActiveConversationId)
  const setDraft = useOrbitStore((s) => s.setDraft)
  const setErrorMessage = useOrbitStore((s) => s.setErrorMessage)
  const setActiveView = useOrbitStore((s) => s.setActiveView)
  const queryClient = useQueryClient()
  const [isStreaming, setIsStreaming] = useState(false)
  const activeStreamRef = useRef<{
    conversationId: string
    messageId: string | null
    controller: AbortController
  } | null>(null)

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

  const sortedConversations = useMemo(
    () =>
      [...conversations].sort(
        (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
      ),
    [conversations],
  )

  const activeConversation = useMemo(
    () => conversations.find((c) => c.id === activeConversationId),
    [conversations, activeConversationId],
  )

  const isLoadingMessages = messagesQuery.isLoading || messagesQuery.isFetching

  useEffect(() => {
    if (!hasUser || activeConversationId || sortedConversations.length === 0) return
    setActiveConversationId(sortedConversations[0].id)
  }, [activeConversationId, hasUser, setActiveConversationId, sortedConversations])

  const formatConversationTitle = useCallback(
    (conversation: Conversation) =>
      conversation.title || `Thread ${conversation.thread_id?.toString().slice(0, 8) ?? conversation.id.slice(0, 8)}`,
    [],
  )

  const selectConversation = useCallback(
    (conversationId: string) => {
      setActiveView('chat')
      setActiveConversationId(conversationId)
      setErrorMessage('')
    },
    [setActiveView, setActiveConversationId, setErrorMessage],
  )

  const createNewThread = useMutation({
    mutationFn: (title?: string | null) =>
      conversationApi.create({ title, chat_mode: 'chat', metadata: {} }),
    onSuccess: (conversation) => {
      queryClient.setQueryData<Conversation[]>(['conversations'], (old = []) => [
        conversation,
        ...old,
      ])
      setActiveConversationId(conversation.id)
      queryClient.setQueryData(['messages', conversation.id], [])
      setActiveView('chat')
    },
    onError: (error: Error) => {
      setErrorMessage(error.message)
    },
  })

  const streamMessage = useCallback(
    async (conversationId: string, content: string) => {
      await queryClient.cancelQueries({ queryKey: ['messages', conversationId] })
      const previousMessages = queryClient.getQueryData<Message[]>(['messages', conversationId]) || []
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
          status: 'streaming',
          created_at: new Date().toISOString(),
        },
      ])

      setIsStreaming(true)
      activeStreamRef.current = { conversationId, messageId: null, controller }

      try {
        for await (const streamEvent of conversationApi.streamMessage(
          conversationId,
          content,
          controller.signal,
        )) {
          if (streamEvent.event === 'message.created') {
            activeStreamRef.current = {
              conversationId,
              messageId: streamEvent.data.assistant_message.id,
              controller,
            }
            queryClient.setQueryData<Message[]>(['messages', conversationId], (old = []) =>
              replaceLocalExchange(
                old,
                streamEvent.data.user_message,
                streamEvent.data.assistant_message,
              ),
            )
            continue
          }

          if (streamEvent.event === 'message.delta') {
            queryClient.setQueryData<Message[]>(['messages', conversationId], (old = []) =>
              appendMessageDelta(old, streamEvent.data.message_id, streamEvent.data.delta),
            )
            continue
          }

          queryClient.setQueryData<Message[]>(['messages', conversationId], (old = []) =>
            upsertMessage(old, streamEvent.data.message),
          )
        }
        queryClient.invalidateQueries({ queryKey: ['conversations'] })
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          return
        }
        queryClient.setQueryData(['messages', conversationId], previousMessages)
        setErrorMessage(error instanceof Error ? error.message : 'Streaming request failed.')
      } finally {
        activeStreamRef.current = null
        setIsStreaming(false)
      }
    },
    [queryClient, setErrorMessage],
  )

  const sendMessage = useCallback(() => {
    const content = draft.trim()
    if (!content || isStreaming) return
    if (!hasUser) {
      setErrorMessage('Sign in before sending messages.')
      return
    }
    setDraft('')
    setErrorMessage('')

    let conversationId = activeConversationId
    if (!conversationId) {
      const title = content.length > 48 ? `${content.slice(0, 48)}...` : content
      createNewThread
        .mutateAsync(title)
        .then((conv) => streamMessage(conv.id, content))
        .catch((error: Error) => setErrorMessage(error.message))
      return
    }

    void streamMessage(conversationId, content)
  }, [
    draft,
    activeConversationId,
    hasUser,
    isStreaming,
    streamMessage,
    createNewThread,
    setDraft,
    setErrorMessage,
  ])

  const stopGeneration = useCallback(async () => {
    const activeStream = activeStreamRef.current
    if (!activeStream?.messageId) return

    try {
      await conversationApi.cancelMessage(activeStream.conversationId, activeStream.messageId)
      // 取消接口可能会直接打断 SSE 任务，前端先用已有增量内容更新为 cancelled。
      queryClient.setQueryData<Message[]>(['messages', activeStream.conversationId], (old = []) =>
        old.map((message) =>
          message.id === activeStream.messageId ? { ...message, status: 'cancelled' } : message,
        ),
      )
      activeStream.controller.abort()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to stop generation.')
    } finally {
      activeStreamRef.current = null
      setIsStreaming(false)
    }
  }, [queryClient, setErrorMessage])

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
    messages,
    isLoadingMessages,
    isSending: isStreaming,
    formatConversationTitle,
    selectConversation,
    createNewThread: (title?: string | null) => {
      if (!hasUser) {
        setErrorMessage('Sign in before creating a conversation.')
        return
      }
      createNewThread.mutate(title)
    },
    sendMessage,
    stopGeneration,
    renameConversation: (id: string, title: string) => renameConversation.mutate({ id, title }),
    archiveConversation: (id: string) => archiveConversation.mutate(id),
    switchConversationLlm: (conversationId: string, configId: string) =>
      switchConversationLlm.mutate({ conversationId, configId }),
  }
}
