import { memo, useEffect, useMemo, useRef, useState } from "react"
import type { ReactNode } from "react"
import { Copy, GitFork, Pencil, RotateCcw, User } from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import type { Message } from "@/api/types"
import { TypingIndicator } from "./TypingIndicator"
import { OrbitIcon } from "@/components/OrbitIcon"
import { FileAttachmentList } from "./FileAttachmentCard"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"

/* DeepSeek-inspired chevron SVGs */
/** agent 工具调用卡片列表：每个 tool_call + tool_result 对渲染为可折叠卡片。*/
function AgentToolCards({ deltas }: { deltas: NonNullable<Message['agentDeltas']> }) {
  const [expanded, setExpanded] = useState<Record<number, boolean>>({})

  if (!deltas || deltas.length === 0) return null

  return (
    <div className="agent-tool-cards">
      {deltas.map((delta, index) => (
        <div key={index} className="agent-tool-card">
          <button
            type="button"
            className="agent-tool-card-header"
            onClick={() => setExpanded((prev) => ({ ...prev, [index]: !prev[index] }))}
          >
            <span className="agent-tool-icon">
              {delta.type === 'tool_call'
                ? '🔧'
                : delta.type === 'tool_result'
                  ? '📄'
                  : delta.type === 'subagent_started' || delta.type === 'subagent_delta' || delta.type === 'subagent_completed'
                    ? '🤖'
                    : '📋'}
            </span>
            <span className="agent-tool-name">
              {delta.type === 'tool_call'
                ? delta.tool_name || 'tool_call'
                : delta.type === 'tool_result'
                  ? '结果'
                  : delta.type === 'subagent_started'
                    ? `${delta.subagent_name || 'subagent'} started`
                    : delta.type === 'subagent_completed'
                      ? `${delta.subagent_name || 'subagent'} completed`
                      : delta.type === 'subagent_delta'
                        ? delta.subagent_name || 'subagent'
                  : 'todo'}
            </span>
            {delta.type === 'tool_call' && delta.tool_input && (
              <span className="agent-tool-input-preview">
                {Object.values(delta.tool_input).slice(0, 2).join(', ')}
              </span>
            )}
            <span className="agent-tool-expand">{expanded[index] ? '▾' : '▸'}</span>
          </button>
          {expanded[index] && (
            <div className="agent-tool-card-body">
              {delta.type === 'tool_call' && delta.tool_input && (
                <pre className="agent-tool-input">
                  {JSON.stringify(delta.tool_input, null, 2)}
                </pre>
              )}
              {delta.type === 'tool_result' && delta.content && (
                <pre className="agent-tool-content">{delta.content}</pre>
              )}
              {delta.type === 'todo' && delta.content && (
                <p className="agent-tool-todo">{delta.content}</p>
              )}
              {delta.type === 'subagent_delta' && delta.content && (
                <pre className="agent-tool-content">{delta.content}</pre>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

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
  isCurrentBranchLeaf?: boolean
  isCurrentBranchRunning?: boolean
  onRetry?: (messageId: string) => void
  onRegenerate?: (messageId: string) => void
  regenerateMessageId?: string | null
  onEdit?: (messageId: string, newContent: string) => void
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

export const MessageBubble = memo(function MessageBubble({
  message,
  isCurrentBranchLeaf,
  isCurrentBranchRunning,
  onRetry,
  onRegenerate,
  regenerateMessageId,
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
  const canSwitchBranch = !actionsDisabled && !String(message.id).startsWith("local-")

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

  const [isEditing, setIsEditing] = useState(false)
  const [editContent, setEditContent] = useState("")
  const editTextareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (isStreaming && hasReasoning && !hasContent) {
      setIsReasoningOpen(true)
      return
    }
    if (hasContent) {
      setIsReasoningOpen(false)
    }
  }, [hasContent, hasReasoning, isStreaming])

  useEffect(() => {
    if (isEditing && editTextareaRef.current) {
      const ta = editTextareaRef.current
      ta.focus()
      ta.selectionStart = ta.value.length
      ta.selectionEnd = ta.value.length
    }
  }, [isEditing])

  const handleStartEdit = () => {
    setEditContent(message.content || "")
    setIsEditing(true)
  }

  const handleSaveEdit = () => {
    const trimmed = editContent.trim()
    if (!trimmed) {
      setIsEditing(false)
      return
    }
    if (trimmed === (message.content || "").trim()) {
      if (regenerateMessageId) {
        onRegenerate?.(regenerateMessageId)
      }
      setIsEditing(false)
      return
    }
    onEdit?.(message.id, trimmed)
    setIsEditing(false)
  }

  const handleCancelEdit = () => {
    setIsEditing(false)
  }

  const handleEditKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleSaveEdit()
    } else if (e.key === "Escape") {
      e.preventDefault()
      handleCancelEdit()
    }
  }

  useEffect(() => {
    if (editTextareaRef.current) {
      const ta = editTextareaRef.current
      ta.style.height = "auto"
      ta.style.height = ta.scrollHeight + "px"
    }
  }, [editContent])

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
        disabled={!canSwitchBranch || !message.previous_sibling_id}
        onClick={() => message.previous_sibling_id && onSwitchBranch?.(message.previous_sibling_id)}
      >
        <ChevronLeftIcon />
      </MessageAction>
      <span className="branch-count">{siblingIndex}/{siblingCount}</span>
      <MessageAction
        label="Next version"
        disabled={!canSwitchBranch || !message.next_sibling_id}
        onClick={() => message.next_sibling_id && onSwitchBranch?.(message.next_sibling_id)}
      >
        <ChevronRightIcon />
      </MessageAction>
    </div>
  ) : null

  // 只在当前 branch 正在生成时打标，避免静态 leaf 状态占用阅读空间。
  const branchStateBadge =
    isCurrentBranchLeaf && isCurrentBranchRunning ? (
      <span className="branch-state-badge" aria-label="Current branch is running">
        Live branch
      </span>
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
            {isEditing ? (
              <div className="user-bubble editing">
                <textarea
                  ref={editTextareaRef}
                  className="edit-textarea"
                  value={editContent}
                  onChange={(e) => setEditContent(e.target.value)}
                  onKeyDown={handleEditKeyDown}
                  rows={Math.min(editContent.split("\n").length, 10)}
                />
                <div className="edit-actions">
                  <button
                    type="button"
                    className="edit-cancel-btn"
                    onClick={handleCancelEdit}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="edit-save-btn"
                    onClick={handleSaveEdit}
                  >
                    Save &amp; Submit
                  </button>
                </div>
              </div>
            ) : (
              <>
                <div className="user-bubble">{message.content}</div>
                <FileAttachmentList contentParts={message.content_parts || []} />
              </>
            )}
            {branchStateBadge && <div className="branch-state-row user">{branchStateBadge}</div>}
            {!isEditing && (
              <div className="message-toolbar user-tools" aria-label="Message actions">
                <MessageAction label="Copy" onClick={handleCopy}>
                  <Copy />
                </MessageAction>
                {branchNavigator}
                <MessageAction
                  label="Edit message"
                  disabled={!canAct}
                  onClick={handleStartEdit}
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
            )}
          </div>
          <div className="user-mark" aria-hidden="true">
            <User />
          </div>
        </>
      ) : (
        <div className="assistant-copy">
          {branchStateBadge && <div className="branch-state-row">{branchStateBadge}</div>}
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
            <>
              <div className="markdown-body">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {message.content}
                </ReactMarkdown>
              </div>
              <FileAttachmentList contentParts={message.content_parts || []} />

              {/* agent 工具调用卡片：折叠式展示 tool_call → tool_result */}
              {(message.agentDeltas?.length ?? 0) > 0 && (
                <AgentToolCards deltas={message.agentDeltas!} />
              )}
            </>
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
})
