import { useMemo } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import type { Message } from "@/api/types"
import { TypingIndicator } from "./TypingIndicator"

function WaterDrop({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="currentColor"
      xmlns="http://www.w3.org/2000/svg"
    >
      <path d="M12 2C12 2 6 10 6 14.5C6 17.8 8.7 20.5 12 20.5C15.3 20.5 18 17.8 18 14.5C18 10 12 2 12 2Z" />
    </svg>
  )
}

interface MessageBubbleProps {
  message: Message & { paragraphs?: string[] }
  onRetry?: (messageId: string) => void
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const isAssistant = message.role === "assistant"
  const isStreaming = message.status === "streaming"
  const isFailed = message.status === "failed"

  const hasContent = useMemo(
    () => (message.content || "").trim().length > 0,
    [message.content],
  )

  return (
    <article
      className={`message-row ${message.role}${isStreaming ? " pending" : ""}${isFailed ? " failed" : ""}`}
    >
      {isAssistant && (
        <div className="assistant-mark" aria-hidden="true">
          <WaterDrop className="assistant-mark-icon" />
        </div>
      )}

      {message.role === "user" ? (
        <div className="user-bubble">{message.content}</div>
      ) : (
        <div className="assistant-copy">
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
        </div>
      )}
    </article>
  )
}
