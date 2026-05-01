import { useState, useRef, useCallback } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import {
  Plus,
  Pencil,
  X,
  LogOut,
  Settings,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  LogIn,
  PanelLeftClose,
  PanelLeftOpen,
  MessageCircle,
} from "lucide-react"
import { useAuth } from "@/hooks/useAuth"
import { useConversations } from "@/hooks/useConversations"
import { useOrbitStore } from "@/stores/useOrbitStore"
import { useClickOutside } from "@/hooks/useClickOutside"
import { Button } from "@/components/ui/button"
import { OrbitIcon } from "@/components/OrbitIcon"
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import type { Conversation } from "@/api/types"
import "./SideNav.css"

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

  const setActiveView = useOrbitStore((s) => s.setActiveView)
  const sidebarCollapsed = useOrbitStore((s) => s.sidebarCollapsed)
  const toggleSidebar = useOrbitStore((s) => s.toggleSidebar)
  const navigate = useNavigate()

  const editingThreadId = useOrbitStore((s) => s.editingThreadId)
  const editingTitle = useOrbitStore((s) => s.editingTitle)
  const setEditingThreadId = useOrbitStore((s) => s.setEditingThreadId)
  const setEditingTitle = useOrbitStore((s) => s.setEditingTitle)

  const [showAccountMenu, setShowAccountMenu] = useState(false)
  const [sessionsExpanded, setSessionsExpanded] = useState(true)
  const [archiveTarget, setArchiveTarget] = useState<Conversation | null>(null)
  const [isArchiving, setIsArchiving] = useState(false)
  const editInputRef = useRef<HTMLInputElement>(null)

  const displayName = user?.display_name || user?.email?.split("@")[0] || ""
  const accountInitial = displayName.slice(0, 1).toUpperCase()

  /* ---------- Rename ---------- */
  const startRename = useCallback(
    (conversation: { id: string; title?: string | null }, event: React.MouseEvent) => {
      event.stopPropagation()
      setEditingThreadId(conversation.id)
      setEditingTitle(conversation.title || "")
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
      try {
        await renameConversation(editingThreadId, title)
        toast.success("Conversation renamed")
      } catch {
        toast.error("Failed to rename conversation")
      }
    }
    setEditingThreadId(null)
    setEditingTitle("")
  }, [editingTitle, editingThreadId, renameConversation, setEditingThreadId, setEditingTitle])

  const cancelEdit = useCallback(() => {
    setEditingThreadId(null)
    setEditingTitle("")
  }, [setEditingThreadId, setEditingTitle])

  /* ---------- Archive ---------- */
  const openArchiveDialog = useCallback(
    (conversation: Conversation, event: React.MouseEvent) => {
      event.stopPropagation()
      setArchiveTarget(conversation)
    },
    [],
  )

  const confirmArchive = useCallback(async () => {
    if (!archiveTarget) return
    setIsArchiving(true)
    try {
      await archiveConversation(archiveTarget.id)
      toast.success(`"${formatConversationTitle(archiveTarget)}" archived`)
    } catch {
      toast.error("Failed to archive conversation")
    } finally {
      setIsArchiving(false)
      setArchiveTarget(null)
    }
  }, [archiveTarget, archiveConversation, formatConversationTitle])

  /* ---------- Navigation ---------- */
  const handleNewThread = useCallback(() => {
    if (!user) {
      openAuth()
      return
    }
    setActiveView("chat")
    navigate("/")
    createNewThread()
  }, [createNewThread, navigate, openAuth, setActiveView, user])

  const handleSelectConversation = useCallback(
    (conversationId: string) => {
      setActiveView("chat")
      navigate(`/conversations/${conversationId}`)
      selectConversation(conversationId)
    },
    [navigate, selectConversation, setActiveView],
  )

  const handleOpenModelConfigs = useCallback(() => {
    setActiveView("model_configs")
    navigate("/model-configs")
  }, [navigate, setActiveView])

  /* ---------- Account ---------- */
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

  const handleToggleSidebar = useCallback(() => {
    setShowAccountMenu(false)
    toggleSidebar()
  }, [toggleSidebar])

  const sidebarClass = `side-nav ${sidebarCollapsed ? "is-drawer-close collapsed" : "is-drawer-open"}`

  return (
    <>
      <aside
        className={sidebarClass}
        data-drawer-state={sidebarCollapsed ? "closed" : "open"}
        aria-label="Primary navigation"
        aria-expanded={!sidebarCollapsed}
      >
        {/* ---- Brand + Toggle ---- */}
        <div className="brand-panel">
          <div className="brand-mark">
            <OrbitIcon size={28} />
            <div className="brand-copy">
              <h1>Orbit</h1>
            </div>
          </div>
          <Tooltip delayDuration={300}>
            <TooltipTrigger asChild>
              <button
                type="button"
                className="sidebar-toggle btn btn-ghost btn-square"
                onClick={handleToggleSidebar}
                aria-label={sidebarCollapsed ? "展开侧边栏" : "折叠侧边栏"}
              >
                {sidebarCollapsed ? (
                  <PanelLeftOpen className="h-7 w-7" />
                ) : (
                  <PanelLeftClose className="h-7 w-7" />
                )}
              </button>
            </TooltipTrigger>
            <TooltipContent side="right">
              {sidebarCollapsed ? "展开侧边栏" : "折叠侧边栏"}
            </TooltipContent>
          </Tooltip>
        </div>

        {/* ---- New Chat ---- */}
        <div className="nav-action">
          <Tooltip delayDuration={300}>
            <TooltipTrigger asChild>
              <Button
                variant="default"
                className="new-chat-button"
                onClick={handleNewThread}
                aria-label="New Chat"
              >
                <Plus className="h-4 w-4" />
                <span className="sidebar-label">New Chat</span>
              </Button>
            </TooltipTrigger>
            {sidebarCollapsed && (
              <TooltipContent side="right">New Chat</TooltipContent>
            )}
          </Tooltip>
        </div>

        {/* ---- Thread List ---- */}
        <nav className="nav-list" aria-label="Workspace">
          <div className="thread-section-header">
            <Tooltip delayDuration={300}>
              <TooltipTrigger asChild>
                <div className="thread-section-title" aria-label="Chats">
                  <MessageCircle className="thread-section-icon h-4 w-4" aria-hidden="true" />
                  <span className="sidebar-label">Chats</span>
                </div>
              </TooltipTrigger>
              {sidebarCollapsed && (
                <TooltipContent side="right">Chats</TooltipContent>
              )}
            </Tooltip>
            <button
              type="button"
              className="sessions-toggle btn btn-ghost btn-xs btn-square"
              onClick={() => setSessionsExpanded((expanded) => !expanded)}
              aria-label={sessionsExpanded ? "Collapse chats" : "Expand chats"}
              aria-expanded={sessionsExpanded}
            >
              {sessionsExpanded ? (
                <ChevronDown className="h-3.5 w-3.5" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" />
              )}
            </button>
          </div>
          <div
            className={`thread-list${sessionsExpanded ? "" : " sessions-collapsed"}`}
            aria-label="Recent conversations"
            aria-hidden={!sessionsExpanded}
          >
            {sortedConversations.map((conversation) => {
              const isPendingTitle = Boolean(conversation.metadata?.pendingTitle)
              return (
                <div
                  key={conversation.id}
                  className={`thread-item${conversation.id === activeConversationId ? " active" : ""}${isPendingTitle ? " pending" : ""}`}
                >
                  {editingThreadId === conversation.id ? (
                    <input
                      ref={editInputRef}
                      value={editingTitle}
                      onChange={(e) => setEditingTitle(e.target.value)}
                      className="thread-edit-input"
                      onKeyDown={(e) => {
                        if (e.key === "Enter") submitRename()
                        if (e.key === "Escape") cancelEdit()
                      }}
                      onBlur={submitRename}
                      onClick={(e) => e.stopPropagation()}
                    />
                  ) : (
                    <>
                      <button
                        type="button"
                        className="thread-button"
                        disabled={isPendingTitle}
                        onClick={() => handleSelectConversation(conversation.id)}
                        aria-label={formatConversationTitle(conversation)}
                      >
                        <span className="thread-title">{formatConversationTitle(conversation)}</span>
                      </button>
                      {!isPendingTitle && (
                      <div className="thread-actions">
                        <button
                          type="button"
                          className="thread-action-btn btn btn-ghost btn-xs btn-square"
                          aria-label="Rename"
                          title="Rename"
                          onClick={(e) => startRename(conversation, e)}
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                        <button
                          type="button"
                          className="thread-action-btn thread-action-delete btn btn-ghost btn-xs btn-square"
                          aria-label="Archive"
                          title="Archive"
                          onClick={(e) => openArchiveDialog(conversation, e)}
                        >
                          <X className="h-3.5 w-3.5" />
                        </button>
                      </div>
                      )}
                    </>
                  )}
                </div>
              )
            })}

            {/* Empty / loading states */}
            {isBooting && (
              <p className="thread-empty-message">Loading workspace...</p>
            )}
            {!isBooting && !user && (
              <p className="thread-empty-message">Sign in to sync chats</p>
            )}
            {!isBooting && user && sortedConversations.length === 0 && (
              <div className="thread-empty-state">
                <p>No conversations yet</p>
                <Button variant="outline" size="sm" onClick={handleNewThread}>
                  <Plus className="h-3.5 w-3.5" />
                  New Chat
                </Button>
              </div>
            )}
          </div>
        </nav>

        {/* ---- Account Section ---- */}
        <div className="account-section" ref={accountMenuRef}>
          <Tooltip delayDuration={300}>
            <TooltipTrigger asChild>
              <button
                type="button"
                className="account-button"
                onClick={toggleAccountMenu}
                aria-label={user ? "Account menu" : "Sign in"}
              >
                <span className="avatar" aria-hidden="true">
                  {user ? accountInitial : "?"}
                </span>
                <span className="account-copy">
                  <strong>{displayName || "Guest"}</strong>
                  <small>{user ? user.email : "Sign in"}</small>
                </span>
                {user ? (
                  showAccountMenu ? (
                    <ChevronUp className="h-4 w-4 text-[var(--ink-muted)]" />
                  ) : (
                    <ChevronDown className="h-4 w-4 text-[var(--ink-muted)]" />
                  )
                ) : (
                  <LogIn className="h-4 w-4 text-[var(--ink-muted)]" />
                )}
              </button>
            </TooltipTrigger>
            {sidebarCollapsed && (
              <TooltipContent side="right">
                {user ? displayName : "Sign in"}
              </TooltipContent>
            )}
          </Tooltip>

          {showAccountMenu && user && (
            <div className="account-menu">
              <button
                type="button"
                className="account-menu-item"
                onClick={() => handleMenuAction(handleOpenModelConfigs)}
              >
                <Settings className="h-4 w-4" />
                <span>LLM Configs</span>
              </button>
              <button
                type="button"
                className="account-menu-item"
                onClick={() =>
                  handleMenuAction(() => {
                    logout()
                    toast.success("Signed out")
                  })
                }
              >
                <LogOut className="h-4 w-4" />
                <span>Sign Out</span>
              </button>
            </div>
          )}
        </div>
      </aside>

      {/* Archive confirmation Dialog */}
      <Dialog open={!!archiveTarget} onOpenChange={(open) => !open && setArchiveTarget(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Archive conversation?</DialogTitle>
            <DialogDescription>
              This action hides the conversation from your sidebar. You can restore or
              inspect archived conversations later if the backend supports it.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setArchiveTarget(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={confirmArchive}
              disabled={isArchiving}
            >
              {isArchiving ? "Archiving..." : "Archive"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
