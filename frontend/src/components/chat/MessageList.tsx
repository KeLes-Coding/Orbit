import { useEffect, useRef } from "react"
import type { Message } from "@/api/types"
import { MessageBubble } from "./MessageBubble"

interface MessageListProps {
  messages: (Message & { paragraphs?: string[] })[]
  onRetry?: (messageId: string) => void
}

export function MessageList({ messages, onRetry }: MessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  const isNearBottom = () => {
    const el = containerRef.current
    if (!el) return true
    return el.scrollHeight - el.scrollTop - el.clientHeight < 120
  }

  const scrollToBottom = (smooth = false) => {
    bottomRef.current?.scrollIntoView({ behavior: smooth ? "smooth" : "auto" })
  }

  /* Scroll to bottom when messages change, if user is near bottom */
  useEffect(() => {
    if (isNearBottom()) {
      scrollToBottom()
    }
  }, [messages])

  /* Scroll to bottom on first load */
  useEffect(() => {
    if (messages.length > 0) {
      scrollToBottom()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="chat-stream" ref={containerRef}>
      {messages.map((message) => (
        <MessageBubble key={message.id} message={message} onRetry={onRetry} />
      ))}
      <div ref={bottomRef} />
    </div>
  )
}
