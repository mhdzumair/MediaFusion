import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Ban, CheckCircle, Loader2, AlertTriangle, ShieldAlert } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { adminApi } from '@/lib/api'
import { useToast } from '@/hooks/use-toast'
import { catalogKeys } from '@/hooks/useCatalog'

interface BlockContentButtonProps {
  mediaId: number
  mediaTitle: string
  mediaType: 'movie' | 'series' | 'tv'
  isBlocked: boolean
  blockReason?: string | null
  className?: string
}

export function BlockContentButton({
  mediaId,
  mediaTitle,
  mediaType,
  isBlocked,
  blockReason,
  className,
}: BlockContentButtonProps) {
  const queryClient = useQueryClient()
  const { toast } = useToast()

  const [blockDialogOpen, setBlockDialogOpen] = useState(false)
  const [reason, setReason] = useState('')

  // Block mutation
  const blockMutation = useMutation({
    mutationFn: () => adminApi.blockMedia(mediaId, { reason: reason || undefined }),
    onSuccess: (data) => {
      toast({
        title: 'Content blocked',
        description: data.message,
      })
      // Invalidate queries to refresh data
      queryClient.invalidateQueries({ queryKey: catalogKeys.item(mediaType, mediaId.toString()) })
      setBlockDialogOpen(false)
      setReason('')
    },
    onError: (error: Error) => {
      toast({
        variant: 'destructive',
        title: 'Block failed',
        description: error.message,
      })
    },
  })

  // Unblock mutation
  const unblockMutation = useMutation({
    mutationFn: () => adminApi.unblockMedia(mediaId),
    onSuccess: (data) => {
      toast({
        title: 'Content unblocked',
        description: data.message,
      })
      // Invalidate queries to refresh data
      queryClient.invalidateQueries({ queryKey: catalogKeys.item(mediaType, mediaId.toString()) })
    },
    onError: (error: Error) => {
      toast({
        variant: 'destructive',
        title: 'Unblock failed',
        description: error.message,
      })
    },
  })

  const handleBlock = () => {
    blockMutation.mutate()
  }

  const handleUnblock = () => {
    unblockMutation.mutate()
  }

  return (
    <TooltipProvider>
      <div className={cn('flex items-center gap-2', className)}>
        {isBlocked ? (
          <>
            {/* Blocked indicator */}
            <Badge variant="destructive" className="gap-1.5 bg-red-500/20 text-red-500 border-red-500/30">
              <ShieldAlert className="h-3 w-3" />
              Blocked
            </Badge>

            {/* Unblock button */}
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="outline"
                  size="sm"
                  className="h-8 gap-1.5 rounded-xl border-emerald-500/50 text-emerald-600 hover:bg-emerald-500/10"
                  onClick={handleUnblock}
                  disabled={unblockMutation.isPending}
                >
                  {unblockMutation.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <CheckCircle className="h-4 w-4" />
                  )}
                  <span className="hidden sm:inline">Unblock</span>
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                <p>Make this content visible to users again</p>
                {blockReason && <p className="text-xs text-muted-foreground mt-1">Blocked for: {blockReason}</p>}
              </TooltipContent>
            </Tooltip>
          </>
        ) : (
          <>
            {/* Block button */}
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="outline"
                  size="sm"
                  className="h-8 gap-1.5 rounded-xl border-red-500/50 text-red-600 hover:bg-red-500/10"
                  onClick={() => setBlockDialogOpen(true)}
                >
                  <Ban className="h-4 w-4" />
                  <span className="hidden sm:inline">Block</span>
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                <p>Hide this content from users</p>
              </TooltipContent>
            </Tooltip>

            {/* Block Dialog */}
            <Dialog open={blockDialogOpen} onOpenChange={setBlockDialogOpen}>
              <DialogContent className="sm:max-w-[425px]">
                <DialogHeader>
                  <DialogTitle className="flex items-center gap-2">
                    <AlertTriangle className="h-5 w-5 text-red-500" />
                    Block Content
                  </DialogTitle>
                  <DialogDescription>
                    This will hide "{mediaTitle}" from all regular users. Only moderators and admins will be able to see
                    it.
                  </DialogDescription>
                </DialogHeader>

                <div className="space-y-4 py-4">
                  <Alert variant="destructive" className="border-red-500/30 bg-red-500/10">
                    <AlertTriangle className="h-4 w-4" />
                    <AlertDescription>
                      Blocked content will not appear in catalog listings or search results for regular users.
                    </AlertDescription>
                  </Alert>

                  <div className="space-y-2">
                    <Label htmlFor="reason">Reason for blocking (optional)</Label>
                    <Input
                      id="reason"
                      placeholder="e.g., Inappropriate content, Copyright violation..."
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                      className="rounded-xl"
                    />
                    <p className="text-xs text-muted-foreground">
                      This will be visible to other moderators and admins.
                    </p>
                  </div>
                </div>

                <DialogFooter>
                  <Button variant="outline" onClick={() => setBlockDialogOpen(false)} className="rounded-xl">
                    Cancel
                  </Button>
                  <Button
                    onClick={handleBlock}
                    disabled={blockMutation.isPending}
                    className="rounded-xl bg-gradient-to-r from-red-500 to-red-600 hover:from-red-600 hover:to-red-700"
                  >
                    {blockMutation.isPending ? (
                      <>
                        <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                        Blocking...
                      </>
                    ) : (
                      <>
                        <Ban className="h-4 w-4 mr-2" />
                        Block Content
                      </>
                    )}
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </>
        )}
      </div>
    </TooltipProvider>
  )
}
