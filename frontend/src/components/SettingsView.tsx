import { useState, useCallback } from "react"
import { toast } from "sonner"
import { Plus } from "lucide-react"
import { useAuth } from "@/hooks/useAuth"
import { useLlmConfigs } from "@/hooks/useLlmConfigs"
import { useOrbitStore } from "@/stores/useOrbitStore"
import { Button } from "@/components/ui/button"
import { ConfigCard } from "@/components/settings/ConfigCard"
import { ConfigFormDialog } from "@/components/settings/ConfigFormDialog"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import type { LlmConfig } from "@/api/types"
import "./SettingsView.css"

export function SettingsView() {
  const { hasUser } = useAuth()
  const {
    configs,
    providers: apiProviders,
    isLoadingConfigs,
    createConfig,
    updateConfig,
    archiveConfig: archiveConfigMutation,
    setDefaultConfig: setDefaultConfigMutation,
    isSaving,
  } = useLlmConfigs(hasUser)

  const errorMessage = useOrbitStore((s) => s.errorMessage)
  const setErrorMessage = useOrbitStore((s) => s.setErrorMessage)

  const [showForm, setShowForm] = useState(false)
  const [editingConfig, setEditingConfig] = useState<LlmConfig | null>(null)
  const [archiveTarget, setArchiveTarget] = useState<LlmConfig | null>(null)
  const [isArchiving, setIsArchiving] = useState(false)

  const handleCreate = useCallback(() => {
    setEditingConfig(null)
    setShowForm(true)
  }, [])

  const handleEdit = useCallback((config: LlmConfig) => {
    setEditingConfig(config)
    setShowForm(true)
  }, [])

  const handleSave = useCallback(
    async (values: {
      name: string
      provider: string
      models: string[]
      base_url: string
      api_key: string
      provider_options: string
      is_default: boolean
    }) => {
      const models = Array.from(
        new Set(values.models.map((model) => model.trim()).filter(Boolean)),
      )
      const payload: {
        name: string
        provider: string
        models: string[]
        is_default: boolean
        base_url?: string
        api_key?: string
        provider_options?: Record<string, unknown>
      } = {
        name: values.name.trim(),
        provider: values.provider.trim(),
        models,
        is_default: values.is_default,
      }

      if (values.base_url.trim()) payload.base_url = values.base_url.trim()
      if (values.api_key.trim()) payload.api_key = values.api_key.trim()
      if (values.provider_options.trim()) {
        payload.provider_options = JSON.parse(values.provider_options.trim())
      }

      if (editingConfig) {
        await updateConfig(editingConfig.id, payload)
        toast.success(`"${values.name}" updated`)
      } else {
        await createConfig(payload)
        toast.success(`"${values.name}" created`)
      }
    },
    [editingConfig, createConfig, updateConfig],
  )

  const handleArchive = useCallback((config: LlmConfig) => {
    setArchiveTarget(config)
  }, [])

  const confirmArchive = useCallback(async () => {
    if (!archiveTarget) return
    setIsArchiving(true)
    try {
      await archiveConfigMutation(archiveTarget.id)
      toast.success(`"${archiveTarget.name}" archived`)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : String(error))
      toast.error("Failed to archive config")
    } finally {
      setIsArchiving(false)
      setArchiveTarget(null)
    }
  }, [archiveTarget, archiveConfigMutation, setErrorMessage])

  const handleSetDefault = useCallback(
    async (configId: string) => {
      try {
        await setDefaultConfigMutation(configId)
        toast.success("Default config updated")
      } catch (error) {
        setErrorMessage(error instanceof Error ? error.message : String(error))
        toast.error("Failed to set default config")
      }
    },
    [setDefaultConfigMutation, setErrorMessage],
  )

  return (
    <main className="chat-shell">
      <header className="mobile-header">
        <strong>Orbit</strong>
      </header>

      <section className="chat-canvas" aria-label="Model configurations">
        <div className="canvas-bar">
          <button type="button" className="model-button">
            <span>Model Configs</span>
          </button>
          <div className="ml-auto">
            <Button onClick={handleCreate}>
              <Plus className="h-4 w-4" />
              New Config
            </Button>
          </div>
        </div>

        <div className="chat-stream">
          {errorMessage && <p className="status-message error">{errorMessage}</p>}

          {!isLoadingConfigs && configs.length === 0 && (
            <div className="flex flex-col items-center gap-4 py-16 text-center">
              <p className="status-message">
                No model configurations yet. Create one to start chatting.
              </p>
              <Button variant="outline" onClick={handleCreate}>
                <Plus className="h-4 w-4" />
                Create First Config
              </Button>
            </div>
          )}

          {configs.map((config) => (
            <ConfigCard
              key={config.id}
              config={config}
              onEdit={handleEdit}
              onArchive={handleArchive}
              onSetDefault={handleSetDefault}
            />
          ))}
        </div>
      </section>

      {/* Form Dialog */}
      <ConfigFormDialog
        open={showForm}
        onOpenChange={setShowForm}
        editingConfig={editingConfig}
        providers={apiProviders}
        isSaving={isSaving}
        onSave={handleSave}
      />

      {/* Archive Dialog */}
      <Dialog open={!!archiveTarget} onOpenChange={(open) => !open && setArchiveTarget(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Archive config?</DialogTitle>
            <DialogDescription>
              This removes &quot;{archiveTarget?.name}&quot; from your configuration list. Any
              conversations using this config will need a new model assigned.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setArchiveTarget(null)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={confirmArchive} disabled={isArchiving}>
              {isArchiving ? "Archiving..." : "Archive"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </main>
  )
}
