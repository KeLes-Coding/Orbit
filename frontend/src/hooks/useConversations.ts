import { useMemo, useCallback, useEffect } from 'react'
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

export function useConversations(hasUser: boolean) {
  const activeConversationId = useOrbitStore((s) => s.activeConversationId)
  const draft = useOrbitStore((s) => s.draft)
  const setActiveConversationId = useOrbitStore((s) => s.setActiveConversationId)
  const setDraft = useOrbitStore((s) => s.setDraft)
  const setErrorMessage = useOrbitStore((s) => s.setErrorMessage)
  const setActiveView = useOrbitStore((s) => s.setActiveView)
  const queryClient = useQueryClient()

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

  const sendMessageMutation = useMutation({
    mutationFn: ({ conversationId, content }: { conversationId: string; content: string }) =>
      conversationApi.sendMessage(conversationId, content),
    onMutate: async ({ conversationId, content }) => {
      await queryClient.cancelQueries({ queryKey: ['messages', conversationId] })
      const previousMessages =
        queryClient.getQueryData<Message[]>(['messages', conversationId]) || []

      const optUser = normalizeMessage({
        id: `local-user-${Date.now()}`,
        conversation_id: conversationId,
        role: 'user',
        content,
        status: 'completed',
        created_at: new Date().toISOString(),
      })!
      const optAsst = normalizeMessage({
        id: `local-assistant-${Date.now()}`,
        conversation_id: conversationId,
        role: 'assistant',
        content: 'Thinking...',
        status: 'streaming',
        created_at: new Date().toISOString(),
      })!

      queryClient.setQueryData<Message[]>(['messages', conversationId], [
        ...previousMessages,
        optUser,
        optAsst,
      ])

      return { previousMessages }
    },
    onError: (error: Error, { conversationId }, context) => {
      if (context?.previousMessages) {
        queryClient.setQueryData(['messages', conversationId], context.previousMessages)
      }
      setErrorMessage(error.message)
    },
    onSuccess: (response, { conversationId }) => {
      queryClient.setQueryData<Message[]>(['messages', conversationId], (old = []) => {
        const filtered = old.filter((m) => !String(m.id).startsWith('local-'))
        return [...filtered, response.user_message, response.assistant_message].sort(
          (a, b) => (a.sequence_no ?? 0) - (b.sequence_no ?? 0),
        )
      })
      queryClient.invalidateQueries({ queryKey: ['conversations'] })
    },
  })

  const sendMessage = useCallback(() => {
    const content = draft.trim()
    if (!content || sendMessageMutation.isPending) return
    if (!hasUser) {
      setErrorMessage('Sign in before sending messages.')
      return
    }
    setDraft('')
    setErrorMessage('')

    let conversationId = activeConversationId
    if (!conversationId) {
      const title = content.length > 48 ? `${content.slice(0, 48)}...` : content
      createNewThread.mutate(title, {
        onSuccess: (conv) => {
          if (conv) {
            sendMessageMutation.mutate({ conversationId: conv.id, content })
          }
        },
      })
      return
    }

    sendMessageMutation.mutate({ conversationId, content })
  }, [draft, activeConversationId, hasUser, sendMessageMutation, createNewThread, setDraft, setErrorMessage])

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
    isSending: sendMessageMutation.isPending,
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
    renameConversation: (id: string, title: string) => renameConversation.mutate({ id, title }),
    archiveConversation: (id: string) => archiveConversation.mutate(id),
    switchConversationLlm: (conversationId: string, configId: string) =>
      switchConversationLlm.mutate({ conversationId, configId }),
  }
}
