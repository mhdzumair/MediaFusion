import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  User,
  Settings,
  Lock,
  Eye,
  EyeOff,
  Save,
  Loader2,
  CheckCircle2,
  AlertCircle,
  UserCog,
  Trash2,
} from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Separator } from '@/components/ui/separator'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { useAuth } from '@/contexts/AuthContext'
import { authApi } from '@/lib/api/auth'
import type { UserUpdateRequest, ChangePasswordRequest } from '@/lib/api/auth'
import { getStoredAnonymousDisplayName, saveAnonymousDisplayName } from '@/lib/anonymousDisplayName'

export function SettingsPage() {
  const { user, refetchUser } = useAuth()
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  // Account details form state
  const [username, setUsername] = useState(user?.username || '')
  const [contributeAnonymously, setContributeAnonymously] = useState(user?.contribute_anonymously ?? false)
  const storedAnonymousDisplayName = getStoredAnonymousDisplayName()
  const [anonymousDisplayName, setAnonymousDisplayName] = useState(storedAnonymousDisplayName)

  // Password change form state
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [showCurrentPassword, setShowCurrentPassword] = useState(false)
  const [showNewPassword, setShowNewPassword] = useState(false)

  // Success/error messages
  const [accountMessage, setAccountMessage] = useState<{
    type: 'success' | 'error'
    text: string
  } | null>(null)
  const [passwordMessage, setPasswordMessage] = useState<{
    type: 'success' | 'error'
    text: string
  } | null>(null)

  // Update account mutation
  const updateAccountMutation = useMutation({
    mutationFn: (data: UserUpdateRequest) => authApi.updateMe(data),
    onSuccess: () => {
      setAccountMessage({ type: 'success', text: 'Account settings updated successfully!' })
      refetchUser()
      queryClient.invalidateQueries({ queryKey: ['auth'] })
      setTimeout(() => setAccountMessage(null), 3000)
    },
    onError: (error: Error) => {
      setAccountMessage({
        type: 'error',
        text: error.message || 'Failed to update account settings',
      })
    },
  })

  // Change password mutation
  const changePasswordMutation = useMutation({
    mutationFn: (data: ChangePasswordRequest) => authApi.changePassword(data),
    onSuccess: () => {
      setPasswordMessage({ type: 'success', text: 'Password changed successfully!' })
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
      setTimeout(() => setPasswordMessage(null), 3000)
    },
    onError: (error: Error) => {
      setPasswordMessage({
        type: 'error',
        text: error.message || 'Failed to change password',
      })
    },
  })

  // Account deletion state
  const [deletePassword, setDeletePassword] = useState('')
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  const deleteAccountMutation = useMutation({
    mutationFn: (password: string) => authApi.deleteAccount(password),
    onSuccess: () => {
      navigate('/')
    },
    onError: (error: Error) => {
      setDeleteError(error.message || 'Failed to delete account')
    },
  })

  const handleSaveAccount = () => {
    const updates: UserUpdateRequest = {}

    if (username !== user?.username) {
      updates.username = username || undefined
    }

    if (contributeAnonymously !== user?.contribute_anonymously) {
      updates.contribute_anonymously = contributeAnonymously
    }

    const normalizedAnonymousDisplayName = anonymousDisplayName.trim().replace(/\s+/g, ' ')
    const hasAnonymousDisplayNameChanges = normalizedAnonymousDisplayName !== storedAnonymousDisplayName

    if (hasAnonymousDisplayNameChanges) {
      saveAnonymousDisplayName(normalizedAnonymousDisplayName)
    }

    if (Object.keys(updates).length > 0) {
      updateAccountMutation.mutate(updates)
      return
    }

    if (hasAnonymousDisplayNameChanges) {
      setAccountMessage({ type: 'success', text: 'Account settings updated successfully!' })
      setTimeout(() => setAccountMessage(null), 3000)
    }
  }

  const handleChangePassword = () => {
    if (newPassword !== confirmPassword) {
      setPasswordMessage({ type: 'error', text: 'New passwords do not match' })
      return
    }

    if (newPassword.length < 8) {
      setPasswordMessage({
        type: 'error',
        text: 'New password must be at least 8 characters',
      })
      return
    }

    changePasswordMutation.mutate({
      current_password: currentPassword,
      new_password: newPassword,
    })
  }

  const hasAccountChanges =
    username !== (user?.username || '') ||
    contributeAnonymously !== (user?.contribute_anonymously ?? false) ||
    anonymousDisplayName.trim().replace(/\s+/g, ' ') !== storedAnonymousDisplayName

  return (
    <div className="container max-w-4xl py-8 space-y-8">
      <div className="flex items-center gap-3">
        <Settings className="h-8 w-8" />
        <div>
          <h1 className="text-3xl font-bold">Account Settings</h1>
          <p className="text-muted-foreground">Manage your account details and preferences</p>
        </div>
      </div>

      {/* Account Details Section */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <User className="h-5 w-5" />
            <CardTitle>Account Details</CardTitle>
          </div>
          <CardDescription>Update your account information</CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {accountMessage && (
            <Alert
              variant={accountMessage.type === 'error' ? 'destructive' : 'default'}
              className={accountMessage.type === 'success' ? 'border-green-500' : ''}
            >
              {accountMessage.type === 'success' ? (
                <CheckCircle2 className="h-4 w-4 text-green-500" />
              ) : (
                <AlertCircle className="h-4 w-4" />
              )}
              <AlertDescription>{accountMessage.text}</AlertDescription>
            </Alert>
          )}

          <div className="grid gap-4">
            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input id="email" type="email" value={user?.email || ''} disabled className="bg-muted" />
              <p className="text-xs text-muted-foreground">Email address cannot be changed</p>
            </div>

            <div className="space-y-2">
              <Label htmlFor="username">Username</Label>
              <Input
                id="username"
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="Enter a username"
              />
              <p className="text-xs text-muted-foreground">Your public display name</p>
            </div>

            <div className="space-y-2">
              <Label>Role</Label>
              <Input value={user?.role || 'user'} disabled className="bg-muted capitalize" />
            </div>

            <div className="space-y-2">
              <Label>Member Since</Label>
              <Input
                value={user?.created_at ? new Date(user.created_at).toLocaleDateString() : 'Unknown'}
                disabled
                className="bg-muted"
              />
            </div>
          </div>

          <Button onClick={handleSaveAccount} disabled={!hasAccountChanges || updateAccountMutation.isPending}>
            {updateAccountMutation.isPending ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Saving...
              </>
            ) : (
              <>
                <Save className="mr-2 h-4 w-4" />
                Save Changes
              </>
            )}
          </Button>
        </CardContent>
      </Card>

      {/* Contribution Preferences Section */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <UserCog className="h-5 w-5" />
            <CardTitle>Contribution Preferences</CardTitle>
          </div>
          <CardDescription>Configure how your contributions are displayed</CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="flex items-center justify-between rounded-lg border p-4">
            <div className="space-y-0.5">
              <Label htmlFor="anonymous-contributions" className="text-base">
                Contribute Anonymously by Default
              </Label>
              <p className="text-sm text-muted-foreground">
                When enabled, your contributions (imports, uploads) will not show your username by default. You can
                still override this per contribution.
              </p>
            </div>
            <Switch
              id="anonymous-contributions"
              checked={contributeAnonymously}
              onCheckedChange={setContributeAnonymously}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="anonymous-display-name">Anonymous Display Name</Label>
            <Input
              id="anonymous-display-name"
              type="text"
              value={anonymousDisplayName}
              onChange={(e) => setAnonymousDisplayName(e.target.value)}
              placeholder='Defaults to "Anonymous"'
              maxLength={32}
            />
            <p className="text-sm text-muted-foreground">
              Stored locally in this browser and used for anonymous contributions in the web UI.
            </p>
          </div>

          <div className="rounded-lg bg-muted/50 p-4">
            <h4 className="font-medium mb-2">What this affects:</h4>
            <ul className="text-sm text-muted-foreground space-y-1 list-disc list-inside">
              <li>Content imports (Magnet, Torrent, YouTube, HTTP, NZB, AceStream)</li>
              <li>Telegram bot contributions</li>
              <li>Browser extension imports</li>
            </ul>
          </div>

          <Button onClick={handleSaveAccount} disabled={!hasAccountChanges || updateAccountMutation.isPending}>
            {updateAccountMutation.isPending ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Saving...
              </>
            ) : (
              <>
                <Save className="mr-2 h-4 w-4" />
                Save Changes
              </>
            )}
          </Button>
        </CardContent>
      </Card>

      <Separator />

      {/* Change Password Section */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Lock className="h-5 w-5" />
            <CardTitle>Change Password</CardTitle>
          </div>
          <CardDescription>Update your account password</CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {passwordMessage && (
            <Alert
              variant={passwordMessage.type === 'error' ? 'destructive' : 'default'}
              className={passwordMessage.type === 'success' ? 'border-green-500' : ''}
            >
              {passwordMessage.type === 'success' ? (
                <CheckCircle2 className="h-4 w-4 text-green-500" />
              ) : (
                <AlertCircle className="h-4 w-4" />
              )}
              <AlertDescription>{passwordMessage.text}</AlertDescription>
            </Alert>
          )}

          <div className="grid gap-4">
            <div className="space-y-2">
              <Label htmlFor="current-password">Current Password</Label>
              <div className="relative">
                <Input
                  id="current-password"
                  type={showCurrentPassword ? 'text' : 'password'}
                  value={currentPassword}
                  onChange={(e) => setCurrentPassword(e.target.value)}
                  placeholder="Enter current password"
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="absolute right-0 top-0 h-full px-3 hover:bg-transparent"
                  onClick={() => setShowCurrentPassword(!showCurrentPassword)}
                >
                  {showCurrentPassword ? (
                    <EyeOff className="h-4 w-4 text-muted-foreground" />
                  ) : (
                    <Eye className="h-4 w-4 text-muted-foreground" />
                  )}
                </Button>
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="new-password">New Password</Label>
              <div className="relative">
                <Input
                  id="new-password"
                  type={showNewPassword ? 'text' : 'password'}
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  placeholder="Enter new password"
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="absolute right-0 top-0 h-full px-3 hover:bg-transparent"
                  onClick={() => setShowNewPassword(!showNewPassword)}
                >
                  {showNewPassword ? (
                    <EyeOff className="h-4 w-4 text-muted-foreground" />
                  ) : (
                    <Eye className="h-4 w-4 text-muted-foreground" />
                  )}
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">Must be at least 8 characters</p>
            </div>

            <div className="space-y-2">
              <Label htmlFor="confirm-password">Confirm New Password</Label>
              <Input
                id="confirm-password"
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="Confirm new password"
              />
            </div>
          </div>

          <Button
            onClick={handleChangePassword}
            disabled={!currentPassword || !newPassword || !confirmPassword || changePasswordMutation.isPending}
          >
            {changePasswordMutation.isPending ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Changing...
              </>
            ) : (
              <>
                <Lock className="mr-2 h-4 w-4" />
                Change Password
              </>
            )}
          </Button>
        </CardContent>
      </Card>

      {/* Contribution Stats (Read-only) */}
      <Card>
        <CardHeader>
          <CardTitle>Contribution Stats</CardTitle>
          <CardDescription>Your contribution reputation</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-4">
            <div className="rounded-lg border p-4">
              <p className="text-sm text-muted-foreground">Points</p>
              <p className="text-2xl font-bold">{user?.contribution_points ?? 0}</p>
            </div>
            <div className="rounded-lg border p-4">
              <p className="text-sm text-muted-foreground">Level</p>
              <p className="text-2xl font-bold capitalize">{user?.contribution_level ?? 'New'}</p>
            </div>
          </div>
        </CardContent>
      </Card>

      <Separator />

      {/* Danger Zone */}
      <Card className="border-destructive/50">
        <CardHeader>
          <div className="flex items-center gap-2">
            <Trash2 className="h-5 w-5 text-destructive" />
            <CardTitle className="text-destructive">Danger Zone</CardTitle>
          </div>
          <CardDescription>Irreversible actions that affect your account</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Permanently delete your account and all associated data including profiles, watch history, and library
            items. This action cannot be undone.
          </p>

          <AlertDialog
            open={deleteDialogOpen}
            onOpenChange={(open) => {
              setDeleteDialogOpen(open)
              if (!open) {
                setDeletePassword('')
                setDeleteError(null)
              }
            }}
          >
            <AlertDialogTrigger asChild>
              <Button variant="destructive">
                <Trash2 className="mr-2 h-4 w-4" />
                Delete My Account
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Are you absolutely sure?</AlertDialogTitle>
                <AlertDialogDescription>
                  This will permanently delete your account, all profiles, watch history, library items, and
                  configuration data. This action cannot be undone.
                </AlertDialogDescription>
              </AlertDialogHeader>

              <div className="space-y-3 py-2">
                <Label htmlFor="delete-password">Enter your password to confirm</Label>
                <Input
                  id="delete-password"
                  type="password"
                  value={deletePassword}
                  onChange={(e) => {
                    setDeletePassword(e.target.value)
                    setDeleteError(null)
                  }}
                  placeholder="Your current password"
                />
                {deleteError && <p className="text-sm text-destructive">{deleteError}</p>}
              </div>

              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction
                  className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                  disabled={!deletePassword || deleteAccountMutation.isPending}
                  onClick={(e) => {
                    e.preventDefault()
                    deleteAccountMutation.mutate(deletePassword)
                  }}
                >
                  {deleteAccountMutation.isPending ? (
                    <>
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      Deleting...
                    </>
                  ) : (
                    'Delete Account'
                  )}
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </CardContent>
      </Card>
    </div>
  )
}
