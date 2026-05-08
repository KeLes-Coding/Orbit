import { useEffect, useRef, useCallback, useState, type KeyboardEvent, type DragEvent, type ClipboardEvent } from "react"
import { ArrowUp, Square, Paperclip } from "lucide-react"
import { useAutosizeTextarea } from "@/hooks/useAutosizeTextarea"
import { FilePreviewItem, type PendingFile } from "./FilePreviewItem"

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
}: ChatComposerProps) {
  const { ref, resize } = useAutosizeTextarea(168)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const dragCounterRef = useRef(0)
  const [isDragging, setIsDragging] = useState(false)

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
      if (e.key !== "Enter" || e.shiftKey || e.nativeEvent.isComposing) return
      e.preventDefault()
      if (!isSending && !isUploading && (draft.trim() || pendingFiles.length > 0)) {
        onSend()
      }
    },
    [onSend, isSending, isUploading, draft, pendingFiles],
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
        <textarea
          ref={ref}
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value)
            if (errorMessage) onClearError()
          }}
          rows={1}
          placeholder="Focus your intent..."
          aria-label="Message"
          disabled={isSending}
          onInput={handleInput}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
        />
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
            className="send-button attach-button"
            aria-label="Attach files"
            title="Attach files"
            disabled={isSending || !onAddFiles}
            onClick={() => fileInputRef.current?.click()}
          >
            <Paperclip className="h-4 w-4" />
          </button>
          {isSending ? (
            <button
              type="button"
              className="send-button stop-button"
              aria-label="Stop generating"
              title="Stop generating"
              onClick={onStop}
            >
              <Square className="h-3.5 w-3.5" />
            </button>
          ) : (
            <button
              type="submit"
              className="send-button"
              aria-label="Send message"
              disabled={!canSend}
            >
              <ArrowUp className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>
      <p>AI may hallucinate. Cultivate discernment.</p>
    </form>
  )
}
