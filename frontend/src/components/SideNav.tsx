import { useState, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '@/hooks/useAuth'
import { useConversations } from '@/hooks/useConversations'
import { useOrbitStore } from '@/stores/useOrbitStore'
import { useClickOutside } from '@/hooks/useClickOutside'
import type { Conversation } from '@/api/types'
import './SideNav.css'

export function SideNav() {
  const { user, hasUser, isBooting, openAuth, logout } = useAuth()
  const {
    sortedConversations,
    activeConversationId,
    formatConversationTitle,
    selectConversation,
    createNewThread,
    renameConversation,
    archiveConversation,
  } = useConversations(hasUser)

  const activeView = useOrbitStore((s) => s.activeView)
  const setActiveView = useOrbitStore((s) => s.setActiveView)
  const navigate = useNavigate()

  const editingThreadId = useOrbitStore((s) => s.editingThreadId)
  const editingTitle = useOrbitStore((s) => s.editingTitle)
  const setEditingThreadId = useOrbitStore((s) => s.setEditingThreadId)
  const setEditingTitle = useOrbitStore((s) => s.setEditingTitle)

  const [showAccountMenu, setShowAccountMenu] = useState(false)
  const editInputRef = useRef<HTMLInputElement>(null)

  const displayName = user?.display_name || user?.email?.split('@')[0] || ''
  const accountInitial = displayName.slice(0, 1).toUpperCase()

  const startRename = useCallback(
    (conversation: { id: string; title?: string | null }, event: React.MouseEvent) => {
      event.stopPropagation()
      setEditingThreadId(conversation.id)
      setEditingTitle(conversation.title || '')
      requestAnimationFrame(() => {
        editInputRef.current?.focus()
        editInputRef.current?.select()
      })
    },
    [setEditingThreadId, setEditingTitle],
  )

  const submitRename = useCallback(async () => {
    const title = editingTitle.trim()
    if (title && editingThreadId) {
      await renameConversation(editingThreadId, title)
    }
    setEditingThreadId(null)
    setEditingTitle('')
  }, [editingTitle, editingThreadId, renameConversation, setEditingThreadId, setEditingTitle])

  const handleNewThread = useCallback(() => {
    if (!user) {
      openAuth()
      return
    }
    setActiveView('chat')
    navigate('/')
    createNewThread()
  }, [createNewThread, navigate, openAuth, setActiveView, user])

  const handleSelectConversation = useCallback(
    (conversationId: string) => {
      setActiveView('chat')
      navigate('/')
      selectConversation(conversationId)
    },
    [navigate, selectConversation, setActiveView],
  )

  const handleOpenLibrary = useCallback(() => {
    setActiveView('library')
    navigate('/library')
  }, [navigate, setActiveView])

  const cancelEdit = useCallback(() => {
    setEditingThreadId(null)
    setEditingTitle('')
  }, [setEditingThreadId, setEditingTitle])

  const handleDelete = useCallback(
    (conversation: Conversation, event: React.MouseEvent) => {
      event.stopPropagation()
      if (!confirm(`Archive "${formatConversationTitle(conversation)}"?`)) return
      archiveConversation(conversation.id)
    },
    [archiveConversation, formatConversationTitle],
  )

  const toggleAccountMenu = useCallback(() => {
    if (!user) {
      openAuth()
      return
    }
    setShowAccountMenu((prev) => !prev)
  }, [user, openAuth])

  const handleMenuAction = useCallback(
    (action: () => void) => {
      setShowAccountMenu(false)
      action()
    },
    [],
  )

  const accountMenuRef = useClickOutside<HTMLDivElement>(
    useCallback(() => setShowAccountMenu(false), []),
  )

  return (
    <aside className="side-nav" aria-label="Primary navigation">
      <div className="brand-panel">
        <h1>Orbit</h1>
        <p>Zen AI Assistant</p>
      </div>

      <div className="nav-action">
        <button type="button" className="primary-button" onClick={handleNewThread}>
          <span className="material-symbols-outlined" aria-hidden="true">add</span>
          <span>New Chat</span>
        </button>
      </div>

      <nav className="nav-list" aria-label="Workspace">
        <div className="thread-section-title">Chats</div>
        <div className="thread-list" aria-label="Recent conversations">
          {sortedConversations.map((conversation) => (
            <div
              key={conversation.id}
              className={`thread-item${conversation.id === activeConversationId ? ' active' : ''}`}
            >
              {editingThreadId === conversation.id ? (
                <input
                  ref={editInputRef}
                  value={editingTitle}
                  onChange={(e) => setEditingTitle(e.target.value)}
                  className="thread-edit-input"
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') submitRename()
                    if (e.key === 'Escape') cancelEdit()
                  }}
                  onBlur={submitRename}
                  onClick={(e) => e.stopPropagation()}
                />
              ) : (
                <>
                  <button
                    type="button"
                    className="thread-button"
                    onClick={() => handleSelectConversation(conversation.id)}
                  >
                    {formatConversationTitle(conversation)}
                  </button>
                  <div className="thread-actions">
                    <button
                      type="button"
                      className="thread-action-btn"
                      aria-label="Rename"
                      title="Rename"
                      onClick={(e) => startRename(conversation, e)}
                    >
                      <span className="material-symbols-outlined">edit</span>
                    </button>
                    <button
                      type="button"
                      className="thread-action-btn thread-action-delete"
                      aria-label="Archive"
                      title="Archive"
                      onClick={(e) => handleDelete(conversation, e)}
                    >
                      <span className="material-symbols-outlined">close</span>
                    </button>
                  </div>
                </>
              )}
            </div>
          ))}
          {isBooting && <p>Loading workspace...</p>}
          {!isBooting && !user && <p>Sign in to sync chats</p>}
          {!isBooting && user && sortedConversations.length === 0 && <p>No conversations yet</p>}
        </div>

        <button
          type="button"
          className={`nav-item${activeView === 'library' ? ' active' : ''}`}
          onClick={handleOpenLibrary}
        >
          <span className="material-symbols-outlined" aria-hidden="true">book_2</span>
          <span>Library</span>
        </button>
      </nav>

      <div className="account-section" ref={accountMenuRef}>
        <button type="button" className="account-button" onClick={toggleAccountMenu}>
          <span className="avatar" aria-hidden="true">{user ? accountInitial : '?'}</span>
          <span className="account-copy">
            <strong>{displayName}</strong>
            <small>{user ? 'Sign out' : 'Sign in'}</small>
          </span>
          <span className="material-symbols-outlined" aria-hidden="true">
            {user ? (showAccountMenu ? 'expand_less' : 'expand_more') : 'login'}
          </span>
        </button>

        {showAccountMenu && user && (
          <div className="account-menu">
            <button
              type="button"
              className="account-menu-item"
              onClick={() => handleMenuAction(handleOpenLibrary)}
            >
              <span className="material-symbols-outlined">tune</span>
              <span>LLM Configs</span>
            </button>
            <button
              type="button"
              className="account-menu-item"
              onClick={() => handleMenuAction(() => logout())}
            >
              <span className="material-symbols-outlined">logout</span>
              <span>Sign Out</span>
            </button>
          </div>
        )}
      </div>
    </aside>
  )
}
