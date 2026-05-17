import { useCallback, useEffect, useMemo } from "react"
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
import { useFileUpload } from "@/hooks/useFileUpload"
import { useTheme } from "@/hooks/useTheme"
import { useOrbitStore } from "@/stores/useOrbitStore"
import { Button } from "@/components/ui/button"
import { MessageList } from "@/components/chat/MessageList"
import { ChatComposer } from "@/components/chat/ChatComposer"
import type { SlashItem } from "@/components/chat/SlashMenu"
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
    chatMode,
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
    setChatMode,
  } = useConversations(hasUser)

  const {
    pendingFiles,
    isUploadingFiles,
    addFiles,
    removeFile,
    uploadPendingFiles,
    clearPendingFiles,
  } = useFileUpload()

  const { configs } = useLlmConfigs(hasUser)
  const navigate = useNavigate()
  const location = useLocation()

  const slashItems = useMemo(() => {
    const items: SlashItem[] = [
      { id: "chat", label: "Chat", detail: "Chat mode", group: "mode" },
      { id: "agent", label: "Agent", detail: "Agent mode (DeepAgent)", group: "mode" },
    ]
    for (const config of configs) {
      for (const model of config.models) {
        items.push({
          id: `${config.id}:${model}`,
          label: model,
          detail: config.name,
          group: "model",
        })
      }
    }
    return items
  }, [configs])

  const draft = useOrbitStore((s) => s.draft)
  const setDraft = useOrbitStore((s) => s.setDraft)
  const errorMessage = useOrbitStore((s) => s.errorMessage)
  const isBooting = useOrbitStore((s) => s.isBooting)
  const setErrorMessage = useOrbitStore((s) => s.setErrorMessage)
  const setActiveView = useOrbitStore((s) => s.setActiveView)

  const { isDark, toggleTheme } = useTheme()

  // Keep URL and store in sync bidirectionally:
  // - URL → store: when navigating directly to /conversations/:id
  // - store → URL: when creating/selecting a conversation from the sidebar
  useEffect(() => {
    if (routeConversationId && routeConversationId !== activeConversationId) {
      selectConversation(routeConversationId)
      return
    }
    if (activeConversationId && !routeConversationId && location.pathname === "/") {
      navigate(`/conversations/${activeConversationId}`, { replace: true })
    }
  }, [activeConversationId, routeConversationId, location.pathname, navigate, selectConversation])

  const currentLlmConfigId = (() => {
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
  })()

  const currentModel = (() => {
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
  })()

  const showVisionHint = (() => {
    const hasImage = pendingFiles.some((pf) => pf.file.type.startsWith("image/"))
    if (!hasImage) return false
    const activeConfig = configs.find((c) => c.id === currentLlmConfigId)
    return activeConfig ? !activeConfig.supports_vision : false
  })()

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

  const handleSlashSelect = useCallback(
    (item: SlashItem) => {
      if (item.group === "mode") {
        if (item.id === "chat" || item.id === "agent") setChatMode(item.id)
      } else {
        const [configId, model] = item.id.split(":")
        selectModel(configId, model)
      }
    },
    [setChatMode, selectModel],
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
    clearPendingFiles()
    createNewThread()
  }, [createNewThread, openAuth, user, clearPendingFiles])

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
    const doSend = async () => {
      const fileIds = await uploadPendingFiles(activeConversationId)
      sendMessage(currentLlmConfigId, currentModel, chatMode, fileIds)
    }
    void doSend().then(() => clearPendingFiles())
  }, [
    activeConversationId,
    configs.length,
    currentLlmConfigId,
    currentModel,
    chatMode,
    navigate,
    openAuth,
    sendMessage,
    setActiveView,
    setErrorMessage,
    user,
    uploadPendingFiles,
    clearPendingFiles,
  ])

  const handleEditMessage = useCallback(
    (messageId: string, newContent: string) => {
      void editUserMessage(messageId, newContent, currentLlmConfigId, currentModel, chatMode)
    },
    [currentLlmConfigId, currentModel, chatMode, editUserMessage],
  )

  const handleRegenerateAssistant = useCallback(
    (messageId: string) => {
      void regenerateAssistant(messageId, currentLlmConfigId, currentModel, chatMode)
    },
    [currentLlmConfigId, currentModel, chatMode, regenerateAssistant],
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
        chatMode={chatMode}
        onChatModeChange={setChatMode}
        slashItems={slashItems}
        onSlashSelect={handleSlashSelect}
      />
    </main>
  )
}
