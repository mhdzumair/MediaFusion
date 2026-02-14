import { useQuery, useMutation, useQueryClient, useInfiniteQuery } from '@tanstack/react-query'
import { 
  contributionsApi, 
  type ContributionListParams,
  type ContributionCreateRequest,
  type ContributionReviewRequest,
  type ContributionType,
} from '@/lib/api'

const CONTRIBUTIONS_QUERY_KEY = ['contributions']

export function useContributions(params: ContributionListParams = {}) {
  return useQuery({
    queryKey: [...CONTRIBUTIONS_QUERY_KEY, params],
    queryFn: () => contributionsApi.list(params),
  })
}

export function useInfiniteContributions(params: Omit<ContributionListParams, 'page'> = {}) {
  return useInfiniteQuery({
    queryKey: [...CONTRIBUTIONS_QUERY_KEY, 'infinite', params],
    queryFn: ({ pageParam = 1 }) => contributionsApi.list({ ...params, page: pageParam }),
    getNextPageParam: (lastPage) => lastPage.has_more ? lastPage.page + 1 : undefined,
    initialPageParam: 1,
  })
}

export function useContribution(contributionId: string | undefined) {
  return useQuery({
    queryKey: [...CONTRIBUTIONS_QUERY_KEY, contributionId],
    queryFn: () => contributionsApi.get(contributionId!),
    enabled: !!contributionId,
  })
}

export function useContributionStats() {
  return useQuery({
    queryKey: [...CONTRIBUTIONS_QUERY_KEY, 'stats'],
    queryFn: () => contributionsApi.getStats(),
  })
}

export function useCreateContribution() {
  const queryClient = useQueryClient()
  
  return useMutation({
    mutationFn: (data: ContributionCreateRequest) => contributionsApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: CONTRIBUTIONS_QUERY_KEY })
    },
  })
}

export function useDeleteContribution() {
  const queryClient = useQueryClient()
  
  return useMutation({
    mutationFn: (contributionId: string) => contributionsApi.delete(contributionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: CONTRIBUTIONS_QUERY_KEY })
    },
  })
}

// Moderator hooks

export function usePendingContributions(params: { contribution_type?: ContributionType; page?: number; page_size?: number } = {}) {
  return useQuery({
    queryKey: [...CONTRIBUTIONS_QUERY_KEY, 'pending', params],
    queryFn: () => contributionsApi.listPending(params),
  })
}

export function useReviewContribution() {
  const queryClient = useQueryClient()
  
  return useMutation({
    mutationFn: ({ contributionId, data }: { contributionId: string; data: ContributionReviewRequest }) =>
      contributionsApi.review(contributionId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: CONTRIBUTIONS_QUERY_KEY })
    },
  })
}

export function useAllContributionStats() {
  return useQuery({
    queryKey: [...CONTRIBUTIONS_QUERY_KEY, 'all-stats'],
    queryFn: () => contributionsApi.getAllStats(),
  })
}

