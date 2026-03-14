import { useState } from 'react'
import { Edit, MoreVertical, Star, Trash2, Copy, Check, RefreshCw } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Badge } from '@/components/ui/badge'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
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

interface ProfileHeaderProps {
  profileId?: number
  profileUuid?: string
  name: string
  isDefault: boolean
  isNew?: boolean
  onNameChange: (name: string) => void
  onDefaultChange: (isDefault: boolean) => void
  onDelete?: () => void
  onSetDefault?: () => void
  onResetUuid?: () => void
  isResettingUuid?: boolean
}

export function ProfileHeader({
  profileId,
  profileUuid,
  name,
  isDefault,
  isNew,
  onNameChange,
  onDefaultChange,
  onDelete,
  onSetDefault,
  onResetUuid,
  isResettingUuid = false,
}: ProfileHeaderProps) {
  const [isEditing, setIsEditing] = useState(isNew)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [resetUuidDialogOpen, setResetUuidDialogOpen] = useState(false)
  const [copiedField, setCopiedField] = useState<'id' | 'uuid' | null>(null)

  const copyProfileId = async () => {
    if (profileId !== undefined) {
      await navigator.clipboard.writeText(String(profileId))
      setCopiedField('id')
      setTimeout(() => setCopiedField(null), 2000)
    }
  }

  const copyProfileUuid = async () => {
    if (profileUuid) {
      await navigator.clipboard.writeText(profileUuid)
      setCopiedField('uuid')
      setTimeout(() => setCopiedField(null), 2000)
    }
  }

  return (
    <Card>
      <CardContent className="p-6">
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1 space-y-4">
            {/* Profile Name */}
            <div className="space-y-2">
              <Label>Profile Name</Label>
              {isEditing ? (
                <div className="flex items-center gap-2">
                  <Input
                    value={name}
                    onChange={(e) => onNameChange(e.target.value)}
                    placeholder="Enter profile name"
                    className="max-w-xs"
                    autoFocus
                  />
                  <Button variant="ghost" size="sm" onClick={() => setIsEditing(false)}>
                    Done
                  </Button>
                </div>
              ) : (
                <div className="flex items-center gap-2">
                  <h2 className="text-xl font-semibold">{name || 'Unnamed Profile'}</h2>
                  {isDefault && <Badge className="bg-primary">Default</Badge>}
                  <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => setIsEditing(true)}>
                    <Edit className="h-4 w-4" />
                  </Button>
                </div>
              )}
            </div>

            {/* Profile ID */}
            {profileId !== undefined && (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <span>ID:</span>
                <code className="bg-muted px-2 py-0.5 rounded text-xs">{profileId}</code>
                <Button variant="ghost" size="icon" className="h-6 w-6" onClick={copyProfileId}>
                  {copiedField === 'id' ? <Check className="h-3 w-3 text-emerald-500" /> : <Copy className="h-3 w-3" />}
                </Button>
              </div>
            )}

            {/* Profile UUID */}
            {profileUuid && (
              <div className="flex items-start gap-2 text-sm text-muted-foreground">
                <span className="pt-0.5">UUID:</span>
                <code className="bg-muted px-2 py-0.5 rounded text-xs break-all font-mono">{profileUuid}</code>
                <Button variant="ghost" size="icon" className="h-6 w-6 shrink-0" onClick={copyProfileUuid}>
                  {copiedField === 'uuid' ? (
                    <Check className="h-3 w-3 text-emerald-500" />
                  ) : (
                    <Copy className="h-3 w-3" />
                  )}
                </Button>
              </div>
            )}

            {/* Default Toggle */}
            <div className="flex items-center gap-3">
              <Switch id="default" checked={isDefault} onCheckedChange={onDefaultChange} />
              <Label htmlFor="default" className="cursor-pointer">
                Set as default profile
              </Label>
            </div>
          </div>

          {/* Actions Menu */}
          {!isNew && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon">
                  <MoreVertical className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {!isDefault && onSetDefault && (
                  <DropdownMenuItem onClick={onSetDefault}>
                    <Star className="h-4 w-4 mr-2" />
                    Set as Default
                  </DropdownMenuItem>
                )}
                {onResetUuid && (
                  <DropdownMenuItem onClick={() => setResetUuidDialogOpen(true)} disabled={isResettingUuid}>
                    <RefreshCw className={`h-4 w-4 mr-2 ${isResettingUuid ? 'animate-spin' : ''}`} />
                    Reset Profile UUID
                  </DropdownMenuItem>
                )}
                <DropdownMenuSeparator />
                <DropdownMenuItem className="text-red-500 focus:text-red-500" onClick={() => setDeleteDialogOpen(true)}>
                  <Trash2 className="h-4 w-4 mr-2" />
                  Delete Profile
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
      </CardContent>

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Profile</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete "{name}"? This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                onDelete?.()
                setDeleteDialogOpen(false)
              }}
              className="bg-red-500 hover:bg-red-600"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* UUID Reset Confirmation Dialog */}
      <AlertDialog open={resetUuidDialogOpen} onOpenChange={setResetUuidDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Reset Profile UUID?</AlertDialogTitle>
            <AlertDialogDescription>
              This revokes your current profile UUID and invalidates old manifest links that use it. Anyone with the old
              link will lose access.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                onResetUuid?.()
                setResetUuidDialogOpen(false)
              }}
              disabled={isResettingUuid}
            >
              {isResettingUuid ? 'Resetting...' : 'Reset UUID'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  )
}
