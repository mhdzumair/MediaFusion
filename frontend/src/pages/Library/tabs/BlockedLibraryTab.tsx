import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
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
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import {
  Ban,
  Search,
  Film,
  Tv,
  Radio,
  ChevronLeft,
  ChevronRight,
  CheckCircle,
  Loader2,
  ShieldAlert,
  Calendar,
  User,
} from 'lucide-react'
import { adminApi, type BlockedMediaItem } from '@/lib/api/admin'
import { useToast } from '@/hooks/use-toast'

const PAGE_SIZE = 24

const TYPE_ICONS = {
  movie: Film,
  series: Tv,
  tv: Radio,
}

const TYPE_LABELS = {
  movie: 'Movie',
  series: 'Series',
  tv: 'TV',
}

function BlockedItemCard({ item, onUnblocked }: { item: BlockedMediaItem; onUnblocked: () => void }) {
  const { toast } = useToast()
  const TypeIcon = TYPE_ICONS[item.type]

  const unblockMutation = useMutation({
    mutationFn: () => adminApi.unblockMedia(item.id),
    onSuccess: (data) => {
      toast({ title: 'Content unblocked', description: data.message })
      onUnblocked()
    },
    onError: (error: Error) => {
      toast({ variant: 'destructive', title: 'Unblock failed', description: error.message })
    },
  })

  const contentPath = `/dashboard/content/${item.type}/${item.id}`

  return (
    <div className="group relative">
      <Card className="overflow-hidden border-border/50 bg-card/50 hover:border-destructive/50 transition-colors">
        {/* Poster */}
        <div className="relative aspect-[2/3] bg-muted overflow-hidden">
          {item.poster ? (
            <img
              src={item.poster}
              alt={item.title}
              className="w-full h-full object-cover opacity-60 group-hover:opacity-70 transition-opacity"
              loading="lazy"
            />
          ) : (
            <div className="w-full h-full flex items-center justify-center">
              <TypeIcon className="h-12 w-12 text-muted-foreground/30" />
            </div>
          )}

          {/* Blocked overlay badge */}
          <div className="absolute top-2 left-2">
            <Badge variant="destructive" className="text-xs gap-1">
              <Ban className="h-3 w-3" />
              Blocked
            </Badge>
          </div>

          {/* Type badge */}
          <div className="absolute top-2 right-2">
            <Badge variant="secondary" className="text-xs">
              {TYPE_LABELS[item.type]}
            </Badge>
          </div>
        </div>

        <CardContent className="p-3 space-y-2">
          {/* Title */}
          <Link
            to={contentPath}
            className="block text-sm font-medium leading-tight hover:text-primary transition-colors line-clamp-2"
            title={item.title}
          >
            {item.title}
            {item.year && <span className="text-muted-foreground font-normal ml-1">({item.year})</span>}
          </Link>

          {/* Block reason */}
          {item.block_reason && (
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <p className="text-xs text-destructive/80 line-clamp-1 cursor-default">{item.block_reason}</p>
                </TooltipTrigger>
                <TooltipContent side="bottom" className="max-w-xs">
                  <p className="text-xs">{item.block_reason}</p>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          )}

          {/* Meta: blocker + date */}
          <div className="flex flex-col gap-1">
            {item.blocked_by && (
              <div className="flex items-center gap-1 text-xs text-muted-foreground">
                <User className="h-3 w-3 shrink-0" />
                <span className="truncate">{item.blocked_by}</span>
              </div>
            )}
            {item.blocked_at && (
              <div className="flex items-center gap-1 text-xs text-muted-foreground">
                <Calendar className="h-3 w-3 shrink-0" />
                <span>{new Date(item.blocked_at).toLocaleDateString()}</span>
              </div>
            )}
          </div>

          {/* Unblock button */}
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button
                size="sm"
                variant="outline"
                className="w-full h-7 text-xs gap-1 border-emerald-500/30 text-emerald-500 hover:bg-emerald-500/10 hover:text-emerald-400"
                disabled={unblockMutation.isPending}
              >
                {unblockMutation.isPending ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  <CheckCircle className="h-3 w-3" />
                )}
                Unblock
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Unblock "{item.title}"?</AlertDialogTitle>
                <AlertDialogDescription>This will make the content visible to all users again.</AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction onClick={() => unblockMutation.mutate()}>Unblock</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </CardContent>
      </Card>
    </div>
  )
}

export function BlockedLibraryTab() {
  const [searchParams, setSearchParams] = useSearchParams()
  const queryClient = useQueryClient()

  const catalogType = (searchParams.get('type') as 'movie' | 'series' | 'tv') || ''
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)

  const setType = (value: string) => {
    const next = new URLSearchParams(searchParams)
    if (value && value !== 'all') {
      next.set('type', value)
    } else {
      next.delete('type')
    }
    setSearchParams(next, { replace: true })
    setPage(1)
  }

  const queryKey = ['admin', 'blocked-media', { type: catalogType || undefined, search, page }]

  const { data, isLoading } = useQuery({
    queryKey,
    queryFn: () =>
      adminApi.getBlockedMedia({
        type: catalogType || undefined,
        search: search || undefined,
        page,
        page_size: PAGE_SIZE,
      }),
  })

  const handleUnblocked = () => {
    queryClient.invalidateQueries({ queryKey: ['admin', 'blocked-media'] })
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3 p-4 rounded-xl bg-destructive/5 border border-destructive/20">
        <ShieldAlert className="h-5 w-5 text-destructive shrink-0" />
        <div>
          <p className="text-sm font-medium text-destructive">Blocked Content</p>
          <p className="text-xs text-muted-foreground">
            {data
              ? `${data.total} item${data.total !== 1 ? 's' : ''} blocked`
              : 'Admin view — blocked items are hidden from regular users'}
          </p>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search blocked items..."
            value={search}
            onChange={(e) => {
              setSearch(e.target.value)
              setPage(1)
            }}
            className="pl-9 rounded-xl"
          />
        </div>

        <Select value={catalogType || 'all'} onValueChange={setType}>
          <SelectTrigger className="w-[140px] rounded-xl">
            <SelectValue placeholder="All Types" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Types</SelectItem>
            <SelectItem value="movie">Movies</SelectItem>
            <SelectItem value="series">Series</SelectItem>
            <SelectItem value="tv">TV</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Grid */}
      {isLoading ? (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-4">
          {[...Array(12)].map((_, i) => (
            <div key={i} className="space-y-2">
              <Skeleton className="aspect-[2/3] rounded-xl" />
              <Skeleton className="h-4 w-3/4" />
            </div>
          ))}
        </div>
      ) : !data?.items.length ? (
        <div className="text-center py-16">
          <CheckCircle className="h-16 w-16 mx-auto text-emerald-500/40" />
          <p className="mt-4 text-muted-foreground font-medium">No blocked content</p>
          <p className="text-sm text-muted-foreground mt-1">
            {search || catalogType ? 'No results match your filters.' : 'All clear — nothing is currently blocked.'}
          </p>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-4">
            {data.items.map((item) => (
              <BlockedItemCard key={item.id} item={item} onUnblocked={handleUnblocked} />
            ))}
          </div>

          {data.total > PAGE_SIZE && (
            <div className="flex justify-center items-center gap-2 pt-4">
              <Button
                variant="outline"
                size="icon"
                disabled={page === 1}
                onClick={() => setPage((p) => p - 1)}
                className="rounded-xl"
              >
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <span className="px-4 text-sm text-muted-foreground">
                Page {page} of {Math.ceil(data.total / PAGE_SIZE)}
              </span>
              <Button
                variant="outline"
                size="icon"
                disabled={!data.has_more}
                onClick={() => setPage((p) => p + 1)}
                className="rounded-xl"
              >
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
