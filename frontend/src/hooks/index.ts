// Utility hooks
export { useDebounce } from './useDebounce'
export { useToast, toast } from './use-toast'

// Auth hooks
export { useAuth } from './useAuth'
export { useRole } from './useRole'
export { usePermission } from './usePermission'

// Profile hooks
export {
  useProfiles,
  useProfile,
  useCreateProfile,
  useUpdateProfile,
  useDeleteProfile,
  useDefaultProfile,
  useSetDefaultProfile,
  useManifestUrl,
  useRpdbApiKey,
  profileKeys,
} from './useProfiles'

// Watch History hooks (includes downloads - unified with action field)
export {
  useWatchHistory,
  useInfiniteWatchHistory,
  useContinueWatching,
  useCreateWatchHistory,
  useUpdateWatchProgress,
  useDeleteWatchHistory,
  useClearWatchHistory,
  useTrackStreamAction,
} from './useWatchHistory'

// Contributions hooks
export {
  useContributions,
  useInfiniteContributions,
  useContribution,
  useContributionStats,
  useCreateContribution,
  useDeleteContribution,
  usePendingContributions,
  useReviewContribution,
  useAllContributionStats,
} from './useContributions'

// RSS Feeds hooks
export {
  useRssFeeds,
  useRssFeed,
  useCreateRssFeed,
  useUpdateRssFeed,
  useDeleteRssFeed,
  useTestRssFeed,
  useTestRssFeedUrl,
  useScrapeRssFeed,
  useRunRssScraper,
  useBulkUpdateRssFeedStatus,
  useRssSchedulerStatus,
} from './useRssFeeds'

// Metrics hooks
export {
  useTorrentCount,
  useTorrentSources,
  useMetadataCount,
  useScrapySchedulers,
  useRedisMetrics,
  useDebridCacheMetrics,
  useWorkerMemoryMetrics,
  useTorrentUploaders,
  useWeeklyUploaders,
  useUserStats,
  useContributionMetrics,
  useActivityStats,
  useSystemOverview,
  useScraperMetrics,
  useScraperAggregatedStats,
  useScraperHistory,
  useScraperLatestMetrics,
  useDashboardMetrics,
} from './useMetrics'

// Users hooks (Admin)
export { useUsers, useInfiniteUsers, useUser, useUpdateUser, useUpdateUserRole, useDeleteUser } from './useUsers'

// Content Import hooks
export {
  useAnalyzeMagnet,
  useAnalyzeTorrent,
  useAnalyzeM3U,
  useImportMagnet,
  useImportTorrent,
  useImportM3U,
  useAnalyzeXtream,
  useImportXtream,
  useIPTVImportSettings,
  useIPTVSources,
  useIPTVSource,
  useUpdateIPTVSource,
  useDeleteIPTVSource,
  useSyncIPTVSource,
  useImportJobStatus,
  useAnalyzeNZBFile,
  useAnalyzeNZBUrl,
  useImportNZBFile,
  useImportNZBUrl,
} from './useContentImport'

// Catalog hooks
export {
  useAvailableCatalogs,
  useGenres,
  useCatalogList,
  useInfiniteCatalog,
  useCatalogItem,
  useCatalogStreams,
  catalogKeys,
  type CatalogType,
  type SortOption,
  type SortDirection,
  type CatalogListParams,
} from './useCatalog'

// Library hooks
export {
  useLibrary,
  useInfiniteLibrary,
  useLibraryStats,
  useLibraryItem,
  useLibraryCheck,
  useAddToLibrary,
  useRemoveFromLibrary,
  useRemoveFromLibraryByMediaId,
  libraryKeys,
  type LibraryListParams,
  type LibraryItemCreate,
} from './useLibrary'

// Voting hooks
export {
  useStreamVotes,
  useBulkStreamVotes,
  useVoteOnStream,
  useRemoveStreamVote,
  useContentLikes,
  useLikeContent,
  useUnlikeContent,
  votingKeys,
} from './useVoting'

// Suggestions hooks
export {
  useSuggestions,
  useSuggestion,
  usePendingSuggestions,
  useSuggestionStats,
  useCreateSuggestion,
  useDeleteSuggestion,
  useReviewSuggestion,
  suggestionKeys,
} from './useSuggestions'

// Stream Suggestions hooks
export {
  useStreamSuggestions,
  useMyStreamSuggestions,
  usePendingStreamSuggestions,
  useStreamSuggestionStats,
  useCreateStreamSuggestion,
  useReviewStreamSuggestion,
  useDeleteStreamSuggestion,
  streamSuggestionKeys,
} from './useStreamSuggestions'

// Episode Suggestions hooks
export {
  useEpisodeSuggestions,
  useEpisodeSuggestion,
  usePendingEpisodeSuggestions,
  useEpisodeSuggestionStats,
  useCreateEpisodeSuggestion,
  useDeleteEpisodeSuggestion,
  useReviewEpisodeSuggestion,
  useBulkReviewEpisodeSuggestions,
  episodeSuggestionKeys,
} from './useEpisodeSuggestions'

// Contribution Settings hooks (Admin)
export {
  useContributionSettings,
  useContributionLevels,
  useUpdateContributionSettings,
  useResetContributionSettings,
  contributionSettingsKeys,
} from './useContributionSettings'

// Scheduler Management hooks (Admin)
export {
  useSchedulerJobs,
  useSchedulerStats,
  useSchedulerJob,
  useSchedulerJobHistory,
  useRunSchedulerJob,
  useRunSchedulerJobInline,
  schedulerKeys,
} from './useScheduler'

// Admin Database Management hooks
export {
  useAdminStats,
  useMetadataList,
  useMetadata,
  useUpdateMetadata,
  useDeleteMetadata,
  useTorrentStreamList,
  useTorrentStream,
  useUpdateTorrentStream,
  useBlockTorrentStream,
  useUnblockTorrentStream,
  useTVStreamList,
  useTVStream,
  useUpdateTVStream,
  useToggleTVStreamWorking,
  useTorrentSources as useAdminTorrentSources,
  useTVSources,
  useCountries,
  useResolutions,
  // Reference Data hooks (paginated)
  useGenres as useAdminGenres,
  useInfiniteGenres,
  useCreateGenre,
  useDeleteGenre,
  useCatalogs,
  useInfiniteCatalogs,
  useCreateCatalog,
  useDeleteCatalog,
  useLanguages,
  useInfiniteLanguages,
  useCreateLanguage,
  useDeleteLanguage,
  useStars,
  useInfiniteStars,
  useCreateStar,
  useDeleteStar,
  useParentalCertificates,
  useInfiniteParentalCertificates,
  useCreateParentalCertificate,
  useDeleteParentalCertificate,
  useNamespaces,
  useInfiniteNamespaces,
  useCreateNamespace,
  useDeleteNamespace,
  useAnnounceUrls,
  useInfiniteAnnounceUrls,
  useCreateAnnounceUrl,
  useDeleteAnnounceUrl,
} from './useAdmin'

// Stream hooks
export { useDeleteStream } from './useStreams'

// File Links hooks (for annotation)
export { useStreamsNeedingAnnotation, useStreamFileLinks, useUpdateFileLinks, fileLinksKeys } from './useFileLinks'

// User Metadata hooks
export {
  useUserMetadataList,
  useUserMetadata,
  useCreateUserMetadata,
  useUpdateUserMetadata,
  useDeleteUserMetadata,
  useAddSeason,
  useAddEpisodes,
  useUpdateEpisode,
  useDeleteEpisode,
  useDeleteSeason,
  useDeleteEpisodeAdmin,
  useDeleteSeasonAdmin,
  useMetadataSearch,
  userMetadataKeys,
} from './useUserMetadata'

// Watchlist hooks (Debrid downloads)
export {
  useWatchlistProviders,
  useWatchlist,
  useInfiniteWatchlist,
  useMissingTorrents,
  useImportTorrents,
  useRemoveTorrent,
  useClearAllTorrents,
  useAdvancedImport,
  watchlistKeys,
} from './useWatchlist'

// Combined Metadata Search hook (searches both internal DB and external providers)
export {
  useCombinedMetadataSearch,
  getBestExternalId,
  combinedSearchKeys,
  type CombinedSearchResult,
  type UseCombinedSearchOptions,
} from './useCombinedMetadataSearch'

// Exception Tracking hooks (Admin)
export {
  useExceptionStatus,
  useExceptionList,
  useExceptionDetail,
  useClearException,
  useClearAllExceptions,
  exceptionKeys,
} from './useExceptions'
