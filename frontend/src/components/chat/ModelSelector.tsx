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

interface ModelEntry {
  configId: string
  configName: string
  provider: string
  model: string
  isDefault: boolean
  isPrimary: boolean
}

interface ModelGroup {
  configId: string
  configName: string
  provider: string
  isDefault: boolean
  entries: ModelEntry[]
}

interface ModelSelectorProps {
  configs: LlmConfig[]
  currentConfigId: string | null
  currentModel: string | null
  onSelect: (configId: string, model: string) => void
  onManage: () => void
}

export function ModelSelector({
  configs,
  currentConfigId,
  currentModel,
  onSelect,
  onManage,
}: ModelSelectorProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")

  const entries: ModelEntry[] = useMemo(() => {
    const result: ModelEntry[] = []
    for (const config of configs) {
      if (!config.models || config.models.length === 0) continue
      for (const [index, model] of config.models.entries()) {
        result.push({
          configId: config.id,
          configName: config.name,
          provider: config.provider,
          model,
          isDefault: config.is_default && index === 0,
          isPrimary: index === 0,
        })
      }
    }
    return result
  }, [configs])

  const selectedLabel = useMemo(() => {
    if (configs.length === 0) return "No model selected"
    const activeConfig = configs.find((c) => c.id === currentConfigId)
    if (activeConfig) {
      return `${activeConfig.name} · ${currentModel || activeConfig.models[0] || "No model"}`
    }
    const defaultConfig = configs.find((c) => c.is_default)
    if (defaultConfig && defaultConfig.models.length > 0) {
      return `${defaultConfig.name} · ${defaultConfig.models[0]}`
    }
    if (entries.length > 0) {
      const first = entries[0]
      return `${first.configName} · ${first.model}`
    }
    return "No model selected"
  }, [configs, currentConfigId, currentModel, entries])

  const filtered = useMemo(() => {
    if (!query.trim()) return entries
    const q = query.toLowerCase()
    return entries.filter(
      (e) =>
        e.configName.toLowerCase().includes(q) ||
        e.provider.toLowerCase().includes(q) ||
        e.model.toLowerCase().includes(q),
    )
  }, [entries, query])

  const groups: ModelGroup[] = useMemo(() => {
    const byConfig = new Map<string, ModelGroup>()
    for (const entry of filtered) {
      const existing = byConfig.get(entry.configId)
      if (existing) {
        existing.entries.push(entry)
        continue
      }
      byConfig.set(entry.configId, {
        configId: entry.configId,
        configName: entry.configName,
        provider: entry.provider,
        isDefault: configs.some((config) => config.id === entry.configId && config.is_default),
        entries: [entry],
      })
    }
    return Array.from(byConfig.values())
  }, [configs, filtered])

  const isEntrySelected = useCallback(
    (entry: ModelEntry) =>
      entry.configId === currentConfigId &&
      entry.model ===
        (currentModel || configs.find((config) => config.id === currentConfigId)?.models[0]),
    [configs, currentConfigId, currentModel],
  )

  const handleSelect = useCallback(
    (entry: ModelEntry) => {
      setOpen(false)
      setQuery("")
      onSelect(entry.configId, entry.model)
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
          <span className="truncate">{selectedLabel}</span>
          <ChevronDown
            className={cn(
              "h-4 w-4 opacity-70 transition-transform duration-200 shrink-0",
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
        <div className="max-h-[320px] overflow-y-auto py-1">
          {filtered.length === 0 ? (
            <div className="px-3 py-8 text-center text-sm text-[var(--ink-soft)]">
              {query.trim() ? "No matching models" : "No models available"}
            </div>
          ) : (
            groups.map((group) => (
              <div key={group.configId} className="py-1 first:pt-0">
                <div className="flex items-center gap-2 px-3 pb-1 pt-2">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[11px] font-semibold uppercase tracking-wider text-[var(--ink-soft)]">
                      {group.configName}
                    </div>
                    <div className="truncate text-[11px] text-[var(--ink-muted)]">
                      {group.provider}
                    </div>
                  </div>
                  {group.isDefault && (
                    <Badge variant="outline" className="shrink-0">
                      Default
                    </Badge>
                  )}
                </div>
                <div>
                  {group.entries.map((entry) => {
                    const key = `${entry.configId}:${entry.model}`
                    const isSelected = isEntrySelected(entry)
                    return (
                      <button
                        key={key}
                        type="button"
                        className={cn(
                          "flex w-full items-center gap-3 px-3 py-2 text-left text-sm transition-colors",
                          "hover:bg-[var(--surface-low)]",
                          isSelected && "bg-[color-mix(in_srgb,var(--accent-orange)_8%,transparent)]",
                        )}
                        onClick={() => handleSelect(entry)}
                      >
                        <Check
                          className={cn(
                            "h-4 w-4 shrink-0",
                            isSelected ? "text-accent-orange" : "text-transparent",
                          )}
                        />
                        <div className="flex min-w-0 flex-1 items-center gap-2">
                          <span className={cn("truncate", isSelected && "font-semibold")}>
                            {entry.model}
                          </span>
                          {entry.isPrimary && (
                            <Badge variant="secondary" className="shrink-0">
                              Primary
                            </Badge>
                          )}
                        </div>
                      </button>
                    )
                  })}
                </div>
              </div>
            ))
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
