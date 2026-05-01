import { useEffect, useMemo, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import type { Message } from "@/api/types"
import { TypingIndicator } from "./TypingIndicator"
import { OrbitIcon } from "@/components/OrbitIcon"

interface MessageBubbleProps {
  message: Message & { paragraphs?: string[] }
  onRetry?: (messageId: string) => void
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const isAssistant = message.role === "assistant"
  const isStreaming = message.status === "streaming"
  const isFailed = message.status === "failed"
  const isPartial = message.status === "partial"
  const isCancelled = message.status === "cancelled"

  const hasContent = useMemo(
    () => (message.content || "").trim().length > 0,
    [message.content],
  )
  const hasReasoning = useMemo(
    () => (message.reasoning_content || "").trim().length > 0,
    [message.reasoning_content],
  )
  const isReasoningOpenByDefault = isStreaming && hasReasoning && !hasContent
  const [isReasoningOpen, setIsReasoningOpen] = useState(isReasoningOpenByDefault)

  useEffect(() => {
    if (isStreaming && hasReasoning && !hasContent) {
      setIsReasoningOpen(true)
      return
    }
    if (hasContent) {
      setIsReasoningOpen(false)
    }
  }, [hasContent, hasReasoning, isStreaming])

  return (
    <article
      className={`message-row ${message.role}${isStreaming ? " pending" : ""}${isFailed ? " failed" : ""}`}
    >
      {isAssistant && (
        <div className="assistant-mark" aria-hidden="true">
          <OrbitIcon size={20} />
        </div>
      )}

      {message.role === "user" ? (
        <div className="user-bubble">{message.content}</div>
      ) : (
        <div className="assistant-copy">
          {hasReasoning && (
            <section
              className={`reasoning-block${isReasoningOpen ? " open" : ""}`}
              aria-label="Thought process"
            >
              <button
                type="button"
                className="reasoning-toggle"
                aria-expanded={isReasoningOpen}
                onClick={() => setIsReasoningOpen((open) => !open)}
              >
                {isStreaming && !hasContent ? "Thinking" : "Thought process"}
              </button>
              {isReasoningOpen && (
                <div className="reasoning-body">
                  {message.reasoning_content}
                </div>
              )}
            </section>
          )}

          {isStreaming && !hasContent ? (
            <TypingIndicator />
          ) : (
            <div className="markdown-body">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {message.content}
              </ReactMarkdown>
            </div>
          )}

          {isStreaming && hasContent && (
            <div className="mt-2">
              <TypingIndicator />
            </div>
          )}

          {isFailed && (
            <p className="status-message error">
              The assistant response failed. Check the model configuration and try again.
            </p>
          )}
          {isPartial && (
            <p className="status-message">
              The assistant response stopped after a partial result.
            </p>
          )}
          {isCancelled && (
            <p className="status-message">
              Generation stopped.
            </p>
          )}
        </div>
      )}
    </article>
  )
}
