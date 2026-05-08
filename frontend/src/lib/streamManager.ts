import { useOrbitStore } from '@/stores/useOrbitStore'

export type ManagedStreamSource = 'created' | 'resumed'
export type ManagedStreamStatus = 'connecting' | 'streaming' | 'completed' | 'failed' | 'cancelled'

export interface ManagedStreamHandle {
  streamKey: string
  conversationId: string
  messageId: string | null
  streamId: string | null
  controller: AbortController
  source: ManagedStreamSource
  status: ManagedStreamStatus
  startedAt: number
  lastEventAt: number
}

const MAX_ACTIVE_STREAMS = 5
const streamsByKey = new Map<string, ManagedStreamHandle>()

function now(): number {
  return Date.now()
}

function markConversationReceiving(conversationId: string): void {
  const hasLocalStream = [...streamsByKey.values()].some(
    (stream) =>
      stream.conversationId === conversationId &&
      stream.status !== 'completed' &&
      stream.status !== 'failed' &&
      stream.status !== 'cancelled',
  )
  useOrbitStore.getState().markConversationReceiving(conversationId, hasLocalStream)
}

function markAllReceiving(): void {
  const conversationIds = new Set([...streamsByKey.values()].map((stream) => stream.conversationId))
  for (const conversationId of conversationIds) {
    markConversationReceiving(conversationId)
  }
}

function pruneInactive(): void {
  const touched = new Set<string>()
  for (const [streamKey, stream] of streamsByKey.entries()) {
    if (stream.status === 'completed' || stream.status === 'failed' || stream.status === 'cancelled') {
      streamsByKey.delete(streamKey)
      touched.add(stream.conversationId)
    }
  }
  for (const conversationId of touched) {
    markConversationReceiving(conversationId)
  }
}

function evictIfNeeded(protectedStreamKey?: string | null): void {
  pruneInactive()
  if (streamsByKey.size < MAX_ACTIVE_STREAMS) return

  const activeConversationId = useOrbitStore.getState().activeConversationId
  const candidates = [...streamsByKey.entries()]
    .filter(([streamKey]) => streamKey !== protectedStreamKey)
    .filter(([, stream]) => stream.conversationId !== activeConversationId)
    .sort(([, a], [, b]) => a.lastEventAt - b.lastEventAt || a.startedAt - b.startedAt)

  const fallbackCandidates = [...streamsByKey.entries()]
    .filter(([streamKey]) => streamKey !== protectedStreamKey)
    .sort(([, a], [, b]) => a.lastEventAt - b.lastEventAt || a.startedAt - b.startedAt)

  const evicted = candidates[0] ?? fallbackCandidates[0]
  if (!evicted) return

  const [streamKey, stream] = evicted
  stream.controller.abort()
  streamsByKey.delete(streamKey)
  markConversationReceiving(stream.conversationId)
}

function findKeyByMessageId(messageId: string): string | null {
  for (const [streamKey, stream] of streamsByKey.entries()) {
    if (stream.messageId === messageId) return streamKey
  }
  return null
}

export const streamManager = {
  maxActiveStreams: MAX_ACTIVE_STREAMS,

  makePendingKey(conversationId: string, prefix = 'pending'): string {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return `${prefix}:${conversationId}:${crypto.randomUUID()}`
    }
    return `${prefix}:${conversationId}:${Date.now()}:${Math.random().toString(36).slice(2, 10)}`
  },

  get(streamKey: string | null | undefined): ManagedStreamHandle | null {
    if (!streamKey) return null
    return streamsByKey.get(streamKey) ?? null
  },

  getByMessageId(messageId: string | null | undefined): ManagedStreamHandle | null {
    if (!messageId) return null
    const key = findKeyByMessageId(messageId)
    return key ? streamsByKey.get(key) ?? null : null
  },

  getByConversation(conversationId: string | null | undefined): ManagedStreamHandle[] {
    if (!conversationId) return []
    return [...streamsByKey.values()].filter((stream) => stream.conversationId === conversationId)
  },

  hasConversation(conversationId: string | null | undefined): boolean {
    return this.getByConversation(conversationId).length > 0
  },

  set(handle: Omit<ManagedStreamHandle, 'startedAt' | 'lastEventAt' | 'status'> & { status?: ManagedStreamStatus }): void {
    const existing = streamsByKey.get(handle.streamKey)
    if (!existing) {
      evictIfNeeded(handle.streamKey)
    }
    const timestamp = now()
    streamsByKey.set(handle.streamKey, {
      ...handle,
      status: handle.status ?? 'connecting',
      startedAt: existing?.startedAt ?? timestamp,
      lastEventAt: timestamp,
    })
    markConversationReceiving(handle.conversationId)
  },

  update(streamKey: string | null | undefined, updates: Partial<ManagedStreamHandle>): void {
    if (!streamKey) return
    const existing = streamsByKey.get(streamKey)
    if (!existing) return
    streamsByKey.set(streamKey, {
      ...existing,
      ...updates,
      lastEventAt: now(),
    })
    markConversationReceiving(updates.conversationId ?? existing.conversationId)
  },

  adoptMessageId(streamKey: string, messageId: string): string {
    const existing = streamsByKey.get(streamKey)
    if (!existing) return streamKey

    const canonicalKey = messageId
    streamsByKey.delete(streamKey)
    const previousCanonical = streamsByKey.get(canonicalKey)
    if (previousCanonical && previousCanonical.controller !== existing.controller) {
      previousCanonical.controller.abort()
    }
    streamsByKey.set(canonicalKey, {
      ...existing,
      streamKey: canonicalKey,
      messageId,
      lastEventAt: now(),
    })
    markConversationReceiving(existing.conversationId)
    return canonicalKey
  },

  remove(streamKey: string | null | undefined): void {
    if (!streamKey) return
    const existing = streamsByKey.get(streamKey)
    if (!existing) return
    streamsByKey.delete(streamKey)
    markConversationReceiving(existing.conversationId)
  },

  abort(streamKey: string | null | undefined): void {
    const stream = this.get(streamKey)
    if (!stream) return
    stream.controller.abort()
    this.remove(streamKey)
  },

  abortAll(): void {
    for (const stream of streamsByKey.values()) {
      stream.controller.abort()
    }
    streamsByKey.clear()
    useOrbitStore.getState().clearReceivingConversations()
  },

  refreshReceivingState(): void {
    markAllReceiving()
  },
}
