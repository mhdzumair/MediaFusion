import { useState } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import {
  Users,
  Search,
  Filter,
  MoreVertical,
  Shield,
  ShieldCheck,
  ShieldAlert,
  User as UserIcon,
  Mail,
  Calendar,
  Ban,
  CheckCircle,
  XCircle,
  Trash2,
  ChevronLeft,
  ChevronRight,
  Trophy,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
} from 'lucide-react'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  toast,
  useDeleteUser,
  useSendUploadWarning,
  useUpdateUser,
  useUpdateUserRole,
  useUsers,
  useUserStats,
} from '@/hooks'
import type { UserRole } from '@/types'

const roleConfig: Record<UserRole, { label: string; icon: typeof Shield; color: string }> = {
  admin: { label: 'Admin', icon: ShieldAlert, color: 'text-red-500' },
  moderator: { label: 'Moderator', icon: ShieldCheck, color: 'text-primary' },
  paid_user: { label: 'Premium', icon: Shield, color: 'text-primary' },
  user: { label: 'User', icon: UserIcon, color: 'text-muted-foreground' },
}

const allRoles: UserRole[] = ['admin', 'moderator', 'paid_user', 'user']
type SortField = 'user' | 'role' | 'contribution' | 'status' | 'joined'
type SortDirection = 'asc' | 'desc'

export function UserManagementPage() {
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [roleFilter, setRoleFilter] = useState<UserRole | undefined>()
  const [sortField, setSortField] = useState<SortField>('joined')
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc')
  const [editUserId, setEditUserId] = useState<string | null>(null)
  const [roleDialogUser, setRoleDialogUser] = useState<{ id: string; role: UserRole } | null>(null)
  const [warningEmailDialogUserId, setWarningEmailDialogUserId] = useState<string | null>(null)
  const [warningEmailReason, setWarningEmailReason] = useState('')
  const [deleteUserId, setDeleteUserId] = useState<string | null>(null)

  const { data: usersData, isLoading } = useUsers({
    page,
    per_page: 20,
    search: search || undefined,
    role: roleFilter,
  })
  const { data: userStats } = useUserStats()
  const updateUser = useUpdateUser()
  const updateRole = useUpdateUserRole()
  const deleteUser = useDeleteUser()
  const sendUploadWarning = useSendUploadWarning()

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault()
    setPage(1) // Reset to first page on new search
  }

  const handleToggleActive = async (userId: string, currentActive: boolean) => {
    await updateUser.mutateAsync({ userId, data: { is_active: !currentActive } })
  }

  const handleToggleVerified = async (userId: string, currentVerified: boolean) => {
    await updateUser.mutateAsync({ userId, data: { is_verified: !currentVerified } })
  }

  const handleToggleUploadRestriction = async (userId: string, isRestricted: boolean | undefined) => {
    await updateUser.mutateAsync({
      userId,
      data: { uploads_restricted: !isRestricted },
    })
  }

  const handleSendUploadWarning = async () => {
    if (!warningEmailDialogUserId) return
    try {
      const response = await sendUploadWarning.mutateAsync({
        userId: warningEmailDialogUserId,
        data: {
          reason: warningEmailReason.trim() || undefined,
        },
      })
      toast({
        title: 'Warning email sent',
        description: response.message,
      })
      setWarningEmailDialogUserId(null)
      setWarningEmailReason('')
    } catch {
      toast({
        title: 'Failed to send warning email',
        description: 'Please verify SMTP configuration and try again.',
        variant: 'destructive',
      })
    }
  }

  const handleUpdateRole = async () => {
    if (!roleDialogUser) return
    await updateRole.mutateAsync({ userId: roleDialogUser.id, data: { role: roleDialogUser.role } })
    setRoleDialogUser(null)
  }

  const handleDeleteUser = async () => {
    if (!deleteUserId) return
    await deleteUser.mutateAsync(deleteUserId)
    setDeleteUserId(null)
  }

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDirection((prev) => (prev === 'asc' ? 'desc' : 'asc'))
      return
    }

    setSortField(field)
    setSortDirection(field === 'contribution' || field === 'joined' ? 'desc' : 'asc')
  }

  const sortedUsers = (() => {
    if (!usersData?.items) return []

    const roleSortOrder: Record<UserRole, number> = {
      admin: 4,
      moderator: 3,
      paid_user: 2,
      user: 1,
    }

    const sorted = [...usersData.items]
    sorted.sort((a, b) => {
      let comparison = 0

      switch (sortField) {
        case 'user': {
          const aName = (a.username || a.email || '').toLowerCase()
          const bName = (b.username || b.email || '').toLowerCase()
          comparison = aName.localeCompare(bName)
          break
        }
        case 'role':
          comparison = roleSortOrder[a.role] - roleSortOrder[b.role]
          break
        case 'contribution':
          comparison = (a.contribution_points ?? 0) - (b.contribution_points ?? 0)
          break
        case 'status': {
          const aStatusRank = (a.is_active ? 2 : 0) + (a.is_verified ? 1 : 0)
          const bStatusRank = (b.is_active ? 2 : 0) + (b.is_verified ? 1 : 0)
          comparison = aStatusRank - bStatusRank
          break
        }
        case 'joined':
          comparison = new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
          break
      }

      return sortDirection === 'asc' ? comparison : -comparison
    })

    return sorted
  })()

  const selectedUser = usersData?.items.find((u) => u.id === editUserId)
  const warningEmailUser = usersData?.items.find((u) => u.id === warningEmailDialogUserId)

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
            <div className="p-2 rounded-xl bg-gradient-to-br from-primary to-primary/80 shadow-lg shadow-primary/20">
              <Users className="h-5 w-5 text-white" />
            </div>
            User Management
          </h1>
          <p className="text-muted-foreground mt-1">Manage users, roles, and permissions</p>
        </div>
      </div>

      {/* Stats */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-primary/10">
                <Users className="h-4 w-4 text-primary" />
              </div>
              <div>
                <p className="text-2xl font-bold">{userStats?.total_users ?? usersData?.total ?? 0}</p>
                <p className="text-xs text-muted-foreground">Total Users</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-red-500/10">
                <ShieldAlert className="h-4 w-4 text-red-500" />
              </div>
              <div>
                <p className="text-2xl font-bold">{userStats?.users_by_role?.admin ?? 0}</p>
                <p className="text-xs text-muted-foreground">Admins</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-primary/10">
                <ShieldCheck className="h-4 w-4 text-primary" />
              </div>
              <div>
                <p className="text-2xl font-bold">{userStats?.users_by_role?.moderator ?? 0}</p>
                <p className="text-xs text-muted-foreground">Moderators</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-emerald-500/10">
                <CheckCircle className="h-4 w-4 text-emerald-500" />
              </div>
              <div>
                <p className="text-2xl font-bold">{userStats?.verified_users ?? 0}</p>
                <p className="text-xs text-muted-foreground">Verified</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Search and Filters */}
      <Card className="glass border-border/50">
        <CardContent className="p-4">
          <div className="flex flex-col md:flex-row gap-4">
            <form onSubmit={handleSearch} className="flex-1 flex gap-2">
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder="Search by email or username..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="pl-10 rounded-xl"
                />
              </div>
              <Button type="submit" variant="outline" className="rounded-xl">
                Search
              </Button>
            </form>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" className="rounded-xl">
                  <Filter className="mr-2 h-4 w-4" />
                  {roleFilter ? roleConfig[roleFilter].label : 'All Roles'}
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={() => setRoleFilter(undefined)}>All Roles</DropdownMenuItem>
                <DropdownMenuSeparator />
                {allRoles.map((role) => (
                  <DropdownMenuItem key={role} onClick={() => setRoleFilter(role)}>
                    {roleConfig[role].label}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </CardContent>
      </Card>

      {/* Users Table */}
      <Card className="glass border-border/50">
        <CardHeader>
          <CardTitle>Users</CardTitle>
          <CardDescription>View and manage all registered users</CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-4">
              {[...Array(5)].map((_, i) => (
                <div key={i} className="flex items-center gap-4">
                  <Skeleton className="h-10 w-10 rounded-full" />
                  <div className="flex-1 space-y-2">
                    <Skeleton className="h-4 w-1/4" />
                    <Skeleton className="h-3 w-1/3" />
                  </div>
                  <Skeleton className="h-6 w-20" />
                </div>
              ))}
            </div>
          ) : usersData?.items.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground">
              <Users className="h-12 w-12 mx-auto mb-4 opacity-50" />
              <p>No users found.</p>
              {search && <p className="text-sm mt-2">Try adjusting your search or filters.</p>}
            </div>
          ) : (
            <>
              <div className="rounded-xl border border-border/50 overflow-hidden">
                <Table>
                  <TableHeader>
                    <TableRow className="hover:bg-transparent">
                      <TableHead>
                        <button
                          type="button"
                          className="inline-flex items-center gap-1 hover:text-foreground transition-colors"
                          onClick={() => handleSort('user')}
                        >
                          User
                          {sortField === 'user' ? (
                            sortDirection === 'asc' ? (
                              <ArrowUp className="h-3.5 w-3.5" />
                            ) : (
                              <ArrowDown className="h-3.5 w-3.5" />
                            )
                          ) : (
                            <ArrowUpDown className="h-3.5 w-3.5 opacity-60" />
                          )}
                        </button>
                      </TableHead>
                      <TableHead>
                        <button
                          type="button"
                          className="inline-flex items-center gap-1 hover:text-foreground transition-colors"
                          onClick={() => handleSort('role')}
                        >
                          Role
                          {sortField === 'role' ? (
                            sortDirection === 'asc' ? (
                              <ArrowUp className="h-3.5 w-3.5" />
                            ) : (
                              <ArrowDown className="h-3.5 w-3.5" />
                            )
                          ) : (
                            <ArrowUpDown className="h-3.5 w-3.5 opacity-60" />
                          )}
                        </button>
                      </TableHead>
                      <TableHead>
                        <button
                          type="button"
                          className="inline-flex items-center gap-1 hover:text-foreground transition-colors"
                          onClick={() => handleSort('contribution')}
                        >
                          Contribution
                          {sortField === 'contribution' ? (
                            sortDirection === 'asc' ? (
                              <ArrowUp className="h-3.5 w-3.5" />
                            ) : (
                              <ArrowDown className="h-3.5 w-3.5" />
                            )
                          ) : (
                            <ArrowUpDown className="h-3.5 w-3.5 opacity-60" />
                          )}
                        </button>
                      </TableHead>
                      <TableHead>
                        <button
                          type="button"
                          className="inline-flex items-center gap-1 hover:text-foreground transition-colors"
                          onClick={() => handleSort('status')}
                        >
                          Status
                          {sortField === 'status' ? (
                            sortDirection === 'asc' ? (
                              <ArrowUp className="h-3.5 w-3.5" />
                            ) : (
                              <ArrowDown className="h-3.5 w-3.5" />
                            )
                          ) : (
                            <ArrowUpDown className="h-3.5 w-3.5 opacity-60" />
                          )}
                        </button>
                      </TableHead>
                      <TableHead>
                        <button
                          type="button"
                          className="inline-flex items-center gap-1 hover:text-foreground transition-colors"
                          onClick={() => handleSort('joined')}
                        >
                          Joined
                          {sortField === 'joined' ? (
                            sortDirection === 'asc' ? (
                              <ArrowUp className="h-3.5 w-3.5" />
                            ) : (
                              <ArrowDown className="h-3.5 w-3.5" />
                            )
                          ) : (
                            <ArrowUpDown className="h-3.5 w-3.5 opacity-60" />
                          )}
                        </button>
                      </TableHead>
                      <TableHead className="text-right">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {sortedUsers.map((user) => {
                      const role = roleConfig[user.role]
                      const RoleIcon = role?.icon ?? UserIcon

                      return (
                        <TableRow key={user.id}>
                          <TableCell>
                            <div className="flex items-center gap-3">
                              <div className="h-9 w-9 rounded-full bg-primary/10 flex items-center justify-center">
                                <UserIcon className="h-4 w-4 text-primary" />
                              </div>
                              <div>
                                <p className="font-medium">{user.username || 'No username'}</p>
                                <p className="text-sm text-muted-foreground">{user.email}</p>
                              </div>
                            </div>
                          </TableCell>
                          <TableCell>
                            <Badge variant="outline" className={role?.color}>
                              <RoleIcon className="mr-1 h-3 w-3" />
                              {role?.label}
                            </Badge>
                          </TableCell>
                          <TableCell>
                            <div className="flex items-center gap-2">
                              <Badge variant="secondary" className="font-mono">
                                <Trophy className="mr-1 h-3 w-3 text-primary" />
                                {user.contribution_points ?? 0}
                              </Badge>
                              {user.contribution_level && (
                                <Badge variant="outline" className="text-xs capitalize">
                                  {user.contribution_level}
                                </Badge>
                              )}
                            </div>
                          </TableCell>
                          <TableCell>
                            <div className="flex items-center gap-2">
                              {user.is_active ? (
                                <Badge variant="secondary" className="text-emerald-500 bg-emerald-500/10">
                                  Active
                                </Badge>
                              ) : (
                                <Badge variant="secondary" className="text-red-500 bg-red-500/10">
                                  Inactive
                                </Badge>
                              )}
                              {user.is_verified && <CheckCircle className="h-4 w-4 text-emerald-500" />}
                              {user.uploads_restricted && (
                                <Badge variant="secondary" className="text-amber-500 bg-amber-500/10">
                                  Uploads Restricted
                                </Badge>
                              )}
                            </div>
                          </TableCell>
                          <TableCell>
                            <span className="text-sm text-muted-foreground">
                              {new Date(user.created_at).toLocaleDateString()}
                            </span>
                          </TableCell>
                          <TableCell className="text-right">
                            <DropdownMenu>
                              <DropdownMenuTrigger asChild>
                                <Button variant="ghost" size="icon" className="h-8 w-8">
                                  <MoreVertical className="h-4 w-4" />
                                </Button>
                              </DropdownMenuTrigger>
                              <DropdownMenuContent align="end">
                                <DropdownMenuItem onClick={() => setEditUserId(user.id)}>
                                  <UserIcon className="mr-2 h-4 w-4" />
                                  View Details
                                </DropdownMenuItem>
                                <DropdownMenuItem onClick={() => setRoleDialogUser({ id: user.id, role: user.role })}>
                                  <Shield className="mr-2 h-4 w-4" />
                                  Change Role
                                </DropdownMenuItem>
                                <DropdownMenuSeparator />
                                <DropdownMenuItem onClick={() => handleToggleActive(user.id, user.is_active)}>
                                  {user.is_active ? (
                                    <>
                                      <Ban className="mr-2 h-4 w-4" />
                                      Deactivate
                                    </>
                                  ) : (
                                    <>
                                      <CheckCircle className="mr-2 h-4 w-4" />
                                      Activate
                                    </>
                                  )}
                                </DropdownMenuItem>
                                <DropdownMenuItem onClick={() => handleToggleVerified(user.id, user.is_verified)}>
                                  {user.is_verified ? (
                                    <>
                                      <XCircle className="mr-2 h-4 w-4" />
                                      Unverify
                                    </>
                                  ) : (
                                    <>
                                      <CheckCircle className="mr-2 h-4 w-4" />
                                      Verify
                                    </>
                                  )}
                                </DropdownMenuItem>
                                <DropdownMenuItem
                                  onClick={() => handleToggleUploadRestriction(user.id, user.uploads_restricted)}
                                >
                                  <Ban className="mr-2 h-4 w-4" />
                                  {user.uploads_restricted ? 'Allow Uploads' : 'Restrict Uploads'}
                                </DropdownMenuItem>
                                <DropdownMenuItem
                                  onClick={() => {
                                    setWarningEmailDialogUserId(user.id)
                                    setWarningEmailReason('')
                                  }}
                                  disabled={sendUploadWarning.isPending}
                                >
                                  <Mail className="mr-2 h-4 w-4" />
                                  Send Warning Email
                                </DropdownMenuItem>
                                <DropdownMenuSeparator />
                                <DropdownMenuItem className="text-destructive" onClick={() => setDeleteUserId(user.id)}>
                                  <Trash2 className="mr-2 h-4 w-4" />
                                  Delete User
                                </DropdownMenuItem>
                              </DropdownMenuContent>
                            </DropdownMenu>
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              </div>

              {/* Pagination */}
              {usersData && usersData.pages > 1 && (
                <div className="flex items-center justify-between mt-4">
                  <p className="text-sm text-muted-foreground">
                    Showing {(page - 1) * 20 + 1} to {Math.min(page * 20, usersData.total)} of {usersData.total} users
                  </p>
                  <div className="flex items-center gap-2">
                    <Button variant="outline" size="sm" disabled={page === 1} onClick={() => setPage((p) => p - 1)}>
                      <ChevronLeft className="h-4 w-4 mr-1" />
                      Previous
                    </Button>
                    <span className="text-sm px-3">
                      Page {page} of {usersData.pages}
                    </span>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={page >= usersData.pages}
                      onClick={() => setPage((p) => p + 1)}
                    >
                      Next
                      <ChevronRight className="h-4 w-4 ml-1" />
                    </Button>
                  </div>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      {/* User Details Dialog */}
      <Dialog open={!!editUserId} onOpenChange={() => setEditUserId(null)}>
        <DialogContent className="glass border-border/50 sm:max-w-[500px]">
          <DialogHeader>
            <DialogTitle>User Details</DialogTitle>
            <DialogDescription>View user information</DialogDescription>
          </DialogHeader>
          {selectedUser && (
            <div className="space-y-4 py-4">
              <div className="flex items-center gap-4">
                <div className="h-16 w-16 rounded-full bg-primary/10 flex items-center justify-center">
                  <UserIcon className="h-8 w-8 text-primary" />
                </div>
                <div>
                  <p className="text-lg font-medium">{selectedUser.username || 'No username'}</p>
                  <Badge variant="outline" className={roleConfig[selectedUser.role]?.color}>
                    {roleConfig[selectedUser.role]?.label}
                  </Badge>
                </div>
              </div>

              <div className="space-y-3">
                <div className="flex items-center gap-3 p-3 rounded-xl bg-muted/50">
                  <Mail className="h-4 w-4 text-muted-foreground" />
                  <div>
                    <p className="text-xs text-muted-foreground">Email</p>
                    <p className="font-medium">{selectedUser.email}</p>
                  </div>
                </div>
                <div className="flex items-center gap-3 p-3 rounded-xl bg-muted/50">
                  <Calendar className="h-4 w-4 text-muted-foreground" />
                  <div>
                    <p className="text-xs text-muted-foreground">Joined</p>
                    <p className="font-medium">
                      {new Date(selectedUser.created_at).toLocaleDateString('en-US', {
                        year: 'numeric',
                        month: 'long',
                        day: 'numeric',
                      })}
                    </p>
                  </div>
                </div>
                {selectedUser.last_login && (
                  <div className="flex items-center gap-3 p-3 rounded-xl bg-muted/50">
                    <Calendar className="h-4 w-4 text-muted-foreground" />
                    <div>
                      <p className="text-xs text-muted-foreground">Last Login</p>
                      <p className="font-medium">{new Date(selectedUser.last_login).toLocaleString()}</p>
                    </div>
                  </div>
                )}
                <div className="flex items-center gap-3 p-3 rounded-xl bg-primary/10">
                  <Trophy className="h-4 w-4 text-primary" />
                  <div className="flex-1">
                    <p className="text-xs text-muted-foreground">Contribution Points</p>
                    <p className="font-medium">{selectedUser.contribution_points ?? 0} points</p>
                  </div>
                  {selectedUser.contribution_level && (
                    <Badge variant="outline" className="capitalize">
                      {selectedUser.contribution_level}
                    </Badge>
                  )}
                </div>
              </div>

              <div className="flex gap-2">
                <Badge variant={selectedUser.is_active ? 'default' : 'secondary'}>
                  {selectedUser.is_active ? 'Active' : 'Inactive'}
                </Badge>
                <Badge variant={selectedUser.is_verified ? 'default' : 'secondary'}>
                  {selectedUser.is_verified ? 'Verified' : 'Unverified'}
                </Badge>
                <Badge variant={selectedUser.uploads_restricted ? 'secondary' : 'default'}>
                  {selectedUser.uploads_restricted ? 'Uploads Restricted' : 'Uploads Allowed'}
                </Badge>
              </div>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditUserId(null)} className="rounded-xl">
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Change Role Dialog */}
      <Dialog open={!!roleDialogUser} onOpenChange={() => setRoleDialogUser(null)}>
        <DialogContent className="glass border-border/50 sm:max-w-[400px]">
          <DialogHeader>
            <DialogTitle>Change User Role</DialogTitle>
            <DialogDescription>Select a new role for this user</DialogDescription>
          </DialogHeader>
          {roleDialogUser && (
            <div className="py-4">
              <Label htmlFor="role">New Role</Label>
              <Select
                value={roleDialogUser.role}
                onValueChange={(v) => setRoleDialogUser({ ...roleDialogUser, role: v as UserRole })}
              >
                <SelectTrigger className="mt-2 rounded-xl">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {allRoles.map((role) => (
                    <SelectItem key={role} value={role}>
                      <div className="flex items-center gap-2">{roleConfig[role].label}</div>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setRoleDialogUser(null)} className="rounded-xl">
              Cancel
            </Button>
            <Button
              onClick={handleUpdateRole}
              disabled={updateRole.isPending}
              className="rounded-xl bg-gradient-to-r from-primary to-primary/80"
            >
              {updateRole.isPending ? 'Updating...' : 'Update Role'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Send Warning Email Dialog */}
      <Dialog open={!!warningEmailDialogUserId} onOpenChange={() => setWarningEmailDialogUserId(null)}>
        <DialogContent className="glass border-border/50 sm:max-w-[500px]">
          <DialogHeader>
            <DialogTitle>Send Warning Email</DialogTitle>
            <DialogDescription>
              {warningEmailUser
                ? `Send a warning email to ${warningEmailUser.username || warningEmailUser.email}.`
                : 'Send a warning email to this user.'}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-2 py-2">
            <Label htmlFor="warning-email-reason">Reason (optional)</Label>
            <Textarea
              id="warning-email-reason"
              value={warningEmailReason}
              onChange={(event) => setWarningEmailReason(event.target.value)}
              placeholder="Describe why this warning is being sent..."
              rows={4}
              maxLength={500}
            />
          </div>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setWarningEmailDialogUserId(null)}
              className="rounded-xl"
              disabled={sendUploadWarning.isPending}
            >
              Cancel
            </Button>
            <Button
              onClick={handleSendUploadWarning}
              disabled={sendUploadWarning.isPending}
              className="rounded-xl bg-gradient-to-r from-primary to-primary/80"
            >
              {sendUploadWarning.isPending ? 'Sending...' : 'Send Email'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete User Dialog */}
      <AlertDialog open={!!deleteUserId} onOpenChange={() => setDeleteUserId(null)}>
        <AlertDialogContent className="glass border-border/50">
          <AlertDialogHeader>
            <AlertDialogTitle>Delete User?</AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently delete this user and all their data. This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={handleDeleteUser}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
