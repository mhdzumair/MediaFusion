import { useState } from 'react'
import { Link, useSearchParams, useLocation } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
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
import {
  EyeOff,
  Eye,
  Search,
  ChevronLeft,
  ChevronRight,
  CheckCircle,
  XCircle,
  Loader2,
  Tag,
  ShieldCheck,
  Info,
} from 'lucide-react'
import { adminApi, type BlockedMediaItem } from '@/lib/api/admin'
import { useToast } from '@/hooks/use-toast'
import { Poster } from '@/components/ui/poster'
import { useRpdb } from '@/contexts/RpdbContext'
import { saveContentDetailReturnUrl } from '../browseNavigation'

const PAGE_SIZE = 24

const TYPE_LABELS: Record<string, string> = {
  movie: 'Movie',
  series: 'Series',
  tv: 'TV',
}

function ScoreBadge({ score }: { score?: number }) {
  if (score == null) return null
  const pct = Math.round(score * 100)
  const color =
    pct >= 80
      ? 'bg-red-600 text-white border-red-600'
      : pct >= 50
        ? 'bg-orange-600 text-white border-orange-600'
        : 'bg-yellow-600 text-white border-yellow-600'
  return <Badge className={`text-xs ${color}`}>{pct}% NSFW</Badge>
}

function NsfwItemCard({
  item,
  isAdmin,
  returnLabel,
}: {
  item: BlockedMediaItem
  isAdmin: boolean
  returnLabel: string
}) {
  const { toast } = useToast()
  const queryClient = useQueryClient()
  const location = useLocation()

  const reviewMutation = useMutation({
    mutationFn: (flagged: boolean) => adminApi.reviewNsfwItem(item.id, flagged),
    onSuccess: (_, flagged) => {
      toast({
        title: flagged ? 'Confirmed NSFW' : 'Cleared — marked as safe',
        description: `"${item.title}" review saved.`,
      })
      queryClient.invalidateQueries({ queryKey: ['media', 'nsfw'] })
    },
    onError: (error: Error) => {
      toast({ variant: 'destructive', title: 'Review failed', description: error.message })
    },
  })

  const { rpdbApiKey } = useRpdb()
  const contentPath = `/dashboard/content/${item.type}/${item.id}`

  const handleContentClick = () => {
    saveContentDetailReturnUrl(location.pathname, location.search, returnLabel)
  }

  return (
    <div className="group relative">
      <Card className="overflow-hidden border-border/50 bg-card/50 hover:border-destructive/50 transition-colors">
        {/* Poster */}
        <div className="relative aspect-[2/3] overflow-hidden">
          <Poster
            metaId={item.imdb_id ?? `mf:${item.id}`}
            catalogType={item.type}
            poster={item.poster}
            rpdbApiKey={item.type !== 'tv' ? rpdbApiKey : null}
            title={item.title}
            className="opacity-70 group-hover:opacity-80 transition-opacity w-full h-full rounded-none"
          />

          {/* Status badges */}
          <div className="absolute top-2 left-2 flex flex-col gap-1">
            {item.nsfw_reviewed ? (
              <Badge className="text-xs gap-1 bg-emerald-600 text-white border-emerald-600">
                <ShieldCheck className="h-3 w-3" />
                Reviewed
              </Badge>
            ) : (
              <Badge variant="destructive" className="text-xs gap-1">
                <EyeOff className="h-3 w-3" />
                Flagged
              </Badge>
            )}
            {item.is_keyword_blocked && (
              <Badge className="text-xs gap-1 bg-orange-600 text-white border-orange-600">
                <Tag className="h-3 w-3" />
                Keyword
              </Badge>
            )}
          </div>

          <div className="absolute top-2 right-2">
            <Badge variant="secondary" className="text-xs">
              {TYPE_LABELS[item.type] ?? item.type}
            </Badge>
          </div>
        </div>

        <CardContent className="p-3 space-y-2">
          <Link
            to={contentPath}
            onClick={handleContentClick}
            className="block text-sm font-medium leading-tight hover:text-primary transition-colors line-clamp-2"
            title={item.title}
          >
            {item.title}
            {item.year && <span className="text-muted-foreground font-normal ml-1">({item.year})</span>}
          </Link>

          <ScoreBadge score={item.nsfw_score} />

          {/* Admin review controls */}
          {isAdmin && !item.nsfw_reviewed && (
            <div className="flex gap-1.5 pt-1">
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button
                    size="sm"
                    variant="outline"
                    className="flex-1 h-7 text-xs gap-1 border-destructive/30 text-destructive hover:bg-destructive/10"
                    disabled={reviewMutation.isPending}
                  >
                    {reviewMutation.isPending ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <XCircle className="h-3 w-3" />
                    )}
                    NSFW
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>Confirm NSFW — "{item.title}"?</AlertDialogTitle>
                    <AlertDialogDescription>
                      Keeps this item hidden from the catalog permanently. The scan job will not re-score it.
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>Cancel</AlertDialogCancel>
                    <AlertDialogAction
                      className="bg-destructive hover:bg-destructive/90"
                      onClick={() => reviewMutation.mutate(true)}
                    >
                      Confirm NSFW
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>

              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button
                    size="sm"
                    variant="outline"
                    className="flex-1 h-7 text-xs gap-1 border-emerald-500/30 text-emerald-500 hover:bg-emerald-500/10"
                    disabled={reviewMutation.isPending}
                  >
                    {reviewMutation.isPending ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <CheckCircle className="h-3 w-3" />
                    )}
                    Safe
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>Clear flag — "{item.title}"?</AlertDialogTitle>
                    <AlertDialogDescription>
                      Marks the poster as safe. The item becomes visible in the catalog again.
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>Cancel</AlertDialogCancel>
                    <AlertDialogAction onClick={() => reviewMutation.mutate(false)}>Mark as Safe</AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            </div>
          )}

          {isAdmin && item.nsfw_reviewed && (
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    size="sm"
                    variant="outline"
                    className="w-full h-7 text-xs gap-1 border-muted-foreground/20 text-muted-foreground hover:bg-muted mt-1"
                    onClick={() => reviewMutation.mutate(!item.nsfw_flagged)}
                    disabled={reviewMutation.isPending}
                  >
                    {reviewMutation.isPending ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <Eye className="h-3 w-3" />
                    )}
                    {item.nsfw_flagged ? 'Undo — Mark safe' : 'Undo — Mark NSFW'}
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="bottom">
                  <p className="text-xs">Override previous review decision</p>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

export function NsfwReviewTab() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [search, setSearch] = useState('')

  const filter = searchParams.get('n_filter') || 'nsfw_flagged'
  const page = parseInt(searchParams.get('n_page') || '1', 10)

  const setFilter = (value: string) => {
    const next = new URLSearchParams(searchParams)
    next.set('n_filter', value)
    next.set('n_page', '1')
    setSearchParams(next, { replace: true })
  }

  const setPage = (value: number) => {
    const next = new URLSearchParams(searchParams)
    next.set('n_page', String(value))
    setSearchParams(next, { replace: true })
  }

  const queryKey = ['media', 'nsfw', { search, page, filter }]

  const { data, isLoading } = useQuery({
    queryKey,
    queryFn: () =>
      adminApi.getBlockedMedia({
        search: search || undefined,
        page,
        page_size: PAGE_SIZE,
        filter,
      }),
  })

  const isAdmin = data?.viewer_is_admin ?? false
  const returnLabel = filter === 'nsfw_reviewed' ? 'NSFW Review (Reviewed)' : 'NSFW Review'

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3 p-4 rounded-xl bg-destructive/5 border border-destructive/20">
        <EyeOff className="h-5 w-5 text-destructive shrink-0" />
        <div className="flex-1">
          <p className="text-sm font-medium text-destructive">NSFW Poster Detection</p>
          <p className="text-xs text-muted-foreground">
            {data
              ? `${data.total} item${data.total !== 1 ? 's' : ''} — posters flagged by the AI classifier`
              : 'Posters classified as potentially explicit'}
          </p>
        </div>
        {!isAdmin && (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger>
                <Info className="h-4 w-4 text-muted-foreground" />
              </TooltipTrigger>
              <TooltipContent side="left" className="max-w-xs">
                <p className="text-xs">Admins can confirm or clear these flags to control catalog visibility.</p>
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )}
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search flagged items..."
            value={search}
            onChange={(e) => {
              setSearch(e.target.value)
              setPage(1)
            }}
            className="pl-9 rounded-xl"
          />
        </div>

        <Select value={filter} onValueChange={setFilter}>
          <SelectTrigger className="w-[160px] rounded-xl">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="nsfw_flagged">Unreviewed</SelectItem>
            <SelectItem value="nsfw_reviewed">Reviewed</SelectItem>
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
          <p className="mt-4 text-muted-foreground font-medium">Nothing to show</p>
          <p className="text-sm text-muted-foreground mt-1">
            {search ? 'No results match your search.' : 'No unreviewed NSFW-flagged items.'}
          </p>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-4">
            {data.items.map((item) => (
              <NsfwItemCard key={item.id} item={item} isAdmin={isAdmin} returnLabel={returnLabel} />
            ))}
          </div>

          {data.total > PAGE_SIZE && (
            <div className="flex justify-center items-center gap-2 pt-4">
              <Button
                variant="outline"
                size="icon"
                disabled={page === 1}
                onClick={() => setPage(page - 1)}
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
                onClick={() => setPage(page + 1)}
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
