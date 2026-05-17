import { useEffect, useCallback, useState, useMemo, useRef, type KeyboardEvent, type DragEvent, type ClipboardEvent } from "react"
import { ArrowUp, Square, Paperclip, MessageSquare, Bot } from "lucide-react"
import { useAutosizeTextarea } from "@/hooks/useAutosizeTextarea"
import { FilePreviewItem, type PendingFile } from "./FilePreviewItem"
import { SlashMenu, type SlashItem } from "./SlashMenu"

interface ChatComposerProps {
  draft: string
  setDraft: (text: string) => void
  isSending: boolean
  onSend: () => void
  onStop: () => void
  onClearError: () => void
  errorMessage?: string
  isAuthenticated: boolean
  hasConfigs: boolean
  pendingFiles?: PendingFile[]
  onAddFiles?: (files: File[]) => void
  onRemoveFile?: (index: number) => void
  isUploading?: boolean
  showVisionHint?: boolean
  chatMode?: 'chat' | 'agent'
  onChatModeChange?: (mode: 'chat' | 'agent') => void
  slashItems?: SlashItem[]
  onSlashSelect?: (item: SlashItem) => void
}

export function ChatComposer({
  draft,
  setDraft,
  isSending,
  onSend,
  onStop,
  onClearError,
  errorMessage,
  isAuthenticated,
  hasConfigs,
  pendingFiles = [],
  onAddFiles,
  onRemoveFile,
  isUploading = false,
  showVisionHint = false,
  chatMode = 'chat',
  onChatModeChange,
  slashItems = [],
  onSlashSelect,
}: ChatComposerProps) {
  const { ref, resize } = useAutosizeTextarea(168)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const dragCounterRef = useRef(0)
  const [isDragging, setIsDragging] = useState(false)

  /* ── Slash menu state ── */
  const [slashOpen, setSlashOpen] = useState(false)
  const [slashQuery, setSlashQuery] = useState("")
  const [slashActive, setSlashActive] = useState(0)

  const slashFiltered = useMemo(() => {
    if (!slashQuery) return slashItems
    const q = slashQuery.toLowerCase()
    return slashItems.filter(
      (item) =>
        item.label.toLowerCase().includes(q) ||
        (item.detail || "").toLowerCase().includes(q)
    )
  }, [slashItems, slashQuery])

  const closeSlash = useCallback(() => {
    setSlashOpen(false)
    setSlashQuery("")
    setSlashActive(0)
  }, [])

  const handleSlashSelect = useCallback(
    (item: SlashItem) => {
      // Remove the "/..." text from draft
      const ta = ref.current
      if (!ta) return
      const pos = ta.selectionStart
      const textBefore = draft.slice(0, pos)
      const textAfter = draft.slice(pos)
      const slashIdx = textBefore.lastIndexOf("/")
      if (slashIdx === -1) return
      const newText = textBefore.slice(0, slashIdx) + textAfter
      setDraft(newText)
      closeSlash()
      onSlashSelect?.(item)
      // Restore cursor position
      requestAnimationFrame(() => {
        ta.focus()
        ta.selectionStart = ta.selectionEnd = slashIdx
      })
    },
    [ref, draft, setDraft, closeSlash, onSlashSelect],
  )

  useEffect(() => {
    resize()
  }, [draft, resize])

  const extractFiles = useCallback((files: FileList | null) => {
    if (!files || !onAddFiles) return
    const fileArray = Array.from(files)
    if (fileArray.length === 0) return
    onAddFiles(fileArray)
  }, [onAddFiles])

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      // Slash menu navigation
      if (slashOpen) {
        if (e.key === "ArrowDown") {
          e.preventDefault()
          setSlashActive((prev) => (prev + 1) % Math.max(slashFiltered.length, 1))
          return
        }
        if (e.key === "ArrowUp") {
          e.preventDefault()
          setSlashActive((prev) => (prev - 1 + slashFiltered.length) % Math.max(slashFiltered.length, 1))
          return
        }
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault()
          if (slashFiltered[slashActive]) {
            handleSlashSelect(slashFiltered[slashActive])
          }
          return
        }
        if (e.key === "Escape") {
          e.preventDefault()
          closeSlash()
          return
        }
      }

      // Regular send
      if (e.key !== "Enter" || e.shiftKey || e.nativeEvent.isComposing) return
      e.preventDefault()
      if (!isSending && !isUploading && (draft.trim() || pendingFiles.length > 0)) {
        onSend()
      }
    },
    [slashOpen, slashFiltered, slashActive, handleSlashSelect, closeSlash, onSend, isSending, isUploading, draft, pendingFiles],
  )

  const handleInput = useCallback(() => {
    resize()
  }, [resize])

  const handlePaste = useCallback((e: ClipboardEvent<HTMLTextAreaElement>) => {
    const items = e.clipboardData?.items
    if (!items || !onAddFiles) return
    const files: File[] = []
    for (let i = 0; i < items.length; i++) {
      const file = items[i].getAsFile()
      if (file) files.push(file)
    }
    if (files.length > 0) {
      e.preventDefault()
      onAddFiles(files)
    }
  }, [onAddFiles])

  const handleDragOver = useCallback((e: DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
  }, [])

  const handleDragEnter = useCallback((e: DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounterRef.current++
    if (dragCounterRef.current === 1) setIsDragging(true)
  }, [])

  const handleDragLeave = useCallback((e: DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounterRef.current--
    if (dragCounterRef.current === 0) setIsDragging(false)
  }, [])

  const handleDrop = useCallback((e: DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounterRef.current = 0
    setIsDragging(false)
    extractFiles(e.dataTransfer.files)
  }, [extractFiles])

  const canSend = !isSending && !isUploading && (draft.trim().length > 0 || pendingFiles.length > 0)

  return (
    <form
      className={`composer-wrap${isDragging ? " composer-dragover" : ""}`}
      onSubmit={(e) => {
        e.preventDefault()
        if (canSend) onSend()
      }}
      onDragOver={handleDragOver}
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {errorMessage && <p className="status-message error">{errorMessage}</p>}
      {!errorMessage && !isAuthenticated && (
        <p className="status-message">
          Sign in from the avatar to send messages and sync chats.
        </p>
      )}
      {!errorMessage && isAuthenticated && !hasConfigs && (
        <p className="status-message">
          Create a default model configuration before sending messages.
        </p>
      )}

      {pendingFiles.length > 0 && (
        <div className="file-preview-row">
          {pendingFiles.map((pf, i) => (
            <FilePreviewItem
              key={pf.file.name + i}
              pendingFile={pf}
              onRemove={() => onRemoveFile?.(i)}
            />
          ))}
        </div>
      )}
      {showVisionHint && pendingFiles.some((pf) => pf.file.type.startsWith("image/")) && (
        <p className="status-message" style={{ paddingInline: 12 }}>
          This model doesn&apos;t have vision support enabled. Images will be sent as file references, not as pixels.
        </p>
      )}

      <div className={`composer${isSending ? " is-sending" : ""}${isDragging ? " composer-dragover-inner" : ""}`}>
        {/* Slash command menu */}
        {slashOpen && slashFiltered.length > 0 && (
          <SlashMenu
            items={slashItems}
            query={slashQuery}
            activeIndex={slashActive}
            onSelect={handleSlashSelect}
            onClose={closeSlash}
          />
        )}

        {/* Text input area */}
        <div className="composer-input-area">
          <textarea
            ref={ref}
            value={draft}
            onChange={(e) => {
              const val = e.target.value
              setDraft(val)
              if (errorMessage) onClearError()
              // Slash detection — synchronous, uses e.target directly
              if (!slashItems.length) return
              const ta = e.target
              const pos = ta.selectionStart
              const textBefore = val.slice(0, pos)
              const slashIdx = textBefore.lastIndexOf("/")
              if (slashIdx !== -1 && (slashIdx === 0 || textBefore[slashIdx - 1] === " " || textBefore[slashIdx - 1] === "\n")) {
                setSlashQuery(textBefore.slice(slashIdx + 1))
                setSlashActive(0)
                setSlashOpen(true)
              } else {
                setSlashOpen(false)
                setSlashQuery("")
                setSlashActive(0)
              }
            }}
            rows={1}
            placeholder="Focus your intent..."
            aria-label="Message"
            disabled={isSending}
            onInput={handleInput}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
          />

          {/* Action buttons */}
          <div className="composer-actions">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              style={{ display: "none" }}
              onChange={(e) => extractFiles(e.target.files)}
              aria-hidden="true"
            />
            <button
              type="button"
              className="composer-btn composer-btn-icon"
              aria-label="Attach files"
              disabled={isSending || !onAddFiles}
              onClick={() => fileInputRef.current?.click()}
            >
              <Paperclip className="h-4 w-4" />
            </button>

            {isSending ? (
              <button
                type="button"
                className="composer-btn composer-btn-stop"
                aria-label="Stop generating"
                onClick={onStop}
              >
                <Square className="h-3.5 w-3.5" />
              </button>
            ) : (
              <button
                type="submit"
                className="composer-btn composer-btn-send"
                aria-label="Send message"
                disabled={!canSend}
              >
                <ArrowUp className="h-4 w-4" />
              </button>
            )}
          </div>
        </div>

        {/* Bottom sub-bar: mode switcher */}
        {onChatModeChange && (
          <div className="composer-sub-bar">
            <button
              type="button"
              className={`composer-mode-pill${chatMode === 'chat' ? ' active' : ''}`}
              onClick={() => onChatModeChange('chat')}
            >
              <MessageSquare className="h-4 w-4" />
              Chat
            </button>
            <button
              type="button"
              className={`composer-mode-pill${chatMode === 'agent' ? ' active' : ''}`}
              onClick={() => onChatModeChange('agent')}
            >
              <Bot className="h-4 w-4" />
              Agent
            </button>
          </div>
        )}
      </div>

      <p className="composer-disclaimer">AI may hallucinate. Cultivate discernment.</p>
    </form>
  )
}
