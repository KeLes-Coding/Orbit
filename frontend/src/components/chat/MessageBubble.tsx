import { memo, useEffect, useMemo, useState } from "react"
import type { ReactNode } from "react"
import { Copy, GitFork, Pencil, RotateCcw, User, Brain, Wrench, FileCheck } from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import type { Message, ToolCallDelta, ToolResultDelta } from "@/api/types"
import { useMessageEdit } from "@/hooks/useMessageEdit"
import { TypingIndicator } from "./TypingIndicator"
import { TimelinePhase } from "./TimelinePhase"
import { OrbitIcon } from "@/components/OrbitIcon"
import { FileAttachmentList } from "./FileAttachmentCard"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { ChevronLeftIcon, ChevronRightIcon } from "./icons"

interface SearchResult {
  url?: string
  title?: string
  snippet?: string
  description?: string
}

function parseSearchResults(output: string): SearchResult[] | null {
  try {
    const data = JSON.parse(output)
    if (Array.isArray(data) && data.length > 0 && data[0].url) return data as SearchResult[]
    if (Array.isArray(data?.results) && data.results.length > 0) return data.results as SearchResult[]
    return null
  } catch {
    return null
  }
}

function extractDomain(url: string): string {
  try { return new URL(url).hostname.replace(/^www\./, '') } catch { return url }
}

interface MessageBubbleProps {
  message: Message
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
  const toolCalls = useMemo(() => {
    const responseMetadata = (message.response_metadata || {}) as Record<string, unknown>
    return Array.isArray(responseMetadata.normalized_tool_calls)
      ? (responseMetadata.normalized_tool_calls as ToolCallDelta[])
      : []
  }, [message.response_metadata])
  const toolResults = useMemo(() => {
    const responseMetadata = (message.response_metadata || {}) as Record<string, unknown>
    return Array.isArray(responseMetadata.normalized_tool_results)
      ? (responseMetadata.normalized_tool_results as ToolResultDelta[])
      : []
  }, [message.response_metadata])
  const {
    isEditing,
    editContent,
    setEditContent,
    textareaRef,
    startEdit,
    saveEdit,
    cancelEdit,
    handleKeyDown: handleEditKeyDown,
  } = useMessageEdit({
    messageId: message.id,
    messageContent: message.content || "",
    regenerateMessageId,
    onEdit,
    onRegenerate,
  })

  const hasAgentPhases = hasReasoning || toolCalls.length > 0 || toolResults.length > 0
  const [thoughtOpen, setThoughtOpen] = useState(false)
  const [expandedTools, setExpandedTools] = useState<Set<string>>(new Set())

  const toggleTool = (key: string) => {
    setExpandedTools((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  // Auto-open thought block during streaming before content arrives; auto-close once content is ready.
  useEffect(() => {
    if (isStreaming && hasReasoning && !hasContent) {
      setThoughtOpen(true)
    } else if (hasContent) {
      setThoughtOpen(false)
    }
  }, [hasContent, hasReasoning, isStreaming])

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
                  ref={textareaRef}
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
                    onClick={cancelEdit}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="edit-save-btn"
                    onClick={saveEdit}
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
                  onClick={startEdit}
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

          {/* Thought block — wraps all agent phases */}
          {hasAgentPhases && (
            <div className={`thought-block${thoughtOpen ? " open" : ""}`}>
              <button
                type="button"
                className="thought-toggle"
                onClick={() => setThoughtOpen((v) => !v)}
              >
                <svg
                  className="thought-chevron"
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
                {isStreaming && !hasContent ? "Thinking..." : "Thought process"}
              </button>
              {thoughtOpen && (
                <div className="thought-body">
                  {hasReasoning && (
                    <TimelinePhase
                      icon={<Brain className="h-3 w-3" />}
                      label="Thinking"
                      isLast={toolCalls.length === 0 && toolResults.length === 0}
                    >
                      {message.reasoning_content}
                    </TimelinePhase>
                  )}

                  {toolCalls.map((tc, i) => {
                    const isLast = i === toolCalls.length - 1 && toolResults.length === 0
                    const key = tc.id || tc.name || `tc-${i}`
                    const open = expandedTools.has(key)
                    return (
                      <TimelinePhase
                        key={key}
                        icon={<Wrench className="h-3 w-3" />}
                        label="Tool call"
                        detail={tc.name ?? undefined}
                        isLast={isLast}
                      >
                        {tc.args !== undefined && (
                          <div className={`tl-tool-card${open ? " tl-tool-open" : ""}`}>
                            <button
                              type="button"
                              className="tl-tool-toggle"
                              onClick={() => toggleTool(key)}
                            >
                              <span className="tl-tool-name">
                                {tc.name || "unknown_tool"}
                              </span>
                              <span className="tl-tool-chevron">
                                <ChevronRightIcon />
                              </span>
                            </button>
                            {open && (
                              <pre className="tl-tool-args">
                                {typeof tc.args === "string"
                                  ? tc.args
                                  : JSON.stringify(tc.args, null, 2)}
                              </pre>
                            )}
                          </div>
                        )}
                      </TimelinePhase>
                    )
                  })}

                  {toolResults.map((tr, i) => {
                    const isLast = i === toolResults.length - 1
                    const key = tr.tool_call_id || `${tr.name}-${i}`
                    const open = expandedTools.has(key)
                    const searchResults = parseSearchResults(tr.output)
                    return (
                      <TimelinePhase
                        key={key}
                        icon={<FileCheck className="h-3 w-3" />}
                        label="Tool result"
                        detail={tr.name}
                        isLast={isLast}
                      >
                        <div className={`tl-tool-card${open ? " tl-tool-open" : ""}`}>
                          <button
                            type="button"
                            className="tl-tool-toggle"
                            onClick={() => toggleTool(key)}
                          >
                            <span className="tl-tool-name">
                              {tr.name}
                              {tr.is_error && <span className="tl-tool-badge">Error</span>}
                            </span>
                            {searchResults && (
                              <span className="tl-tool-badge">
                                {searchResults.length} results
                              </span>
                            )}
                            <span className={`tl-tool-chevron${open ? " tl-tool-chevron-open" : ""}`}>
                              <ChevronRightIcon />
                            </span>
                          </button>
                          {open && (
                            <>
                              {searchResults ? (
                                <div className="tl-search-results">
                                  {searchResults.map((r, si) => (
                                    <a
                                      key={si}
                                      href={r.url}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                      className="tl-search-card"
                                    >
                                      <div className="tl-search-domain">
                                        <img
                                          src={`https://www.google.com/s2/favicons?domain=${extractDomain(r.url || '')}&sz=32`}
                                          alt=""
                                          width="16"
                                          height="16"
                                          className="tl-search-favicon"
                                        />
                                        {extractDomain(r.url || '')}
                                      </div>
                                      <div className="tl-search-title">{r.title || r.url}</div>
                                      {(r.snippet || r.description) && (
                                        <div className="tl-search-snippet">
                                          {r.snippet || r.description}
                                        </div>
                                      )}
                                    </a>
                                  ))}
                                </div>
                              ) : (
                                <pre className={`tl-tool-output${tr.is_error ? " is-error" : ""}`}>
                                  {tr.output}
                                </pre>
                              )}
                            </>
                          )}
                        </div>
                      </TimelinePhase>
                    )
                  })}
                </div>
              )}
            </div>
          )}

          {isStreaming && !hasContent && !hasReasoning && (
            <TypingIndicator />
          )}

          {hasContent && (
            <>
              <div className="markdown-body">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {message.content}
                </ReactMarkdown>
              </div>
              <FileAttachmentList contentParts={message.content_parts || []} />
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
