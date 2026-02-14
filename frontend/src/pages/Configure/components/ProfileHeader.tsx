import { useState } from 'react'
import { Edit, MoreVertical, Star, Trash2, Copy, Check } from 'lucide-react'
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
  name: string
  isDefault: boolean
  isNew?: boolean
  onNameChange: (name: string) => void
  onDefaultChange: (isDefault: boolean) => void
  onDelete?: () => void
  onSetDefault?: () => void
}

export function ProfileHeader({
  profileId,
  name,
  isDefault,
  isNew,
  onNameChange,
  onDefaultChange,
  onDelete,
  onSetDefault,
}: ProfileHeaderProps) {
  const [isEditing, setIsEditing] = useState(isNew)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [copied, setCopied] = useState(false)

  const copyProfileId = async () => {
    if (profileId) {
      await navigator.clipboard.writeText(String(profileId))
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
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
            {profileId && (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <span>ID:</span>
                <code className="bg-muted px-2 py-0.5 rounded text-xs">{String(profileId).slice(0, 12)}...</code>
                <Button variant="ghost" size="icon" className="h-6 w-6" onClick={copyProfileId}>
                  {copied ? <Check className="h-3 w-3 text-emerald-500" /> : <Copy className="h-3 w-3" />}
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
    </Card>
  )
}
