import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { keywordFiltersApi } from '@/lib/api'

export const keywordFilterKeys = {
  all: ['keyword-filters'] as const,
  keywords: (params?: { page?: number; page_size?: number; search?: string }) =>
    [...keywordFilterKeys.all, 'keywords', params] as const,
  whitelist: (params?: { page?: number; page_size?: number }) =>
    [...keywordFilterKeys.all, 'whitelist', params] as const,
}

export function useKeywordFilters(params?: { page?: number; page_size?: number; search?: string }) {
  return useQuery({
    queryKey: keywordFilterKeys.keywords(params),
    queryFn: () => keywordFiltersApi.listKeywords(params),
  })
}

export function useKeywordWhitelist(params?: { page?: number; page_size?: number }) {
  return useQuery({
    queryKey: keywordFilterKeys.whitelist(params),
    queryFn: () => keywordFiltersApi.listWhitelist(params),
  })
}

export function useAddKeyword() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (keyword: string) => keywordFiltersApi.addKeyword(keyword),
    onSuccess: () => qc.invalidateQueries({ queryKey: keywordFilterKeys.all }),
  })
}

export function useToggleKeyword() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, is_active }: { id: number; is_active: boolean }) =>
      keywordFiltersApi.toggleKeyword(id, is_active),
    onSuccess: () => qc.invalidateQueries({ queryKey: keywordFilterKeys.all }),
  })
}

export function useDeleteKeyword() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => keywordFiltersApi.deleteKeyword(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: keywordFilterKeys.all }),
  })
}

export function useAddWhitelistPhrase() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ phrase, reason }: { phrase: string; reason?: string }) =>
      keywordFiltersApi.addWhitelistPhrase(phrase, reason),
    onSuccess: () => qc.invalidateQueries({ queryKey: keywordFilterKeys.all }),
  })
}

export function useDeleteWhitelistPhrase() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => keywordFiltersApi.deleteWhitelistPhrase(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: keywordFilterKeys.all }),
  })
}

export function useReloadKeywordCache() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => keywordFiltersApi.reloadCache(),
    onSuccess: () => qc.invalidateQueries({ queryKey: keywordFilterKeys.all }),
  })
}
