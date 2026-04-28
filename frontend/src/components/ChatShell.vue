<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useOrbit } from '../composables/useOrbit'

const {
  isDark,
  user,
  messages,
  llmConfigs,
  activeConversation,
  activeConversationId,
  isBooting,
  isLoadingMessages,
  isSending,
  draft,
  errorMessage,
  selectedModelName,
  setActiveView,
  toggleTheme,
  createNewThread,
  sendMessage,
  switchConversationLlm,
  logout,
  openAuth,
} = useOrbit()

const showModelMenu = ref(false)
const modelBtnRef = ref(null)

const currentLlmConfigId = () => {
  if (!activeConversation.value) return null
  const activeConfig = llmConfigs.value.find((c) => c.id === activeConversation.value.llm_config_id)
  if (activeConfig) return activeConfig.id
  const defaultConfig = llmConfigs.value.find((c) => c.is_default)
  if (defaultConfig) return defaultConfig.id
  return null
}

const toggleModelMenu = () => {
  showModelMenu.value = !showModelMenu.value
}

const selectModel = async (config) => {
  showModelMenu.value = false
  if (!activeConversationId.value) return
  await switchConversationLlm(config.id)
}

const goToConfigs = () => {
  showModelMenu.value = false
  setActiveView('library')
}

const onClickOutside = (event) => {
  if (showModelMenu.value && modelBtnRef.value && !modelBtnRef.value.contains(event.target)) {
    showModelMenu.value = false
  }
}

onMounted(() => document.addEventListener('click', onClickOutside))
onUnmounted(() => document.removeEventListener('click', onClickOutside))
</script>

<template>
  <main class="chat-shell">
    <header class="mobile-header">
      <strong>Orbit</strong>
      <div class="header-actions">
        <button type="button" aria-label="New thread" @click="createNewThread()">
          <span class="material-symbols-outlined" aria-hidden="true">add</span>
        </button>
        <button type="button" :aria-label="user ? 'Sign out' : 'Sign in'" @click="user ? logout() : openAuth()">
          <span class="material-symbols-outlined" aria-hidden="true">
            {{ user ? 'logout' : 'login' }}
          </span>
        </button>
      </div>
    </header>

    <div class="floating-actions">
      <button
        type="button"
        class="icon-button"
        :aria-label="isDark ? 'Switch to light mode' : 'Switch to dark mode'"
        @click="toggleTheme"
      >
        <span class="material-symbols-outlined" aria-hidden="true">
          {{ isDark ? 'light_mode' : 'dark_mode' }}
        </span>
      </button>
    </div>

    <section class="chat-canvas" aria-label="Conversation">
      <div class="canvas-bar">
        <div class="model-selector" ref="modelBtnRef">
          <button type="button" class="model-button" @click="toggleModelMenu">
            <span>{{ selectedModelName }}</span>
            <span class="material-symbols-outlined" aria-hidden="true">expand_more</span>
          </button>

          <div v-if="showModelMenu" class="model-menu">
            <div class="model-menu-label">Select Model</div>
            <button
              v-for="config in llmConfigs"
              :key="config.id"
              type="button"
              class="model-menu-item"
              :class="{ active: config.id === currentLlmConfigId() }"
              @click="selectModel(config)"
            >
              <span class="model-menu-name">{{ config.name }}</span>
              <span class="model-menu-sub">{{ config.provider }} / {{ config.model }}</span>
            </button>
            <div v-if="llmConfigs.length === 0" class="model-menu-empty">
              No configs available
            </div>
            <div class="model-menu-divider"></div>
            <button type="button" class="model-menu-item" @click="goToConfigs">
              <span class="material-symbols-outlined model-menu-icon">tune</span>
              <span>Manage Configs</span>
            </button>
          </div>
        </div>
      </div>

      <div class="chat-stream">
        <section v-if="isBooting" class="empty-state" aria-live="polite">
          <span class="material-symbols-outlined" aria-hidden="true">hourglass_empty</span>
          <p>Preparing your workspace...</p>
        </section>

        <section v-else-if="isLoadingMessages" class="empty-state" aria-live="polite">
          <span class="material-symbols-outlined" aria-hidden="true">hourglass_empty</span>
          <p>Loading conversation...</p>
        </section>

        <section v-else-if="messages.length === 0" class="empty-state" aria-label="Assistant greeting">
          <span class="material-symbols-outlined" aria-hidden="true">water_drop</span>
          <p>How may I clarify your thoughts today?</p>
        </section>

        <article
          v-for="message in messages"
          :key="message.id"
          :class="['message-row', message.role, { pending: message.status === 'streaming' }]"
        >
          <div v-if="message.role === 'assistant'" class="assistant-mark" aria-hidden="true">
            <span class="material-symbols-outlined">water_drop</span>
          </div>

          <div v-if="message.role === 'user'" class="user-bubble">
            {{ message.content }}
          </div>

          <div v-else class="assistant-copy">
            <p v-for="paragraph in message.paragraphs" :key="paragraph">{{ paragraph }}</p>
            <p v-if="message.status === 'failed'" class="status-message error">
              The assistant response failed. Check the model configuration and try again.
            </p>
          </div>
        </article>
      </div>
    </section>

    <form class="composer-wrap" @submit.prevent="sendMessage">
      <p v-if="errorMessage" class="status-message error">{{ errorMessage }}</p>
      <p v-else-if="!user" class="status-message">
        Sign in from the avatar to send messages and sync history.
      </p>
      <p v-else-if="llmConfigs.length === 0" class="status-message">
        Create a default model configuration before sending messages.
      </p>
      <div class="composer">
        <textarea
          v-model="draft"
          rows="1"
          placeholder="Focus your intent..."
          aria-label="Message"
          :disabled="isSending"
        ></textarea>
        <button type="submit" class="send-button" aria-label="Send message" :disabled="isSending">
          <span class="material-symbols-outlined" aria-hidden="true">arrow_upward</span>
        </button>
      </div>
      <p>AI may hallucinate. Cultivate discernment.</p>
    </form>
  </main>
</template>

<style scoped>
.model-selector {
  position: relative;
}

.model-menu {
  position: absolute;
  top: calc(100% + 8px);
  left: 0;
  min-width: 300px;
  max-width: 420px;
  padding: 4px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
  box-shadow: var(--shadow);
  z-index: 30;
}

.model-menu-label {
  padding: 8px 12px 4px;
  color: var(--ink-soft);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}

.model-menu-item {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--ink);
  font-size: 14px;
  text-align: left;
  cursor: pointer;
  transition: background 140ms ease;
}

.model-menu-item:hover {
  background: var(--surface-low);
}

.model-menu-item.active {
  background: color-mix(in srgb, var(--primary) 8%, transparent);
}

.model-menu-name {
  font-weight: 600;
  white-space: nowrap;
}

.model-menu-sub {
  margin-left: auto;
  color: var(--ink-soft);
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.model-menu-empty {
  padding: 12px;
  color: var(--ink-soft);
  font-size: 13px;
  text-align: center;
}

.model-menu-divider {
  height: 1px;
  margin: 4px 8px;
  background: var(--line);
}

.model-menu-icon {
  font-size: 18px !important;
  color: var(--ink-muted) !important;
}
</style>
