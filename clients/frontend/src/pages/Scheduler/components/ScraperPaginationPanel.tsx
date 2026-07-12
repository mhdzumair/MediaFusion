import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import {
  supportsArabTorrentsFilters,
  supportsScrapeAll,
  type ScraperPaginationFormState,
} from '@/lib/scraperPagination'

interface ScraperPaginationPanelProps {
  jobId: string
  value: ScraperPaginationFormState
  onChange: (value: ScraperPaginationFormState) => void
  disabled?: boolean
  compact?: boolean
}

function clampPages(value: number): number {
  if (!Number.isFinite(value)) return 1
  return Math.min(100, Math.max(1, Math.trunc(value)))
}

function clampStartPage(value: number): number {
  if (!Number.isFinite(value)) return 1
  return Math.max(1, Math.trunc(value))
}

export function ScraperPaginationPanel({
  jobId,
  value,
  onChange,
  disabled = false,
  compact = false,
}: ScraperPaginationPanelProps) {
  const showScrapeAll = supportsScrapeAll(jobId)
  const showArabFilters = supportsArabTorrentsFilters(jobId)
  const listingDisabled = disabled || value.scrapeAll

  return (
    <div className={compact ? 'space-y-3' : 'space-y-4 rounded-lg border border-border/50 bg-muted/20 p-4'}>
      {!compact && (
        <div>
          <p className="text-sm font-medium">Listing pagination</p>
          <p className="text-xs text-muted-foreground mt-1">
            Scheduled runs default to <strong>1 page</strong> for new content. Increase pages for a deeper manual or
            periodic backfill scrape.
          </p>
        </div>
      )}

      {showScrapeAll && (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-border/40 bg-background/40 p-3">
          <div>
            <Label htmlFor={`scrape-all-${jobId}`} className="text-sm">
              Scrape all pages
            </Label>
            <p className="text-xs text-muted-foreground">
              Follow every listing page until pagination ends (ext.to only).
            </p>
          </div>
          <Switch
            id={`scrape-all-${jobId}`}
            checked={value.scrapeAll}
            disabled={disabled}
            onCheckedChange={(checked) => onChange({ ...value, scrapeAll: checked })}
          />
        </div>
      )}

      <div className={`grid gap-3 ${compact ? 'grid-cols-2' : 'grid-cols-2 md:grid-cols-4'}`}>
        <div className="space-y-1.5">
          <Label htmlFor={`pages-${jobId}`}>Pages</Label>
          <Input
            id={`pages-${jobId}`}
            type="number"
            min={1}
            max={100}
            value={value.pages}
            disabled={listingDisabled}
            onChange={(event) =>
              onChange({
                ...value,
                pages: clampPages(Number(event.target.value)),
              })
            }
          />
          <p className="text-xs text-muted-foreground">Pages to scrape per run</p>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`start-page-${jobId}`}>Start page</Label>
          <Input
            id={`start-page-${jobId}`}
            type="number"
            min={1}
            value={value.startPage}
            disabled={listingDisabled}
            onChange={(event) =>
              onChange({
                ...value,
                startPage: clampStartPage(Number(event.target.value)),
              })
            }
          />
          <p className="text-xs text-muted-foreground">First listing page number</p>
        </div>
      </div>

      {showArabFilters && (
        <div className="grid gap-3 md:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor={`search-keyword-${jobId}`}>Search keyword</Label>
            <Input
              id={`search-keyword-${jobId}`}
              value={value.searchKeyword}
              disabled={disabled}
              placeholder="Optional forum search"
              onChange={(event) => onChange({ ...value, searchKeyword: event.target.value })}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`catalog-id-${jobId}`}>Catalog filter</Label>
            <Input
              id={`catalog-id-${jobId}`}
              value={value.scrapCatalogId}
              disabled={disabled}
              placeholder="all"
              onChange={(event) => onChange({ ...value, scrapCatalogId: event.target.value })}
            />
          </div>
        </div>
      )}
    </div>
  )
}
