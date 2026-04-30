import apiClient from './client'

export const healthApi = {
  check(): Promise<{ status: string }> {
    return apiClient.get('/health')
  },
}
