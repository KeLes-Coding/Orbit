import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { llmConfigApi } from '@/api/llmConfigs'
import type { LlmConfig, CreateLlmConfigPayload, UpdateLlmConfigPayload } from '@/api/types'

export function useLlmConfigs(hasUser: boolean) {
  const queryClient = useQueryClient()

  const configsQuery = useQuery({
    queryKey: ['llm-configs'],
    queryFn: llmConfigApi.list,
    enabled: hasUser,
  })

  const configs = configsQuery.data || []

  const createConfig = useMutation({
    mutationFn: (payload: CreateLlmConfigPayload) => llmConfigApi.create(payload),
    onSuccess: (config) => {
      queryClient.setQueryData<LlmConfig[]>(['llm-configs'], (old = []) => [
        config,
        ...old.map((c) => (config.is_default ? { ...c, is_default: false } : c)),
      ])
    },
  })

  const updateConfig = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: UpdateLlmConfigPayload }) =>
      llmConfigApi.update(id, payload),
    onSuccess: (updated) => {
      queryClient.setQueryData<LlmConfig[]>(['llm-configs'], (old = []) =>
        old.map((c) => {
          if (c.id === updated.id) return updated
          return updated.is_default ? { ...c, is_default: false } : c
        }),
      )
    },
  })

  const archiveConfig = useMutation({
    mutationFn: llmConfigApi.archive,
    onSuccess: (_data, configId) => {
      queryClient.setQueryData<LlmConfig[]>(['llm-configs'], (old = []) =>
        old.filter((c) => c.id !== configId),
      )
    },
  })

  const setDefaultConfig = useMutation({
    mutationFn: llmConfigApi.setDefault,
    onSuccess: (updated) => {
      queryClient.setQueryData<LlmConfig[]>(['llm-configs'], (old = []) =>
        old.map((c) => ({
          ...c,
          is_default: c.id === updated.id,
        })),
      )
    },
  })

  const providersQuery = useQuery({
    queryKey: ['llm-providers'],
    queryFn: llmConfigApi.providers,
    enabled: hasUser,
  })

  const providers = providersQuery.data || []

  return {
    configs,
    providers,
    isLoadingConfigs: configsQuery.isLoading,
    isLoadingProviders: providersQuery.isLoading,
    createConfig: createConfig.mutateAsync,
    updateConfig: (id: string, payload: UpdateLlmConfigPayload) =>
      updateConfig.mutateAsync({ id, payload }),
    archiveConfig: archiveConfig.mutateAsync,
    setDefaultConfig: setDefaultConfig.mutateAsync,
    isSaving: createConfig.isPending || updateConfig.isPending || archiveConfig.isPending || setDefaultConfig.isPending,
  }
}
