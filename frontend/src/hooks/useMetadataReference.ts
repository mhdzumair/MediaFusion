import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { metadataReferenceApi, type MetadataReferenceListParams } from '@/lib/api'

const METADATA_REFERENCE_KEY = ['metadata-reference'] as const
const DEFAULT_PER_PAGE = 50

interface MetadataReferenceOptions {
  enabled?: boolean
  perPage?: number
}

function createReferenceDataHook(
  key: readonly string[],
  fetchFn: (params: MetadataReferenceListParams) => ReturnType<typeof metadataReferenceApi.listGenres>,
) {
  return function useReferenceData(params: MetadataReferenceListParams = {}, options: MetadataReferenceOptions = {}) {
    const { enabled = true, perPage = DEFAULT_PER_PAGE } = options
    const queryParams = { ...params, per_page: params.per_page ?? perPage }

    return useQuery({
      queryKey: [...key, queryParams],
      queryFn: () => fetchFn(queryParams),
      staleTime: 60 * 1000,
      enabled,
    })
  }
}

function createInfiniteReferenceDataHook(
  key: readonly string[],
  fetchFn: (params: MetadataReferenceListParams) => ReturnType<typeof metadataReferenceApi.listGenres>,
) {
  return function useInfiniteReferenceData(search?: string, options: MetadataReferenceOptions = {}) {
    const { enabled = true, perPage = DEFAULT_PER_PAGE } = options

    return useInfiniteQuery({
      queryKey: [...key, 'infinite', { search, perPage }],
      queryFn: ({ pageParam = 1 }) =>
        fetchFn({
          search,
          page: pageParam,
          per_page: perPage,
        }),
      initialPageParam: 1,
      getNextPageParam: (lastPage) => (lastPage.has_more ? lastPage.page + 1 : undefined),
      staleTime: 60 * 1000,
      enabled,
    })
  }
}

export const useMetadataReferenceGenres = createReferenceDataHook(
  [...METADATA_REFERENCE_KEY, 'genres'],
  metadataReferenceApi.listGenres,
)
export const useInfiniteMetadataReferenceGenres = createInfiniteReferenceDataHook(
  [...METADATA_REFERENCE_KEY, 'genres'],
  metadataReferenceApi.listGenres,
)

export const useMetadataReferenceCatalogs = createReferenceDataHook(
  [...METADATA_REFERENCE_KEY, 'catalogs'],
  metadataReferenceApi.listCatalogs,
)
export const useInfiniteMetadataReferenceCatalogs = createInfiniteReferenceDataHook(
  [...METADATA_REFERENCE_KEY, 'catalogs'],
  metadataReferenceApi.listCatalogs,
)

export const useMetadataReferenceStars = createReferenceDataHook(
  [...METADATA_REFERENCE_KEY, 'stars'],
  metadataReferenceApi.listStars,
)
export const useInfiniteMetadataReferenceStars = createInfiniteReferenceDataHook(
  [...METADATA_REFERENCE_KEY, 'stars'],
  metadataReferenceApi.listStars,
)

export const useMetadataReferenceParentalCertificates = createReferenceDataHook(
  [...METADATA_REFERENCE_KEY, 'parental-certificates'],
  metadataReferenceApi.listParentalCertificates,
)
export const useInfiniteMetadataReferenceParentalCertificates = createInfiniteReferenceDataHook(
  [...METADATA_REFERENCE_KEY, 'parental-certificates'],
  metadataReferenceApi.listParentalCertificates,
)
