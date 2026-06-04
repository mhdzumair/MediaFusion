import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { genreAdminApi, type CreateGenreRequest, type UpdateGenreRequest } from '@/lib/api'

export const genreAdminKeys = {
  all: ['admin-genres'] as const,
  list: (params?: { page?: number; page_size?: number; search?: string; media_type?: string }) =>
    [...genreAdminKeys.all, 'list', params] as const,
}

export function useAdminGenres(params?: { page?: number; page_size?: number; search?: string; media_type?: string }) {
  return useQuery({
    queryKey: genreAdminKeys.list(params),
    queryFn: () => genreAdminApi.list(params),
  })
}

export function useCreateGenre() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (req: CreateGenreRequest) => genreAdminApi.create(req),
    onSuccess: () => qc.invalidateQueries({ queryKey: genreAdminKeys.all }),
  })
}

export function useUpdateGenre() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, req }: { id: number; req: UpdateGenreRequest }) => genreAdminApi.update(id, req),
    onSuccess: () => qc.invalidateQueries({ queryKey: genreAdminKeys.all }),
  })
}

export function useDeleteGenre() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => genreAdminApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: genreAdminKeys.all }),
  })
}

export function useDeleteGenreType() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, mediaType }: { id: number; mediaType: string }) => genreAdminApi.deleteType(id, mediaType),
    onSuccess: () => qc.invalidateQueries({ queryKey: genreAdminKeys.all }),
  })
}

export function useReloadGenresCache() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => genreAdminApi.reloadCache(),
    onSuccess: () => qc.invalidateQueries({ queryKey: genreAdminKeys.all }),
  })
}
