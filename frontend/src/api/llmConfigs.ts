import apiClient from './client'
import type {
  CreateLlmConfigPayload,
  LlmConfig,
  LlmModel,
  LlmProvider,
  ProbeModelsPayload,
  UpdateLlmConfigPayload,
} from './types'

export const llmConfigApi = {
  list(): Promise<LlmConfig[]> {
    return apiClient.get('/llm-configs')
  },

  providers(): Promise<LlmProvider[]> {
    return apiClient.get('/llm-configs/providers')
  },

  models(payload: ProbeModelsPayload): Promise<LlmModel[]> {
    return apiClient.post('/llm-configs/models', payload)
  },

  configModels(configId: string): Promise<LlmModel[]> {
    return apiClient.get(`/llm-configs/${configId}/models`)
  },

  get(configId: string): Promise<LlmConfig> {
    return apiClient.get(`/llm-configs/${configId}`)
  },

  create(payload: CreateLlmConfigPayload): Promise<LlmConfig> {
    return apiClient.post('/llm-configs', payload)
  },

  update(configId: string, payload: UpdateLlmConfigPayload): Promise<LlmConfig> {
    return apiClient.patch(`/llm-configs/${configId}`, payload)
  },

  setDefault(configId: string): Promise<LlmConfig> {
    return apiClient.post(`/llm-configs/${configId}/default`)
  },

  archive(configId: string): Promise<null> {
    return apiClient.delete(`/llm-configs/${configId}`)
  },
}
