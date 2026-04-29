import { useState, useMemo, useEffect, useCallback, useRef } from 'react'
import { useAuth } from '@/hooks/useAuth'
import { useLlmConfigs } from '@/hooks/useLlmConfigs'
import { useOrbitStore } from '@/stores/useOrbitStore'
import { llmConfigApi } from '@/api/llmConfigs'
import type { LlmConfig, LlmProvider, LlmModel } from '@/api/types'
import './SettingsView.css'

const FALLBACK_PROVIDERS: LlmProvider[] = [
  {
    id: 'openai',
    name: 'OpenAI',
    requires_api_key: true,
    supports_custom_base_url: true,
    supports_model_list: true,
    default_base_url: 'https://api.openai.com/v1',
  },
  {
    id: 'openai_compatible',
    name: 'OpenAI Compatible',
    requires_api_key: true,
    supports_custom_base_url: true,
    supports_model_list: true,
  },
  {
    id: 'anthropic',
    name: 'Claude / Anthropic',
    requires_api_key: true,
    supports_custom_base_url: true,
    supports_model_list: true,
  },
  {
    id: 'gemini',
    name: 'Gemini',
    requires_api_key: true,
    supports_custom_base_url: false,
    supports_model_list: true,
  },
  {
    id: 'ollama',
    name: 'Ollama',
    requires_api_key: false,
    supports_custom_base_url: true,
    supports_model_list: true,
    default_base_url: 'http://127.0.0.1:11434',
  },
  {
    id: 'deepseek',
    name: 'DeepSeek',
    requires_api_key: true,
    supports_custom_base_url: true,
    supports_model_list: true,
    default_base_url: 'https://api.deepseek.com',
  },
  {
    id: 'qwen',
    name: 'Qwen',
    requires_api_key: true,
    supports_custom_base_url: true,
    supports_model_list: true,
    default_base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
  },
]

interface ConfigForm {
  name: string
  provider: string
  model: string
  base_url: string
  api_key: string
  provider_options: string
  is_default: boolean
}

const defaultForm: ConfigForm = {
  name: '',
  provider: '',
  model: '',
  base_url: '',
  api_key: '',
  provider_options: '',
  is_default: false,
}

export function SettingsView() {
  const { hasUser } = useAuth()
  const {
    configs,
    providers: apiProviders,
    isLoadingConfigs,
    isLoadingProviders,
    createConfig,
    updateConfig,
    archiveConfig: archiveConfigMutation,
    setDefaultConfig: setDefaultConfigMutation,
    isSaving,
  } = useLlmConfigs(hasUser)

  const errorMessage = useOrbitStore((s) => s.errorMessage)
  const setErrorMessage = useOrbitStore((s) => s.setErrorMessage)

  const [showForm, setShowForm] = useState(false)
  const [editingConfigId, setEditingConfigId] = useState<string | null>(null)
  const [formError, setFormError] = useState('')
  const [providerList, setProviderList] = useState<LlmProvider[]>([])
  const [modelOptions, setModelOptions] = useState<LlmModel[]>([])
  const [modelStatus, setModelStatus] = useState('')
  const [isLoadingModels, setIsLoadingModels] = useState(false)
  const [configForm, setConfigForm] = useState<ConfigForm>({ ...defaultForm })

  const providers = useMemo(
    () => (providerList.length > 0 ? providerList : FALLBACK_PROVIDERS),
    [providerList],
  )

  const selectedProvider = useMemo(
    () => providers.find((p) => p.id === configForm.provider),
    [providers, configForm.provider],
  )

  const modelOptionIds = useMemo(
    () => modelOptions.map((m) => m.id).filter(Boolean),
    [modelOptions],
  )

  const canFetchModels = useMemo(() => {
    if (!selectedProvider?.supports_model_list || isLoadingModels) return false
    if (editingConfigId) {
      const editingConfig = configs.find((c) => c.id === editingConfigId)
      if (editingConfig?.has_api_key && !configForm.api_key.trim()) return true
    }
    if (selectedProvider?.requires_api_key === false) return true
    return Boolean(configForm.api_key.trim())
  }, [selectedProvider, isLoadingModels, editingConfigId, configs, configForm.api_key])

  const resetForm = useCallback(() => {
    setConfigForm({ ...defaultForm })
    setEditingConfigId(null)
    setFormError('')
    setModelOptions([])
    setModelStatus('')
    setShowForm(false)
  }, [])

  const startCreate = useCallback(() => {
    resetForm()
    const activeProviders = providers
    if (activeProviders.length > 0) {
      const p = activeProviders[0]
      setConfigForm((prev) => ({
        ...prev,
        provider: p.id,
        base_url: p.default_base_url && !prev.base_url ? p.default_base_url : '',
      }))
    }
    setShowForm(true)
  }, [resetForm, providers])

  const startEdit = useCallback((config: LlmConfig) => {
    setConfigForm({
      name: config.name || '',
      provider: config.provider || '',
      model: config.model || '',
      base_url: config.base_url || '',
      api_key: '',
      provider_options: config.provider_options
        ? JSON.stringify(config.provider_options, null, 2)
        : '',
      is_default: Boolean(config.is_default),
    })
    setEditingConfigId(config.id)
    setFormError('')
    setModelOptions([])
    setModelStatus('')
    setShowForm(true)
  }, [])

  const parseProviderOptions = useCallback((): Record<string, unknown> | null => {
    const raw = configForm.provider_options.trim()
    if (!raw) return {}
    try {
      const parsed = JSON.parse(raw)
      if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
        setFormError('Provider Options must be a JSON object.')
        return null
      }
      return parsed
    } catch {
      setFormError('Provider Options is not valid JSON.')
      return null
    }
  }, [configForm.provider_options])

  const formatProviderOptions = useCallback(() => {
    setFormError('')
    const parsed = parseProviderOptions()
    if (parsed === null) return
    setConfigForm((prev) => ({ ...prev, provider_options: JSON.stringify(parsed, null, 2) }))
  }, [parseProviderOptions])

  const fetchModels = useCallback(async () => {
    setFormError('')
    setModelStatus('')
    setModelOptions([])

    const providerOptions = parseProviderOptions()
    if (providerOptions === null) return

    setIsLoadingModels(true)
    try {
      let models: LlmModel[]
      if (editingConfigId) {
        const editingConfig = configs.find((c) => c.id === editingConfigId)
        if (editingConfig?.has_api_key && !configForm.api_key.trim()) {
          models = await llmConfigApi.configModels(editingConfigId)
        } else {
          models = await llmConfigApi.models({
            provider: configForm.provider,
            base_url: configForm.base_url.trim() || null,
            api_key: configForm.api_key.trim() || null,
            provider_options: providerOptions,
          })
        }
      } else {
        models = await llmConfigApi.models({
          provider: configForm.provider,
          base_url: configForm.base_url.trim() || null,
          api_key: configForm.api_key.trim() || null,
          provider_options: providerOptions,
        })
      }
      setModelOptions(models || [])
      setModelStatus(
        models.length > 0 ? `${models.length} models loaded.` : 'No models returned.',
      )
    } catch (error) {
      setModelStatus('')
      setFormError(error instanceof Error ? error.message : String(error))
    } finally {
      setIsLoadingModels(false)
    }
  }, [
    configForm,
    editingConfigId,
    configs,
    parseProviderOptions,
  ])

  const submitConfig = useCallback(async () => {
    setFormError('')
    const payload: {
      name: string
      provider: string
      model: string
      is_default: boolean
      base_url?: string
      api_key?: string
      provider_options?: Record<string, unknown>
    } = {
      name: configForm.name.trim(),
      provider: configForm.provider.trim(),
      model: configForm.model.trim(),
      is_default: configForm.is_default,
    }

    if (configForm.base_url.trim()) {
      payload.base_url = configForm.base_url.trim()
    }
    if (configForm.api_key.trim()) {
      payload.api_key = configForm.api_key.trim()
    }
    const providerOptions = parseProviderOptions()
    if (providerOptions === null) return
    payload.provider_options = providerOptions

    let result: LlmConfig | null = null
    try {
      if (editingConfigId) {
        result = await updateConfig(editingConfigId, payload)
      } else {
        result = await createConfig(payload)
      }
    } catch (error) {
      setFormError(error instanceof Error ? error.message : String(error))
      return
    }

    if (result) {
      resetForm()
    }
  }, [configForm, editingConfigId, createConfig, updateConfig, resetForm, parseProviderOptions])

  const handleDelete = useCallback(
    async (config: LlmConfig) => {
      if (!confirm(`Archive "${config.name}"?`)) return
      try {
        await archiveConfigMutation(config.id)
      } catch (error) {
        setErrorMessage(error instanceof Error ? error.message : String(error))
      }
    },
    [archiveConfigMutation, setErrorMessage],
  )

  const handleSetDefault = useCallback(
    async (configId: string) => {
      try {
        await setDefaultConfigMutation(configId)
      } catch (error) {
        setErrorMessage(error instanceof Error ? error.message : String(error))
      }
    },
    [setDefaultConfigMutation, setErrorMessage],
  )

  const providerOptionsPreview = useCallback(
    (options: Record<string, unknown> | null | undefined) => {
      if (!options || Object.keys(options).length === 0) return null
      const keys = Object.keys(options)
      if (keys.length <= 2) {
        return keys.map((k) => `${k}: ${options[k]}`).join(', ')
      }
      return `${keys.length} options`
    },
    [],
  )

  useEffect(() => {
    const loadProviders = async () => {
      if (!hasUser) return
      try {
        const list = await llmConfigApi.providers()
        setProviderList(list)
      } catch {
        setProviderList(FALLBACK_PROVIDERS)
      }
    }
    loadProviders()
  }, [hasUser])

  useEffect(() => {
    if (configForm.provider) {
      setModelOptions([])
      setModelStatus('')
      const provider = selectedProvider
      if (!provider) return
      if (provider.default_base_url && !configForm.base_url.trim()) {
        setConfigForm((prev) => ({ ...prev, base_url: provider.default_base_url! }))
      }
      if (provider.supports_custom_base_url === false) {
        setConfigForm((prev) => ({ ...prev, base_url: '' }))
      }
    }
  }, [configForm.provider])

  const editingConfig = useMemo(
    () => (editingConfigId ? configs.find((c) => c.id === editingConfigId) : null),
    [editingConfigId, configs],
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
          <div style={{ marginLeft: 'auto' }}>
            <button
              type="button"
              className="primary-button"
              style={{ width: 'auto', padding: '0 24px' }}
              onClick={startCreate}
            >
              <span className="material-symbols-outlined" aria-hidden="true">add</span>
              <span>New Config</span>
            </button>
          </div>
        </div>

        <div className="chat-stream">
          {errorMessage && <p className="status-message error">{errorMessage}</p>}

          {!isLoadingConfigs && configs.length === 0 && (
            <p className="status-message">
              No model configurations yet. Create one to start chatting.
            </p>
          )}

          {configs.map((config) => (
            <div key={config.id} className="config-card">
              <div className="config-header">
                <span className="config-name">{config.name}</span>
                {config.is_default && <span className="config-badge">Default</span>}
                {!config.is_enabled && (
                  <span className="config-badge config-badge-disabled">Disabled</span>
                )}
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
              {providerOptionsPreview(config.provider_options) && (
                <div className="config-options-preview">
                  {providerOptionsPreview(config.provider_options)}
                </div>
              )}
              <div className="config-actions">
                <button
                  type="button"
                  className="text-button"
                  style={{ width: 'auto' }}
                  onClick={() => startEdit(config)}
                >
                  Edit
                </button>
                {!config.is_default && (
                  <button
                    type="button"
                    className="text-button"
                    style={{ width: 'auto' }}
                    onClick={() => handleSetDefault(config.id)}
                  >
                    Set Default
                  </button>
                )}
                <button
                  type="button"
                  className="text-button"
                  style={{ width: 'auto', color: 'var(--ink-soft)' }}
                  onClick={() => handleDelete(config)}
                >
                  Archive
                </button>
              </div>
            </div>
          ))}

          {showForm && (
            <div className="config-form-panel">
              <h2>{editingConfigId ? 'Edit Config' : 'New Config'}</h2>
              <form
                className="auth-form"
                onSubmit={(e) => {
                  e.preventDefault()
                  submitConfig()
                }}
              >
                <label>
                  <span>Name</span>
                  <input
                    value={configForm.name}
                    onChange={(e) =>
                      setConfigForm((prev) => ({ ...prev, name: e.target.value }))
                    }
                    placeholder="My OpenAI Config"
                    required
                  />
                </label>
                <label>
                  <span>Provider</span>
                  <select
                    value={configForm.provider}
                    onChange={(e) =>
                      setConfigForm((prev) => ({ ...prev, provider: e.target.value }))
                    }
                    required
                  >
                    {isLoadingProviders && (
                      <option disabled value="">
                        Loading providers
                      </option>
                    )}
                    {providers.map((provider) => (
                      <option key={provider.id} value={provider.id}>
                        {provider.name || provider.id}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>Model</span>
                  <div className="model-picker-row">
                    <input
                      value={configForm.model}
                      onChange={(e) =>
                        setConfigForm((prev) => ({ ...prev, model: e.target.value }))
                      }
                      placeholder="Enter or fetch a model"
                      list="model-list"
                      required
                    />
                    <button
                      type="button"
                      className="icon-form-button"
                      disabled={!canFetchModels}
                      title="Fetch models"
                      onClick={fetchModels}
                    >
                      <span className="material-symbols-outlined" aria-hidden="true">sync</span>
                    </button>
                  </div>
                  <datalist id="model-list">
                    {modelOptionIds.map((id) => (
                      <option key={id} value={id} />
                    ))}
                  </datalist>
                </label>
                <label>
                  <span>Base URL (optional)</span>
                  <input
                    value={configForm.base_url}
                    onChange={(e) =>
                      setConfigForm((prev) => ({ ...prev, base_url: e.target.value }))
                    }
                    disabled={selectedProvider?.supports_custom_base_url === false}
                    placeholder={
                      selectedProvider?.default_base_url || 'https://api.openai.com/v1'
                    }
                  />
                </label>
                <label>
                  <span>
                    API Key (optional
                    {editingConfigId && editingConfig?.has_api_key
                      ? ', leave blank to keep current'
                      : ''}
                    )
                  </span>
                  <input
                    value={configForm.api_key}
                    onChange={(e) =>
                      setConfigForm((prev) => ({ ...prev, api_key: e.target.value }))
                    }
                    type="password"
                    placeholder="sk-..."
                  />
                </label>
                <label>
                  <span>Provider Options (JSON, optional)</span>
                  <textarea
                    value={configForm.provider_options}
                    onChange={(e) =>
                      setConfigForm((prev) => ({
                        ...prev,
                        provider_options: e.target.value,
                      }))
                    }
                    rows={4}
                    placeholder='{"generation": {"temperature": 0.7}, "connection": {"timeout": 60}}'
                  ></textarea>
                </label>
                <div className="form-inline-actions">
                  <button
                    type="button"
                    className="text-button compact"
                    onClick={formatProviderOptions}
                  >
                    Format JSON
                  </button>
                </div>
                <label className="checkbox-field">
                  <input
                    checked={configForm.is_default}
                    onChange={(e) =>
                      setConfigForm((prev) => ({
                        ...prev,
                        is_default: e.target.checked,
                      }))
                    }
                    type="checkbox"
                  />
                  <span>Use as default model</span>
                </label>

                {modelStatus && <p className="status-message">{modelStatus}</p>}
                {formError && <p className="status-message error">{formError}</p>}

                <button type="submit" className="primary-button" disabled={isSaving}>
                  <span className="material-symbols-outlined" aria-hidden="true">save</span>
                  <span>
                    {isSaving
                      ? 'Saving'
                      : editingConfigId
                        ? 'Save Changes'
                        : 'Create Config'}
                  </span>
                </button>
                <button type="button" className="text-button" onClick={resetForm}>
                  Cancel
                </button>
              </form>
            </div>
          )}
        </div>
      </section>
    </main>
  )
}
