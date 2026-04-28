<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { llmConfigApi } from '../api'
import { useOrbit } from '../composables/useOrbit'

const {
  llmConfigs,
  isSaving,
  errorMessage,
  loadLlmConfigs,
  createLlmConfig,
  updateLlmConfig,
  archiveLlmConfig,
  setDefaultLlmConfig,
} = useOrbit()

const FALLBACK_PROVIDERS = [
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

const showForm = ref(false)
const editingConfigId = ref(null)
const formError = ref('')
const providerList = ref([])
const modelOptions = ref([])
const modelStatus = ref('')
const isLoadingProviders = ref(false)
const isLoadingModels = ref(false)
const configForm = ref({
  name: '',
  provider: '',
  model: '',
  base_url: '',
  api_key: '',
  provider_options: '',
  is_default: false,
})

const isEditing = computed(() => editingConfigId.value !== null)

const editingConfig = computed(() =>
  editingConfigId.value ? llmConfigs.value.find((c) => c.id === editingConfigId.value) : null,
)

const providers = computed(() => (providerList.value.length > 0 ? providerList.value : FALLBACK_PROVIDERS))

const selectedProvider = computed(() =>
  providers.value.find((provider) => provider.id === configForm.value.provider),
)

const modelOptionIds = computed(() => modelOptions.value.map((model) => model.id).filter(Boolean))

const canFetchModels = computed(() => {
  if (!selectedProvider.value?.supports_model_list || isLoadingModels.value) return false
  if (isEditing.value && editingConfig.value?.has_api_key && !configForm.value.api_key.trim()) return true
  if (selectedProvider.value?.requires_api_key === false) return true
  return Boolean(configForm.value.api_key.trim())
})

const resetForm = () => {
  configForm.value = {
    name: '',
    provider: '',
    model: '',
    base_url: '',
    api_key: '',
    provider_options: '',
    is_default: false,
  }
  editingConfigId.value = null
  formError.value = ''
  modelOptions.value = []
  modelStatus.value = ''
  showForm.value = false
}

const startCreate = () => {
  resetForm()
  if (providers.value.length > 0) {
    configForm.value.provider = providers.value[0].id
    applyProviderDefaultBaseUrl()
  }
  showForm.value = true
}

const startEdit = (config) => {
  configForm.value = {
    name: config.name || '',
    provider: config.provider || '',
    model: config.model || '',
    base_url: config.base_url || '',
    api_key: '',
    provider_options: config.provider_options ? JSON.stringify(config.provider_options, null, 2) : '',
    is_default: Boolean(config.is_default),
  }
  editingConfigId.value = config.id
  formError.value = ''
  modelOptions.value = []
  modelStatus.value = ''
  showForm.value = true
}

const loadProviders = async () => {
  isLoadingProviders.value = true
  try {
    providerList.value = await llmConfigApi.providers()
    if (showForm.value && !configForm.value.provider && providers.value.length > 0) {
      configForm.value.provider = providers.value[0].id
      applyProviderDefaultBaseUrl()
    }
  } catch (error) {
    console.warn(error)
    providerList.value = FALLBACK_PROVIDERS
  } finally {
    isLoadingProviders.value = false
  }
}

const applyProviderDefaultBaseUrl = () => {
  const provider = selectedProvider.value
  if (!provider) return
  if (provider.default_base_url && !configForm.value.base_url.trim()) {
    configForm.value.base_url = provider.default_base_url
  }
  if (provider.supports_custom_base_url === false) {
    configForm.value.base_url = ''
  }
}

const parseProviderOptions = () => {
  const rawOptions = configForm.value.provider_options.trim()
  if (!rawOptions) return {}
  try {
    const parsed = JSON.parse(rawOptions)
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
      formError.value = 'Provider Options must be a JSON object.'
      return null
    }
    return parsed
  } catch {
    formError.value = 'Provider Options is not valid JSON.'
    return null
  }
}

const formatProviderOptions = () => {
  formError.value = ''
  const parsed = parseProviderOptions()
  if (parsed === null) return
  configForm.value.provider_options = JSON.stringify(parsed, null, 2)
}

const fetchModels = async () => {
  formError.value = ''
  modelStatus.value = ''
  modelOptions.value = []

  const providerOptions = parseProviderOptions()
  if (providerOptions === null) return

  isLoadingModels.value = true
  try {
    let models
    if (isEditing.value && editingConfig.value?.has_api_key && !configForm.value.api_key.trim()) {
      models = await llmConfigApi.configModels(editingConfigId.value)
    } else {
      models = await llmConfigApi.models({
        provider: configForm.value.provider,
        base_url: configForm.value.base_url.trim() || null,
        api_key: configForm.value.api_key.trim() || null,
        provider_options: providerOptions,
      })
    }
    modelOptions.value = models || []
    modelStatus.value = modelOptions.value.length > 0 ? `${modelOptions.value.length} models loaded.` : 'No models returned.'
  } catch (error) {
    modelStatus.value = ''
    formError.value = error instanceof Error ? error.message : String(error)
  } finally {
    isLoadingModels.value = false
  }
}

const submitConfig = async () => {
  formError.value = ''
  const payload = {
    name: configForm.value.name.trim(),
    provider: configForm.value.provider.trim(),
    model: configForm.value.model.trim(),
    is_default: configForm.value.is_default,
  }

  if (configForm.value.base_url.trim()) {
    payload.base_url = configForm.value.base_url.trim()
  }

  if (configForm.value.api_key.trim()) {
    payload.api_key = configForm.value.api_key.trim()
  }

  const providerOptions = parseProviderOptions()
  if (providerOptions === null) return
  payload.provider_options = providerOptions

  let result
  if (isEditing.value) {
    result = await updateLlmConfig(editingConfigId.value, payload)
  } else {
    result = await createLlmConfig(payload)
  }

  if (result) {
    resetForm()
  }
}

const handleDelete = async (config) => {
  if (!confirm(`Archive "${config.name}"?`)) return
  await archiveLlmConfig(config.id)
}

const handleSetDefault = async (config) => {
  await setDefaultLlmConfig(config.id)
}

const providerOptionsPreview = (options) => {
  if (!options || Object.keys(options).length === 0) return null
  const keys = Object.keys(options)
  if (keys.length <= 2) {
    return keys.map((k) => `${k}: ${options[k]}`).join(', ')
  }
  return `${keys.length} options`
}

watch(
  () => configForm.value.provider,
  () => {
    modelOptions.value = []
    modelStatus.value = ''
    applyProviderDefaultBaseUrl()
  },
)

onMounted(loadProviders)
</script>

<template>
  <main class="chat-shell">
    <header class="mobile-header">
      <strong>Orbit</strong>
    </header>

    <section class="chat-canvas" aria-label="Model configurations">
      <div class="canvas-bar">
        <button type="button" class="model-button">
          <span>Model Configs</span>
        </button>
        <div style="margin-left: auto">
          <button type="button" class="primary-button" style="width: auto; padding: 0 24px" @click="startCreate">
            <span class="material-symbols-outlined" aria-hidden="true">add</span>
            <span>New Config</span>
          </button>
        </div>
      </div>

      <div class="chat-stream">
        <p v-if="errorMessage" class="status-message error">{{ errorMessage }}</p>

        <p v-if="llmConfigs.length === 0" class="status-message">
          No model configurations yet. Create one to start chatting.
        </p>

        <div
          v-for="config in llmConfigs"
          :key="config.id"
          class="config-card"
        >
          <div class="config-header">
            <span class="config-name">{{ config.name }}</span>
            <span v-if="config.is_default" class="config-badge">Default</span>
            <span v-if="!config.is_enabled" class="config-badge config-badge-disabled">Disabled</span>
          </div>
          <div class="config-meta">
            <span><strong>Provider:</strong> {{ config.provider }}</span>
            <span><strong>Model:</strong> {{ config.model }}</span>
            <span v-if="config.base_url"><strong>Base URL:</strong> {{ config.base_url }}</span>
            <span v-if="config.has_api_key"><strong>API Key:</strong> ••••••••</span>
          </div>
          <div v-if="providerOptionsPreview(config.provider_options)" class="config-options-preview">
            {{ providerOptionsPreview(config.provider_options) }}
          </div>
          <div class="config-actions">
            <button type="button" class="text-button" style="width: auto" @click="startEdit(config)">Edit</button>
            <button
              v-if="!config.is_default"
              type="button"
              class="text-button"
              style="width: auto"
              @click="handleSetDefault(config)"
            >
              Set Default
            </button>
            <button type="button" class="text-button" style="width: auto; color: var(--ink-soft)" @click="handleDelete(config)">Archive</button>
          </div>
        </div>

        <div v-if="showForm" class="config-form-panel">
          <h2>{{ isEditing ? 'Edit Config' : 'New Config' }}</h2>
          <form class="auth-form" @submit.prevent="submitConfig">
            <label>
              <span>Name</span>
              <input v-model="configForm.name" placeholder="My OpenAI Config" required />
            </label>
            <label>
              <span>Provider</span>
              <select v-model="configForm.provider" required>
                <option v-if="isLoadingProviders" disabled value="">Loading providers</option>
                <option v-for="provider in providers" :key="provider.id" :value="provider.id">
                  {{ provider.name || provider.id }}
                </option>
              </select>
            </label>
            <label>
              <span>Model</span>
              <div class="model-picker-row">
                <input v-model="configForm.model" placeholder="Enter or fetch a model" list="model-list" required />
                <button type="button" class="icon-form-button" :disabled="!canFetchModels" title="Fetch models" @click="fetchModels">
                  <span class="material-symbols-outlined" aria-hidden="true">sync</span>
                </button>
              </div>
              <datalist id="model-list">
                <option v-for="model in modelOptionIds" :key="model" :value="model" />
              </datalist>
            </label>
            <label>
              <span>Base URL (optional)</span>
              <input
                v-model="configForm.base_url"
                :disabled="selectedProvider?.supports_custom_base_url === false"
                :placeholder="selectedProvider?.default_base_url || 'https://api.openai.com/v1'"
              />
            </label>
            <label>
              <span>API Key (optional{{ isEditing && editingConfig?.has_api_key ? ', leave blank to keep current' : '' }})</span>
              <input v-model="configForm.api_key" type="password" placeholder="sk-..." />
            </label>
            <label>
              <span>Provider Options (JSON, optional)</span>
              <textarea
                v-model="configForm.provider_options"
                rows="4"
                placeholder='{"generation": {"temperature": 0.7}, "connection": {"timeout": 60}}'
              ></textarea>
            </label>
            <div class="form-inline-actions">
              <button type="button" class="text-button compact" @click="formatProviderOptions">Format JSON</button>
            </div>
            <label class="checkbox-field">
              <input v-model="configForm.is_default" type="checkbox" />
              <span>Use as default model</span>
            </label>

            <p v-if="modelStatus" class="status-message">{{ modelStatus }}</p>
            <p v-if="formError" class="status-message error">{{ formError }}</p>

            <button type="submit" class="primary-button" :disabled="isSaving">
              <span class="material-symbols-outlined" aria-hidden="true">save</span>
              <span>{{ isSaving ? 'Saving' : isEditing ? 'Save Changes' : 'Create Config' }}</span>
            </button>
            <button type="button" class="text-button" @click="resetForm">Cancel</button>
          </form>
        </div>
      </div>
    </section>
  </main>
</template>

<style scoped>
.config-card {
  padding: 24px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
}

.config-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.config-name {
  font-family: "Noto Serif", Georgia, serif;
  font-size: 18px;
  font-weight: 600;
  color: var(--ink);
}

.config-badge {
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  background: var(--primary);
  color: var(--on-primary);
}

.config-badge-disabled {
  background: var(--surface-mid);
  color: var(--ink-soft);
}

.config-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 20px;
  margin-bottom: 12px;
  color: var(--ink-muted);
  font-size: 13px;
  line-height: 1.6;
}

.config-actions {
  display: flex;
  gap: 8px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
}

.config-form-panel {
  padding: 32px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
}

.config-form-panel h2 {
  margin: 0 0 24px;
  font-family: "Noto Serif", Georgia, serif;
  font-size: 22px;
  font-weight: 600;
  color: var(--ink);
}

.config-form-panel textarea {
  width: 100%;
  min-height: 80px;
  padding: 11px 14px;
  border: 1px solid var(--line);
  border-radius: 6px;
  outline: 0;
  background: var(--surface);
  color: var(--ink);
  font: inherit;
  font-size: 14px;
  resize: vertical;
  transition: border-color 180ms ease, box-shadow 180ms ease;
}

.config-form-panel select {
  width: 100%;
  min-height: 48px;
  padding: 0 14px;
  border: 1px solid var(--line);
  border-radius: 6px;
  outline: 0;
  background: var(--surface);
  color: var(--ink);
  font: inherit;
  transition: border-color 180ms ease, box-shadow 180ms ease;
}

.config-form-panel select:focus {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--primary) 12%, transparent);
}

.config-form-panel textarea:focus {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--primary) 12%, transparent);
}

.model-picker-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 48px;
  gap: 8px;
}

.icon-form-button {
  display: grid;
  min-width: 48px;
  min-height: 48px;
  place-items: center;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--surface);
  color: var(--ink);
}

.icon-form-button:hover:not(:disabled) {
  border-color: var(--primary);
}

.form-inline-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: -10px;
}

.text-button.compact {
  width: auto;
  margin-top: 0;
  padding: 0;
  font-size: 13px;
}

.checkbox-field {
  display: flex !important;
  align-items: center;
  gap: 10px !important;
}

.checkbox-field input {
  width: 18px;
  min-height: 18px;
}

.checkbox-field span {
  letter-spacing: 0 !important;
  text-transform: none !important;
}

.config-options-preview {
  margin-bottom: 12px;
  padding: 8px 12px;
  border-radius: 4px;
  background: var(--surface-low);
  color: var(--ink-soft);
  font-size: 12px;
  font-family: monospace;
}
</style>
