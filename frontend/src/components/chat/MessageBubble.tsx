import { useEffect, useMemo, useRef, useState } from "react"
import type { ReactNode } from "react"
import { Copy, GitFork, Pencil, RotateCcw, User } from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import type { Message } from "@/api/types"
import { TypingIndicator } from "./TypingIndicator"
import { OrbitIcon } from "@/components/OrbitIcon"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"

/* DeepSeek-inspired chevron SVGs */
function ChevronLeftIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 14 14" fill="none">
      <path
        d="M8.5 2.15137L8.07617 2.57617L5.34863 5.30273C5.09294 5.55843 4.86618 5.78438 4.70215 5.98828C4.53117 6.20088 4.38244 6.44405 4.33398 6.75C4.30778 6.91565 4.30778 7.08435 4.33398 7.25C4.38244 7.55595 4.53117 7.79912 4.70215 8.01172C4.86618 8.21561 5.09294 8.44157 5.34863 8.69727L8.07617 11.4238L8.5 11.8486L9.34863 11L8.92383 10.5762L6.19727 7.84863C5.92268 7.57405 5.75151 7.40124 5.6377 7.25977C5.53096 7.12709 5.52187 7.07728 5.51953 7.0625C5.51297 7.02105 5.51297 6.97895 5.51953 6.9375C5.52187 6.92272 5.53096 6.87291 5.6377 6.74023C5.75152 6.59876 5.92268 6.42595 6.19727 6.15137L8.92383 3.42383L9.34863 3L8.5 2.15137Z"
        fill="currentColor"
      />
    </svg>
  )
}

function ChevronRightIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 14 14" fill="none">
      <path
        d="M5.5 2.15137L5.92383 2.57617L8.65137 5.30273C8.90706 5.55843 9.13382 5.78438 9.29785 5.98828C9.46883 6.20088 9.61756 6.44405 9.66602 6.75C9.69222 6.91565 9.69222 7.08435 9.66602 7.25C9.61756 7.55595 9.46883 7.79912 9.29785 8.01172C9.13382 8.21561 8.90706 8.44157 8.65137 8.69727L5.92383 11.4238L5.5 11.8486L4.65137 11L5.07617 10.5762L7.80273 7.84863C8.07732 7.57405 8.24849 7.40124 8.3623 7.25977C8.46904 7.12709 8.47813 7.07728 8.48047 7.0625C8.48703 7.02105 8.48703 6.97895 8.48047 6.9375C8.47813 6.92272 8.46904 6.87291 8.3623 6.74023C8.24848 6.59876 8.07732 6.42595 7.80273 6.15137L5.07617 3.42383L4.65137 3L5.5 2.15137Z"
        fill="currentColor"
      />
    </svg>
  )
}

interface MessageBubbleProps {
  message: Message & { paragraphs?: string[] }
  onRetry?: (messageId: string) => void
  onRegenerate?: (messageId: string) => void
  onEdit?: (messageId: string, currentContent: string) => void
  onSwitchBranch?: (messageId: string) => void
  onFork?: (messageId: string) => void
  actionsDisabled?: boolean
  shouldAnimate?: boolean
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
  shouldAnimate = false,
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

  const paragraphCounterRef = useRef(0)
  paragraphCounterRef.current = 0

  const markdownComponents = useMemo(
    () => ({
      p({ children }: { children?: ReactNode }) {
        if (!shouldAnimate) return <p>{children}</p>
        const idx = paragraphCounterRef.current++
        return (
          <p
            className="stagger-paragraph"
            style={{ animationDelay: `${idx * 48}ms` }}
          >
            {children}
          </p>
        )
      },
    }),
    [shouldAnimate],
  )

  const branchDots = useMemo(() => {
    if (siblingCount <= 1) return null
    return (
      <div className="branch-dots">
        {Array.from({ length: siblingCount }, (_, i) => (
          <span
            key={i}
            className={`branch-dot${i + 1 === siblingIndex ? " active" : ""}`}
          />
        ))}
      </div>
    )
  }, [siblingCount, siblingIndex])

  /* Copy message content to clipboard */
  const handleCopy = () => {
    const text = isAssistant ? message.content || (message.reasoning_content || "") : message.content || ""
    navigator.clipboard.writeText(text).catch(() => {})
  }

  /* Branch navigator shared across user and assistant toolbars */
  const branchNavigator = hasSiblings ? (
    <div className="branch-switcher" aria-label="Message versions">
      <MessageAction
        label="Previous version"
        disabled={!canAct || !message.previous_sibling_id}
        onClick={() => message.previous_sibling_id && onSwitchBranch?.(message.previous_sibling_id)}
      >
        <ChevronLeftIcon />
      </MessageAction>
      {branchDots}
      <span className="branch-count">{siblingIndex}/{siblingCount}</span>
      <MessageAction
        label="Next version"
        disabled={!canAct || !message.next_sibling_id}
        onClick={() => message.next_sibling_id && onSwitchBranch?.(message.next_sibling_id)}
      >
        <ChevronRightIcon />
      </MessageAction>
    </div>
  ) : null

  return (
    <article
      className={`message-row ${message.role}${isStreaming ? " pending" : ""}${isFailed ? " failed" : ""}`}
    >
      {isAssistant && (
        <div className="assistant-mark" aria-hidden="true">
          <OrbitIcon size={18} />
        </div>
      )}

      {isUser ? (
        <>
          <div className="message-user-wrap">
            <div className="user-bubble">{message.content}</div>
            <div className="message-toolbar user-tools" aria-label="Message actions">
              <MessageAction label="Copy" onClick={handleCopy}>
                <Copy />
              </MessageAction>
              {branchNavigator}
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
          <div className="user-mark" aria-hidden="true">
            <User />
          </div>
        </>
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
                <svg
                  className="chevron-icon"
                  width="12"
                  height="12"
                  viewBox="0 0 14 14"
                  fill="none"
                >
                  <path
                    d="M5.5 2.15137L5.92383 2.57617L8.65137 5.30273C8.90706 5.55843 9.13382 5.78438 9.29785 5.98828C9.46883 6.20088 9.61756 6.44405 9.66602 6.75C9.69222 6.91565 9.69222 7.08435 9.66602 7.25C9.61756 7.55595 9.46883 7.79912 9.29785 8.01172C9.13382 8.21561 8.90706 8.44157 8.65137 8.69727L5.92383 11.4238L5.5 11.8486L4.65137 11L5.07617 10.5762L7.80273 7.84863C8.07732 7.57405 8.24849 7.40124 8.3623 7.25977C8.46904 7.12709 8.47813 7.07728 8.48047 7.0625C8.48703 7.02105 8.48703 6.97895 8.48047 6.9375C8.47813 6.92272 8.46904 6.87291 8.3623 6.74023C8.24848 6.59876 8.07732 6.42595 7.80273 6.15137L5.07617 3.42383L4.65137 3L5.5 2.15137Z"
                    fill="currentColor"
                  />
                </svg>
                {isStreaming && !hasContent ? "Thinking" : "Thought process"}
              </button>
              <div className="reasoning-body">
                {message.reasoning_content}
              </div>
            </section>
          )}

          {isStreaming && !hasContent ? (
            <TypingIndicator />
          ) : (
            <div className="markdown-body">
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
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
            <MessageAction label="Copy" onClick={handleCopy}>
              <Copy />
            </MessageAction>
            {branchNavigator}
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
