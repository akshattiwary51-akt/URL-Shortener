import { useMutation, useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query'
import { urlApi } from '../services/api'
import type { CreateUrlInput, UpdateUrlInput } from '../types/api'
import { useAuth } from '../context/AuthContext'

export const keys = {
  urls: ['urls'] as const,
  list: (q: string, cursor: string | null) => ['urls', 'list', q, cursor] as const,
  detail: (code: string) => ['urls', 'detail', code] as const,
  dashboard: ['dashboard'] as const,
  analytics: (code: string) => ['analytics', code] as const,
}

export function useUrlList(q: string, cursor: string | null, limit = 10) {
  return useQuery({
    queryKey: keys.list(q, cursor),
    queryFn: () => urlApi.list({ q, cursor, limit }),
    placeholderData: keepPreviousData,
  })
}

export function useUrl(code: string) {
  return useQuery({ queryKey: keys.detail(code), queryFn: () => urlApi.get(code), retry: false })
}

export function useCreateUrl() {
  const qc = useQueryClient()
  const { status } = useAuth()
  return useMutation({
    mutationFn: (input: CreateUrlInput) => urlApi.create(input),
    onSuccess: () => {
      if (status === 'authenticated') {
        void qc.invalidateQueries({ queryKey: keys.urls })
        void qc.invalidateQueries({ queryKey: keys.dashboard })
      }
    },
  })
}

export function useUpdateUrl() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ code, ...input }: UpdateUrlInput & { code: string }) => urlApi.update(code, input),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.urls })
      void qc.invalidateQueries({ queryKey: keys.dashboard })
    },
  })
}

export function useDeleteUrl() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (code: string) => urlApi.remove(code),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.urls })
      void qc.invalidateQueries({ queryKey: keys.dashboard })
    },
  })
}
