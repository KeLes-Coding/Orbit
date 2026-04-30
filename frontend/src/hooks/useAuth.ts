import { useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { authApi } from '@/api/auth'
import { getStoredToken, setStoredToken, clearStoredToken } from '@/api/client'
import { useOrbitStore } from '@/stores/useOrbitStore'
import type { RegisterPayload } from '@/api/types'

export function useAuth() {
  const showAuth = useOrbitStore((s) => s.showAuth)
  const isBooting = useOrbitStore((s) => s.isBooting)
  const errorMessage = useOrbitStore((s) => s.errorMessage)
  const authMode = useOrbitStore((s) => s.authMode)
  const authForm = useOrbitStore((s) => s.authForm)
  const setShowAuth = useOrbitStore((s) => s.setShowAuth)
  const setErrorMessage = useOrbitStore((s) => s.setErrorMessage)
  const setAuthMode = useOrbitStore((s) => s.setAuthMode)
  const setAuthForm = useOrbitStore((s) => s.setAuthForm)
  const resetAuthForm = useOrbitStore((s) => s.resetAuthForm)
  const setIsBooting = useOrbitStore((s) => s.setIsBooting)
  const logoutStore = useOrbitStore((s) => s.logout)

  const queryClient = useQueryClient()
  const token = getStoredToken()

  const {
    data: user,
    isLoading: isRestoringSession,
    isError: isRestoreError,
  } = useQuery({
    queryKey: ['user'],
    queryFn: authApi.me,
    enabled: !!token,
    retry: false,
    staleTime: 5 * 60 * 1000,
  })

  useEffect(() => {
    if (!token) {
      setIsBooting(false)
      return
    }
    if (!isRestoringSession) {
      setIsBooting(false)
    }
  }, [token, isRestoringSession, setIsBooting])

  useEffect(() => {
    if (isRestoreError) {
      clearStoredToken()
    }
  }, [isRestoreError])

  const loginMutation = useMutation({
    mutationFn: authApi.login,
    onSuccess: (data) => {
      setStoredToken(data.access_token)
      queryClient.setQueryData(['user'], data.user)
      setShowAuth(false)
      resetAuthForm()
      setErrorMessage('')
      queryClient.invalidateQueries({ queryKey: ['conversations'] })
      queryClient.invalidateQueries({ queryKey: ['llm-configs'] })
    },
    onError: (error: Error) => {
      setErrorMessage(error.message)
    },
  })

  const registerMutation = useMutation({
    mutationFn: authApi.register,
    onSuccess: (data) => {
      setStoredToken(data.access_token)
      queryClient.setQueryData(['user'], data.user)
      setShowAuth(false)
      resetAuthForm()
      setErrorMessage('')
      queryClient.invalidateQueries({ queryKey: ['conversations'] })
      queryClient.invalidateQueries({ queryKey: ['llm-configs'] })
    },
    onError: (error: Error) => {
      setErrorMessage(error.message)
    },
  })

  const submitAuth = () => {
    setErrorMessage('')
    const payload: RegisterPayload = {
      email: authForm.email,
      password: authForm.password,
    }
    if (authMode === 'register' && authForm.displayName.trim()) {
      payload.display_name = authForm.displayName.trim()
    }
    if (authMode === 'register') {
      registerMutation.mutate(payload)
    } else {
      loginMutation.mutate(payload)
    }
  }

  const toggleAuthMode = () => {
    setErrorMessage('')
    setAuthMode(authMode === 'login' ? 'register' : 'login')
  }

  const openAuth = () => {
    setErrorMessage('')
    setShowAuth(true)
  }

  const closeAuth = () => {
    setErrorMessage('')
    setShowAuth(false)
  }

  const logout = () => {
    clearStoredToken()
    logoutStore()
    setErrorMessage('')
    queryClient.removeQueries({ queryKey: ['user'] })
    queryClient.removeQueries({ queryKey: ['conversations'] })
    queryClient.removeQueries({ queryKey: ['messages'] })
    queryClient.removeQueries({ queryKey: ['llm-configs'] })
    queryClient.removeQueries({ queryKey: ['health'] })
  }

  const isAuthenticating = loginMutation.isPending || registerMutation.isPending
  const hasUser = !!user

  return {
    user,
    hasUser,
    showAuth,
    isBooting,
    isRestoringSession,
    isAuthenticating,
    errorMessage,
    authMode,
    authForm,
    submitAuth,
    openAuth,
    closeAuth,
    toggleAuthMode,
    logout,
    setAuthForm,
  }
}
