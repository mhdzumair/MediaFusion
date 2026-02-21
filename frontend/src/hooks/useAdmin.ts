import { useQuery, useMutation, useQueryClient, useInfiniteQuery } from '@tanstack/react-query'
import {
  adminApi,
  type MetadataListParams,
  type MetadataUpdateRequest,
  type TorrentStreamListParams,
  type TorrentStreamUpdateRequest,
  type TVStreamListParams,
  type TVStreamUpdateRequest,
  type ReferenceItemCreate,
  type ReferenceListParams,
} from '@/lib/api'

// ============================================
// Query Keys
// ============================================

const ADMIN_STATS_KEY = ['admin', 'stats']
const ADMIN_METADATA_KEY = ['admin', 'metadata']
const ADMIN_TORRENT_STREAMS_KEY = ['admin', 'torrent-streams']
const ADMIN_TV_STREAMS_KEY = ['admin', 'tv-streams']
const ADMIN_SOURCES_TORRENT_KEY = ['admin', 'sources', 'torrent']
const ADMIN_SOURCES_TV_KEY = ['admin', 'sources', 'tv']
const ADMIN_COUNTRIES_KEY = ['admin', 'countries']
const ADMIN_RESOLUTIONS_KEY = ['admin', 'resolutions']

// Reference Data Keys
const ADMIN_GENRES_KEY = ['admin', 'reference', 'genres']
const ADMIN_CATALOGS_KEY = ['admin', 'reference', 'catalogs']
const ADMIN_LANGUAGES_KEY = ['admin', 'reference', 'languages']
const ADMIN_STARS_KEY = ['admin', 'reference', 'stars']
const ADMIN_PARENTAL_CERTS_KEY = ['admin', 'reference', 'parental-certificates']
const ADMIN_NAMESPACES_KEY = ['admin', 'reference', 'namespaces']
const ADMIN_ANNOUNCE_URLS_KEY = ['admin', 'reference', 'announce-urls']

// ============================================
// Stats Hook
// ============================================

export function useAdminStats() {
  return useQuery({
    queryKey: ADMIN_STATS_KEY,
    queryFn: () => adminApi.getStats(),
    staleTime: 30 * 1000, // 30 seconds
  })
}

// ============================================
// Metadata Hooks
// ============================================

export function useMetadataList(params: MetadataListParams = {}) {
  return useQuery({
    queryKey: [...ADMIN_METADATA_KEY, 'list', params],
    queryFn: () => adminApi.listMetadata(params),
  })
}

export function useMetadata(metaId: number | undefined) {
  return useQuery({
    queryKey: [...ADMIN_METADATA_KEY, metaId],
    queryFn: () => adminApi.getMetadata(metaId!),
    enabled: metaId !== undefined,
  })
}

export function useUpdateMetadata() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ metaId, data }: { metaId: number; data: MetadataUpdateRequest }) =>
      adminApi.updateMetadata(metaId, data),
    onSuccess: (_, { metaId }) => {
      queryClient.invalidateQueries({ queryKey: ADMIN_METADATA_KEY })
      queryClient.invalidateQueries({ queryKey: [...ADMIN_METADATA_KEY, metaId] })
      queryClient.invalidateQueries({ queryKey: ADMIN_STATS_KEY })
    },
  })
}

export function useDeleteMetadata() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (metaId: number) => adminApi.deleteMetadata(metaId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ADMIN_METADATA_KEY })
      queryClient.invalidateQueries({ queryKey: ADMIN_STATS_KEY })
      queryClient.invalidateQueries({ queryKey: ADMIN_TORRENT_STREAMS_KEY })
      queryClient.invalidateQueries({ queryKey: ADMIN_TV_STREAMS_KEY })
    },
  })
}

// ============================================
// Torrent Streams Hooks
// ============================================

export function useTorrentStreamList(params: TorrentStreamListParams = {}) {
  return useQuery({
    queryKey: [...ADMIN_TORRENT_STREAMS_KEY, 'list', params],
    queryFn: () => adminApi.listTorrentStreams(params),
  })
}

export function useTorrentStream(streamId: string | undefined) {
  return useQuery({
    queryKey: [...ADMIN_TORRENT_STREAMS_KEY, streamId],
    queryFn: () => adminApi.getTorrentStream(streamId!),
    enabled: !!streamId,
  })
}

export function useUpdateTorrentStream() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ streamId, data }: { streamId: string; data: TorrentStreamUpdateRequest }) =>
      adminApi.updateTorrentStream(streamId, data),
    onSuccess: (_, { streamId }) => {
      queryClient.invalidateQueries({ queryKey: ADMIN_TORRENT_STREAMS_KEY })
      queryClient.invalidateQueries({ queryKey: [...ADMIN_TORRENT_STREAMS_KEY, streamId] })
    },
  })
}

export function useBlockTorrentStream() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (streamId: number) => adminApi.blockTorrentStream(streamId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ADMIN_TORRENT_STREAMS_KEY })
    },
  })
}

export function useUnblockTorrentStream() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (streamId: number) => adminApi.unblockTorrentStream(streamId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ADMIN_TORRENT_STREAMS_KEY })
    },
  })
}

// ============================================
// TV Streams Hooks
// ============================================

export function useTVStreamList(params: TVStreamListParams = {}) {
  return useQuery({
    queryKey: [...ADMIN_TV_STREAMS_KEY, 'list', params],
    queryFn: () => adminApi.listTVStreams(params),
  })
}

export function useTVStream(streamId: number | undefined) {
  return useQuery({
    queryKey: [...ADMIN_TV_STREAMS_KEY, streamId],
    queryFn: () => adminApi.getTVStream(streamId!),
    enabled: streamId !== undefined,
  })
}

export function useUpdateTVStream() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ streamId, data }: { streamId: number; data: TVStreamUpdateRequest }) =>
      adminApi.updateTVStream(streamId, data),
    onSuccess: (_, { streamId }) => {
      queryClient.invalidateQueries({ queryKey: ADMIN_TV_STREAMS_KEY })
      queryClient.invalidateQueries({ queryKey: [...ADMIN_TV_STREAMS_KEY, streamId] })
    },
  })
}

export function useToggleTVStreamActive() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (streamId: number) => adminApi.toggleTVStreamActive(streamId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ADMIN_TV_STREAMS_KEY })
    },
  })
}

// Alias for backward compatibility
export const useToggleTVStreamWorking = useToggleTVStreamActive

// ============================================
// Filter Options Hooks
// ============================================

export function useTorrentSources() {
  return useQuery({
    queryKey: ADMIN_SOURCES_TORRENT_KEY,
    queryFn: () => adminApi.getTorrentSources(),
    staleTime: 5 * 60 * 1000, // 5 minutes
  })
}

export function useTVSources() {
  return useQuery({
    queryKey: ADMIN_SOURCES_TV_KEY,
    queryFn: () => adminApi.getTVSources(),
    staleTime: 5 * 60 * 1000, // 5 minutes
  })
}

export function useCountries() {
  return useQuery({
    queryKey: ADMIN_COUNTRIES_KEY,
    queryFn: () => adminApi.getCountries(),
    staleTime: 5 * 60 * 1000, // 5 minutes
  })
}

export function useResolutions() {
  return useQuery({
    queryKey: ADMIN_RESOLUTIONS_KEY,
    queryFn: () => adminApi.getResolutions(),
    staleTime: 5 * 60 * 1000, // 5 minutes
  })
}

// ============================================
// Reference Data Hook Factory
// ============================================

const DEFAULT_PER_PAGE = 50

interface UseReferenceDataOptions {
  enabled?: boolean
  perPage?: number
}

// Generic hook for paginated reference data with search
function createReferenceDataHook(
  queryKey: readonly string[],
  fetchFn: (params: ReferenceListParams) => ReturnType<typeof adminApi.listGenres>,
) {
  return function useReferenceData(params: ReferenceListParams = {}, options: UseReferenceDataOptions = {}) {
    const { enabled = true, perPage = DEFAULT_PER_PAGE } = options
    const queryParams = { ...params, per_page: params.per_page ?? perPage }

    return useQuery({
      queryKey: [...queryKey, queryParams],
      queryFn: () => fetchFn(queryParams),
      staleTime: 60 * 1000, // 1 minute
      enabled,
    })
  }
}

// Generic hook for infinite loading reference data
function createInfiniteReferenceDataHook(
  queryKey: readonly string[],
  fetchFn: (params: ReferenceListParams) => ReturnType<typeof adminApi.listGenres>,
) {
  return function useInfiniteReferenceData(search?: string, options: UseReferenceDataOptions = {}) {
    const { enabled = true, perPage = DEFAULT_PER_PAGE } = options

    return useInfiniteQuery({
      queryKey: [...queryKey, 'infinite', { search }],
      queryFn: ({ pageParam = 1 }) =>
        fetchFn({
          search,
          page: pageParam,
          per_page: perPage,
        }),
      initialPageParam: 1,
      getNextPageParam: (lastPage) => {
        if (lastPage.page < lastPage.pages) {
          return lastPage.page + 1
        }
        return undefined
      },
      staleTime: 60 * 1000,
      enabled,
    })
  }
}

// ============================================
// Reference Data Hooks - Genres
// ============================================

export const useGenres = createReferenceDataHook(ADMIN_GENRES_KEY, adminApi.listGenres)
export const useInfiniteGenres = createInfiniteReferenceDataHook(ADMIN_GENRES_KEY, adminApi.listGenres)

export function useCreateGenre() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: ReferenceItemCreate) => adminApi.createGenre(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ADMIN_GENRES_KEY })
    },
  })
}

export function useDeleteGenre() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (genreId: number) => adminApi.deleteGenre(genreId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ADMIN_GENRES_KEY })
    },
  })
}

// ============================================
// Reference Data Hooks - Catalogs
// ============================================

export const useCatalogs = createReferenceDataHook(ADMIN_CATALOGS_KEY, adminApi.listCatalogs)
export const useInfiniteCatalogs = createInfiniteReferenceDataHook(ADMIN_CATALOGS_KEY, adminApi.listCatalogs)

export function useCreateCatalog() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: ReferenceItemCreate) => adminApi.createCatalog(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ADMIN_CATALOGS_KEY })
    },
  })
}

export function useDeleteCatalog() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (catalogId: number) => adminApi.deleteCatalog(catalogId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ADMIN_CATALOGS_KEY })
    },
  })
}

// ============================================
// Reference Data Hooks - Languages
// ============================================

export const useLanguages = createReferenceDataHook(ADMIN_LANGUAGES_KEY, adminApi.listLanguages)
export const useInfiniteLanguages = createInfiniteReferenceDataHook(ADMIN_LANGUAGES_KEY, adminApi.listLanguages)

export function useCreateLanguage() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: ReferenceItemCreate) => adminApi.createLanguage(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ADMIN_LANGUAGES_KEY })
    },
  })
}

export function useDeleteLanguage() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (languageId: number) => adminApi.deleteLanguage(languageId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ADMIN_LANGUAGES_KEY })
    },
  })
}

// ============================================
// Reference Data Hooks - Stars
// ============================================

export const useStars = createReferenceDataHook(ADMIN_STARS_KEY, adminApi.listStars)
export const useInfiniteStars = createInfiniteReferenceDataHook(ADMIN_STARS_KEY, adminApi.listStars)

export function useCreateStar() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: ReferenceItemCreate) => adminApi.createStar(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ADMIN_STARS_KEY })
    },
  })
}

export function useDeleteStar() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (starId: number) => adminApi.deleteStar(starId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ADMIN_STARS_KEY })
    },
  })
}

// ============================================
// Reference Data Hooks - Parental Certificates
// ============================================

export const useParentalCertificates = createReferenceDataHook(
  ADMIN_PARENTAL_CERTS_KEY,
  adminApi.listParentalCertificates,
)
export const useInfiniteParentalCertificates = createInfiniteReferenceDataHook(
  ADMIN_PARENTAL_CERTS_KEY,
  adminApi.listParentalCertificates,
)

export function useCreateParentalCertificate() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: ReferenceItemCreate) => adminApi.createParentalCertificate(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ADMIN_PARENTAL_CERTS_KEY })
    },
  })
}

export function useDeleteParentalCertificate() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (certId: number) => adminApi.deleteParentalCertificate(certId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ADMIN_PARENTAL_CERTS_KEY })
    },
  })
}

// ============================================
// Reference Data Hooks - Namespaces
// ============================================

export const useNamespaces = createReferenceDataHook(ADMIN_NAMESPACES_KEY, adminApi.listNamespaces)
export const useInfiniteNamespaces = createInfiniteReferenceDataHook(ADMIN_NAMESPACES_KEY, adminApi.listNamespaces)

export function useCreateNamespace() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: ReferenceItemCreate) => adminApi.createNamespace(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ADMIN_NAMESPACES_KEY })
    },
  })
}

export function useDeleteNamespace() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (namespaceId: number) => adminApi.deleteNamespace(namespaceId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ADMIN_NAMESPACES_KEY })
    },
  })
}

// ============================================
// Reference Data Hooks - Announce URLs
// ============================================

export const useAnnounceUrls = createReferenceDataHook(ADMIN_ANNOUNCE_URLS_KEY, adminApi.listAnnounceUrls)
export const useInfiniteAnnounceUrls = createInfiniteReferenceDataHook(
  ADMIN_ANNOUNCE_URLS_KEY,
  adminApi.listAnnounceUrls,
)

export function useCreateAnnounceUrl() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: ReferenceItemCreate) => adminApi.createAnnounceUrl(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ADMIN_ANNOUNCE_URLS_KEY })
    },
  })
}

export function useDeleteAnnounceUrl() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (urlId: number) => adminApi.deleteAnnounceUrl(urlId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ADMIN_ANNOUNCE_URLS_KEY })
    },
  })
}
