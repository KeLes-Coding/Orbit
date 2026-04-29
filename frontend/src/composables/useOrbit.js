import { computed, onMounted, ref, watch } from 'vue'
import {
  authApi,
  clearStoredToken,
  conversationApi,
  getStoredToken,
  healthApi,
  llmConfigApi,
  setStoredToken,
} from '../api'

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
const isSaving = ref(false)
const isHealthy = ref(false)
const authMode = ref('login')
const authForm = ref({
  email: '',
  password: '',
  displayName: '',
})

const activeConversation = computed(() =>
  conversations.value.find((conversation) => conversation.id === activeConversationId.value),
)

const selectedModelName = computed(() => {
  const activeConfig = llmConfigs.value.find((config) => config.id === activeConversation.value?.llm_config_id)
  const defaultConfig = llmConfigs.value.find((config) => config.is_default)
  const config = activeConfig || defaultConfig || llmConfigs.value[0]

  return config ? `${config.name}` : 'No model selected'
})

const displayName = computed(() => user.value?.display_name || user.value?.email?.split('@')[0] || '')

const accountInitial = computed(() => displayName.value.slice(0, 1).toUpperCase())

const sortedConversations = computed(() =>
  [...conversations.value].sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at)),
)

const setError = (error) => {
  errorMessage.value = error instanceof Error ? error.message : String(error)
}

const normalizeMessage = (message) => {
  if (!message) return null
  return {
    ...message,
    content: message.content || '',
    paragraphs: (message.content || '').split(/\n{2,}/).filter(Boolean),
  }
}

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
    messages.value = response.map(normalizeMessage).filter(Boolean)
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
    await Promise.all([loadWorkspace(), checkHealth()])
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

const toggleAuthMode = () => {
  authMode.value = authMode.value === 'login' ? 'register' : 'login'
}

const setActiveView = (view) => {
  activeView.value = view
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

const checkHealth = async () => {
  try {
    await healthApi.check()
    isHealthy.value = true
  } catch {
    isHealthy.value = false
  }
}

const loadLlmConfigs = async () => {
  errorMessage.value = ''
  try {
    llmConfigs.value = await llmConfigApi.list()
  } catch (error) {
    setError(error)
  }
}

const createLlmConfig = async (payload) => {
  isSaving.value = true
  errorMessage.value = ''
  try {
    const config = await llmConfigApi.create(payload)
    llmConfigs.value = [
      config,
      ...llmConfigs.value.map((c) => (config.is_default ? { ...c, is_default: false } : c)),
    ]
    return config
  } catch (error) {
    setError(error)
    return null
  } finally {
    isSaving.value = false
  }
}

const updateLlmConfig = async (configId, payload) => {
  isSaving.value = true
  errorMessage.value = ''
  try {
    const updated = await llmConfigApi.update(configId, payload)
    llmConfigs.value = llmConfigs.value.map((c) => {
      if (c.id === configId) return updated
      return updated.is_default ? { ...c, is_default: false } : c
    })
    return updated
  } catch (error) {
    setError(error)
    return null
  } finally {
    isSaving.value = false
  }
}

const archiveLlmConfig = async (configId) => {
  errorMessage.value = ''
  try {
    await llmConfigApi.archive(configId)
    llmConfigs.value = llmConfigs.value.filter((c) => c.id !== configId)
    return true
  } catch (error) {
    setError(error)
    return false
  }
}

const setDefaultLlmConfig = async (configId) => {
  errorMessage.value = ''
  try {
    const updated = await llmConfigApi.setDefault(configId)
    llmConfigs.value = llmConfigs.value.map((c) => ({
      ...c,
      is_default: c.id === updated.id,
    }))
    return true
  } catch (error) {
    setError(error)
    return false
  }
}

const renameConversation = async (conversationId, title) => {
  errorMessage.value = ''
  try {
    const updated = await conversationApi.update(conversationId, { title })
    conversations.value = conversations.value.map((c) => (c.id === conversationId ? { ...c, ...updated } : c))
    return true
  } catch (error) {
    setError(error)
    return false
  }
}

const switchConversationLlm = async (configId) => {
  const conversationId = activeConversationId.value
  if (!conversationId) return false
  errorMessage.value = ''
  try {
    const updated = await conversationApi.update(conversationId, { llm_config_id: configId })
    conversations.value = conversations.value.map((c) => (c.id === conversationId ? { ...c, ...updated } : c))
    return true
  } catch (error) {
    setError(error)
    return false
  }
}

const archiveConversation = async (conversationId) => {
  errorMessage.value = ''
  try {
    await conversationApi.archive(conversationId)
    conversations.value = conversations.value.filter((c) => c.id !== conversationId)
    if (activeConversationId.value === conversationId) {
      activeConversationId.value = null
      messages.value = []
    }
    return true
  } catch (error) {
    setError(error)
    return false
  }
}

watch(isDark, (value) => {
  localStorage.setItem(THEME_KEY, value ? 'dark' : 'light')
})

let initialized = false

export function useOrbit() {
  onMounted(async () => {
    if (initialized) return
    initialized = true
    await restoreSession()
  })

  return {
    isDark,
    activeView,
    draft,
    user,
    showAuth,
    conversations,
    messages,
    llmConfigs,
    activeConversationId,
    errorMessage,
    isBooting,
    isAuthenticating,
    isLoadingMessages,
    isSending,
    isSaving,
    isHealthy,
    authMode,
    authForm,
    activeConversation,
    selectedModelName,
    displayName,
    accountInitial,
    sortedConversations,
    setError,
    normalizeMessage,
    formatConversationTitle,
    loadMessages,
    loadWorkspace,
    restoreSession,
    submitAuth,
    logout,
    openAuth,
    closeAuth,
    toggleTheme,
    toggleAuthMode,
    setActiveView,
    selectConversation,
    createNewThread,
    sendMessage,
    checkHealth,
    loadLlmConfigs,
    createLlmConfig,
    updateLlmConfig,
    archiveLlmConfig,
    setDefaultLlmConfig,
    renameConversation,
    archiveConversation,
    switchConversationLlm,
  }
}
