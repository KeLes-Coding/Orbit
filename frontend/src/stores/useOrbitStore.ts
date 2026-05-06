import { create } from 'zustand'

const THEME_KEY = 'orbit.theme'
const SIDEBAR_KEY = 'orbit.sidebarCollapsed'

const getInitialTheme = (): boolean => {
  if (typeof window === 'undefined') return false
  const storedTheme = localStorage.getItem(THEME_KEY)
  if (storedTheme === 'dark') return true
  if (storedTheme === 'light') return false
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false
}

const getInitialSidebarCollapsed = (): boolean => {
  if (typeof window === 'undefined') return false
  return localStorage.getItem(SIDEBAR_KEY) === 'true'
}

const getInitialActiveView = (): OrbitStore['activeView'] => {
  if (typeof window === 'undefined') return 'chat'
  return window.location.pathname === '/model-configs' ? 'model_configs' : 'chat'
}

interface OrbitStore {
  isDark: boolean
  activeView: 'chat' | 'model_configs'
  draft: string
  showAuth: boolean
  errorMessage: string
  isBooting: boolean
  isAuthenticating: boolean
  isSending: boolean
  isSaving: boolean
  isCreatingConversationTitle: boolean
  authMode: 'login' | 'register'
  authForm: { email: string; password: string; displayName: string }
  activeConversationId: string | null
  pendingConversationLlmConfigId: string | null
  editingThreadId: string | null
  editingTitle: string
  sidebarCollapsed: boolean
  completedOffscreenConversationIds: Record<string, boolean>

  toggleTheme: () => void
  toggleSidebar: () => void
  setSidebarCollapsed: (collapsed: boolean) => void
  setActiveView: (view: 'chat' | 'model_configs') => void
  setDraft: (text: string) => void
  setShowAuth: (show: boolean) => void
  setErrorMessage: (msg: string) => void
  setIsBooting: (val: boolean) => void
  setIsAuthenticating: (val: boolean) => void
  setIsSending: (val: boolean) => void
  setIsSaving: (val: boolean) => void
  setIsCreatingConversationTitle: (val: boolean) => void
  setAuthMode: (mode: 'login' | 'register') => void
  setAuthForm: (form: Partial<OrbitStore['authForm']>) => void
  resetAuthForm: () => void
  setActiveConversationId: (id: string | null) => void
  setPendingConversationLlmConfigId: (id: string | null) => void
  setEditingThreadId: (id: string | null) => void
  setEditingTitle: (title: string) => void
  markConversationCompletedOffscreen: (id: string) => void
  clearConversationCompletionNotice: (id: string) => void
  logout: () => void
}

const defaultAuthForm = { email: '', password: '', displayName: '' }

export const useOrbitStore = create<OrbitStore>((set) => ({
  isDark: getInitialTheme(),
  activeView: getInitialActiveView(),
  draft: '',
  showAuth: false,
  errorMessage: '',
  isBooting: true,
  isAuthenticating: false,
  isSending: false,
  isSaving: false,
  isCreatingConversationTitle: false,
  authMode: 'login',
  authForm: { ...defaultAuthForm },
  activeConversationId: null,
  pendingConversationLlmConfigId: null,
  editingThreadId: null,
  editingTitle: '',
  sidebarCollapsed: getInitialSidebarCollapsed(),
  completedOffscreenConversationIds: {},

  toggleTheme: () =>
    set((state) => {
      const next = !state.isDark
      localStorage.setItem(THEME_KEY, next ? 'dark' : 'light')
      return { isDark: next }
    }),

  toggleSidebar: () =>
    set((state) => {
      const next = !state.sidebarCollapsed
      localStorage.setItem(SIDEBAR_KEY, String(next))
      return { sidebarCollapsed: next }
    }),

  setSidebarCollapsed: (collapsed) =>
    set(() => {
      localStorage.setItem(SIDEBAR_KEY, String(collapsed))
      return { sidebarCollapsed: collapsed }
    }),

  setActiveView: (view) => set({ activeView: view }),
  setDraft: (text) => set({ draft: text }),
  setShowAuth: (show) => set({ showAuth: show }),
  setErrorMessage: (msg) => set({ errorMessage: msg }),
  setIsBooting: (val) => set({ isBooting: val }),
  setIsAuthenticating: (val) => set({ isAuthenticating: val }),
  setIsSending: (val) => set({ isSending: val }),
  setIsSaving: (val) => set({ isSaving: val }),
  setIsCreatingConversationTitle: (val) => set({ isCreatingConversationTitle: val }),
  setAuthMode: (mode) => set({ authMode: mode }),
  setAuthForm: (form) =>
    set((state) => ({ authForm: { ...state.authForm, ...form } })),
  resetAuthForm: () => set({ authForm: { ...defaultAuthForm } }),
  setActiveConversationId: (id) => set({ activeConversationId: id }),
  setPendingConversationLlmConfigId: (id) => set({ pendingConversationLlmConfigId: id }),
  setEditingThreadId: (id) => set({ editingThreadId: id }),
  setEditingTitle: (title) => set({ editingTitle: title }),
  markConversationCompletedOffscreen: (id) =>
    set((state) => ({
      completedOffscreenConversationIds: {
        ...state.completedOffscreenConversationIds,
        [id]: true,
      },
    })),
  clearConversationCompletionNotice: (id) =>
    set((state) => {
      if (!state.completedOffscreenConversationIds[id]) return state
      const { [id]: _removed, ...remaining } = state.completedOffscreenConversationIds
      return { completedOffscreenConversationIds: remaining }
    }),

  logout: () =>
    set({
      draft: '',
      activeConversationId: null,
      pendingConversationLlmConfigId: null,
      isCreatingConversationTitle: false,
      editingThreadId: null,
      editingTitle: '',
      completedOffscreenConversationIds: {},
      authForm: { ...defaultAuthForm },
      activeView: 'chat',
    }),
}))
