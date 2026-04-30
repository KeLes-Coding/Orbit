import { useAuth } from "@/hooks/useAuth"
import { useTheme } from "@/hooks/useTheme"
import { Moon, Sun, X, LogIn, UserPlus } from "lucide-react"

export function AuthScreen() {
  const {
    showAuth,
    isAuthenticating,
    errorMessage,
    authMode,
    authForm,
    setAuthForm,
    submitAuth,
    closeAuth,
    toggleAuthMode,
  } = useAuth()
  const { isDark, toggleTheme } = useTheme()

  if (!showAuth) return null

  return (
    <section className="auth-screen">
      <div className="auth-actions">
        <button
          type="button"
          className="icon-button"
          aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
          onClick={toggleTheme}
        >
          {isDark ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
        </button>
        <button type="button" className="icon-button" aria-label="Back to chat" onClick={closeAuth}>
          <X className="h-5 w-5" />
        </button>
      </div>
      <div className="auth-panel">
        <p className="auth-kicker">{authMode === "login" ? "Login" : "Register"}</p>
        <h1>Orbit</h1>
        <p className="auth-copy">Sign in to continue your conversations with the backend workspace.</p>

        <form
          className="auth-form"
          onSubmit={(e) => {
            e.preventDefault()
            submitAuth()
          }}
        >
          {authMode === "register" && (
            <label>
              <span>Display name</span>
              <input
                value={authForm.displayName}
                onChange={(e) => setAuthForm({ displayName: e.target.value })}
                autoComplete="name"
                placeholder="Master Ink"
              />
            </label>
          )}
          <label>
            <span>Email</span>
            <input
              value={authForm.email}
              onChange={(e) => setAuthForm({ email: e.target.value })}
              autoComplete="email"
              placeholder="you@example.com"
              type="email"
              required
            />
          </label>
          <label>
            <span>Password</span>
            <input
              value={authForm.password}
              onChange={(e) => setAuthForm({ password: e.target.value })}
              autoComplete={authMode === "login" ? "current-password" : "new-password"}
              placeholder="At least 8 characters"
              type="password"
              required
            />
          </label>

          {errorMessage && <p className="status-message error">{errorMessage}</p>}

          <button type="submit" className="primary-button" disabled={isAuthenticating}>
            {authMode === "login" ? (
              <LogIn className="h-5 w-5" />
            ) : (
              <UserPlus className="h-5 w-5" />
            )}
            <span>
              {isAuthenticating ? "Working" : authMode === "login" ? "Sign In" : "Create Account"}
            </span>
          </button>
        </form>

        <button type="button" className="text-button" onClick={toggleAuthMode}>
          {authMode === "login" ? "Create a new account" : "Use an existing account"}
        </button>
      </div>
    </section>
  )
}
