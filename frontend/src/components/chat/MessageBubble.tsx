import { useMemo } from "react"
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
          <OrbitIcon size={18} alwaysInvert />
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
