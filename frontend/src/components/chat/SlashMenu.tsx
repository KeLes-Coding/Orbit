import { useEffect, useRef, useMemo } from "react"
import { MessageSquare, Bot } from "lucide-react"

export interface SlashItem {
  id: string
  label: string
  detail?: string
  icon?: React.ReactNode
  group: "mode" | "model"
}

interface SlashMenuProps {
  items: SlashItem[]
  query: string
  activeIndex: number
  onSelect: (item: SlashItem) => void
  onClose: () => void
}

export function SlashMenu({
  items,
  query,
  activeIndex,
  onSelect,
  onClose,
}: SlashMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null)
  const activeRef = useRef<HTMLButtonElement>(null)

  const filtered = useMemo(() => {
    if (!query) return items
    const q = query.toLowerCase()
    return items.filter(
      (item) =>
        item.label.toLowerCase().includes(q) ||
        (item.detail || "").toLowerCase().includes(q)
    )
  }, [items, query])

  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: "nearest" })
  }, [activeIndex])

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("keydown", handleKey)
    return () => window.removeEventListener("keydown", handleKey)
  }, [onClose])

  if (filtered.length === 0) return null

  const modeItems = filtered.filter((i) => i.group === "mode")
  const modelItems = filtered.filter((i) => i.group === "model")

  return (
    <div className="slash-menu" ref={menuRef}>
      {modeItems.length > 0 && (
        <div className="slash-group">
          <div className="slash-group-label">Mode</div>
          {modeItems.map((item, idx) => {
            const globalIdx = filtered.indexOf(item)
            return (
              <button
                key={item.id}
                ref={globalIdx === activeIndex ? activeRef : undefined}
                type="button"
                className={`slash-item${globalIdx === activeIndex ? " active" : ""}`}
                onClick={() => onSelect(item)}
                onMouseEnter={() => {
                  /* handled by parent */
                }}
              >
                <span className="slash-item-icon">
                  {item.icon || (
                    item.id === "chat" ? <MessageSquare className="h-4 w-4" /> : <Bot className="h-4 w-4" />
                  )}
                </span>
                <span className="slash-item-label">{item.label}</span>
                {item.detail && (
                  <span className="slash-item-detail">{item.detail}</span>
                )}
              </button>
            )
          })}
        </div>
      )}
      {modelItems.length > 0 && (
        <div className="slash-group">
          <div className="slash-group-label">Models</div>
          {modelItems.map((item) => {
            const globalIdx = filtered.indexOf(item)
            return (
              <button
                key={item.id}
                ref={globalIdx === activeIndex ? activeRef : undefined}
                type="button"
                className={`slash-item${globalIdx === activeIndex ? " active" : ""}`}
                onClick={() => onSelect(item)}
              >
                <span className="slash-item-icon">
                  <span className="slash-model-dot" />
                </span>
                <span className="slash-item-label">{item.label}</span>
                {item.detail && (
                  <span className="slash-item-detail">{item.detail}</span>
                )}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
