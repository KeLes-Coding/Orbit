import { useEffect, useMemo, useState } from "react"
import type { ReactNode } from "react"
import { ChevronLeft, ChevronRight, GitFork, Pencil, RotateCcw } from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import type { Message } from "@/api/types"
import { TypingIndicator } from "./TypingIndicator"
import { OrbitIcon } from "@/components/OrbitIcon"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"

interface MessageBubbleProps {
  message: Message & { paragraphs?: string[] }
  onRetry?: (messageId: string) => void
  onRegenerate?: (messageId: string) => void
  onEdit?: (messageId: string, currentContent: string) => void
  onSwitchBranch?: (messageId: string) => void
  onFork?: (messageId: string) => void
  actionsDisabled?: boolean
}

function MessageAction({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string
  disabled?: boolean
  onClick?: () => void
  children: ReactNode
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          className="message-action"
          aria-label={label}
          disabled={disabled}
          onClick={onClick}
        >
          {children}
        </button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}

export function MessageBubble({
  message,
  onRetry,
  onRegenerate,
  onEdit,
  onSwitchBranch,
  onFork,
  actionsDisabled,
}: MessageBubbleProps) {
  const isAssistant = message.role === "assistant"
  const isUser = message.role === "user"
  const isStreaming = message.status === "streaming"
  const isFailed = message.status === "failed"
  const isPartial = message.status === "partial"
  const isCancelled = message.status === "cancelled"
  const siblingCount = message.sibling_count ?? 1
  const siblingIndex = message.sibling_index ?? 1
  const hasSiblings = siblingCount > 1
  const canAct = !actionsDisabled && !isStreaming && !String(message.id).startsWith("local-")

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

      {isUser ? (
        <div className="message-user-wrap">
          <div className="user-bubble">{message.content}</div>
          <div className="message-toolbar user-tools" aria-label="Message actions">
            {hasSiblings && (
              <div className="branch-switcher" aria-label="Message versions">
                <MessageAction
                  label="Previous version"
                  disabled={!canAct || !message.previous_sibling_id}
                  onClick={() => message.previous_sibling_id && onSwitchBranch?.(message.previous_sibling_id)}
                >
                  <ChevronLeft />
                </MessageAction>
                <span className="branch-count">{siblingIndex} / {siblingCount}</span>
                <MessageAction
                  label="Next version"
                  disabled={!canAct || !message.next_sibling_id}
                  onClick={() => message.next_sibling_id && onSwitchBranch?.(message.next_sibling_id)}
                >
                  <ChevronRight />
                </MessageAction>
              </div>
            )}
            <MessageAction
              label="Edit message"
              disabled={!canAct}
              onClick={() => onEdit?.(message.id, message.content || "")}
            >
              <Pencil />
            </MessageAction>
            <MessageAction
              label="Fork from here"
              disabled={!canAct}
              onClick={() => onFork?.(message.id)}
            >
              <GitFork />
            </MessageAction>
          </div>
        </div>
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
          <div className="message-toolbar assistant-tools" aria-label="Message actions">
            {hasSiblings && (
              <div className="branch-switcher" aria-label="Message versions">
                <MessageAction
                  label="Previous version"
                  disabled={!canAct || !message.previous_sibling_id}
                  onClick={() => message.previous_sibling_id && onSwitchBranch?.(message.previous_sibling_id)}
                >
                  <ChevronLeft />
                </MessageAction>
                <span className="branch-count">{siblingIndex} / {siblingCount}</span>
                <MessageAction
                  label="Next version"
                  disabled={!canAct || !message.next_sibling_id}
                  onClick={() => message.next_sibling_id && onSwitchBranch?.(message.next_sibling_id)}
                >
                  <ChevronRight />
                </MessageAction>
              </div>
            )}
            <MessageAction
              label="Regenerate response"
              disabled={!canAct}
              onClick={() => (onRegenerate || onRetry)?.(message.id)}
            >
              <RotateCcw />
            </MessageAction>
            <MessageAction
              label="Fork from here"
              disabled={!canAct}
              onClick={() => onFork?.(message.id)}
            >
              <GitFork />
            </MessageAction>
          </div>
        </div>
      )}
    </article>
  )
}
