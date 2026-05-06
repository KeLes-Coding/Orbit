import { useEffect, useMemo, useRef, useState } from "react"
import type { CSSProperties } from "react"
import { ArrowDown } from "lucide-react"
import type { Message } from "@/api/types"
import { MessageBubble } from "./MessageBubble"

interface MessageListProps {
  messages: (Message & { paragraphs?: string[] })[]
  currentLeafMessageId?: string | null
  hasActiveRun?: boolean
  onRetry?: (messageId: string) => void
  onRegenerate?: (messageId: string) => void
  onEdit?: (messageId: string, currentContent: string) => void
  onSwitchBranch?: (messageId: string) => void
  onFork?: (messageId: string) => void
  isSending?: boolean
}

export function MessageList({
  messages,
  currentLeafMessageId,
  hasActiveRun,
  onRetry,
  onRegenerate,
  onEdit,
  onSwitchBranch,
  onFork,
  isSending,
}: MessageListProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const scrollParentRef = useRef<HTMLElement | null>(null)
  const messageRefs = useRef<Map<string, HTMLDivElement>>(new Map())
  const isPinnedToBottomRef = useRef(true)
  const isProgrammaticScrollRef = useRef(false)
  const rafIdRef = useRef<number | null>(null)
  const activeRoundIdRef = useRef<string | null>(null)
  const [activeRoundId, setActiveRoundId] = useState<string | null>(null)
  const [isPinnedToBottom, setIsPinnedToBottom] = useState(true)

  const rounds = useMemo(
    () =>
      messages
        .filter((message) => message.role === "user")
        .map((message, index) => ({
          id: message.id,
          index: index + 1,
          siblingIndex: message.sibling_index ?? 1,
          siblingCount: message.sibling_count ?? 1,
          preview: (message.content || "New message").replace(/\s+/g, " ").trim().slice(0, 150),
        })),
    [messages],
  )

  const latestMessage = messages[messages.length - 1]
  const latestMessageKey = latestMessage
    ? [
        latestMessage.id,
        latestMessage.status,
        latestMessage.content?.length ?? 0,
        latestMessage.reasoning_content?.length ?? 0,
      ].join(":")
    : "empty"

  const getScrollParent = () => {
    if (scrollParentRef.current) return scrollParentRef.current
    scrollParentRef.current = containerRef.current?.closest<HTMLElement>(".chat-canvas") ?? null
    return scrollParentRef.current
  }

  const updatePinnedState = () => {
    const el = getScrollParent()
    if (!el) return true
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    const threshold = isSending ? 88 : 120
    const isNearBottom = distanceFromBottom <= threshold
    isPinnedToBottomRef.current = isNearBottom
    setIsPinnedToBottom(isNearBottom)
    return isNearBottom
  }

  const scrollToBottom = (smooth = false) => {
    const el = getScrollParent()
    if (!el) return
    isProgrammaticScrollRef.current = true
    if (smooth) {
      el.scrollTo({ top: el.scrollHeight, behavior: "smooth" })
    } else {
      el.scrollTop = el.scrollHeight
    }
    requestAnimationFrame(() => {
      isProgrammaticScrollRef.current = false
    })
  }

  const setActiveRound = (roundId: string | null) => {
    if (activeRoundIdRef.current === roundId) return
    activeRoundIdRef.current = roundId
    setActiveRoundId(roundId)
  }

  const updateActiveRound = () => {
    const scrollParent = getScrollParent()
    if (!scrollParent || rounds.length === 0) {
      setActiveRound(null)
      return
    }

    const parentTop = scrollParent.getBoundingClientRect().top
    const anchorLine = parentTop + 132
    let currentRoundId = rounds[0]?.id ?? null

    for (const round of rounds) {
      const node = messageRefs.current.get(round.id)
      if (!node) continue
      if (node.getBoundingClientRect().top <= anchorLine) {
        currentRoundId = round.id
      } else {
        break
      }
    }
    setActiveRound(currentRoundId)
  }

  const scrollToRound = (roundId: string) => {
    const scrollParent = getScrollParent()
    const target = messageRefs.current.get(roundId)
    if (!scrollParent || !target) return

    isPinnedToBottomRef.current = false
    setIsPinnedToBottom(false)
    const parentRect = scrollParent.getBoundingClientRect()
    const targetRect = target.getBoundingClientRect()
    scrollParent.scrollTo({
      top: scrollParent.scrollTop + targetRect.top - parentRect.top - 104,
      behavior: "smooth",
    })
    setActiveRound(roundId)
  }

  useEffect(() => {
    const scrollParent = getScrollParent()
    if (!scrollParent) return
    const handleScroll = () => {
      if (isProgrammaticScrollRef.current) {
        updateActiveRound()
        return
      }
      updatePinnedState()
      updateActiveRound()
    }
    handleScroll()
    scrollParent.addEventListener("scroll", handleScroll, { passive: true })
    return () => scrollParent.removeEventListener("scroll", handleScroll)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rounds])

  /* Only keep following the stream while the user remains near the bottom. */
  useEffect(() => {
    if (isPinnedToBottomRef.current) {
      if (rafIdRef.current !== null) {
        cancelAnimationFrame(rafIdRef.current)
      }
      rafIdRef.current = requestAnimationFrame(() => {
        scrollToBottom()
        rafIdRef.current = null
      })
    }
    updateActiveRound()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [latestMessageKey, messages.length])

  useEffect(() => {
    return () => {
      if (rafIdRef.current !== null) {
        cancelAnimationFrame(rafIdRef.current)
      }
    }
  }, [])

  /* Scroll to bottom on first load */
  useEffect(() => {
    if (messages.length > 0) {
      scrollToBottom()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /* Check if a round separator should be shown between two messages */
  const shouldShowSeparator = (current: Message & { paragraphs?: string[] }, prev: Message & { paragraphs?: string[] } | null) => {
    if (!prev) return false
    if (current.role !== "user") return false
    return prev.role === "assistant"
  }

  const getRoundLabel = (roundIndex: number) => {
    return `Round ${roundIndex}`
  }

  return (
    <>
      {rounds.length > 1 && (
        <nav
          className="message-jump-nav"
          aria-label="Conversation rounds"
          style={{ "--message-jump-count": rounds.length } as CSSProperties}
        >
          <span className="message-jump-rail" aria-hidden="true" />
          {rounds.map((round) => (
            <button
              key={round.id}
              type="button"
              className={`message-jump${round.id === activeRoundId ? " active" : ""}`}
              aria-label={`Go to round ${round.index}: ${round.preview}`}
              onClick={() => scrollToRound(round.id)}
            >
              <span className="message-jump-index">{round.index}</span>
              {round.siblingCount > 1 && (
                <span className="message-jump-branch-count">
                  {round.siblingIndex}/{round.siblingCount}
                </span>
              )}
              <span className="message-jump-preview">{round.preview}</span>
            </button>
          ))}
        </nav>
      )}
      <div className="chat-stream" ref={containerRef}>
        {messages.map((message, idx) => {
          const prev = idx > 0 ? messages[idx - 1] : null
          const showSep = shouldShowSeparator(message, prev)
          const roundIdx = idx === 0
            ? 1
            : messages.slice(0, idx).filter((m) => m.role === "user").length + 1

          return (
            <div key={message.id}>
              {showSep && (
                <div className="round-separator" aria-hidden="true">
                  <span className="round-separator-label">
                    {getRoundLabel(roundIdx)}
                  </span>
                </div>
              )}
              <div
                className="message-anchor"
                ref={(node) => {
                  if (node) {
                    messageRefs.current.set(message.id, node)
                  } else {
                    messageRefs.current.delete(message.id)
                  }
                }}
              >
                <MessageBubble
                  message={message}
                  isCurrentBranchLeaf={message.id === currentLeafMessageId}
                  isCurrentBranchRunning={message.id === currentLeafMessageId && !!hasActiveRun}
                  onRetry={onRetry}
                  onRegenerate={onRegenerate}
                  onEdit={onEdit}
                  onSwitchBranch={onSwitchBranch}
                  onFork={onFork}
                  actionsDisabled={isSending}
                />
              </div>
            </div>
          )
        })}
      </div>
      {!isPinnedToBottom && messages.length > 0 && (
        <button
          type="button"
          className="scroll-bottom-btn"
          aria-label="Scroll to bottom"
          onClick={() => {
            isPinnedToBottomRef.current = true
            setIsPinnedToBottom(true)
            scrollToBottom()
          }}
        >
          <ArrowDown className="h-4 w-4" />
        </button>
      )}
    </>
  )
}
