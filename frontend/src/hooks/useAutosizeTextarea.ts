import { useCallback, useRef, useEffect, RefObject } from 'react'

export function useAutosizeTextarea(
  maxHeight = 168,
): { ref: RefObject<HTMLTextAreaElement>; resize: () => void } {
  const ref = useRef<HTMLTextAreaElement>(null!)

  const resize = useCallback(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, maxHeight)}px`
  }, [maxHeight])

  useEffect(() => {
    resize()
  }, [resize])

  return { ref, resize }
}
