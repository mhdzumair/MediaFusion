import { useState } from 'react'
import { Loader2, Sparkles } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Input } from '@/components/ui/input'
import { useToast } from '@/hooks/use-toast'
import { userMetadataApi } from '@/lib/api/user-metadata'
import { scrapersApi } from '@/lib/api/scrapers'
import type { DiscoverItem } from '@/lib/api/discover'
import type { ImportProvider } from '@/lib/api/user-metadata'

interface DiscoverActionDialogProps {
  item: DiscoverItem | null
  onOpenChange: (open: boolean) => void
  /** Called with the new Media id after successful import */
  onSuccess: (mediaId: number, title: string) => void
}

export function DiscoverActionDialog({ item, onOpenChange, onSuccess }: DiscoverActionDialogProps) {
  const { toast } = useToast()
  const [season, setSeason] = useState('1')
  const [episode, setEpisode] = useState('1')
  const [importing, setImporting] = useState(false)

  const open = !!item

  const isSeries = item?.media_type === 'series'
  const provider = (item?.provider === 'anilist' ? 'mal' : item?.provider) as ImportProvider | undefined

  async function handleAddAndScrape() {
    if (!item || !provider) return
    setImporting(true)
    try {
      const mediaType = item.media_type === 'movie' ? 'movie' : 'series'

      const imported = await userMetadataApi.importFromExternal({
        provider,
        external_id: item.external_id,
        media_type: mediaType,
        is_public: true,
      })

      const seasonNum = isSeries ? parseInt(season) || 1 : undefined
      const episodeNum = isSeries ? parseInt(episode) || 1 : undefined

      await scrapersApi.triggerScrape(imported.id, {
        media_type: mediaType === 'movie' ? 'movie' : 'series',
        season: seasonNum,
        episode: episodeNum,
      })

      toast({
        title: 'Added & scraping',
        description: `"${imported.title}" added. Searching for streams…`,
      })
      onSuccess(imported.id, imported.title)
      onOpenChange(false)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to add content'
      toast({ title: 'Error', description: message, variant: 'destructive' })
    } finally {
      setImporting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md" onOpenAutoFocus={(e) => e.preventDefault()}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-primary" />
            Add to Library
          </DialogTitle>
          <DialogDescription>
            <span className="font-medium text-foreground">{item?.title}</span>
            {item?.year ? ` (${item.year})` : ''}
            {' · '}
            {isSeries ? 'Series' : 'Movie'}
            {' · '}
            <span className="capitalize">{item?.provider}</span>
          </DialogDescription>
        </DialogHeader>

        {item?.poster && (
          <div className="flex justify-center">
            <img src={item.poster} alt={item.title} className="h-40 rounded-md object-cover shadow" />
          </div>
        )}

        {item?.overview && <p className="text-sm text-muted-foreground line-clamp-3">{item.overview}</p>}

        {isSeries && (
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label htmlFor="discover-season">Start Season</Label>
              <Input
                id="discover-season"
                type="number"
                min={1}
                value={season}
                onChange={(e) => setSeason(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="discover-episode">Start Episode</Label>
              <Input
                id="discover-episode"
                type="number"
                min={1}
                value={episode}
                onChange={(e) => setEpisode(e.target.value)}
              />
            </div>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={importing}>
            Cancel
          </Button>
          <Button onClick={handleAddAndScrape} disabled={importing}>
            {importing ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Adding…
              </>
            ) : (
              <>
                <Sparkles className="mr-2 h-4 w-4" />
                Add & Scrape
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
