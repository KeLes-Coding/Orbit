import { lazy, Suspense, useEffect, useState } from "react"
import { Routes, Route, useLocation, Navigate } from "react-router-dom"
import { Menu } from "lucide-react"
import { useAuth } from "@/hooks/useAuth"
import { useTheme } from "@/hooks/useTheme"
import { AuthScreen } from "@/components/AuthScreen"
import { SideNav } from "@/components/SideNav"
import { LoadingSpinner } from "@/components/LoadingSpinner"
import { MobileDrawer } from "@/components/MobileDrawer"
import { Sheet } from "@/components/ui/sheet"
import { getStoredToken } from "@/api/client"
import { useOrbitStore } from "@/stores/useOrbitStore"

const ChatShell = lazy(() =>
  import("@/components/ChatShell").then((m) => ({ default: m.ChatShell })),
)
const SettingsView = lazy(() =>
  import("@/components/SettingsView").then((m) => ({ default: m.SettingsView })),
)

function RouteSync() {
  const setActiveView = useOrbitStore((s) => s.setActiveView)
  const setIsBooting = useOrbitStore((s) => s.setIsBooting)
  const { isBooting } = useAuth()
  const location = useLocation()
  const routeView = location.pathname === "/model-configs" ? "model_configs" : "chat"

  useEffect(() => {
    if (!getStoredToken()) {
      setIsBooting(false)
    }
  }, [setIsBooting])

  useEffect(() => {
    if (useOrbitStore.getState().activeView !== routeView) {
      setActiveView(routeView)
    }
  }, [routeView, setActiveView])

  return (
    <Suspense fallback={<LoadingSpinner />}>
      {isBooting ? (
        <LoadingSpinner />
      ) : (
        <Routes>
          <Route path="/" element={<ChatShell />} />
          <Route path="/conversations/:conversationId" element={<ChatShell />} />
          <Route path="/model-configs" element={<SettingsView />} />
          <Route path="/library" element={<Navigate to="/model-configs" replace />} />
          <Route path="*" element={<ChatShell />} />
        </Routes>
      )}
    </Suspense>
  )
}

export default function App() {
  const { showAuth } = useAuth()
  const { isDark } = useTheme()
  const sidebarCollapsed = useOrbitStore((s) => s.sidebarCollapsed)
  const setSidebarCollapsed = useOrbitStore((s) => s.setSidebarCollapsed)
  const [mobileOpen, setMobileOpen] = useState(false)

  useEffect(() => {
    // Radix portal 内容挂在 app-shell 外层，需要把主题类同步到 documentElement。
    document.documentElement.classList.toggle("theme-dark", isDark)
  }, [isDark])

  const appClass = `app-shell${isDark ? " theme-dark" : ""}`

  return (
    <div className={appClass}>
      <AuthScreen />

      {/* Mobile menu button */}
      <button
        type="button"
        className="mobile-menu-trigger"
        aria-label="Open navigation menu"
        onClick={() => setMobileOpen(true)}
      >
        <Menu className="h-5 w-5" />
      </button>

      {/* Mobile Sheet */}
      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <MobileDrawer open={mobileOpen} onOpenChange={setMobileOpen} />
      </Sheet>

      {!showAuth && (
        <div className="orbit-drawer drawer lg:drawer-open">
          <input
            id="orbit-sidebar-drawer"
            type="checkbox"
            className="drawer-toggle"
            checked={!sidebarCollapsed}
            onChange={(event) => setSidebarCollapsed(!event.target.checked)}
          />

          <main className="drawer-content orbit-drawer-content">
            <RouteSync />
          </main>

          <div className="drawer-side orbit-drawer-side is-drawer-close:overflow-visible">
            <label
              htmlFor="orbit-sidebar-drawer"
              aria-label="Close sidebar"
              className="drawer-overlay"
            />
            <SideNav />
          </div>
        </div>
      )}
    </div>
  )
}
