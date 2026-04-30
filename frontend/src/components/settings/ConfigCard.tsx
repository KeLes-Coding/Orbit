import { Pencil, Archive, Star } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import type { LlmConfig } from "@/api/types"

interface ConfigCardProps {
  config: LlmConfig
  onEdit: (config: LlmConfig) => void
  onArchive: (config: LlmConfig) => void
  onSetDefault: (configId: string) => void
}

function providerOptionsPreview(options: Record<string, unknown> | null | undefined): string | null {
  if (!options || Object.keys(options).length === 0) return null
  const keys = Object.keys(options)
  if (keys.length <= 2) {
    return keys.map((k) => `${k}: ${options[k]}`).join(", ")
  }
  return `${keys.length} options`
}

export function ConfigCard({ config, onEdit, onArchive, onSetDefault }: ConfigCardProps) {
  const preview = providerOptionsPreview(config.provider_options)

  return (
    <div className="config-card">
      <div className="config-header">
        <span className="config-name">{config.name}</span>
        {config.is_default && <Badge>Default</Badge>}
        {!config.is_enabled && <Badge variant="secondary">Disabled</Badge>}
      </div>

      <div className="config-meta">
        <span>
          <strong>Provider:</strong> {config.provider}
        </span>
        <span>
          <strong>Model:</strong> {config.model}
        </span>
        {config.base_url && (
          <span>
            <strong>Base URL:</strong> {config.base_url}
          </span>
        )}
        {config.has_api_key && (
          <span>
            <strong>API Key:</strong> ••••••••
          </span>
        )}
      </div>

      {preview && <div className="config-options-preview">{preview}</div>}

      <div className="config-actions">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => onEdit(config)}
        >
          <Pencil className="h-3.5 w-3.5" />
          Edit
        </Button>
        {!config.is_default && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onSetDefault(config.id)}
          >
            <Star className="h-3.5 w-3.5" />
            Set Default
          </Button>
        )}
        <Button
          variant="ghost"
          size="sm"
          className="text-[var(--ink-soft)] hover:text-[var(--color-danger)]"
          onClick={() => onArchive(config)}
        >
          <Archive className="h-3.5 w-3.5" />
          Archive
        </Button>
      </div>
    </div>
  )
}
