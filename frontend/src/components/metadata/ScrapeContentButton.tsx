import { useState, useEffect } from 'react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Checkbox } from '@/components/ui/checkbox'
import { Search, Loader2, Clock, CheckCircle2, AlertCircle, Zap, Shield } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { scrapersApi, type ScrapeStatusResponse } from '@/lib/api'
import { useToast } from '@/hooks/use-toast'
import { catalogKeys } from '@/hooks/useCatalog'
import { formatDistanceToNow } from 'date-fns'

interface ScrapeContentButtonProps {
  mediaId: number
  mediaType: 'movie' | 'series'
  title?: string
  season?: number
  episode?: number
  className?: string
}

/**
 * ScrapeContentButton - Allows users to trigger content scraping and view scrape status
 *
 * - Shows last scraped time
 * - Displays cooldown status for each scraper
 * - Allows selecting specific scrapers
 * - Force scrape option for moderators/admins
 */
export function ScrapeContentButton({
  mediaId,
  mediaType,
  title = '',
  season,
  episode,
  className,
}: ScrapeContentButtonProps) {
  const queryClient = useQueryClient()
  const { toast } = useToast()

  const [dialogOpen, setDialogOpen] = useState(false)
  const [forceScrap, setForceScrap] = useState(false)
  const [selectedScrapers, setSelectedScrapers] = useState<Set<string>>(new Set())

  // For series, require episode selection before querying status
  const canQueryStatus = mediaType === 'movie' || (season !== undefined && episode !== undefined)

  // Query scrape status
  const { data: scrapeStatus, isLoading: statusLoading } = useQuery({
    queryKey: ['scrapeStatus', mediaId, mediaType, season, episode],
    queryFn: () => scrapersApi.getScrapeStatus(mediaId, mediaType, season, episode),
    enabled: dialogOpen && canQueryStatus,
    refetchInterval: dialogOpen && canQueryStatus ? 30000 : false, // Refresh every 30s when dialog is open
  })

  // Initialize selected scrapers when status loads
  useEffect(() => {
    if (scrapeStatus?.available_scrapers) {
      // Select all available scrapers by default
      // If user has debrid, include debrid scrapers too
      const defaultSelected = new Set(
        scrapeStatus.available_scrapers
          .filter((s) => s.enabled && (!s.requires_debrid || scrapeStatus.has_debrid))
          .map((s) => s.id),
      )
      setSelectedScrapers(defaultSelected)
    }
  }, [scrapeStatus?.available_scrapers, scrapeStatus?.has_debrid])

  // Scrape mutation
  const scrapeMutation = useMutation({
    mutationFn: () =>
      scrapersApi.triggerScrape(mediaId, {
        media_type: mediaType,
        season,
        episode,
        force: forceScrap,
        scrapers: selectedScrapers.size > 0 ? Array.from(selectedScrapers) : undefined,
      }),
    onSuccess: (data) => {
      toast({
        title: 'Scraping completed',
        description: `Found ${data.streams_found} streams for "${title || data.title || 'content'}" using ${data.scrapers_used.length} scrapers`,
      })
      // Invalidate queries to refresh data
      queryClient.invalidateQueries({ queryKey: ['scrapeStatus', mediaId] })
      queryClient.invalidateQueries({ queryKey: catalogKeys.streams(mediaType, mediaId.toString(), season, episode) })
      queryClient.invalidateQueries({ queryKey: catalogKeys.item(mediaType, mediaId.toString()) })
      setDialogOpen(false)
      setForceScrap(false)
    },
    onError: (error: Error) => {
      toast({
        variant: 'destructive',
        title: 'Scraping failed',
        description: error.message,
      })
    },
  })

  const handleScrape = () => {
    scrapeMutation.mutate()
  }

  // Toggle scraper selection
  const toggleScraper = (scraperId: string) => {
    setSelectedScrapers((prev) => {
      const next = new Set(prev)
      if (next.has(scraperId)) {
        next.delete(scraperId)
      } else {
        next.add(scraperId)
      }
      return next
    })
  }

  // Format cooldown time
  const formatCooldown = (seconds: number): string => {
    if (seconds < 60) return `${seconds}s`
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
    return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
  }

  // Count available scrapers
  const getScraperSummary = (status: ScrapeStatusResponse | undefined) => {
    if (!status?.scraper_statuses) return { available: 0, total: 0, selectedAvailable: 0 }
    const entries = Object.entries(status.scraper_statuses)
    // Count scrapers that can run (considering debrid requirement)
    const available = entries.filter(([, s]) => s.can_scrape && (!s.requires_debrid || status.has_debrid)).length
    const selectedAvailable = entries.filter(
      ([id, s]) => s.can_scrape && (!s.requires_debrid || status.has_debrid) && selectedScrapers.has(id),
    ).length
    return { available, total: entries.length, selectedAvailable }
  }

  const scraperSummary = getScraperSummary(scrapeStatus)

  // For series, require episode selection
  const needsEpisodeSelection = mediaType === 'series' && (season === undefined || episode === undefined)

  // Check if any selected scraper can run
  const canScrapeSelected = scrapeStatus?.scraper_statuses
    ? Array.from(selectedScrapers).some((id) => {
        const status = scrapeStatus.scraper_statuses?.[id]
        return status?.can_scrape && (!status.requires_debrid || scrapeStatus.has_debrid)
      })
    : false

  return (
    <TooltipProvider>
      <div className={cn('flex items-center', className)}>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="outline"
              size="sm"
              className="h-8 gap-1.5 rounded-xl border-cyan-500/50 text-cyan-600 hover:bg-cyan-500/10"
              onClick={() => setDialogOpen(true)}
            >
              <Search className="h-4 w-4" />
              <span className="hidden sm:inline">Scrape Streams</span>
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            <p>Search for streams from torrent indexers</p>
          </TooltipContent>
        </Tooltip>

        {/* Scrape Dialog */}
        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogContent className="sm:max-w-[550px]">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <Search className="h-5 w-5 text-cyan-500" />
                Scrape Streams
              </DialogTitle>
              <DialogDescription>
                Search for streams from configured torrent indexers.
                {mediaType === 'series' && season !== undefined && episode !== undefined && (
                  <span className="block mt-1 font-medium">
                    Season {season}, Episode {episode}
                  </span>
                )}
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-4 py-4">
              {/* Series: require episode selection */}
              {needsEpisodeSelection && (
                <div className="flex items-start gap-2 p-3 rounded-xl bg-primary/10 text-sm">
                  <AlertCircle className="h-4 w-4 text-primary mt-0.5 flex-shrink-0" />
                  <p className="text-primary">
                    Please select an episode first. Scraping searches for streams for a specific episode, not the entire
                    series.
                  </p>
                </div>
              )}

              {/* Status Loading */}
              {statusLoading && canQueryStatus && (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              )}

              {/* Scrape Status */}
              {scrapeStatus && !needsEpisodeSelection && (
                <>
                  {/* Last Scraped */}
                  <div className="p-3 rounded-xl bg-muted/50">
                    <div className="flex items-center gap-2 text-sm">
                      <Clock className="h-4 w-4 text-muted-foreground" />
                      <span className="text-muted-foreground">Last scraped:</span>
                      <span className="font-medium">
                        {scrapeStatus.last_scraped_at
                          ? formatDistanceToNow(new Date(scrapeStatus.last_scraped_at), { addSuffix: true })
                          : 'Never'}
                      </span>
                    </div>
                  </div>

                  {/* Scraper Selection */}
                  <div className="space-y-2">
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-muted-foreground font-medium">Select Indexers</span>
                      <Badge
                        variant="outline"
                        className={cn(
                          scraperSummary.selectedAvailable > 0
                            ? 'bg-emerald-500/10 text-emerald-600 border-emerald-500/30'
                            : 'bg-primary/10 text-primary border-primary/30',
                        )}
                      >
                        {scraperSummary.selectedAvailable} selected ready
                      </Badge>
                    </div>

                    <div className="grid grid-cols-2 gap-2">
                      {scrapeStatus.available_scrapers
                        .filter((scraper) => !scraper.requires_debrid || scrapeStatus.has_debrid) // Show debrid scrapers only if user has debrid
                        .map((scraper) => {
                          const status = scrapeStatus.scraper_statuses?.[scraper.id]
                          const isSelected = selectedScrapers.has(scraper.id)
                          const canScrape = status?.can_scrape ?? true

                          return (
                            <label
                              key={scraper.id}
                              className={cn(
                                'flex items-center gap-2 p-2 rounded-lg border text-xs cursor-pointer transition-colors',
                                isSelected
                                  ? canScrape
                                    ? 'bg-emerald-500/10 border-emerald-500/30'
                                    : 'bg-primary/10 border-primary/30'
                                  : 'bg-muted/30 border-border/50 hover:bg-muted/50',
                              )}
                            >
                              <Checkbox
                                checked={isSelected}
                                onCheckedChange={() => toggleScraper(scraper.id)}
                                className="h-3.5 w-3.5"
                              />
                              <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-1.5">
                                  {canScrape ? (
                                    <CheckCircle2 className="h-3 w-3 text-emerald-500 flex-shrink-0" />
                                  ) : (
                                    <Clock className="h-3 w-3 text-primary flex-shrink-0" />
                                  )}
                                  <p className="font-medium truncate">{scraper.name}</p>
                                  {scraper.requires_debrid && (
                                    <Badge
                                      variant="outline"
                                      className="text-[9px] px-1 py-0 h-3.5 bg-blue-500/10 text-blue-600 border-blue-500/30"
                                    >
                                      Debrid
                                    </Badge>
                                  )}
                                </div>
                                {!canScrape && status && status.cooldown_remaining > 0 && (
                                  <p className="text-muted-foreground text-[10px]">
                                    Cooldown: {formatCooldown(status.cooldown_remaining)}
                                  </p>
                                )}
                              </div>
                            </label>
                          )
                        })}
                    </div>

                    {/* Info about debrid scrapers - only show if user doesn't have debrid */}
                    {!scrapeStatus.has_debrid && scrapeStatus.available_scrapers.some((s) => s.requires_debrid) && (
                      <p className="text-xs text-muted-foreground mt-2">
                        Some indexers (Torrentio, MediaFusion) require a debrid service. Configure one in your profile
                        to enable them.
                      </p>
                    )}
                  </div>

                  {/* Force Scrape Option - Moderators/Admins only */}
                  {scrapeStatus.is_moderator && (
                    <label className="flex items-center gap-3 p-3 rounded-xl bg-primary/10 border border-primary/20 cursor-pointer">
                      <Checkbox checked={forceScrap} onCheckedChange={(checked) => setForceScrap(checked as boolean)} />
                      <div className="flex items-center gap-2">
                        <Shield className="h-4 w-4 text-primary" />
                        <div>
                          <p className="text-sm font-medium text-primary">Force scrape (Admin)</p>
                          <p className="text-xs text-muted-foreground">Bypass cooldowns for selected indexers</p>
                        </div>
                      </div>
                    </label>
                  )}

                  {/* Warning if no scrapers can run */}
                  {!canScrapeSelected && !forceScrap && selectedScrapers.size > 0 && (
                    <div className="flex items-start gap-2 p-3 rounded-xl bg-primary/10 text-sm">
                      <AlertCircle className="h-4 w-4 text-primary mt-0.5 flex-shrink-0" />
                      <p className="text-primary">
                        All selected indexers are on cooldown. Wait for cooldowns to expire or select different
                        indexers.
                      </p>
                    </div>
                  )}

                  {/* Warning if no scrapers selected */}
                  {selectedScrapers.size === 0 && (
                    <div className="flex items-start gap-2 p-3 rounded-xl bg-primary/10 text-sm">
                      <AlertCircle className="h-4 w-4 text-primary mt-0.5 flex-shrink-0" />
                      <p className="text-primary">Please select at least one indexer to scrape.</p>
                    </div>
                  )}
                </>
              )}
            </div>

            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => {
                  setDialogOpen(false)
                  setForceScrap(false)
                }}
                className="rounded-xl"
              >
                Cancel
              </Button>
              <Button
                onClick={handleScrape}
                disabled={
                  scrapeMutation.isPending ||
                  statusLoading ||
                  needsEpisodeSelection ||
                  selectedScrapers.size === 0 ||
                  (!canScrapeSelected && !forceScrap)
                }
                className="rounded-xl bg-gradient-to-r from-cyan-500 to-blue-600 hover:from-cyan-600 hover:to-blue-700"
              >
                {scrapeMutation.isPending ? (
                  <>
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    Scraping...
                  </>
                ) : (
                  <>
                    <Zap className="h-4 w-4 mr-2" />
                    {forceScrap ? 'Force Scrape' : `Scrape (${selectedScrapers.size})`}
                  </>
                )}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </TooltipProvider>
  )
}
