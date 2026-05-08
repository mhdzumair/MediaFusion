import { useMemo, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import { ChevronDown, ChevronUp, FolderOpen } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { ContentType } from '@/lib/types'

// Movie catalogs categorized by quality
const MOVIE_CATALOGS = {
  // High quality catalogs (HDRip)
  highQuality: [
    { value: 'arabic_movies', text: 'Arabic Movies' },
    { value: 'bangla_movies', text: 'Bangla Movies' },
    { value: 'english_hdrip', text: 'English HD Movies' },
    { value: 'hindi_hdrip', text: 'Hindi HD Movies' },
    { value: 'kannada_hdrip', text: 'Kannada HD Movies' },
    { value: 'malayalam_hdrip', text: 'Malayalam HD Movies' },
    { value: 'punjabi_movies', text: 'Punjabi Movies' },
    { value: 'tamil_hdrip', text: 'Tamil HD Movies' },
    { value: 'telugu_hdrip', text: 'Telugu HD Movies' },
  ],

  // Low quality catalogs (TCRip)
  lowQuality: [
    { value: 'english_tcrip', text: 'English TCRip Movies' },
    { value: 'hindi_tcrip', text: 'Hindi TCRip Movies' },
    { value: 'kannada_tcrip', text: 'Kannada TCRip Movies' },
    { value: 'malayalam_tcrip', text: 'Malayalam TCRip Movies' },
    { value: 'tamil_tcrip', text: 'Tamil TCRip Movies' },
    { value: 'telugu_tcrip', text: 'Telugu TCRip Movies' },
  ],

  // Quality-independent catalogs (always shown)
  independent: [
    { value: 'anime_movies', text: 'Anime Movies' },
    { value: 'hindi_dubbed', text: 'Hindi Dubbed Movies' },
    { value: 'hindi_old', text: 'Hindi Old Movies' },
    { value: 'kannada_dubbed', text: 'Kannada Dubbed Movies' },
    { value: 'kannada_old', text: 'Kannada Old Movies' },
    { value: 'malayalam_dubbed', text: 'Malayalam Dubbed Movies' },
    { value: 'malayalam_old', text: 'Malayalam Old Movies' },
    { value: 'tamil_dubbed', text: 'Tamil Dubbed Movies' },
    { value: 'tamil_old', text: 'Tamil Old Movies' },
    { value: 'telugu_dubbed', text: 'Telugu Dubbed Movies' },
    { value: 'telugu_old', text: 'Telugu Old Movies' },
  ],
}

// Series catalogs
const SERIES_CATALOGS = [
  { value: 'anime_series', text: 'Anime Series' },
  { value: 'arabic_series', text: 'Arabic Series' },
  { value: 'bangla_series', text: 'Bangla Series' },
  { value: 'english_series', text: 'English Series' },
  { value: 'hindi_series', text: 'Hindi Series' },
  { value: 'kannada_series', text: 'Kannada Series' },
  { value: 'malayalam_series', text: 'Malayalam Series' },
  { value: 'punjabi_series', text: 'Punjabi Series' },
  { value: 'tamil_series', text: 'Tamil Series' },
  { value: 'telugu_series', text: 'Telugu Series' },
]

// Low quality indicators
const LOW_QUALITY_TYPES = ['cam', 'telecine', 'telesync', 'scr', 'screener', 'tc', 'ts', 'hdcam']

interface CatalogSelectorProps {
  contentType: ContentType
  selectedCatalogs: string[]
  onChange: (catalogs: string[]) => void
  quality?: string
  className?: string
  defaultExpanded?: boolean
}

export function CatalogSelector({
  contentType,
  selectedCatalogs,
  onChange,
  quality,
  className,
  defaultExpanded = false,
}: CatalogSelectorProps) {
  const [isExpanded, setIsExpanded] = useState(defaultExpanded)

  // Determine if low quality
  const isLowQuality = useMemo(() => {
    if (!quality) return false
    const qualityLower = quality.toLowerCase()
    return LOW_QUALITY_TYPES.some(type => qualityLower.includes(type))
  }, [quality])

  // Get available catalogs based on content type and quality
  const availableCatalogs = useMemo(() => {
    if (contentType === 'series') {
      return SERIES_CATALOGS
    }
    
    if (contentType === 'movie') {
      const catalogs = [...MOVIE_CATALOGS.independent]
      
      if (isLowQuality) {
        catalogs.push(...MOVIE_CATALOGS.lowQuality)
      } else {
        catalogs.push(...MOVIE_CATALOGS.highQuality)
      }
      
      // Sort alphabetically
      return catalogs.sort((a, b) => a.text.localeCompare(b.text))
    }
    
    // Sports don't use catalogs in the same way
    return []
  }, [contentType, isLowQuality])

  // Toggle a catalog
  const toggleCatalog = (catalogValue: string) => {
    if (selectedCatalogs.includes(catalogValue)) {
      onChange(selectedCatalogs.filter(c => c !== catalogValue))
    } else {
      onChange([...selectedCatalogs, catalogValue])
    }
  }

  // Select all visible catalogs
  const selectAll = () => {
    const allValues = availableCatalogs.map(c => c.value)
    const uniqueValues = [...new Set([...selectedCatalogs, ...allValues])]
    onChange(uniqueValues)
  }

  // Clear all selections
  const clearAll = () => {
    const catalogValues = new Set(availableCatalogs.map(c => c.value))
    onChange(selectedCatalogs.filter(c => !catalogValues.has(c)))
  }

  // Don't render for sports
  if (contentType === 'sports') {
    return null
  }

  return (
    <Card className={cn("border-dashed", className)}>
      <CardHeader className="pb-2 cursor-pointer" onClick={() => setIsExpanded(!isExpanded)}>
        <CardTitle className="text-sm flex items-center justify-between">
          <div className="flex items-center gap-2">
            <FolderOpen className="h-4 w-4" />
            Catalogs
            {selectedCatalogs.length > 0 && (
              <Badge variant="secondary" className="text-[10px]">
                {selectedCatalogs.length} selected
              </Badge>
            )}
          </div>
          <Button variant="ghost" size="sm" className="h-6 w-6 p-0">
            {isExpanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </Button>
        </CardTitle>
        {!isExpanded && selectedCatalogs.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1">
            {selectedCatalogs.slice(0, 3).map(cat => (
              <Badge key={cat} variant="outline" className="text-[10px]">
                {availableCatalogs.find(c => c.value === cat)?.text || cat}
              </Badge>
            ))}
            {selectedCatalogs.length > 3 && (
              <Badge variant="outline" className="text-[10px]">
                +{selectedCatalogs.length - 3} more
              </Badge>
            )}
          </div>
        )}
      </CardHeader>

      {isExpanded && (
        <CardContent className="space-y-3">
          {/* Quality indicator for movies */}
          {contentType === 'movie' && (
            <div className={cn(
              "text-xs px-2 py-1 rounded border",
              isLowQuality 
                ? "bg-yellow-500/10 border-yellow-500/30 text-yellow-600 dark:text-yellow-400" 
                : "bg-green-500/10 border-green-500/30 text-green-600 dark:text-green-400"
            )}>
              {isLowQuality 
                ? `Showing low quality (${quality?.toUpperCase()}) catalogs`
                : 'Showing high quality catalogs'
              }
            </div>
          )}

          {/* Select/Clear buttons */}
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={(e) => { e.stopPropagation(); selectAll(); }}
              className="h-6 text-[10px] flex-1"
            >
              Select All
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={(e) => { e.stopPropagation(); clearAll(); }}
              className="h-6 text-[10px] flex-1"
            >
              Clear All
            </Button>
          </div>

          {/* Catalog checkboxes */}
          <ScrollArea className="h-[150px]">
            <div className="grid grid-cols-2 gap-2">
              {availableCatalogs.map((catalog) => (
                <div
                  key={catalog.value}
                  className="flex items-center space-x-2"
                >
                  <Checkbox
                    id={`catalog-${catalog.value}`}
                    checked={selectedCatalogs.includes(catalog.value)}
                    onCheckedChange={() => toggleCatalog(catalog.value)}
                  />
                  <Label
                    htmlFor={`catalog-${catalog.value}`}
                    className="text-xs cursor-pointer leading-tight"
                  >
                    {catalog.text}
                  </Label>
                </div>
              ))}
            </div>
          </ScrollArea>
        </CardContent>
      )}
    </Card>
  )
}
