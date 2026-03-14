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
  useResetProfileUuid,
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
  useContributionContributors,
  useInfiniteContributions,
  useContribution,
  useContributionStats,
  useCreateContribution,
  useDeleteContribution,
  usePendingContributions,
  useReviewContribution,
  useFlagContributionForAdminReview,
  useRejectApprovedContribution,
  useBulkReviewContributions,
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
  usePublicIndexerSourceHealth,
  useScraperMetrics,
  useScraperAggregatedStats,
  useScraperHistory,
  useScraperSearchRuns,
  useScraperLatestMetrics,
  useDashboardMetrics,
} from './useMetrics'

// Users hooks (Admin)
export {
  useUsers,
  useInfiniteUsers,
  useUser,
  useUpdateUser,
  useUpdateUserRole,
  useDeleteUser,
  useSendUploadWarning,
} from './useUsers'

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

// Metadata reference hooks (non-admin)
export {
  useMetadataReferenceGenres,
  useInfiniteMetadataReferenceGenres,
  useMetadataReferenceCatalogs,
  useInfiniteMetadataReferenceCatalogs,
  useMetadataReferenceStars,
  useInfiniteMetadataReferenceStars,
  useMetadataReferenceParentalCertificates,
  useInfiniteMetadataReferenceParentalCertificates,
} from './useMetadataReference'

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
  useBulkReviewStreamSuggestions,
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
  useDmmHashlistStatus,
  useRunSchedulerJob,
  useRunSchedulerJobInline,
  useRunDmmHashlistFull,
  schedulerKeys,
} from './useScheduler'
export {
  useTaskOverview,
  useTaskList,
  useTaskDetail,
  useCancelTask,
  useRetryTask,
  useBulkCancelTasks,
  useBulkRetryTasks,
  useTaskStreamUpdates,
  taskManagementKeys,
} from './useTaskManagement'

// Admin Database Management hooks
export { useDeleteMetadata, useBlockTorrentStream } from './useAdmin'

// Stream hooks
export { useDeleteStream } from './useStreams'

// File Links hooks (for annotation)
export {
  useStreamsNeedingAnnotation,
  useStreamFileLinks,
  useUpdateFileLinks,
  useDismissAnnotationRequest,
  fileLinksKeys,
} from './useFileLinks'

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
