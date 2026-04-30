import { useState, useCallback } from "react"
import { RefreshCw } from "lucide-react"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import type { LlmModel } from "@/api/types"

interface ModelPickerProps {
  value: string
  onChange: (value: string) => void
  modelOptions: LlmModel[]
  isLoading: boolean
  statusMessage: string
  canFetch: boolean
  onFetch: () => void
}

export function ModelPicker({
  value,
  onChange,
  modelOptions,
  isLoading,
  statusMessage,
  canFetch,
  onFetch,
}: ModelPickerProps) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex gap-2">
        <Input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Enter or fetch a model"
          list="model-options-list"
          className="flex-1"
        />
        <Button
          type="button"
          variant="outline"
          size="icon"
          disabled={!canFetch}
          onClick={onFetch}
          title="Fetch available models"
        >
          <RefreshCw
            className={`h-4 w-4 ${isLoading ? "animate-spin" : ""}`}
          />
        </Button>
      </div>
      <datalist id="model-options-list">
        {modelOptions.map((m) => (
          <option key={m.id} value={m.id} />
        ))}
      </datalist>
      {isLoading && (
        <p className="text-xs text-[var(--ink-soft)]">Fetching models...</p>
      )}
      {statusMessage && !isLoading && (
        <p className="text-xs text-[var(--ink-muted)]">{statusMessage}</p>
      )}
    </div>
  )
}
