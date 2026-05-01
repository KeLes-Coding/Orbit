import { useEffect, useRef, useCallback, type KeyboardEvent } from "react"
import { ArrowUp, Square } from "lucide-react"
import { useAutosizeTextarea } from "@/hooks/useAutosizeTextarea"

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
}: ChatComposerProps) {
  const { ref, resize } = useAutosizeTextarea(168)

  useEffect(() => {
    resize()
  }, [draft, resize])

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key !== "Enter" || e.shiftKey || e.nativeEvent.isComposing) return
      e.preventDefault()
      if (!isSending && draft.trim()) {
        onSend()
      }
    },
    [onSend, isSending, draft],
  )

  const handleInput = useCallback(() => {
    resize()
  }, [resize])

  const canSend = !isSending && draft.trim().length > 0

  return (
    <form
      className="composer-wrap"
      onSubmit={(e) => {
        e.preventDefault()
        if (canSend) onSend()
      }}
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
      <div className={`composer${isSending ? " is-sending" : ""}`}>
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
        />
        <div className="composer-actions">
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
