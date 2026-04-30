import { useMemo, useCallback } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import {
  Moon,
  Sun,
  Plus,
  LogOut,
  LogIn,
} from "lucide-react"
import { useAuth } from "@/hooks/useAuth"
import { useConversations } from "@/hooks/useConversations"
import { useLlmConfigs } from "@/hooks/useLlmConfigs"
import { useTheme } from "@/hooks/useTheme"
import { useOrbitStore } from "@/stores/useOrbitStore"
import { Button } from "@/components/ui/button"
import { MessageList } from "@/components/chat/MessageList"
import { ChatComposer } from "@/components/chat/ChatComposer"
import { EmptyChatState } from "@/components/chat/EmptyChatState"
import { ModelSelector } from "@/components/chat/ModelSelector"
import type { LlmConfig } from "@/api/types"
import "./ChatShell.css"

export function ChatShell() {
  const { user, hasUser, openAuth, logout } = useAuth()
  const {
    messages,
    activeConversation,
    activeConversationId,
    isLoadingMessages,
    isSending,
    selectConversation,
    createNewThread,
    sendMessage,
    switchConversationLlm,
  } = useConversations(hasUser)

  const { configs } = useLlmConfigs(hasUser)
  const navigate = useNavigate()

  const draft = useOrbitStore((s) => s.draft)
  const setDraft = useOrbitStore((s) => s.setDraft)
  const errorMessage = useOrbitStore((s) => s.errorMessage)
  const isBooting = useOrbitStore((s) => s.isBooting)
  const setErrorMessage = useOrbitStore((s) => s.setErrorMessage)
  const setActiveView = useOrbitStore((s) => s.setActiveView)

  const { isDark, toggleTheme } = useTheme()

  const currentLlmConfigId = useMemo(() => {
    if (!activeConversation) return null
    const activeConfig = configs.find((c) => c.id === activeConversation.llm_config_id)
    if (activeConfig) return activeConfig.id
    const defaultConfig = configs.find((c) => c.is_default)
    if (defaultConfig) return defaultConfig.id
    return null
  }, [activeConversation, configs])

  const selectModel = useCallback(
    async (config: LlmConfig) => {
      if (!activeConversationId) return
      try {
        await switchConversationLlm(activeConversationId, config.id)
        toast.success(`Switched to ${config.name}`)
      } catch {
        toast.error("Failed to switch model")
      }
    },
    [activeConversationId, switchConversationLlm],
  )

  const goToConfigs = useCallback(() => {
    setActiveView("library")
    navigate("/library")
  }, [navigate, setActiveView])

  const handleNewThread = useCallback(() => {
    if (!user) {
      openAuth()
      return
    }
    createNewThread()
  }, [createNewThread, openAuth, user])

  const handleSendMessage = useCallback(() => {
    if (!user) {
      openAuth()
      return
    }
    if (configs.length === 0) {
      setErrorMessage("Create a model configuration before sending messages.")
      setActiveView("library")
      navigate("/library")
      return
    }
    sendMessage()
  }, [configs.length, navigate, openAuth, sendMessage, setActiveView, setErrorMessage, user])

  const isEmpty = !isBooting && !isLoadingMessages && messages.length === 0

  return (
    <main className="chat-shell">
      {/* Mobile header */}
      <header className="mobile-header">
        <strong>Orbit</strong>
        <div className="header-actions">
          <Button variant="ghost" size="icon-sm" aria-label="New chat" onClick={handleNewThread}>
            <Plus className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={user ? "Sign out" : "Sign in"}
            onClick={() => (user ? logout() : openAuth())}
          >
            {user ? <LogOut className="h-4 w-4" /> : <LogIn className="h-4 w-4" />}
          </Button>
        </div>
      </header>

      {/* Floating theme toggle */}
      <div className="floating-actions">
        <button
          type="button"
          className="icon-button"
          aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
          onClick={toggleTheme}
        >
          {isDark ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
        </button>
      </div>

      <section className="chat-canvas" aria-label="Conversation">
        {/* Canvas bar with model selector */}
        <div className="canvas-bar">
          <ModelSelector
            configs={configs}
            currentConfigId={currentLlmConfigId}
            onSelect={selectModel}
            onManage={goToConfigs}
          />
        </div>

        {/* Message area */}
        {isBooting ? (
          <EmptyChatState variant="booting" />
        ) : isLoadingMessages ? (
          <EmptyChatState variant="loading" />
        ) : isEmpty ? (
          <EmptyChatState variant="greeting" />
        ) : (
          <MessageList messages={messages} />
        )}
      </section>

      {/* Composer */}
      <ChatComposer
        draft={draft}
        setDraft={setDraft}
        isSending={isSending}
        onSend={handleSendMessage}
        onClearError={() => setErrorMessage("")}
        errorMessage={errorMessage}
        isAuthenticated={!!user}
        hasConfigs={configs.length > 0}
      />
    </main>
  )
}
