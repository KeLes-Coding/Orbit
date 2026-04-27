<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import {
  authApi,
  clearStoredToken,
  conversationApi,
  getStoredToken,
  llmConfigApi,
  setStoredToken,
} from './api'

const THEME_KEY = 'orbit.theme'

const getInitialTheme = () => {
  const storedTheme = localStorage.getItem(THEME_KEY)
  if (storedTheme === 'dark') {
    return true
  }
  if (storedTheme === 'light') {
    return false
  }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false
}

const isDark = ref(getInitialTheme())
const activeView = ref('chat')
const draft = ref('')
const user = ref(null)
const showAuth = ref(false)
const conversations = ref([])
const messages = ref([])
const llmConfigs = ref([])
const activeConversationId = ref(null)
const errorMessage = ref('')
const isBooting = ref(true)
const isAuthenticating = ref(false)
const isLoadingMessages = ref(false)
const isSending = ref(false)
const authMode = ref('login')
const authForm = ref({
  email: '',
  password: '',
  displayName: '',
})

const appClass = computed(() => ({
  'app-shell': true,
  'theme-dark': isDark.value,
}))

const activeConversation = computed(() =>
  conversations.value.find((conversation) => conversation.id === activeConversationId.value),
)

const selectedModelName = computed(() => {
  const activeConfig = llmConfigs.value.find((config) => config.id === activeConversation.value?.llm_config_id)
  const defaultConfig = llmConfigs.value.find((config) => config.is_default)
  const config = activeConfig || defaultConfig || llmConfigs.value[0]

  return config ? `${config.name}` : 'Shuimo-4'
})

const displayName = computed(() => user.value?.display_name || user.value?.email?.split('@')[0] || 'Master Ink')

const accountInitial = computed(() => displayName.value.slice(0, 1).toUpperCase())

const sortedConversations = computed(() =>
  [...conversations.value].sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at)),
)

const setError = (error) => {
  errorMessage.value = error instanceof Error ? error.message : String(error)
}

const normalizeMessage = (message) => ({
  ...message,
  content: message.content || '',
  paragraphs: (message.content || '').split(/\n{2,}/).filter(Boolean),
})

const formatConversationTitle = (conversation) => {
  if (conversation.title) {
    return conversation.title
  }
  return `Thread ${conversation.thread_id?.slice(0, 8) || conversation.id.slice(0, 8)}`
}

const loadMessages = async (conversationId) => {
  if (!conversationId) {
    messages.value = []
    return
  }

  isLoadingMessages.value = true
  errorMessage.value = ''

  try {
    const response = await conversationApi.messages(conversationId)
    messages.value = response.map(normalizeMessage)
  } catch (error) {
    setError(error)
  } finally {
    isLoadingMessages.value = false
  }
}

const loadWorkspace = async () => {
  errorMessage.value = ''

  try {
    const [configResponse, conversationResponse] = await Promise.all([
      llmConfigApi.list(),
      conversationApi.list(),
    ])

    llmConfigs.value = configResponse
    conversations.value = conversationResponse

    const firstConversation = sortedConversations.value[0]
    activeConversationId.value = firstConversation?.id || null
    await loadMessages(activeConversationId.value)
  } catch (error) {
    setError(error)
  }
}

const restoreSession = async () => {
  if (!getStoredToken()) {
    isBooting.value = false
    return
  }

  try {
    user.value = await authApi.me()
    await loadWorkspace()
  } catch (error) {
    clearStoredToken()
    user.value = null
    console.warn(error)
  } finally {
    isBooting.value = false
  }
}

const submitAuth = async () => {
  isAuthenticating.value = true
  errorMessage.value = ''

  try {
    const payload = {
      email: authForm.value.email,
      password: authForm.value.password,
    }

    if (authMode.value === 'register' && authForm.value.displayName.trim()) {
      payload.display_name = authForm.value.displayName.trim()
    }

    const response =
      authMode.value === 'register' ? await authApi.register(payload) : await authApi.login(payload)

    setStoredToken(response.access_token)
    user.value = response.user
    await loadWorkspace()
    showAuth.value = false
  } catch (error) {
    setError(error)
  } finally {
    isAuthenticating.value = false
  }
}

const logout = () => {
  clearStoredToken()
  user.value = null
  conversations.value = []
  messages.value = []
  llmConfigs.value = []
  activeConversationId.value = null
  activeView.value = 'chat'
}

const openAuth = () => {
  errorMessage.value = ''
  showAuth.value = true
}

const closeAuth = () => {
  errorMessage.value = ''
  showAuth.value = false
}

const toggleTheme = () => {
  isDark.value = !isDark.value
}

const selectConversation = async (conversationId) => {
  activeView.value = 'chat'
  activeConversationId.value = conversationId
  await loadMessages(conversationId)
}

const createNewThread = async (title = null) => {
  if (!user.value) {
    openAuth()
    return null
  }

  errorMessage.value = ''

  try {
    const conversation = await conversationApi.create({
      title,
      chat_mode: 'chat',
      metadata: {},
    })
    conversations.value = [conversation, ...conversations.value]
    activeConversationId.value = conversation.id
    messages.value = []
    activeView.value = 'chat'
    return conversation
  } catch (error) {
    setError(error)
    return null
  }
}

const sendMessage = async () => {
  const content = draft.value.trim()
  if (!content || isSending.value) {
    return
  }

  if (!user.value) {
    openAuth()
    return
  }

  isSending.value = true
  errorMessage.value = ''
  draft.value = ''

  let conversationId = activeConversationId.value
  if (!conversationId) {
    const title = content.length > 48 ? `${content.slice(0, 48)}...` : content
    const conversation = await createNewThread(title)
    conversationId = conversation?.id
  }

  if (!conversationId) {
    isSending.value = false
    draft.value = content
    return
  }

  const optimisticUser = normalizeMessage({
    id: `local-user-${Date.now()}`,
    role: 'user',
    content,
    status: 'completed',
    created_at: new Date().toISOString(),
  })
  const optimisticAssistant = normalizeMessage({
    id: `local-assistant-${Date.now()}`,
    role: 'assistant',
    content: 'Thinking...',
    status: 'streaming',
    created_at: new Date().toISOString(),
  })

  messages.value = [...messages.value, optimisticUser, optimisticAssistant]

  try {
    const response = await conversationApi.sendMessage(conversationId, content)
    messages.value = [
      ...messages.value.filter(
        (message) => message.id !== optimisticUser.id && message.id !== optimisticAssistant.id,
      ),
      normalizeMessage(response.user_message),
      normalizeMessage(response.assistant_message),
    ].sort((a, b) => (a.sequence_no || 0) - (b.sequence_no || 0))

    conversations.value = await conversationApi.list()
  } catch (error) {
    messages.value = messages.value.filter(
      (message) => message.id !== optimisticAssistant.id,
    )
    draft.value = content
    setError(error)
  } finally {
    isSending.value = false
  }
}

watch(isDark, (value) => {
  localStorage.setItem(THEME_KEY, value ? 'dark' : 'light')
})

onMounted(restoreSession)
</script>

<template>
  <div :class="appClass">
    <section v-if="showAuth" class="auth-screen">
      <div class="auth-actions">
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
        <button type="button" class="icon-button" aria-label="Back to chat" @click="closeAuth">
          <span class="material-symbols-outlined" aria-hidden="true">close</span>
        </button>
      </div>
      <div class="auth-panel">
        <p class="auth-kicker">Orbit</p>
        <h1>Ink Intelligence</h1>
        <p class="auth-copy">Sign in to continue your conversations with the backend workspace.</p>

        <form class="auth-form" @submit.prevent="submitAuth">
          <label v-if="authMode === 'register'">
            <span>Display name</span>
            <input v-model="authForm.displayName" autocomplete="name" placeholder="Master Ink" />
          </label>
          <label>
            <span>Email</span>
            <input v-model="authForm.email" autocomplete="email" placeholder="you@example.com" type="email" required />
          </label>
          <label>
            <span>Password</span>
            <input
              v-model="authForm.password"
              :autocomplete="authMode === 'login' ? 'current-password' : 'new-password'"
              placeholder="At least 8 characters"
              type="password"
              required
            />
          </label>

          <p v-if="errorMessage" class="status-message error">{{ errorMessage }}</p>

          <button type="submit" class="primary-button" :disabled="isAuthenticating">
            <span class="material-symbols-outlined" aria-hidden="true">
              {{ authMode === 'login' ? 'login' : 'person_add' }}
            </span>
            <span>{{ isAuthenticating ? 'Working' : authMode === 'login' ? 'Sign In' : 'Create Account' }}</span>
          </button>
        </form>

        <button
          type="button"
          class="text-button"
          @click="authMode = authMode === 'login' ? 'register' : 'login'"
        >
          {{ authMode === 'login' ? 'Create a new account' : 'Use an existing account' }}
        </button>
      </div>
    </section>

    <template v-else>
      <aside class="side-nav" aria-label="Primary navigation">
        <div class="brand-panel">
          <h1>Orbit</h1>
          <p>Zen AI Assistant</p>
        </div>

        <div class="nav-action">
          <button type="button" class="primary-button" @click="createNewThread()">
            <span class="material-symbols-outlined" aria-hidden="true">add</span>
            <span>New Thread</span>
          </button>
        </div>

        <nav class="nav-list" aria-label="Workspace">
          <button
            type="button"
            :class="['nav-item', { active: activeView === 'chat' }]"
            @click="activeView = 'chat'"
          >
            <span class="material-symbols-outlined" aria-hidden="true">chat_bubble</span>
            <span>New Chat</span>
          </button>

          <button
            type="button"
            :class="['nav-item', { active: activeView === 'history' }]"
            @click="activeView = 'history'"
          >
            <span class="material-symbols-outlined" aria-hidden="true">history</span>
            <span>History</span>
          </button>

          <div class="thread-list" aria-label="Recent conversations">
            <button
              v-for="conversation in sortedConversations"
              :key="conversation.id"
              type="button"
              :class="{ active: conversation.id === activeConversationId }"
              @click="selectConversation(conversation.id)"
            >
              {{ formatConversationTitle(conversation) }}
            </button>
            <p v-if="isBooting">Loading workspace...</p>
            <p v-else-if="!user">Sign in to sync history</p>
            <p v-else-if="sortedConversations.length === 0">No conversations yet</p>
          </div>

          <button
            type="button"
            :class="['nav-item', { active: activeView === 'library' }]"
            @click="activeView = 'library'"
          >
            <span class="material-symbols-outlined" aria-hidden="true">book_2</span>
            <span>Library</span>
          </button>
        </nav>

        <button type="button" class="account-button" @click="user ? logout() : openAuth()">
          <span class="avatar" aria-hidden="true">{{ accountInitial }}</span>
          <span class="account-copy">
            <strong>{{ displayName }}</strong>
            <small>{{ user ? 'Sign out' : 'Sign in' }}</small>
          </span>
          <span class="material-symbols-outlined" aria-hidden="true">
            {{ user ? 'logout' : 'login' }}
          </span>
        </button>
      </aside>

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
            <button type="button" class="model-button">
              <span>{{ selectedModelName }}</span>
              <span class="material-symbols-outlined" aria-hidden="true">expand_more</span>
            </button>
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
  </div>
</template>
