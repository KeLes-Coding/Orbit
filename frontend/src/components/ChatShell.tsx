import { useMemo, useCallback, useEffect } from "react"
import { useLocation, useNavigate, useParams } from "react-router-dom"
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
import "./ChatShell.css"

export function ChatShell() {
  const { conversationId: routeConversationId } = useParams<{ conversationId?: string }>()
  const { user, hasUser, openAuth, logout } = useAuth()
  const {
    messages,
    activeConversation,
    activeConversationId,
    pendingConversationLlmConfigId,
    pendingConversationLlmModel,
    isLoadingMessages,
    isSending,
    selectConversation,
    createNewThread,
    sendMessage,
    regenerateAssistant,
    editUserMessage,
    switchBranch,
    forkConversation,
    stopGeneration,
    switchConversationLlm,
    selectPendingConversationLlm,
    pendingFiles,
    isUploadingFiles,
    addFiles,
    removeFile,
  } = useConversations(hasUser)

  const { configs } = useLlmConfigs(hasUser)
  const navigate = useNavigate()
  const location = useLocation()

  const draft = useOrbitStore((s) => s.draft)
  const setDraft = useOrbitStore((s) => s.setDraft)
  const errorMessage = useOrbitStore((s) => s.errorMessage)
  const isBooting = useOrbitStore((s) => s.isBooting)
  const setErrorMessage = useOrbitStore((s) => s.setErrorMessage)
  const setActiveView = useOrbitStore((s) => s.setActiveView)

  const { isDark, toggleTheme } = useTheme()

  useEffect(() => {
    if (routeConversationId && routeConversationId !== activeConversationId) {
      selectConversation(routeConversationId)
    }
  }, [activeConversationId, routeConversationId, selectConversation])

  useEffect(() => {
    if (activeConversationId && !routeConversationId && location.pathname === "/") {
      navigate(`/conversations/${activeConversationId}`, { replace: true })
    }
  }, [activeConversationId, location.pathname, navigate, routeConversationId])

  const currentLlmConfigId = useMemo(() => {
    const pendingConfig = configs.find((c) => c.id === pendingConversationLlmConfigId)
    if (pendingConfig) return pendingConfig.id
    const latestAssistantConfigId = [...messages]
      .reverse()
      .find((message) => message.role === "assistant" && message.llm_config_id)?.llm_config_id
    if (latestAssistantConfigId && configs.some((c) => c.id === latestAssistantConfigId)) {
      return latestAssistantConfigId
    }
    if (!activeConversation) {
      const defaultConfig = configs.find((c) => c.is_default)
      if (defaultConfig) return defaultConfig.id
      return null
    }
    const activeConfig = configs.find((c) => c.id === activeConversation.llm_config_id)
    if (activeConfig) return activeConfig.id
    const defaultConfig = configs.find((c) => c.is_default)
    if (defaultConfig) return defaultConfig.id
    return null
  }, [activeConversation, configs, messages, pendingConversationLlmConfigId])

  const currentModel = useMemo(() => {
    if (pendingConversationLlmModel) return pendingConversationLlmModel
    const latestAssistantModel = [...messages]
      .reverse()
      .find(
        (message) =>
          message.role === "assistant" &&
          message.llm_config_id === currentLlmConfigId &&
          message.model,
      )?.model
    if (latestAssistantModel) return latestAssistantModel
    const activeConfig = configs.find((c) => c.id === currentLlmConfigId)
    return activeConfig?.models[0] || null
  }, [configs, currentLlmConfigId, messages, pendingConversationLlmModel])

  const showVisionHint = useMemo(() => {
    const hasImage = pendingFiles.some((pf) => pf.file.type.startsWith("image/"))
    if (!hasImage) return false
    const activeConfig = configs.find((c) => c.id === currentLlmConfigId)
    return activeConfig ? !activeConfig.supports_vision : false
  }, [pendingFiles, configs, currentLlmConfigId])

  const selectModel = useCallback(
    async (configId: string, model: string) => {
      if (!activeConversationId) {
        selectPendingConversationLlm(configId, model)
        const config = configs.find((c) => c.id === configId)
        toast.success(`New chat will use ${config?.name || "config"} · ${model}`)
        return
      }
      try {
        await switchConversationLlm(activeConversationId, configId)
        selectPendingConversationLlm(configId, model)
        const config = configs.find((c) => c.id === configId)
        toast.success(`Switched to ${config?.name || "config"} · ${model}`)
      } catch {
        toast.error("Failed to switch model")
      }
    },
    [activeConversationId, configs, selectPendingConversationLlm, switchConversationLlm],
  )

  const goToConfigs = useCallback(() => {
    setActiveView("model_configs")
    navigate("/model-configs")
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
      setActiveView("model_configs")
      navigate("/model-configs")
      return
    }
    sendMessage(currentLlmConfigId, currentModel)
  }, [
    configs.length,
    currentLlmConfigId,
    currentModel,
    navigate,
    openAuth,
    sendMessage,
    setActiveView,
    setErrorMessage,
    user,
  ])

  const handleEditMessage = useCallback(
    (messageId: string, newContent: string) => {
      void editUserMessage(messageId, newContent, currentLlmConfigId, currentModel)
    },
    [currentLlmConfigId, currentModel, editUserMessage],
  )

  const handleRegenerateAssistant = useCallback(
    (messageId: string) => {
      void regenerateAssistant(messageId, currentLlmConfigId, currentModel)
    },
    [currentLlmConfigId, currentModel, regenerateAssistant],
  )

  const handleForkMessage = useCallback(
    async (messageId: string) => {
      const title = window.prompt("New conversation title", activeConversation?.title || "")
      if (title === null) return
      const newConversationId = await forkConversation(messageId, title.trim() || null)
      if (newConversationId) {
        navigate(`/conversations/${newConversationId}`)
        toast.success("Forked into a new conversation")
      }
    },
    [activeConversation?.title, forkConversation, navigate],
  )

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
            currentModel={currentModel}
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
          <MessageList
            messages={messages}
            currentLeafMessageId={activeConversation?.active_leaf_message_id ?? null}
            hasActiveRun={isSending}
            isSending={isSending}
            onRegenerate={handleRegenerateAssistant}
            onEdit={handleEditMessage}
            onSwitchBranch={switchBranch}
            onFork={handleForkMessage}
          />
        )}
      </section>

      {/* Composer */}
      <ChatComposer
        draft={draft}
        setDraft={setDraft}
        isSending={isSending}
        onSend={handleSendMessage}
        onStop={stopGeneration}
        onClearError={() => setErrorMessage("")}
        errorMessage={errorMessage}
        isAuthenticated={!!user}
        hasConfigs={configs.length > 0}
        pendingFiles={pendingFiles}
        onAddFiles={addFiles}
        onRemoveFile={removeFile}
        isUploading={isUploadingFiles}
        showVisionHint={showVisionHint}
      />
    </main>
  )
}
