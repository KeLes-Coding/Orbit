import { useState, useMemo, useCallback, useEffect } from "react"
import { toast } from "sonner"
import { ChevronDown } from "lucide-react"
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { ModelPicker } from "./ModelPicker"
import { llmConfigApi } from "@/api/llmConfigs"
import type { LlmConfig, LlmProvider, LlmModel } from "@/api/types"

const FALLBACK_PROVIDERS: LlmProvider[] = [
  { id: "openai", name: "OpenAI", requires_api_key: true, supports_custom_base_url: true, supports_model_list: true, default_base_url: "https://api.openai.com/v1" },
  { id: "openai_compatible", name: "OpenAI Compatible", requires_api_key: true, supports_custom_base_url: true, supports_model_list: true },
  { id: "anthropic", name: "Claude / Anthropic", requires_api_key: true, supports_custom_base_url: true, supports_model_list: true, default_base_url: "https://api.anthropic.com" },
  { id: "gemini", name: "Gemini", requires_api_key: true, supports_custom_base_url: false, supports_model_list: true, default_base_url: "https://generativelanguage.googleapis.com/v1beta" },
  { id: "ollama", name: "Ollama", requires_api_key: false, supports_custom_base_url: true, supports_model_list: true, default_base_url: "http://127.0.0.1:11434" },
  { id: "deepseek", name: "DeepSeek", requires_api_key: true, supports_custom_base_url: true, supports_model_list: true, default_base_url: "https://api.deepseek.com" },
  { id: "qwen", name: "Qwen", requires_api_key: true, supports_custom_base_url: true, supports_model_list: true, default_base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1" },
]

interface ConfigFormValues {
  name: string
  provider: string
  models: string[]
  base_url: string
  api_key: string
  provider_options: string
  is_default: boolean
  supports_vision: boolean
}

const defaultForm: ConfigFormValues = {
  name: "",
  provider: "",
  models: [],
  base_url: "",
  api_key: "",
  provider_options: "",
  is_default: false,
  supports_vision: false,
}

interface ConfigFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  editingConfig: LlmConfig | null
  providers: LlmProvider[]
  isSaving: boolean
  onSave: (values: ConfigFormValues) => Promise<void>
}

export function ConfigFormDialog({
  open,
  onOpenChange,
  editingConfig,
  providers: apiProviders,
  isSaving,
  onSave,
}: ConfigFormDialogProps) {
  const [form, setForm] = useState<ConfigFormValues>({ ...defaultForm })
  const [formError, setFormError] = useState("")
  const [modelOptions, setModelOptions] = useState<LlmModel[]>([])
  const [modelStatus, setModelStatus] = useState("")
  const [isLoadingModels, setIsLoadingModels] = useState(false)

  const providers = useMemo(() => (apiProviders.length > 0 ? apiProviders : FALLBACK_PROVIDERS), [apiProviders])

  const selectedProvider = useMemo(
    () => providers.find((p) => p.id === form.provider),
    [providers, form.provider],
  )

  const canFetchModels = useMemo(() => {
    if (!selectedProvider?.supports_model_list || isLoadingModels) return false
    if (editingConfig) {
      if (editingConfig.has_api_key && !form.api_key.trim()) return true
    }
    if (selectedProvider?.requires_api_key === false) return true
    return Boolean(form.api_key.trim())
  }, [selectedProvider, isLoadingModels, editingConfig, form.api_key])

  /* Init form when dialog opens */
  useEffect(() => {
    if (!open) return
    if (editingConfig) {
      setForm({
        name: editingConfig.name || "",
        provider: editingConfig.provider || "",
        models: editingConfig.models || [],
        base_url: editingConfig.base_url || "",
        api_key: "",
        provider_options: editingConfig.provider_options
          ? JSON.stringify(editingConfig.provider_options, null, 2)
          : "",
        is_default: Boolean(editingConfig.is_default),
        supports_vision: Boolean(editingConfig.supports_vision),
      })
    } else {
      const p = providers[0]
      setForm({
        ...defaultForm,
        provider: p?.id || "",
        base_url: p?.default_base_url || "",
      })
    }
    setFormError("")
    setModelOptions([])
    setModelStatus("")
  }, [open, editingConfig, providers])

  /* Provider change side effects */
  useEffect(() => {
    if (!form.provider) return
    setModelOptions([])
    setModelStatus("")
  }, [form.provider])

  const handleProviderChange = useCallback(
    (providerId: string) => {
      const nextProvider = providers.find((p) => p.id === providerId)
      setForm((prev) => ({
        ...prev,
        provider: providerId,
        models: [],
        base_url: nextProvider?.default_base_url || "",
      }))
    },
    [providers],
  )

  const parseProviderOptions = useCallback((): Record<string, unknown> | null => {
    const raw = form.provider_options.trim()
    if (!raw) return {}
    try {
      const parsed = JSON.parse(raw)
      if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
        setFormError("Provider Options must be a JSON object.")
        return null
      }
      return parsed
    } catch {
      setFormError("Provider Options is not valid JSON.")
      return null
    }
  }, [form.provider_options])

  const formatProviderOptions = useCallback(() => {
    setFormError("")
    const parsed = parseProviderOptions()
    if (parsed === null) return
    setForm((prev) => ({ ...prev, provider_options: JSON.stringify(parsed, null, 2) }))
  }, [parseProviderOptions])

  const fetchModels = useCallback(async () => {
    setFormError("")
    setModelStatus("")
    setModelOptions([])
    setIsLoadingModels(true)

    const providerOptions = parseProviderOptions()
    if (providerOptions === null) { setIsLoadingModels(false); return }

    try {
      let models: LlmModel[]
      if (editingConfig?.has_api_key && !form.api_key.trim()) {
        models = await llmConfigApi.configModels(editingConfig.id)
      } else {
        models = await llmConfigApi.models({
          provider: form.provider,
          base_url: form.base_url.trim() || null,
          api_key: form.api_key.trim() || null,
          provider_options: providerOptions,
        })
      }
      setModelOptions(models || [])
      setModelStatus(models.length > 0 ? `${models.length} models loaded.` : "No models returned.")
    } catch (error) {
      setModelStatus("")
      toast.error(error instanceof Error ? error.message : "Failed to fetch models")
    } finally {
      setIsLoadingModels(false)
    }
  }, [editingConfig, form, parseProviderOptions])

  const handleSubmit = useCallback(async () => {
    setFormError("")
    if (!form.name.trim()) {
      setFormError("Name is required.")
      return
    }
    if (!form.provider.trim()) {
      setFormError("Provider is required.")
      return
    }
    if (form.models.length === 0) {
      setFormError("At least one model is required.")
      return
    }
    const providerOptions = parseProviderOptions()
    if (providerOptions === null) return

    try {
      await onSave(form)
      onOpenChange(false)
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "Failed to save config")
    }
  }, [form, parseProviderOptions, onSave, onOpenChange])

  const isEditing = !!editingConfig

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[520px] max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEditing ? "Edit Config" : "New Config"}</DialogTitle>
          <DialogDescription>
            {isEditing
              ? `Update configuration for "${editingConfig.name}"`
              : "Create a new model configuration to use in chat"}
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4 py-2">
          {/* Name */}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cfg-name">Name</Label>
            <Input
              id="cfg-name"
              value={form.name}
              onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))}
              placeholder="My OpenAI Config"
            />
          </div>

          {/* Provider */}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cfg-provider">Provider</Label>
            <div className="relative">
              <select
                id="cfg-provider"
                value={form.provider}
                onChange={(e) => handleProviderChange(e.target.value)}
                className="flex h-10 w-full rounded-sm border border-[var(--line)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--ink)] appearance-none transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-orange/30"
              >
                {providers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name || p.id}
                  </option>
                ))}
              </select>
              <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--ink-soft)]" />
            </div>
            {selectedProvider && (
              <p className="text-xs text-[var(--ink-soft)] mt-1">
                {selectedProvider.requires_api_key
                  ? "Requires API Key. "
                  : "Does not require API Key. "}
                {selectedProvider.supports_custom_base_url
                  ? "Custom base URL supported."
                  : "Custom base URL not supported."}
              </p>
            )}
          </div>

          {/* Base URL */}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cfg-baseurl">Base URL (optional)</Label>
            <Input
              id="cfg-baseurl"
              value={form.base_url}
              onChange={(e) => setForm((prev) => ({ ...prev, base_url: e.target.value }))}
              disabled={selectedProvider?.supports_custom_base_url === false}
              placeholder={selectedProvider?.default_base_url || "https://api.openai.com/v1"}
            />
          </div>

          {/* API Key */}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cfg-apikey">
              API Key
              {isEditing && editingConfig.has_api_key ? (
                <span className="text-[var(--ink-soft)] font-normal">
                  {" "}— Saved API key. Leave blank to keep current.
                </span>
              ) : (
                <span className="text-[var(--ink-soft)] font-normal"> (optional)</span>
              )}
            </Label>
            <Input
              id="cfg-apikey"
              value={form.api_key}
              onChange={(e) => setForm((prev) => ({ ...prev, api_key: e.target.value }))}
              type="password"
              placeholder="sk-..."
            />
          </div>

          {/* Models */}
          <div className="flex flex-col gap-1.5">
            <Label>Models</Label>
            <ModelPicker
              selectedModels={form.models}
              onChange={(models) => setForm((prev) => ({ ...prev, models }))}
              modelOptions={modelOptions}
              isLoading={isLoadingModels}
              statusMessage={modelStatus}
              canFetch={canFetchModels}
              onFetch={fetchModels}
            />
          </div>

          {/* Provider Options */}
          <div className="flex flex-col gap-1.5">
            <div className="flex items-center justify-between">
              <Label htmlFor="cfg-provider-options">Advanced Options (JSON)</Label>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-auto py-0 text-xs"
                onClick={formatProviderOptions}
              >
                Format JSON
              </Button>
            </div>
            <Textarea
              id="cfg-provider-options"
              value={form.provider_options}
              onChange={(e) => setForm((prev) => ({ ...prev, provider_options: e.target.value }))}
              rows={4}
              placeholder='{"temperature": 0.7, "max_tokens": 4096}'
              className="font-mono text-xs"
            />
          </div>

          {/* Default checkbox */}
          <label className="flex items-center gap-2.5 cursor-pointer">
            <input
              checked={form.is_default}
              onChange={(e) => setForm((prev) => ({ ...prev, is_default: e.target.checked }))}
              type="checkbox"
              className="h-4 w-4 rounded border-[var(--line)] text-accent-orange focus:ring-accent-orange/30"
            />
            <span className="text-sm text-[var(--ink)]">Use as default model</span>
          </label>

          {/* Vision checkbox */}
          <label className="flex items-start gap-2.5 cursor-pointer">
            <input
              checked={form.supports_vision}
              onChange={(e) => setForm((prev) => ({ ...prev, supports_vision: e.target.checked }))}
              type="checkbox"
              className="mt-0.5 h-4 w-4 rounded border-[var(--line)] text-accent-orange focus:ring-accent-orange/30"
            />
            <div>
              <span className="text-sm text-[var(--ink)]">Support image input (vision / multimodal)</span>
              <p className="text-xs text-[var(--ink-soft)] mt-0.5">
                Enable if the model supports image recognition (e.g., GPT-4o, Claude 3+). Keep off for text-only models.
              </p>
            </div>
          </label>

          {/* Error */}
          {formError && <p className="text-sm text-[var(--color-danger)]">{formError}</p>}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isSaving}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={isSaving}>
            {isSaving ? "Saving..." : isEditing ? "Save Changes" : "Create Config"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
