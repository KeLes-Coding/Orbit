<script setup>
import { ref, computed } from 'vue'
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

const DEFAULT_PROVIDERS = [
  'openai',
  'openai_compatible',
  'anthropic',
  'google_genai',
  'ollama',
]

const showForm = ref(false)
const editingConfigId = ref(null)
const configForm = ref({
  name: '',
  provider: '',
  model: '',
  base_url: '',
  api_key: '',
  provider_options: '',
})

const isEditing = computed(() => editingConfigId.value !== null)

const editingConfig = computed(() =>
  editingConfigId.value ? llmConfigs.value.find((c) => c.id === editingConfigId.value) : null,
)

const resetForm = () => {
  configForm.value = {
    name: '',
    provider: '',
    model: '',
    base_url: '',
    api_key: '',
    provider_options: '',
  }
  editingConfigId.value = null
  showForm.value = false
}

const startCreate = () => {
  resetForm()
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
  }
  editingConfigId.value = config.id
  showForm.value = true
}

const submitConfig = async () => {
  const payload = {
    name: configForm.value.name.trim(),
    provider: configForm.value.provider.trim(),
    model: configForm.value.model.trim(),
  }

  if (configForm.value.base_url.trim()) {
    payload.base_url = configForm.value.base_url.trim()
  }

  if (configForm.value.api_key.trim()) {
    payload.api_key = configForm.value.api_key.trim()
  }

  if (configForm.value.provider_options.trim()) {
    try {
      payload.provider_options = JSON.parse(configForm.value.provider_options)
    } catch {
      return
    }
  }

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
              <input v-model="configForm.provider" placeholder="openai" list="provider-list" required />
              <datalist id="provider-list">
                <option v-for="p in DEFAULT_PROVIDERS" :key="p" :value="p" />
              </datalist>
            </label>
            <label>
              <span>Model</span>
              <input v-model="configForm.model" placeholder="gpt-4o-mini" required />
            </label>
            <label>
              <span>Base URL (optional)</span>
              <input v-model="configForm.base_url" placeholder="https://api.openai.com/v1" />
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
                placeholder='{"temperature": 0.7, "max_tokens": 2048}'
              ></textarea>
            </label>

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

.config-form-panel textarea:focus {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--primary) 12%, transparent);
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
