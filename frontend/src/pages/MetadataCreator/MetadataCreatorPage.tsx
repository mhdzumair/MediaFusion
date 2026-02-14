import { useState, useCallback } from 'react'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Input } from '@/components/ui/input'
import { Film, Tv, Radio, Plus, Search, Trash2, Edit, ChevronRight, Loader2, Sparkles, Download } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useUserMetadataList, useDeleteUserMetadata } from '@/hooks'
import type { UserMediaResponse } from '@/lib/api'
import { useToast } from '@/hooks/use-toast'
import { MovieMetadataForm } from './components/MovieMetadataForm'
import { SeriesMetadataForm } from './components/SeriesMetadataForm'
import { TVMetadataForm } from './components/TVMetadataForm'
import { MetadataDetailDialog } from './components/MetadataDetailDialog'
import { ImportFromExternalDialog } from './components/ImportFromExternalDialog'
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

type CreatorMode = 'list' | 'create-movie' | 'create-series' | 'create-tv' | 'edit'

export function MetadataCreatorPage() {
  const [mode, setMode] = useState<CreatorMode>('list')
  const [activeTab, setActiveTab] = useState<'all' | 'movie' | 'series' | 'tv'>('all')
  const [searchQuery, setSearchQuery] = useState('')
  const [page, setPage] = useState(1)
  const [selectedMedia, setSelectedMedia] = useState<UserMediaResponse | null>(null)
  const [detailDialogOpen, setDetailDialogOpen] = useState(false)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [importDialogOpen, setImportDialogOpen] = useState(false)
  const [mediaToDelete, setMediaToDelete] = useState<UserMediaResponse | null>(null)

  const { toast } = useToast()

  const {
    data: metadataList,
    isLoading,
    refetch,
  } = useUserMetadataList({
    page,
    per_page: 20,
    type: activeTab,
    search: searchQuery || undefined,
  })

  const deleteMetadata = useDeleteUserMetadata()

  const handleCreateSuccess = useCallback(() => {
    setMode('list')
    refetch()
    toast({
      title: 'Success',
      description: 'Metadata created successfully',
    })
  }, [refetch, toast])

  const handleEditSuccess = useCallback(() => {
    setMode('list')
    setSelectedMedia(null)
    refetch()
    toast({
      title: 'Success',
      description: 'Metadata updated successfully',
    })
  }, [refetch, toast])

  const handleViewDetails = useCallback((media: UserMediaResponse) => {
    setSelectedMedia(media)
    setDetailDialogOpen(true)
  }, [])

  const handleEdit = useCallback((media: UserMediaResponse) => {
    setSelectedMedia(media)
    setMode('edit')
  }, [])

  const handleDeleteClick = useCallback((media: UserMediaResponse) => {
    setMediaToDelete(media)
    setDeleteDialogOpen(true)
  }, [])

  const handleDeleteConfirm = useCallback(async () => {
    if (!mediaToDelete) return

    try {
      await deleteMetadata.mutateAsync({ mediaId: mediaToDelete.id })
      toast({
        title: 'Deleted',
        description: `"${mediaToDelete.title}" has been deleted`,
      })
      setDeleteDialogOpen(false)
      setMediaToDelete(null)
    } catch (error) {
      toast({
        title: 'Error',
        description: error instanceof Error ? error.message : 'Failed to delete',
        variant: 'destructive',
      })
    }
  }, [mediaToDelete, deleteMetadata, toast])

  const handleBack = useCallback(() => {
    setMode('list')
    setSelectedMedia(null)
  }, [])

  // Render create/edit forms
  if (mode === 'create-movie') {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-4">
          <Button variant="ghost" onClick={handleBack}>
            ← Back
          </Button>
          <h1 className="text-2xl font-bold">Create Movie Metadata</h1>
        </div>
        <MovieMetadataForm onSuccess={handleCreateSuccess} onCancel={handleBack} />
      </div>
    )
  }

  if (mode === 'create-series') {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-4">
          <Button variant="ghost" onClick={handleBack}>
            ← Back
          </Button>
          <h1 className="text-2xl font-bold">Create Series Metadata</h1>
        </div>
        <SeriesMetadataForm onSuccess={handleCreateSuccess} onCancel={handleBack} />
      </div>
    )
  }

  if (mode === 'create-tv') {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-4">
          <Button variant="ghost" onClick={handleBack}>
            ← Back
          </Button>
          <h1 className="text-2xl font-bold">Create TV Channel Metadata</h1>
        </div>
        <TVMetadataForm onSuccess={handleCreateSuccess} onCancel={handleBack} />
      </div>
    )
  }

  if (mode === 'edit' && selectedMedia) {
    if (selectedMedia.type === 'movie') {
      return (
        <div className="space-y-6">
          <div className="flex items-center gap-4">
            <Button variant="ghost" onClick={handleBack}>
              ← Back
            </Button>
            <h1 className="text-2xl font-bold">Edit Movie: {selectedMedia.title}</h1>
          </div>
          <MovieMetadataForm initialData={selectedMedia} onSuccess={handleEditSuccess} onCancel={handleBack} />
        </div>
      )
    } else if (selectedMedia.type === 'tv') {
      return (
        <div className="space-y-6">
          <div className="flex items-center gap-4">
            <Button variant="ghost" onClick={handleBack}>
              ← Back
            </Button>
            <h1 className="text-2xl font-bold">Edit TV Channel: {selectedMedia.title}</h1>
          </div>
          <TVMetadataForm initialData={selectedMedia} onSuccess={handleEditSuccess} onCancel={handleBack} />
        </div>
      )
    } else {
      return (
        <div className="space-y-6">
          <div className="flex items-center gap-4">
            <Button variant="ghost" onClick={handleBack}>
              ← Back
            </Button>
            <h1 className="text-2xl font-bold">Edit Series: {selectedMedia.title}</h1>
          </div>
          <SeriesMetadataForm initialData={selectedMedia} onSuccess={handleEditSuccess} onCancel={handleBack} />
        </div>
      )
    }
  }

  // Render list view
  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Sparkles className="h-6 w-6 text-primary" />
            Metadata Creator
          </h1>
          <p className="text-muted-foreground mt-1">Create and manage your own movie, series, and TV metadata</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => setImportDialogOpen(true)} variant="outline">
            <Download className="h-4 w-4 mr-2" />
            Import from ID
          </Button>
          <Button onClick={() => setMode('create-movie')} variant="outline">
            <Film className="h-4 w-4 mr-2" />
            New Movie
          </Button>
          <Button onClick={() => setMode('create-series')} variant="outline">
            <Tv className="h-4 w-4 mr-2" />
            New Series
          </Button>
          <Button onClick={() => setMode('create-tv')} variant="outline">
            <Radio className="h-4 w-4 mr-2" />
            New TV
          </Button>
        </div>
      </div>

      {/* Filters */}
      <Card className="border-border/50 bg-card/50 backdrop-blur">
        <CardContent className="pt-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="Search your metadata..."
                value={searchQuery}
                onChange={(e) => {
                  setSearchQuery(e.target.value)
                  setPage(1)
                }}
                className="pl-9"
              />
            </div>
            <Tabs
              value={activeTab}
              onValueChange={(v) => {
                setActiveTab(v as 'all' | 'movie' | 'series' | 'tv')
                setPage(1)
              }}
            >
              <TabsList className="bg-muted/50">
                <TabsTrigger value="all">All</TabsTrigger>
                <TabsTrigger value="movie" className="gap-1.5">
                  <Film className="h-3.5 w-3.5" />
                  Movies
                </TabsTrigger>
                <TabsTrigger value="series" className="gap-1.5">
                  <Tv className="h-3.5 w-3.5" />
                  Series
                </TabsTrigger>
                <TabsTrigger value="tv" className="gap-1.5">
                  <Radio className="h-3.5 w-3.5" />
                  TV
                </TabsTrigger>
              </TabsList>
            </Tabs>
          </div>
        </CardContent>
      </Card>

      {/* Content */}
      <Card className="border-border/50 bg-card/50 backdrop-blur">
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle className="text-lg">Your Metadata</CardTitle>
            {metadataList && (
              <Badge variant="secondary" className="font-normal">
                {metadataList.total} item{metadataList.total !== 1 ? 's' : ''}
              </Badge>
            )}
          </div>
          <CardDescription>Manage your custom movie, series, and TV entries</CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-8 w-8 animate-spin text-primary" />
            </div>
          ) : metadataList?.items.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-center">
              <div className="rounded-full bg-primary/10 p-4 mb-4">
                <Plus className="h-8 w-8 text-primary" />
              </div>
              <h3 className="font-semibold text-lg">No metadata yet</h3>
              <p className="text-muted-foreground mt-1 max-w-sm">
                Create your first movie, series, or TV metadata to get started
              </p>
              <div className="flex flex-wrap gap-2 mt-4">
                <Button
                  onClick={() => setMode('create-movie')}
                  size="sm"
                  className="bg-gradient-to-r from-primary to-primary/80"
                >
                  <Film className="h-4 w-4 mr-1.5" />
                  Create Movie
                </Button>
                <Button onClick={() => setMode('create-series')} size="sm" variant="outline">
                  <Tv className="h-4 w-4 mr-1.5" />
                  Create Series
                </Button>
                <Button onClick={() => setMode('create-tv')} size="sm" variant="outline">
                  <Radio className="h-4 w-4 mr-1.5" />
                  Create TV
                </Button>
              </div>
            </div>
          ) : (
            <ScrollArea className="h-[500px]">
              <div className="space-y-2">
                {metadataList?.items.map((media) => (
                  <MetadataListItem
                    key={media.id}
                    media={media}
                    onView={() => handleViewDetails(media)}
                    onEdit={() => handleEdit(media)}
                    onDelete={() => handleDeleteClick(media)}
                  />
                ))}
              </div>
            </ScrollArea>
          )}

          {/* Pagination */}
          {metadataList && metadataList.pages > 1 && (
            <div className="flex items-center justify-center gap-2 mt-4 pt-4 border-t">
              <Button variant="outline" size="sm" disabled={page === 1} onClick={() => setPage(page - 1)}>
                Previous
              </Button>
              <span className="text-sm text-muted-foreground">
                Page {page} of {metadataList.pages}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={page === metadataList.pages}
                onClick={() => setPage(page + 1)}
              >
                Next
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Detail Dialog */}
      <MetadataDetailDialog
        open={detailDialogOpen}
        onOpenChange={setDetailDialogOpen}
        media={selectedMedia}
        onEdit={() => {
          setDetailDialogOpen(false)
          if (selectedMedia) handleEdit(selectedMedia)
        }}
      />

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Metadata</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete "{mediaToDelete?.title}"? This action cannot be undone.
              {mediaToDelete?.total_streams && mediaToDelete.total_streams > 0 && (
                <span className="block mt-2 text-primary">
                  Warning: This metadata has {mediaToDelete.total_streams} stream(s) linked to it.
                </span>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDeleteConfirm}
              className="bg-red-600 hover:bg-red-700"
              disabled={deleteMetadata.isPending}
            >
              {deleteMetadata.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
              ) : (
                <Trash2 className="h-4 w-4 mr-2" />
              )}
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Import from External Dialog */}
      <ImportFromExternalDialog
        open={importDialogOpen}
        onOpenChange={setImportDialogOpen}
        onSuccess={() => refetch()}
      />
    </div>
  )
}

// List item component
interface MetadataListItemProps {
  media: UserMediaResponse
  onView: () => void
  onEdit: () => void
  onDelete: () => void
}

function MetadataListItem({ media, onView, onEdit, onDelete }: MetadataListItemProps) {
  return (
    <div
      className={cn(
        'flex items-center gap-4 p-3 rounded-lg border border-border/50',
        'hover:bg-muted/30 transition-colors group cursor-pointer',
      )}
      onClick={onView}
    >
      {/* Poster */}
      <div className="w-12 h-16 rounded bg-muted/50 flex-shrink-0 overflow-hidden">
        {media.poster_url ? (
          <img src={media.poster_url} alt={media.title} className="w-full h-full object-cover" />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            {media.type === 'movie' ? (
              <Film className="h-5 w-5 text-muted-foreground" />
            ) : media.type === 'tv' ? (
              <Radio className="h-5 w-5 text-muted-foreground" />
            ) : (
              <Tv className="h-5 w-5 text-muted-foreground" />
            )}
          </div>
        )}
      </div>

      {/* Info */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <h3 className="font-medium truncate">{media.title}</h3>
          {media.year && <span className="text-sm text-muted-foreground">({media.year})</span>}
        </div>
        <div className="flex items-center gap-2 mt-1">
          <Badge
            variant="outline"
            className={cn(
              'text-xs',
              media.type === 'movie'
                ? 'border-blue-500/50 text-blue-500'
                : media.type === 'tv'
                  ? 'border-orange-500/50 text-orange-500'
                  : 'border-green-500/50 text-green-500',
            )}
          >
            {media.type === 'movie' ? (
              <Film className="h-3 w-3 mr-1" />
            ) : media.type === 'tv' ? (
              <Radio className="h-3 w-3 mr-1" />
            ) : (
              <Tv className="h-3 w-3 mr-1" />
            )}
            {media.type}
          </Badge>
          {media.type === 'series' && media.total_seasons !== undefined && (
            <span className="text-xs text-muted-foreground">
              {media.total_seasons} season{media.total_seasons !== 1 ? 's' : ''} · {media.total_episodes || 0} episodes
            </span>
          )}
          {media.total_streams > 0 && (
            <Badge variant="secondary" className="text-xs">
              {media.total_streams} stream{media.total_streams !== 1 ? 's' : ''}
            </Badge>
          )}
          {!media.is_public && (
            <Badge variant="outline" className="text-xs border-primary/50 text-primary">
              Private
            </Badge>
          )}
        </div>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8"
          onClick={(e) => {
            e.stopPropagation()
            onEdit()
          }}
        >
          <Edit className="h-4 w-4" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 text-red-500 hover:text-red-600 hover:bg-red-500/10"
          onClick={(e) => {
            e.stopPropagation()
            onDelete()
          }}
        >
          <Trash2 className="h-4 w-4" />
        </Button>
        <ChevronRight className="h-4 w-4 text-muted-foreground" />
      </div>
    </div>
  )
}
