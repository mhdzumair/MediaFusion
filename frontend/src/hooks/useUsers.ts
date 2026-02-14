import { useQuery, useMutation, useQueryClient, useInfiniteQuery } from '@tanstack/react-query'
import { usersApi, type UserListParams, type UserUpdateRequest, type RoleUpdateRequest } from '@/lib/api'

const USERS_QUERY_KEY = ['users']

export function useUsers(params: UserListParams = {}) {
  return useQuery({
    queryKey: [...USERS_QUERY_KEY, params],
    queryFn: () => usersApi.list(params),
  })
}

export function useInfiniteUsers(params: Omit<UserListParams, 'page'> = {}) {
  return useInfiniteQuery({
    queryKey: [...USERS_QUERY_KEY, 'infinite', params],
    queryFn: ({ pageParam = 1 }) => usersApi.list({ ...params, page: pageParam }),
    getNextPageParam: (lastPage) => (lastPage.page < lastPage.pages ? lastPage.page + 1 : undefined),
    initialPageParam: 1,
  })
}

export function useUser(userId: string | undefined) {
  return useQuery({
    queryKey: [...USERS_QUERY_KEY, userId],
    queryFn: () => usersApi.get(userId!),
    enabled: !!userId,
  })
}

export function useUpdateUser() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ userId, data }: { userId: string; data: UserUpdateRequest }) => usersApi.update(userId, data),
    onSuccess: (_, { userId }) => {
      queryClient.invalidateQueries({ queryKey: USERS_QUERY_KEY })
      queryClient.invalidateQueries({ queryKey: [...USERS_QUERY_KEY, userId] })
    },
  })
}

export function useUpdateUserRole() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ userId, data }: { userId: string; data: RoleUpdateRequest }) => usersApi.updateRole(userId, data),
    onSuccess: (_, { userId }) => {
      queryClient.invalidateQueries({ queryKey: USERS_QUERY_KEY })
      queryClient.invalidateQueries({ queryKey: [...USERS_QUERY_KEY, userId] })
    },
  })
}

export function useDeleteUser() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (userId: string) => usersApi.delete(userId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: USERS_QUERY_KEY })
    },
  })
}
