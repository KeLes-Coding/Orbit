import apiClient from './client'
import type { AgentDescriptor } from './types'

export const agentApi = {
  descriptors(): Promise<AgentDescriptor[]> {
    return apiClient.get('/agents/descriptors')
  },
}
