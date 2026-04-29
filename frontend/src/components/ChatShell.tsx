import { useState, useMemo, useCallback, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '@/hooks/useAuth'
import { useConversations } from '@/hooks/useConversations'
import { useLlmConfigs } from '@/hooks/useLlmConfigs'
import { useTheme } from '@/hooks/useTheme'
import { useOrbitStore } from '@/stores/useOrbitStore'
import { useClickOutside } from '@/hooks/useClickOutside'
import { useAutosizeTextarea } from '@/hooks/useAutosizeTextarea'
import type { LlmConfig } from '@/api/types'
import './ChatShell.css'

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

  const selectedModelName = useMemo(() => {
    if (configs.length === 0) return 'No model selected'
    const activeConfig = configs.find(
      (c) => c.id === activeConversation?.llm_config_id,
    )
    const defaultConfig = configs.find((c) => c.is_default)
    const config = activeConfig || defaultConfig || configs[0]
    return config ? `${config.name}` : 'No model selected'
  }, [configs, activeConversation])

  const draft = useOrbitStore((s) => s.draft)
  const setDraft = useOrbitStore((s) => s.setDraft)
  const errorMessage = useOrbitStore((s) => s.errorMessage)
  const isBooting = useOrbitStore((s) => s.isBooting)
  const setErrorMessage = useOrbitStore((s) => s.setErrorMessage)
  const setActiveView = useOrbitStore((s) => s.setActiveView)

  const { isDark, toggleTheme } = useTheme()

  const [showModelMenu, setShowModelMenu] = useState(false)
  const { ref: composerRef, resize: resizeComposer } = useAutosizeTextarea(168)
  const modelBtnRef = useClickOutside<HTMLDivElement>(
    useCallback(() => setShowModelMenu(false), []),
  )

  const currentLlmConfigId = useMemo(() => {
    if (!activeConversation) return null
    const activeConfig = configs.find((c) => c.id === activeConversation.llm_config_id)
    if (activeConfig) return activeConfig.id
    const defaultConfig = configs.find((c) => c.is_default)
    if (defaultConfig) return defaultConfig.id
    return null
  }, [activeConversation, configs])

  const toggleModelMenu = useCallback(() => {
    setShowModelMenu((prev) => !prev)
  }, [])

  const selectModel = useCallback(
    async (config: LlmConfig) => {
      setShowModelMenu(false)
      if (!activeConversationId) return
      await switchConversationLlm(activeConversationId, config.id)
    },
    [activeConversationId, switchConversationLlm],
  )

  const goToConfigs = useCallback(() => {
    setShowModelMenu(false)
    setActiveView('library')
    navigate('/library')
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
      setErrorMessage('Create a model configuration before sending messages.')
      setActiveView('library')
      navigate('/library')
      return
    }
    sendMessage()
  }, [configs.length, navigate, openAuth, sendMessage, setActiveView, setErrorMessage, user])

  const handleComposerKeydown = useCallback(
    (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return
      event.preventDefault()
      handleSendMessage()
    },
    [handleSendMessage],
  )

  const handleComposerInput = useCallback(() => {
    resizeComposer()
  }, [resizeComposer])

  useEffect(() => {
    resizeComposer()
  }, [draft, resizeComposer])

  return (
    <main className="chat-shell">
      <header className="mobile-header">
        <strong>Orbit</strong>
        <div className="header-actions">
          <button type="button" aria-label="New chat" onClick={handleNewThread}>
            <span className="material-symbols-outlined" aria-hidden="true">add</span>
          </button>
          <button
            type="button"
            aria-label={user ? 'Sign out' : 'Sign in'}
            onClick={() => (user ? logout() : openAuth())}
          >
            <span className="material-symbols-outlined" aria-hidden="true">
              {user ? 'logout' : 'login'}
            </span>
          </button>
        </div>
      </header>

      <div className="floating-actions">
        <button
          type="button"
          className="icon-button"
          aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
          onClick={toggleTheme}
        >
          <span className="material-symbols-outlined" aria-hidden="true">
            {isDark ? 'light_mode' : 'dark_mode'}
          </span>
        </button>
      </div>

      <section className="chat-canvas" aria-label="Conversation">
        <div className="canvas-bar">
          <div className="model-selector" ref={modelBtnRef}>
            <button type="button" className="model-button" onClick={toggleModelMenu}>
              <span>{selectedModelName}</span>
              <span className="material-symbols-outlined" aria-hidden="true">expand_more</span>
            </button>

            {showModelMenu && (
              <div className="model-menu">
                <div className="model-menu-label">Select Model</div>
                {configs.map((config) => (
                  <button
                    key={config.id}
                    type="button"
                    className={`model-menu-item${config.id === currentLlmConfigId ? ' active' : ''}`}
                    onClick={() => selectModel(config)}
                  >
                    <span className="model-menu-name">{config.name}</span>
                    <span className="model-menu-sub">
                      {config.provider} / {config.model}
                    </span>
                  </button>
                ))}
                {configs.length === 0 && (
                  <div className="model-menu-empty">No configs available</div>
                )}
                <div className="model-menu-divider"></div>
                <button type="button" className="model-menu-item" onClick={goToConfigs}>
                  <span className="material-symbols-outlined model-menu-icon">tune</span>
                  <span>Manage Configs</span>
                </button>
              </div>
            )}
          </div>
        </div>

        <div className="chat-stream">
          {isBooting ? (
            <section className="empty-state" aria-live="polite">
              <span className="material-symbols-outlined" aria-hidden="true">hourglass_empty</span>
              <p>Preparing your workspace...</p>
            </section>
          ) : isLoadingMessages ? (
            <section className="empty-state" aria-live="polite">
              <span className="material-symbols-outlined" aria-hidden="true">hourglass_empty</span>
              <p>Loading conversation...</p>
            </section>
          ) : messages.length === 0 ? (
            <section className="empty-state" aria-label="Assistant greeting">
              <span className="material-symbols-outlined" aria-hidden="true">water_drop</span>
              <p>How may I clarify your thoughts today?</p>
            </section>
          ) : (
            messages.map((message) => (
              <article
                key={message.id}
                className={`message-row ${message.role}${message.status === 'streaming' ? ' pending' : ''}`}
              >
                {message.role === 'assistant' && (
                  <div className="assistant-mark" aria-hidden="true">
                    <span className="material-symbols-outlined">water_drop</span>
                  </div>
                )}

                {message.role === 'user' ? (
                  <div className="user-bubble">{message.content}</div>
                ) : (
                  <div className="assistant-copy">
                    {message.paragraphs.map((paragraph, i) => (
                      <p key={i}>{paragraph}</p>
                    ))}
                    {message.status === 'failed' && (
                      <p className="status-message error">
                        The assistant response failed. Check the model configuration and try
                        again.
                      </p>
                    )}
                  </div>
                )}
              </article>
            ))
          )}
        </div>
      </section>

      <form
        className="composer-wrap"
        onSubmit={(e) => {
          e.preventDefault()
          handleSendMessage()
        }}
      >
        {errorMessage && <p className="status-message error">{errorMessage}</p>}
        {!errorMessage && !user && (
          <p className="status-message">
            Sign in from the avatar to send messages and sync chats.
          </p>
        )}
        {!errorMessage && user && configs.length === 0 && (
          <p className="status-message">
            Create a default model configuration before sending messages.
          </p>
        )}
        <div className="composer">
          <textarea
            ref={composerRef}
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value)
              setErrorMessage('')
            }}
            rows={1}
            placeholder="Focus your intent..."
            aria-label="Message"
            disabled={isSending}
            onInput={handleComposerInput}
            onKeyDown={handleComposerKeydown}
          ></textarea>
          <div className="composer-actions">
            <button
              type="submit"
              className="send-button"
              aria-label="Send message"
              disabled={isSending || !draft.trim()}
            >
              <span className="material-symbols-outlined" aria-hidden="true">arrow_upward</span>
            </button>
          </div>
        </div>
        <p>AI may hallucinate. Cultivate discernment.</p>
      </form>
    </main>
  )
}
