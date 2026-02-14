import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import type { 
  TorrentAnalyzeResponse, 
  TorrentMatch, 
  ContentType,
  ImportMode,
} from '@/lib/types'
import { 
  Film, 
  Tv, 
  HardDrive, 
  FileVideo, 
  Check,
  Star,
  Calendar,
  Layers,
  Library,
  Database,
} from 'lucide-react'
import { cn } from '@/lib/utils'

// Provider source configurations
const PROVIDERS = {
  imdb: { label: 'IMDB', color: 'bg-yellow-500/20 text-yellow-500 border-yellow-500/30' },
  tmdb: { label: 'TMDB', color: 'bg-blue-500/20 text-blue-500 border-blue-500/30' },
  mal: { label: 'MAL', color: 'bg-cyan-500/20 text-cyan-500 border-cyan-500/30' },
  kitsu: { label: 'Kitsu', color: 'bg-orange-500/20 text-orange-500 border-orange-500/30' },
} as const

// Helper to get a unique identifier for a match
// Uses the same format as the import API expects
function getMatchId(match: TorrentMatch): string {
  // Prefer IMDB ID as it's used directly
  if (match.imdb_id) return match.imdb_id
  // For other providers, prefix with provider name
  if (match.tmdb_id) return `tmdb:${match.tmdb_id}`
  if (match.mal_id) return `mal:${match.mal_id}`
  if (match.kitsu_id) return `kitsu:${match.kitsu_id}`
  // Fallback to id field or generate from title
  return match.id || `${match.title}-${match.year || 'unknown'}`
}

// Helper to get all provider IDs from a match
function getProviderIds(match: TorrentMatch): Array<{ provider: keyof typeof PROVIDERS; id: string }> {
  const ids: Array<{ provider: keyof typeof PROVIDERS; id: string }> = []
  if (match.imdb_id) ids.push({ provider: 'imdb', id: match.imdb_id })
  if (match.tmdb_id) ids.push({ provider: 'tmdb', id: match.tmdb_id })
  if (match.mal_id) ids.push({ provider: 'mal', id: match.mal_id })
  if (match.kitsu_id) ids.push({ provider: 'kitsu', id: match.kitsu_id })
  return ids
}

// Get the primary source provider
function getPrimarySource(match: TorrentMatch): keyof typeof PROVIDERS {
  // Priority: IMDB > TMDB > MAL > Kitsu
  if (match.imdb_id) return 'imdb'
  if (match.tmdb_id) return 'tmdb'
  if (match.mal_id) return 'mal'
  if (match.kitsu_id) return 'kitsu'
  return 'imdb' // fallback
}

interface AnalysisResultsProps {
  result: TorrentAnalyzeResponse
  contentType: ContentType
  selectedMatch: TorrentMatch | null
  onSelectMatch: (match: TorrentMatch | null) => void
  importMode: ImportMode
  onImportModeChange: (mode: ImportMode) => void
}

export function AnalysisResults({
  result,
  contentType,
  selectedMatch,
  onSelectMatch,
  importMode,
  onImportModeChange,
}: AnalysisResultsProps) {
  const hasMultipleFiles = (result.file_count || 0) > 1
  const showImportModeSelector = hasMultipleFiles && contentType !== 'sports'

  return (
    <div className="space-y-4">
      {/* Torrent Info */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <FileVideo className="h-4 w-4" />
            Torrent Info
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <div className="text-sm font-medium truncate" title={result.torrent_name}>
            {result.torrent_name}
          </div>
          
          <div className="flex flex-wrap gap-2 text-xs">
            {result.total_size_readable && (
              <span className="flex items-center gap-1 bg-secondary px-2 py-1 rounded">
                <HardDrive className="h-3 w-3" />
                {result.total_size_readable}
              </span>
            )}
            {result.file_count && (
              <span className="flex items-center gap-1 bg-secondary px-2 py-1 rounded">
                <Layers className="h-3 w-3" />
                {result.file_count} file{result.file_count > 1 ? 's' : ''}
              </span>
            )}
            {result.resolution && (
              <span className="bg-primary/20 text-primary px-2 py-1 rounded">
                {result.resolution}
              </span>
            )}
            {result.quality && (
              <span className="bg-accent/20 text-accent px-2 py-1 rounded">
                {result.quality}
              </span>
            )}
            {result.codec && (
              <span className="bg-muted px-2 py-1 rounded">
                {result.codec}
              </span>
            )}
          </div>

          {result.audio && result.audio.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {result.audio.map((a) => (
                <span key={a} className="text-xs bg-muted/50 px-2 py-0.5 rounded">
                  {a}
                </span>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Import Mode Selector */}
      {showImportModeSelector && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2">
              <Library className="h-4 w-4" />
              Import Mode
            </CardTitle>
          </CardHeader>
          <CardContent>
            <Select value={importMode} onValueChange={(v) => onImportModeChange(v as ImportMode)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="single">
                  Single {contentType === 'movie' ? 'Movie' : 'Series'}
                </SelectItem>
                {contentType === 'movie' && (
                  <SelectItem value="collection">Movie Collection</SelectItem>
                )}
                {contentType === 'series' && (
                  <SelectItem value="pack">Series Pack</SelectItem>
                )}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground mt-2">
              {importMode === 'single' && 'All files link to a single metadata entry'}
              {importMode === 'collection' && 'Each file links to a different movie'}
              {importMode === 'pack' && 'Import multiple series or seasons from this torrent'}
            </p>
          </CardContent>
        </Card>
      )}

      {/* Matches */}
      {result.matches && result.matches.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">
              {result.matches.length} Match{result.matches.length > 1 ? 'es' : ''} Found
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 max-h-[200px] overflow-y-auto">
            {result.matches.map((match, index) => {
              const matchId = getMatchId(match)
              const selectedId = selectedMatch ? getMatchId(selectedMatch) : null
              return (
                <MatchCard
                  key={matchId || `match-${index}`}
                  match={match}
                  selected={matchId === selectedId}
                  onSelect={() => onSelectMatch(match)}
                />
              )
            })}
          </CardContent>
        </Card>
      )}

      {/* No matches warning */}
      {(!result.matches || result.matches.length === 0) && contentType !== 'sports' && (
        <Card className="border-yellow-500/50">
          <CardContent className="pt-4">
            <p className="text-sm text-yellow-500">
              No matches found. You can still import with a custom title.
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  )
}

interface MatchCardProps {
  match: TorrentMatch
  selected: boolean
  onSelect: () => void
}

function MatchCard({ match, selected, onSelect }: MatchCardProps) {
  const isSelected = selected === true
  const providerIds = getProviderIds(match)
  const primarySource = getPrimarySource(match)
  
  // Get the best rating to display
  const rating = match.imdb_rating || match.tmdb_rating || match.mal_rating ||
    (match.kitsu_rating ? parseFloat(match.kitsu_rating) : null)
  
  return (
    <button
      type="button"
      onClick={(e) => {
        e.preventDefault()
        e.stopPropagation()
        onSelect()
      }}
      className={cn(
        "w-full flex gap-3 p-2 rounded-lg text-left transition-colors cursor-pointer",
        isSelected 
          ? "bg-primary/10 border-2 border-primary" 
          : "bg-secondary/50 hover:bg-secondary border-2 border-transparent"
      )}
    >
      {/* Selection indicator */}
      <div className="flex items-center justify-center w-6 flex-shrink-0">
        <div className={cn(
          "w-5 h-5 rounded-full border-2 flex items-center justify-center transition-colors",
          isSelected
            ? "border-primary bg-primary"
            : "border-muted-foreground/50 bg-transparent"
        )}>
          {isSelected && (
            <Check className="h-3 w-3 text-primary-foreground" />
          )}
        </div>
      </div>

      {/* Poster */}
      <div className="w-12 h-16 rounded bg-muted flex-shrink-0 overflow-hidden relative">
        {match.poster ? (
          <img 
            src={match.poster} 
            alt={match.title}
            className="w-full h-full object-cover"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            {match.type === 'movie' ? (
              <Film className="h-5 w-5 text-muted-foreground" />
            ) : (
              <Tv className="h-5 w-5 text-muted-foreground" />
            )}
          </div>
        )}
        {/* Source indicator badge */}
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <div className={cn(
                "absolute bottom-0 left-0 right-0 text-center text-[8px] font-semibold py-0.5",
                PROVIDERS[primarySource].color
              )}>
                {PROVIDERS[primarySource].label}
              </div>
            </TooltipTrigger>
            <TooltipContent side="right" className="text-xs">
              <p>Data source: {PROVIDERS[primarySource].label}</p>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </div>

      {/* Info */}
      <div className="flex-1 min-w-0">
        <h4 className="font-medium text-sm truncate">{match.title}</h4>
        
        <div className="flex items-center gap-2 mt-1 text-xs text-muted-foreground">
          {match.year && (
            <span className="flex items-center gap-1">
              <Calendar className="h-3 w-3" />
              {match.year}
            </span>
          )}
          {rating && (
            <span className="flex items-center gap-1 text-yellow-500">
              <Star className="h-3 w-3 fill-current" />
              {typeof rating === 'number' ? rating.toFixed(1) : rating}
            </span>
          )}
          <span className="capitalize">{match.type}</span>
        </div>

        {/* Provider IDs */}
        <div className="flex flex-wrap gap-1 mt-1">
          {providerIds.map(({ provider, id }) => (
            <TooltipProvider key={provider}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Badge 
                    variant="outline" 
                    className={cn(
                      "text-[9px] h-4 px-1 font-mono",
                      PROVIDERS[provider].color
                    )}
                  >
                    <Database className="h-2 w-2 mr-0.5" />
                    {provider.toUpperCase()}: {id.length > 12 ? `${id.slice(0, 12)}...` : id}
                  </Badge>
                </TooltipTrigger>
                <TooltipContent side="bottom" className="text-xs font-mono">
                  {id}
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          ))}
        </div>

        {match.genres && match.genres.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1">
            {match.genres.slice(0, 3).map((genre) => (
              <span key={genre} className="text-xs bg-muted px-1.5 py-0.5 rounded">
                {genre}
              </span>
            ))}
          </div>
        )}
      </div>
    </button>
  )
}
