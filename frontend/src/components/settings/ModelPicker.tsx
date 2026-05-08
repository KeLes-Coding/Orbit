import { useState, useMemo } from "react"
import { ArrowDown, ArrowUp, Check, Plus, RefreshCw, Star, X } from "lucide-react"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import type { LlmModel } from "@/api/types"

interface ModelPickerProps {
  selectedModels: string[]
  onChange: (models: string[]) => void
  modelOptions: LlmModel[]
  isLoading: boolean
  statusMessage: string
  canFetch: boolean
  onFetch: () => void
}

export function ModelPicker({
  selectedModels,
  onChange,
  modelOptions,
  isLoading,
  statusMessage,
  canFetch,
  onFetch,
}: ModelPickerProps) {
  const [query, setQuery] = useState("")

  const commitModels = (raw: string) => {
    const nextModels = raw
      .split(/[,\n]/)
      .map((model) => model.trim())
      .filter(Boolean)
    if (nextModels.length === 0) return

    onChange(Array.from(new Set([...selectedModels, ...nextModels])))
    setQuery("")
  }

  const toggleModel = (id: string) => {
    if (selectedModels.includes(id)) {
      onChange(selectedModels.filter((m) => m !== id))
    } else {
      onChange([...selectedModels, id])
    }
  }

  const removeModel = (index: number) => {
    onChange(selectedModels.filter((_, selectedIndex) => selectedIndex !== index))
  }

  const makePrimary = (index: number) => {
    if (index <= 0) return
    const next = [...selectedModels]
    const [model] = next.splice(index, 1)
    onChange([model, ...next])
  }

  const moveModel = (index: number, direction: -1 | 1) => {
    const nextIndex = index + direction
    if (nextIndex < 0 || nextIndex >= selectedModels.length) return
    const next = [...selectedModels]
    const [model] = next.splice(index, 1)
    next.splice(nextIndex, 0, model)
    onChange(next)
  }

  const filteredModels = useMemo(() => {
    if (!query.trim()) return modelOptions
    const q = query.toLowerCase()
    return modelOptions.filter(
      (m) => m.id.toLowerCase().includes(q) || (m.name && m.name.toLowerCase().includes(q)),
    )
  }, [modelOptions, query])

  const selectedCount = selectedModels.length
  const availableCount = modelOptions.length
  const availableIds = useMemo(() => new Set(modelOptions.map((model) => model.id)), [modelOptions])
  const selectedAvailableCount = selectedModels.filter((model) => availableIds.has(model)).length
  const allAvailableSelected = availableCount > 0 && selectedAvailableCount === availableCount
  const canAddQuery = query.trim().length > 0

  return (
    <div className="flex flex-col gap-2">
      <div className="flex gap-2">
        <div className="flex-1 relative">
          <Input
            value={query}
            onChange={(e) => {
              const value = e.target.value
              if (value.includes(",")) {
                commitModels(value)
                return
              }
              setQuery(value)
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault()
                commitModels(query)
              }
            }}
            placeholder={selectedCount > 0 ? `${selectedCount} selected` : "Search or enter model names"}
            className={cn(selectedCount > 0 && "pr-20")}
          />
          {selectedCount > 0 && (
            <button
              type="button"
              className="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-[var(--ink-soft)] hover:text-[var(--ink)]"
              onClick={() => onChange([])}
            >
              clear all
            </button>
          )}
        </div>
        <Button
          type="button"
          variant="outline"
          size="icon"
          disabled={!canFetch}
          onClick={onFetch}
          title="Fetch available models"
        >
          <RefreshCw className={`h-4 w-4 ${isLoading ? "animate-spin" : ""}`} />
        </Button>
        <Button
          type="button"
          variant="outline"
          size="icon"
          disabled={!canAddQuery}
          onClick={() => commitModels(query)}
          title="Add typed model"
        >
          <Plus className="h-4 w-4" />
        </Button>
      </div>

      {selectedModels.length > 0 && (
        <div className="overflow-hidden rounded-sm border border-[var(--line)]">
          {selectedModels.map((model, index) => (
            <div
              key={`${model}:${index}`}
              className="flex min-h-10 items-center gap-2 border-b border-[var(--line)] px-2 py-1.5 last:border-b-0"
            >
              <div className="min-w-0 flex-1">
                <div className="flex min-w-0 items-center gap-2">
                  {index === 0 && (
                    <span className="shrink-0 rounded-sm bg-[color-mix(in_srgb,var(--accent-orange)_12%,transparent)] px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-accent-orange">
                      Primary
                    </span>
                  )}
                  <span className="truncate text-sm text-[var(--ink)]">{model}</span>
                </div>
              </div>
              {index > 0 && (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  onClick={() => makePrimary(index)}
                  title="Set as primary"
                >
                  <Star className="h-3.5 w-3.5" />
                </Button>
              )}
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                disabled={index === 0}
                onClick={() => moveModel(index, -1)}
                title="Move up"
              >
                <ArrowUp className="h-3.5 w-3.5" />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                disabled={index === selectedModels.length - 1}
                onClick={() => moveModel(index, 1)}
                title="Move down"
              >
                <ArrowDown className="h-3.5 w-3.5" />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                className="hover:text-[var(--color-danger)]"
                onClick={() => removeModel(index)}
                title="Remove model"
              >
                <X className="h-3.5 w-3.5" />
              </Button>
            </div>
          ))}
        </div>
      )}

      {isLoading && <p className="text-xs text-[var(--ink-soft)]">Fetching models...</p>}
      {statusMessage && !isLoading && (
        <p className="text-xs text-[var(--ink-muted)]">{statusMessage}</p>
      )}

      {availableCount > 0 && (
        <div className="flex flex-wrap gap-1.5">
          <button
            type="button"
            className={cn(
              "text-xs px-2 py-0.5 rounded border transition-colors",
              allAvailableSelected
                ? "border-accent-orange bg-[color-mix(in_srgb,var(--accent-orange)_12%,transparent)] text-[var(--ink)]"
                : "border-[var(--line)] text-[var(--ink-soft)] hover:text-[var(--ink)]",
            )}
            onClick={() => {
              const filteredIds = filteredModels.map((m) => m.id)
              if (allAvailableSelected) {
                onChange(selectedModels.filter((model) => !availableIds.has(model)))
              } else {
                onChange(Array.from(new Set([...selectedModels, ...filteredIds])))
              }
            }}
          >
            {allAvailableSelected ? "Deselect fetched" : "Select shown"}
          </button>
        </div>
      )}

      {filteredModels.length > 0 && (
        <div className="max-h-64 overflow-y-auto border border-[var(--line)] rounded-sm">
          {filteredModels.map((m) => {
            const isSelected = selectedModels.includes(m.id)
            return (
              <button
                key={m.id}
                type="button"
                className={cn(
                  "flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors hover:bg-[var(--surface-low)]",
                  isSelected && "bg-[color-mix(in_srgb,var(--accent-orange)_8%,transparent)]",
                )}
                onClick={() => toggleModel(m.id)}
              >
                <Check
                  className={cn(
                    "h-3.5 w-3.5 shrink-0",
                    isSelected ? "text-accent-orange" : "text-transparent",
                  )}
                />
                <span className="flex-1 truncate">{m.id}</span>
                {m.name && m.name !== m.id && (
                  <span className="text-xs text-[var(--ink-muted)] truncate">{m.name}</span>
                )}
              </button>
            )
          })}
        </div>
      )}

      {!isLoading && modelOptions.length === 0 && !statusMessage && (
        <p className="text-xs text-[var(--ink-muted)]">No models available. You can also type a model name directly.</p>
      )}
    </div>
  )
}
