import axios, { AxiosError } from 'axios'
import type { ApiErrorDetail } from './types'

export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL || '/api/v1'
const TOKEN_KEY = 'orbit.accessToken'

export const getStoredToken = (): string | null => localStorage.getItem(TOKEN_KEY)

export const setStoredToken = (token: string): void => {
  localStorage.setItem(TOKEN_KEY, token)
}

export const clearStoredToken = (): void => {
  localStorage.removeItem(TOKEN_KEY)
}

const parseError = (error: AxiosError<ApiErrorDetail>): string => {
  const fallback = `Request failed with status ${error.response?.status ?? 'unknown'}`

  try {
    const detail = error.response?.data?.detail
    if (typeof detail === 'string') {
      return detail
    }
    if (Array.isArray(detail)) {
      return detail.map((item) => item.msg).filter(Boolean).join(', ') || fallback
    }
    return error.response?.data?.message || fallback
  } catch {
    return fallback
  }
}

const apiClient = axios.create({
  baseURL: API_BASE_URL,
})

apiClient.interceptors.request.use((config) => {
  const token = getStoredToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  if (config.method !== 'get' && config.method !== 'head' && config.data !== undefined) {
    config.headers['Content-Type'] = 'application/json'
  }
  return config
})

apiClient.interceptors.response.use(
  (response) => {
    if (response.status === 204) {
      return null
    }
    return response.data
  },
  (error: AxiosError<ApiErrorDetail>) => {
    if (error.response?.status === 401) {
      clearStoredToken()
    }
    throw new Error(parseError(error))
  },
)

export default apiClient
