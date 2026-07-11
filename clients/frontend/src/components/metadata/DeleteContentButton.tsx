import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Loader2, Trash2 } from 'lucide-react'

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
import { Button } from '@/components/ui/button'
import { useDeleteMetadata } from '@/hooks'
import { useToast } from '@/hooks/use-toast'
import { catalogKeys, type CatalogType } from '@/hooks/useCatalog'

interface DeleteContentButtonProps {
  mediaId: number
  mediaTitle: string
  mediaType: CatalogType
}

export function DeleteContentButton({ mediaId, mediaTitle, mediaType }: DeleteContentButtonProps) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const deleteMetadata = useDeleteMetadata()

  const isDeleting = deleteMetadata.isPending

  const handleDelete = async () => {
    try {
      await deleteMetadata.mutateAsync(mediaId)

      queryClient.invalidateQueries({ queryKey: catalogKeys.item(mediaType, mediaId.toString()) })
      queryClient.invalidateQueries({ queryKey: ['catalog', 'list', mediaType] })

      toast({
        title: 'Metadata deleted',
        description: `"${mediaTitle}" was permanently deleted.`,
      })
      navigate(`/dashboard/library?tab=browse&type=${mediaType}`)
    } catch (error) {
      toast({
        title: 'Delete failed',
        description: error instanceof Error ? error.message : 'Failed to delete metadata.',
        variant: 'destructive',
      })
    }
  }

  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="h-8 gap-1.5 rounded-xl border-red-500/50 text-red-600 hover:bg-red-500/10"
        >
          <Trash2 className="h-4 w-4" />
          <span className="hidden sm:inline">Delete</span>
        </Button>
      </AlertDialogTrigger>

      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-red-500" />
            Permanently Delete Metadata
          </AlertDialogTitle>
          <AlertDialogDescription>
            This action is permanent and cannot be undone. Are you sure you want to delete "{mediaTitle}"?
          </AlertDialogDescription>
        </AlertDialogHeader>

        <AlertDialogFooter>
          <AlertDialogCancel className="rounded-xl" disabled={isDeleting}>
            Cancel
          </AlertDialogCancel>
          <AlertDialogAction asChild>
            <Button variant="destructive" className="rounded-xl" onClick={handleDelete} disabled={isDeleting}>
              {isDeleting ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Deleting...
                </>
              ) : (
                <>
                  <Trash2 className="mr-2 h-4 w-4" />
                  Delete Metadata
                </>
              )}
            </Button>
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
