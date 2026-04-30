import { useState, useMemo, useCallback } from "react"
import {
  Check,
  ChevronDown,
  Search,
  SlidersHorizontal,
} from "lucide-react"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import type { LlmConfig } from "@/api/types"

interface ModelSelectorProps {
  configs: LlmConfig[]
  currentConfigId: string | null
  onSelect: (config: LlmConfig) => void
  onManage: () => void
}

export function ModelSelector({
  configs,
  currentConfigId,
  onSelect,
  onManage,
}: ModelSelectorProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")

  const selectedName = useMemo(() => {
    if (configs.length === 0) return "No model selected"
    const active = configs.find((c) => c.id === currentConfigId)
    const defaultConfig = configs.find((c) => c.is_default)
    const config = active || defaultConfig || configs[0]
    return config.name
  }, [configs, currentConfigId])

  const filtered = useMemo(() => {
    if (!query.trim()) return configs
    const q = query.toLowerCase()
    return configs.filter(
      (c) =>
        c.name.toLowerCase().includes(q) ||
        c.provider.toLowerCase().includes(q) ||
        c.model.toLowerCase().includes(q),
    )
  }, [configs, query])

  const handleSelect = useCallback(
    (config: LlmConfig) => {
      setOpen(false)
      setQuery("")
      onSelect(config)
    },
    [onSelect],
  )

  const handleOpenChange = useCallback((next: boolean) => {
    setOpen(next)
    if (!next) setQuery("")
  }, [])

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <button type="button" className="model-button">
          <span>{selectedName}</span>
          <ChevronDown
            className={cn(
              "h-4 w-4 opacity-70 transition-transform duration-200",
              open && "rotate-180",
            )}
          />
        </button>
      </PopoverTrigger>

      <PopoverContent
        className="w-[min(90vw,370px)] p-0"
        align="start"
        sideOffset={8}
      >
        {/* Search input */}
        <div className="flex items-center gap-2 border-b border-[var(--line)] px-3 py-2.5">
          <Search className="h-4 w-4 shrink-0 text-[var(--ink-soft)]" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search model configs..."
            className="flex-1 border-0 bg-transparent text-sm text-[var(--ink)] placeholder:text-[var(--ink-soft)] outline-none"
            autoFocus
          />
        </div>

        {/* List */}
        <div className="max-h-[280px] overflow-y-auto py-1">
          {filtered.length === 0 ? (
            <div className="px-3 py-8 text-center text-sm text-[var(--ink-soft)]">
              {query.trim() ? "No matching configs" : "No configs available"}
            </div>
          ) : (
            filtered.map((config) => {
              const isSelected = config.id === currentConfigId
              return (
                <button
                  key={config.id}
                  type="button"
                  className={cn(
                    "flex w-full items-center gap-3 px-3 py-2.5 text-left text-sm transition-colors",
                    "hover:bg-[var(--surface-low)]",
                    isSelected && "bg-[color-mix(in_srgb,var(--accent-orange)_8%,transparent)]",
                  )}
                  onClick={() => handleSelect(config)}
                >
                  <Check
                    className={cn(
                      "h-4 w-4 shrink-0",
                      isSelected ? "text-accent-orange" : "text-transparent",
                    )}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className={cn("truncate", isSelected && "font-semibold")}>
                        {config.name}
                      </span>
                      {config.is_default && (
                        <Badge variant="default" className="shrink-0">
                          Default
                        </Badge>
                      )}
                    </div>
                    <div className="text-xs text-[var(--ink-soft)] truncate">
                      {config.provider} / {config.model}
                    </div>
                  </div>
                </button>
              )
            })
          )}
        </div>

        {/* Footer */}
        <div className="border-t border-[var(--line)] px-1 py-1">
          <button
            type="button"
            className="flex w-full items-center gap-2 rounded-sm px-2 py-2 text-sm text-[var(--ink-muted)] hover:bg-[var(--surface-low)] transition-colors"
            onClick={() => {
              setOpen(false)
              onManage()
            }}
          >
            <SlidersHorizontal className="h-4 w-4" />
            <span>Manage model configs</span>
          </button>
        </div>
      </PopoverContent>
    </Popover>
  )
}
