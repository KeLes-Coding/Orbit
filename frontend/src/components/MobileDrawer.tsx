import { useCallback } from "react"
import { useNavigate } from "react-router-dom"
import { Plus, LogOut, LogIn, Settings } from "lucide-react"
import { toast } from "sonner"
import { useAuth } from "@/hooks/useAuth"
import { useConversations } from "@/hooks/useConversations"
import { useOrbitStore } from "@/stores/useOrbitStore"
import { SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Button } from "@/components/ui/button"
import { OrbitIcon } from "@/components/OrbitIcon"
import "./SideNav.css"

interface MobileDrawerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function MobileDrawer({ onOpenChange }: MobileDrawerProps) {
  const { user, hasUser, openAuth, logout } = useAuth()
  const {
    sortedConversations,
    activeConversationId,
    formatConversationTitle,
    selectConversation,
    createNewThread,
  } = useConversations(hasUser)

  const setActiveView = useOrbitStore((s) => s.setActiveView)
  const navigate = useNavigate()

  const closeSheet = useCallback(() => onOpenChange(false), [onOpenChange])

  const handleSelectConversation = useCallback(
    (conversationId: string) => {
      closeSheet()
      setActiveView("chat")
      navigate(`/conversations/${conversationId}`)
      selectConversation(conversationId)
    },
    [closeSheet, navigate, selectConversation, setActiveView],
  )

  const handleNewThread = useCallback(() => {
    if (!user) {
      openAuth()
      return
    }
    closeSheet()
    setActiveView("chat")
    navigate("/")
    createNewThread()
  }, [closeSheet, createNewThread, navigate, openAuth, setActiveView, user])

  const handleModelConfigs = useCallback(() => {
    closeSheet()
    setActiveView("model_configs")
    navigate("/model-configs")
  }, [closeSheet, navigate, setActiveView])

  const displayName = user?.display_name || user?.email?.split("@")[0] || ""
  const accountInitial = displayName.slice(0, 1).toUpperCase()

  return (
    <SheetContent side="left" aria-describedby="mobile-nav-title">
      <SheetHeader>
        <div className="flex items-center gap-2">
          <OrbitIcon size={28} />
          <SheetTitle id="mobile-nav-title">Orbit</SheetTitle>
        </div>
      </SheetHeader>

      {/* New Chat */}
      <div className="px-4">
        <Button className="w-full" onClick={handleNewThread}>
          <Plus className="h-4 w-4" />
          New Chat
        </Button>
      </div>

      {/* Chats list */}
      <ScrollArea className="flex-1 -mx-2">
        <div className="py-1">
          <div className="thread-section-title">Chats</div>
          <div className="thread-list">
            {sortedConversations.length === 0 ? (
              <p className="thread-empty-message">No conversations yet</p>
            ) : (
              sortedConversations.map((conv) => {
                const isPendingTitle = Boolean(conv.metadata?.pendingTitle)
                return (
                  <div
                    key={conv.id}
                    className={`thread-item${conv.id === activeConversationId ? " active" : ""}${isPendingTitle ? " pending" : ""}`}
                  >
                    <button
                      type="button"
                      className="thread-button"
                      disabled={isPendingTitle}
                      onClick={() => handleSelectConversation(conv.id)}
                    >
                      {formatConversationTitle(conv)}
                    </button>
                  </div>
                )
              })
            )}
          </div>
        </div>
      </ScrollArea>

      {/* Bottom: user info + actions */}
      <div className="mt-auto border-t border-[var(--line)] pt-3 pb-4 px-3 flex flex-col gap-1">
        {user && (
          <div className="flex items-center gap-3 px-2 py-2">
            <span className="avatar" aria-hidden="true">
              {accountInitial}
            </span>
            <span className="flex-1 min-w-0">
              <span className="block text-sm font-semibold text-[var(--ink)] truncate">
                {displayName}
              </span>
              <span className="block text-[10px] text-[var(--ink-soft)] uppercase tracking-wider truncate">
                {user.email}
              </span>
            </span>
          </div>
        )}
        <button
          type="button"
          className="account-menu-item w-full"
          onClick={handleModelConfigs}
        >
          <Settings className="h-4 w-4" />
          <span>LLM Configs</span>
        </button>
        {user ? (
          <button
            type="button"
            className="account-menu-item w-full"
            onClick={() => {
              closeSheet()
              logout()
              toast.success("Signed out")
            }}
          >
            <LogOut className="h-4 w-4" />
            <span>Sign Out</span>
          </button>
        ) : (
          <button
            type="button"
            className="account-menu-item w-full"
            onClick={() => {
              closeSheet()
              openAuth()
            }}
          >
            <LogIn className="h-4 w-4" />
            <span>Sign In</span>
          </button>
        )}
      </div>
    </SheetContent>
  )
}
