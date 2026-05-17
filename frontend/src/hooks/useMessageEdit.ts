import { useCallback, useEffect, useRef, useState } from "react"

interface UseMessageEditOptions {
  messageId: string
  messageContent: string
  regenerateMessageId?: string | null
  onEdit?: (messageId: string, newContent: string) => void
  onRegenerate?: (messageId: string) => void
}

export function useMessageEdit({
  messageId,
  messageContent,
  regenerateMessageId,
  onEdit,
  onRegenerate,
}: UseMessageEditOptions) {
  const [isEditing, setIsEditing] = useState(false)
  const [editContent, setEditContent] = useState("")
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (isEditing && textareaRef.current) {
      const ta = textareaRef.current
      ta.focus()
      ta.selectionStart = ta.value.length
      ta.selectionEnd = ta.value.length
    }
  }, [isEditing])

  useEffect(() => {
    if (textareaRef.current) {
      const ta = textareaRef.current
      ta.style.height = "auto"
      ta.style.height = ta.scrollHeight + "px"
    }
  }, [editContent])

  const startEdit = useCallback(() => {
    setEditContent(messageContent)
    setIsEditing(true)
  }, [messageContent])

  const saveEdit = useCallback(() => {
    const trimmed = editContent.trim()
    if (!trimmed) {
      setIsEditing(false)
      return
    }
    if (trimmed === messageContent.trim()) {
      if (regenerateMessageId) {
        onRegenerate?.(regenerateMessageId)
      }
      setIsEditing(false)
      return
    }
    onEdit?.(messageId, trimmed)
    setIsEditing(false)
  }, [editContent, messageContent, regenerateMessageId, onEdit, onRegenerate])

  const cancelEdit = useCallback(() => {
    setIsEditing(false)
  }, [])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault()
        saveEdit()
      } else if (e.key === "Escape") {
        e.preventDefault()
        cancelEdit()
      }
    },
    [saveEdit, cancelEdit],
  )

  return {
    isEditing,
    editContent,
    setEditContent,
    textareaRef,
    startEdit,
    saveEdit,
    cancelEdit,
    handleKeyDown,
  }
}
