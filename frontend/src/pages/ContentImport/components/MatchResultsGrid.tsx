import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { ScrollArea } from '@/components/ui/scroll-area'
import { 
  Star, 
  Clock, 
  Film, 
  Tv, 
  Globe, 
  Languages as LanguagesIcon,
  User,
  Check,
} from 'lucide-react'
import type { TorrentMatch } from '@/lib/api'
import { cn } from '@/lib/utils'

export interface ExtendedMatch extends TorrentMatch {
  imdb_id?: string
  imdb_rating?: number
  runtime?: string
  description?: string
  genres?: string[]
  stars?: string[]
  countries?: string[]
  languages?: string[]
  aka_titles?: string[]
}

interface MatchResultsGridProps {
  matches: ExtendedMatch[]
  selectedIndex?: number | null
  onSelectMatch: (match: ExtendedMatch, index: number) => void
  className?: string
}

export function MatchResultsGrid({
  matches,
  selectedIndex,
  onSelectMatch,
  className,
}: MatchResultsGridProps) {
  if (matches.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
        <Film className="h-12 w-12 mb-3 opacity-50" />
        <p className="text-sm">No matches found</p>
        <p className="text-xs mt-1">Try adjusting the content type or enter the IMDb ID manually</p>
      </div>
    )
  }

  return (
    <ScrollArea className={cn("h-[400px]", className)}>
      <div className="space-y-3 pr-4">
        {matches.map((match, index) => {
          const isSelected = selectedIndex === index
          const TypeIcon = match.type === 'series' ? Tv : Film
          
          return (
            <Card
              key={`${match.id}-${index}`}
              className={cn(
                "p-3 cursor-pointer transition-all hover:border-primary/50",
                isSelected && "border-primary bg-primary/5 ring-1 ring-primary/30"
              )}
              onClick={() => onSelectMatch(match, index)}
            >
              <div className="flex gap-3">
                {/* Poster */}
                <div className="flex-shrink-0 w-20 h-28 rounded-lg overflow-hidden bg-muted">
                  {match.poster ? (
                    <img
                      src={match.poster}
                      alt={match.title}
                      className="w-full h-full object-cover"
                      onError={(e) => {
                        e.currentTarget.style.display = 'none'
                        e.currentTarget.parentElement?.classList.add('flex', 'items-center', 'justify-center')
                      }}
                    />
                  ) : (
                    <div className="w-full h-full flex items-center justify-center">
                      <TypeIcon className="h-8 w-8 text-muted-foreground" />
                    </div>
                  )}
                </div>

                {/* Content */}
                <div className="flex-1 min-w-0">
                  {/* Header */}
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <h4 className="font-semibold truncate">
                        {match.title}
                        {match.year && (
                          <span className="text-muted-foreground font-normal ml-1">
                            ({match.year})
                          </span>
                        )}
                      </h4>
                      {match.aka_titles && match.aka_titles.length > 0 && (
                        <p className="text-xs text-muted-foreground truncate">
                          Also: {match.aka_titles.slice(0, 2).join(' • ')}
                        </p>
                      )}
                    </div>
                    {isSelected && (
                      <div className="flex-shrink-0 p-1 rounded-full bg-primary">
                        <Check className="h-3 w-3 text-white" />
                      </div>
                    )}
                  </div>

                  {/* Meta Info */}
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-1.5 text-xs text-muted-foreground">
                    {match.imdb_rating && (
                      <span className="flex items-center gap-1">
                        <Star className="h-3 w-3 text-primary fill-primary" />
                        {match.imdb_rating.toFixed(1)}
                      </span>
                    )}
                    {match.runtime && (
                      <span className="flex items-center gap-1">
                        <Clock className="h-3 w-3" />
                        {match.runtime}
                      </span>
                    )}
                    <span className="flex items-center gap-1">
                      <TypeIcon className="h-3 w-3" />
                      {match.type.charAt(0).toUpperCase() + match.type.slice(1)}
                    </span>
                    {match.imdb_id && (
                      <Badge variant="outline" className="text-[10px] h-4 px-1">
                        {match.imdb_id}
                      </Badge>
                    )}
                  </div>

                  {/* Description */}
                  {match.description && (
                    <p className="text-xs text-muted-foreground mt-1.5 line-clamp-2">
                      {match.description}
                    </p>
                  )}

                  {/* Genres */}
                  {match.genres && match.genres.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-2">
                      {match.genres.slice(0, 4).map(genre => (
                        <Badge 
                          key={genre} 
                          variant="secondary" 
                          className="text-[10px] h-5 px-1.5"
                        >
                          {genre}
                        </Badge>
                      ))}
                      {match.genres.length > 4 && (
                        <Badge 
                          variant="secondary" 
                          className="text-[10px] h-5 px-1.5"
                        >
                          +{match.genres.length - 4}
                        </Badge>
                      )}
                    </div>
                  )}

                  {/* Additional Info Row */}
                  <div className="flex flex-wrap items-center gap-2 mt-2">
                    {match.countries && match.countries.length > 0 && (
                      <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                        <Globe className="h-3 w-3" />
                        {match.countries.slice(0, 2).join(', ')}
                      </span>
                    )}
                    {match.languages && match.languages.length > 0 && (
                      <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                        <LanguagesIcon className="h-3 w-3" />
                        {match.languages.slice(0, 2).join(', ')}
                      </span>
                    )}
                    {match.stars && match.stars.length > 0 && (
                      <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                        <User className="h-3 w-3" />
                        {match.stars.slice(0, 2).join(', ')}
                      </span>
                    )}
                  </div>
                </div>

                {/* Select Button */}
                <div className="flex-shrink-0 flex flex-col justify-center">
                  <Button
                    variant={isSelected ? "default" : "outline"}
                    size="sm"
                    className={cn(
                      "h-8 text-xs",
                      isSelected && "bg-primary hover:bg-primary/90"
                    )}
                    onClick={(e) => {
                      e.stopPropagation()
                      onSelectMatch(match, index)
                    }}
                  >
                    {isSelected ? (
                      <>
                        <Check className="h-3 w-3 mr-1" />
                        Selected
                      </>
                    ) : (
                      'Select'
                    )}
                  </Button>
                </div>
              </div>
            </Card>
          )
        })}
      </div>
    </ScrollArea>
  )
}

