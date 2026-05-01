import apiClient from './client'
import type { AuthToken, LoginPayload, RegisterPayload, User } from './types'

export const authApi = {
  login(payload: LoginPayload): Promise<AuthToken> {
    return apiClient.post('/auth/login', payload)
  },

  register(payload: RegisterPayload): Promise<User> {
    return apiClient.post('/auth/register', payload)
  },

  me(): Promise<User> {
    return apiClient.get('/auth/me')
  },
}
