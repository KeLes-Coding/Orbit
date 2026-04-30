import { useCallback } from "react"
import { useNavigate } from "react-router-dom"
import {
  Plus,
  BookOpen,
  MessageSquare,
  LogOut,
  Settings,
  LogIn,
} from "lucide-react"
import { useAuth } from "@/hooks/useAuth"
import { useConversations } from "@/hooks/useConversations"
import { useOrbitStore } from "@/stores/useOrbitStore"
import { SheetContent, SheetHeader, SheetTitle, SheetClose } from "@/components/ui/sheet"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Button } from "@/components/ui/button"
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
      navigate("/")
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

  const handleLibrary = useCallback(() => {
    closeSheet()
    setActiveView("library")
    navigate("/library")
  }, [closeSheet, navigate, setActiveView])

  const handleLogout = useCallback(() => {
    closeSheet()
    logout()
  }, [closeSheet, logout])

  return (
    <SheetContent side="left" aria-describedby="mobile-nav-title">
      <SheetHeader>
        <SheetTitle id="mobile-nav-title">Orbit</SheetTitle>
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
              sortedConversations.map((conv) => (
                <div
                  key={conv.id}
                  className={`thread-item${conv.id === activeConversationId ? " active" : ""}`}
                >
                  <button
                    type="button"
                    className="thread-button"
                    onClick={() => handleSelectConversation(conv.id)}
                  >
                    {formatConversationTitle(conv)}
                  </button>
                </div>
              ))
            )}
          </div>
        </div>
      </ScrollArea>

      {/* Bottom actions */}
      <div className="mt-auto border-t border-[var(--line)] pt-2 pb-4 px-2 flex flex-col gap-1">
        <button
          type="button"
          className="account-menu-item w-full"
          onClick={handleLibrary}
        >
          <Settings className="h-4 w-4" />
          <span>LLM Configs</span>
        </button>
        {user ? (
          <button
            type="button"
            className="account-menu-item w-full"
            onClick={handleLogout}
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
