import { useState, useEffect, useMemo } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { 
  Edit,
  Loader2,
  CheckCircle2,
  AlertCircle,
  Image,
  Film,
  Users,
  Link2,
  Tag,
  Globe,
  Folder,
  FileText,
  Shield,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { 
  useCreateSuggestion, 
  useCatalogItem, 
  useAdminGenres, 
  useStars, 
  useParentalCertificates, 
  useCatalogs,
  type CatalogType 
} from '@/hooks'
import { MultiSelect, type MultiSelectOption } from '@/components/ui/multi-select'
import { TagInput } from '@/components/ui/tag-input'
import { NUDITY_STATUS_OPTIONS } from '@/lib/api'

// Helper to get original poster URL (exclude RPDB and similar service URLs)
// The API returns original database URLs, but this provides extra safety
function getOriginalPosterUrl(url: string | undefined): string {
  if (!url) return ''
  
  try {
    const urlObj = new URL(url)
    
    // If this is an RPDB URL, return empty (we want the original from DB)
    // RPDB URLs look like: https://api.ratingposterdb.com/{api_key}/imdb/poster-default/{imdb_id}.jpg
    if (urlObj.hostname.includes('ratingposterdb') || urlObj.hostname.includes('rpdb')) {
      return ''
    }
    
    // Remove any API key query params for safety
    const paramsToRemove = ['apikey', 'api_key', 'key', 'token', 'fallback']
    paramsToRemove.forEach(param => urlObj.searchParams.delete(param))
    
    return urlObj.toString()
  } catch {
    return url
  }
}

type FieldName = 'title' | 'description' | 'year' | 'runtime' | 'poster' | 'background' | 
                 'genres' | 'country' | 'language' | 'aka_titles' | 'cast' | 'directors' | 
                 'writers' | 'imdb_id' | 'tmdb_id' | 'tvdb_id' | 'mal_id' | 'kitsu_id' | 
                 'parental_certificate' | 'catalogs' | 'nudity_status'

interface FieldState {
  value: string
  original: string
  isModified: boolean
}

interface MetadataEditSheetProps {
  mediaId: number
  catalogType?: CatalogType
  trigger?: React.ReactNode
  onSuccess?: () => void
}

export function MetadataEditSheet({
  mediaId,
  catalogType = 'movie',
  trigger,
  onSuccess,
}: MetadataEditSheetProps) {
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [submitResults, setSubmitResults] = useState<{ field: string; success: boolean }[]>([])
  
  // Fetch full metadata when sheet opens
  const { data: metadata, isLoading: metadataLoading } = useCatalogItem(
    catalogType,
    mediaId,
    { enabled: open }
  )
  
  // Fetch reference data for dropdowns - only when sheet is open
  const { data: genresData, isLoading: genresLoading } = useAdminGenres({}, { enabled: open })
  const { data: starsData, isLoading: starsLoading } = useStars({}, { enabled: open })
  const { data: parentalData, isLoading: parentalLoading } = useParentalCertificates({}, { enabled: open })
  const { data: catalogsData, isLoading: catalogsLoading } = useCatalogs({}, { enabled: open })
  
  const createSuggestion = useCreateSuggestion()
  
  // Convert reference data to options
  const genreOptions: MultiSelectOption[] = useMemo(() => 
    genresData?.items?.map(g => ({ value: g.name, label: g.name })) || [],
    [genresData]
  )
  
  const starOptions: MultiSelectOption[] = useMemo(() => 
    starsData?.items?.map(s => ({ value: s.name, label: s.name })) || [],
    [starsData]
  )
  
  const parentalOptions: MultiSelectOption[] = useMemo(() => 
    parentalData?.items?.map(p => ({ value: p.name, label: p.name })) || [],
    [parentalData]
  )
  // Ensure parentalOptions is used (for future expansion)
  void parentalOptions
  
  const catalogOptions: MultiSelectOption[] = useMemo(() => 
    catalogsData?.items?.map(c => ({ value: c.name, label: c.name })) || [],
    [catalogsData]
  )
  
  // Field states - initialized from metadata
  const getInitialFields = (): Record<FieldName, FieldState> => {
    const cleanedPoster = getOriginalPosterUrl(metadata?.poster)
    const cleanedBackground = getOriginalPosterUrl(metadata?.background)
    
    return {
      title: { value: metadata?.title || '', original: metadata?.title || '', isModified: false },
      description: { value: metadata?.description || '', original: metadata?.description || '', isModified: false },
      year: { value: metadata?.year?.toString() || '', original: metadata?.year?.toString() || '', isModified: false },
      runtime: { value: metadata?.runtime || '', original: metadata?.runtime || '', isModified: false },
      poster: { value: cleanedPoster, original: cleanedPoster, isModified: false },
      background: { value: cleanedBackground, original: cleanedBackground, isModified: false },
      genres: { value: metadata?.genres?.join(', ') || '', original: metadata?.genres?.join(', ') || '', isModified: false },
      country: { value: metadata?.country || '', original: metadata?.country || '', isModified: false },
      language: { value: metadata?.tv_language || '', original: metadata?.tv_language || '', isModified: false },
      aka_titles: { value: metadata?.aka_titles?.join(', ') || '', original: metadata?.aka_titles?.join(', ') || '', isModified: false },
      cast: { value: metadata?.cast?.join(', ') || '', original: metadata?.cast?.join(', ') || '', isModified: false },
      directors: { value: metadata?.directors?.join(', ') || '', original: metadata?.directors?.join(', ') || '', isModified: false },
      writers: { value: metadata?.writers?.join(', ') || '', original: metadata?.writers?.join(', ') || '', isModified: false },
      imdb_id: { value: metadata?.external_ids?.imdb || '', original: metadata?.external_ids?.imdb || '', isModified: false },
      tmdb_id: { value: metadata?.external_ids?.tmdb || '', original: metadata?.external_ids?.tmdb || '', isModified: false },
      tvdb_id: { value: metadata?.external_ids?.tvdb || '', original: metadata?.external_ids?.tvdb || '', isModified: false },
      mal_id: { value: metadata?.external_ids?.mal || '', original: metadata?.external_ids?.mal || '', isModified: false },
      kitsu_id: { value: metadata?.external_ids?.kitsu || '', original: metadata?.external_ids?.kitsu || '', isModified: false },
      parental_certificate: { value: '', original: '', isModified: false }, // Not in API yet
      catalogs: { value: metadata?.catalogs?.join(', ') || '', original: metadata?.catalogs?.join(', ') || '', isModified: false },
      nudity_status: { value: metadata?.nudity || 'Unknown', original: metadata?.nudity || 'Unknown', isModified: false },
    }
  }
  
  const [fields, setFields] = useState<Record<FieldName, FieldState>>(getInitialFields())
  
  // Array states for multi-selects
  const [selectedGenres, setSelectedGenres] = useState<string[]>([])
  const [selectedCast, setSelectedCast] = useState<string[]>([])
  const [selectedDirectors, setSelectedDirectors] = useState<string[]>([])
  const [selectedWriters, setSelectedWriters] = useState<string[]>([])
  const [selectedCatalogs, setSelectedCatalogs] = useState<string[]>([])
  const [akaTitles, setAkaTitles] = useState<string[]>([])
  
  // Reset when metadata loads
  useEffect(() => {
    if (metadata && open) {
      setFields(getInitialFields())
      setSelectedGenres(metadata.genres || [])
      setSelectedCast(metadata.cast || [])
      setSelectedDirectors(metadata.directors || [])
      setSelectedWriters(metadata.writers || [])
      setSelectedCatalogs(metadata.catalogs || [])
      setAkaTitles(metadata.aka_titles || [])
      setReason('')
      setSubmitResults([])
    }
  }, [metadata, open])
  
  const updateField = (fieldName: FieldName, value: string) => {
    setFields(prev => ({
      ...prev,
      [fieldName]: {
        ...prev[fieldName],
        value,
        isModified: value !== prev[fieldName].original,
      },
    }))
  }
  
  // Track array field modifications
  const genresModified = useMemo(() => {
    const original = metadata?.genres || []
    return JSON.stringify([...selectedGenres].sort()) !== JSON.stringify([...original].sort())
  }, [selectedGenres, metadata?.genres])
  
  const castModified = useMemo(() => {
    const original = metadata?.cast || []
    return JSON.stringify([...selectedCast].sort()) !== JSON.stringify([...original].sort())
  }, [selectedCast, metadata?.cast])
  
  const directorsModified = useMemo(() => {
    const original = metadata?.directors || []
    return JSON.stringify([...selectedDirectors].sort()) !== JSON.stringify([...original].sort())
  }, [selectedDirectors, metadata?.directors])
  
  const writersModified = useMemo(() => {
    const original = metadata?.writers || []
    return JSON.stringify([...selectedWriters].sort()) !== JSON.stringify([...original].sort())
  }, [selectedWriters, metadata?.writers])
  
  const catalogsModified = useMemo(() => {
    const original = metadata?.catalogs || []
    return JSON.stringify([...selectedCatalogs].sort()) !== JSON.stringify([...original].sort())
  }, [selectedCatalogs, metadata?.catalogs])
  
  const akaTitlesModified = useMemo(() => {
    const original = metadata?.aka_titles || []
    return JSON.stringify([...akaTitles].sort()) !== JSON.stringify([...original].sort())
  }, [akaTitles, metadata?.aka_titles])
  
  // Calculate all modifications
  const modifiedFields = useMemo(() => {
    const result: { field: FieldName; currentValue: string; newValue: string }[] = []
    
    // Check string fields
    Object.entries(fields).forEach(([key, state]) => {
      if (state.isModified && !['genres', 'cast', 'directors', 'writers', 'catalogs', 'aka_titles'].includes(key)) {
        result.push({ field: key as FieldName, currentValue: state.original, newValue: state.value })
      }
    })
    
    // Check array fields
    if (genresModified) {
      result.push({ 
        field: 'genres', 
        currentValue: metadata?.genres?.join(', ') || '', 
        newValue: selectedGenres.join(', ') 
      })
    }
    if (castModified) {
      result.push({ 
        field: 'cast', 
        currentValue: metadata?.cast?.join(', ') || '', 
        newValue: selectedCast.join(', ') 
      })
    }
    if (directorsModified) {
      result.push({ 
        field: 'directors', 
        currentValue: metadata?.directors?.join(', ') || '', 
        newValue: selectedDirectors.join(', ') 
      })
    }
    if (writersModified) {
      result.push({ 
        field: 'writers', 
        currentValue: metadata?.writers?.join(', ') || '', 
        newValue: selectedWriters.join(', ') 
      })
    }
    if (catalogsModified) {
      result.push({ 
        field: 'catalogs', 
        currentValue: metadata?.catalogs?.join(', ') || '', 
        newValue: selectedCatalogs.join(', ') 
      })
    }
    if (akaTitlesModified) {
      result.push({ 
        field: 'aka_titles', 
        currentValue: metadata?.aka_titles?.join(', ') || '', 
        newValue: akaTitles.join(', ') 
      })
    }
    
    return result
  }, [fields, genresModified, castModified, directorsModified, writersModified, catalogsModified, akaTitlesModified, metadata, selectedGenres, selectedCast, selectedDirectors, selectedWriters, selectedCatalogs, akaTitles])
  
  const modifiedCount = modifiedFields.length
  
  const handleSubmit = async () => {
    if (modifiedCount === 0) return
    
    setIsSubmitting(true)
    setSubmitResults([])
    const results: { field: string; success: boolean }[] = []
    
    for (const { field, currentValue, newValue } of modifiedFields) {
      try {
        // Map field names to API field names
        let apiFieldName = field
        if (field === 'parental_certificate') continue // Skip for now
        if (field === 'catalogs') continue // Skip - might not be in suggestions API
        
        await createSuggestion.mutateAsync({
          mediaId,
          data: {
            field_name: apiFieldName as any,
            current_value: currentValue || undefined,
            suggested_value: newValue,
            reason: reason.trim() || undefined,
          },
        })
        results.push({ field, success: true })
      } catch (error) {
        results.push({ field, success: false })
      }
    }
    
    setSubmitResults(results)
    setIsSubmitting(false)
    
    const successCount = results.filter(r => r.success).length
    if (successCount > 0 && successCount === results.length) {
      setTimeout(() => {
        setOpen(false)
        onSuccess?.()
      }, 1500)
    }
  }
  
  const isLoading = metadataLoading || genresLoading || starsLoading || parentalLoading || catalogsLoading

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        {trigger || (
          <Button variant="outline" size="sm" className="gap-1.5 rounded-xl">
            <Edit className="h-4 w-4" />
            Edit Metadata
          </Button>
        )}
      </SheetTrigger>
      
      <SheetContent className="w-full sm:max-w-[540px] p-0 flex flex-col">
        <SheetHeader className="px-6 py-4 border-b">
          <SheetTitle className="flex items-center gap-2">
            <Edit className="h-5 w-5 text-primary" />
            Edit Metadata
          </SheetTitle>
          <SheetDescription>
            Suggest corrections to this content's information
          </SheetDescription>
        </SheetHeader>
        
        <ScrollArea className="flex-1 px-6">
          {isLoading ? (
            <div className="py-6 space-y-6">
              {[...Array(5)].map((_, i) => (
                <div key={i} className="space-y-2">
                  <Skeleton className="h-4 w-20" />
                  <Skeleton className="h-10 w-full" />
                </div>
              ))}
            </div>
          ) : (
            <div className="py-6 space-y-6">
              {/* Preview */}
              <div className="flex gap-4 p-4 rounded-xl bg-muted/50">
                <div className="w-16 h-24 rounded-lg overflow-hidden bg-muted flex-shrink-0">
                  {metadata?.poster ? (
                    <img 
                      src={getOriginalPosterUrl(metadata.poster) || metadata.poster} 
                      alt={metadata.title}
                      className="w-full h-full object-cover"
                    />
                  ) : (
                    <div className="w-full h-full flex items-center justify-center">
                      <Film className="h-6 w-6 text-muted-foreground" />
                    </div>
                  )}
                </div>
                <div className="flex-1 min-w-0">
                  <h3 className="font-semibold truncate">{metadata?.title}</h3>
                  <p className="text-sm text-muted-foreground">
                    {metadata?.year} • {catalogType}
                  </p>
                  {metadata?.imdb_rating && (
                    <p className="text-sm text-primary">★ {metadata.imdb_rating.toFixed(1)}</p>
                  )}
                </div>
              </div>
              
              {/* Basic Info Section */}
              <div className="space-y-4">
                <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                  <FileText className="h-4 w-4" />
                  Basic Information
                </div>
                
                <div className="space-y-3">
                  <div className="space-y-1.5">
                    <Label className="text-xs">Title</Label>
                    <Input
                      value={fields.title.value}
                      onChange={(e) => updateField('title', e.target.value)}
                      className={cn(
                        'rounded-xl',
                        fields.title.isModified && 'border-primary/50 bg-primary/5'
                      )}
                    />
                  </div>
                  
                  <div className="space-y-1.5">
                    <Label className="text-xs">Description</Label>
                    <Textarea
                      value={fields.description.value}
                      onChange={(e) => updateField('description', e.target.value)}
                      rows={3}
                      className={cn(
                        'rounded-xl resize-none',
                        fields.description.isModified && 'border-primary/50 bg-primary/5'
                      )}
                    />
                  </div>
                  
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1.5">
                      <Label className="text-xs">Year</Label>
                      <Input
                        type="number"
                        value={fields.year.value}
                        onChange={(e) => updateField('year', e.target.value)}
                        className={cn(
                          'rounded-xl',
                          fields.year.isModified && 'border-primary/50 bg-primary/5'
                        )}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label className="text-xs">Runtime</Label>
                      <Input
                        value={fields.runtime.value}
                        onChange={(e) => updateField('runtime', e.target.value)}
                        placeholder="e.g., 2h 30m"
                        className={cn(
                          'rounded-xl',
                          fields.runtime.isModified && 'border-primary/50 bg-primary/5'
                        )}
                      />
                    </div>
                  </div>
                </div>
              </div>
              
              <Separator />
              
              {/* Media Section */}
              <div className="space-y-4">
                <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                  <Image className="h-4 w-4" />
                  Media
                </div>
                
                <div className="space-y-3">
                  <div className="space-y-1.5">
                    <Label className="text-xs">Poster URL</Label>
                    <Input
                      value={fields.poster.value}
                      onChange={(e) => updateField('poster', e.target.value)}
                      placeholder="https://..."
                      className={cn(
                        'rounded-xl text-xs',
                        fields.poster.isModified && 'border-primary/50 bg-primary/5'
                      )}
                    />
                  </div>
                  
                  <div className="space-y-1.5">
                    <Label className="text-xs">Background URL</Label>
                    <Input
                      value={fields.background.value}
                      onChange={(e) => updateField('background', e.target.value)}
                      placeholder="https://..."
                      className={cn(
                        'rounded-xl text-xs',
                        fields.background.isModified && 'border-primary/50 bg-primary/5'
                      )}
                    />
                  </div>
                </div>
              </div>
              
              <Separator />
              
              {/* External IDs Section */}
              <div className="space-y-4">
                <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                  <Link2 className="h-4 w-4" />
                  External IDs
                </div>
                
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label className="text-xs">IMDb ID</Label>
                    <Input
                      value={fields.imdb_id.value}
                      onChange={(e) => updateField('imdb_id', e.target.value)}
                      placeholder="tt1234567"
                      className={cn(
                        'rounded-xl',
                        fields.imdb_id.isModified && 'border-primary/50 bg-primary/5'
                      )}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-xs">TMDB ID</Label>
                    <Input
                      value={fields.tmdb_id.value}
                      onChange={(e) => updateField('tmdb_id', e.target.value)}
                      placeholder="12345"
                      className={cn(
                        'rounded-xl',
                        fields.tmdb_id.isModified && 'border-primary/50 bg-primary/5'
                      )}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-xs">TVDB ID</Label>
                    <Input
                      value={fields.tvdb_id.value}
                      onChange={(e) => updateField('tvdb_id', e.target.value)}
                      placeholder="123456"
                      className={cn(
                        'rounded-xl',
                        fields.tvdb_id.isModified && 'border-primary/50 bg-primary/5'
                      )}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-xs">MAL ID</Label>
                    <Input
                      value={fields.mal_id.value}
                      onChange={(e) => updateField('mal_id', e.target.value)}
                      placeholder="12345"
                      className={cn(
                        'rounded-xl',
                        fields.mal_id.isModified && 'border-primary/50 bg-primary/5'
                      )}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-xs">Kitsu ID</Label>
                    <Input
                      value={fields.kitsu_id.value}
                      onChange={(e) => updateField('kitsu_id', e.target.value)}
                      placeholder="12345"
                      className={cn(
                        'rounded-xl',
                        fields.kitsu_id.isModified && 'border-primary/50 bg-primary/5'
                      )}
                    />
                  </div>
                </div>
              </div>
              
              <Separator />
              
              {/* Classification Section */}
              <div className="space-y-4">
                <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                  <Globe className="h-4 w-4" />
                  Classification
                </div>
                
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label className="text-xs">Country</Label>
                    <Input
                      value={fields.country.value}
                      onChange={(e) => updateField('country', e.target.value)}
                      placeholder="United States"
                      className={cn(
                        'rounded-xl',
                        fields.country.isModified && 'border-primary/50 bg-primary/5'
                      )}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-xs">Language</Label>
                    <Input
                      value={fields.language.value}
                      onChange={(e) => updateField('language', e.target.value)}
                      placeholder="English"
                      className={cn(
                        'rounded-xl',
                        fields.language.isModified && 'border-primary/50 bg-primary/5'
                      )}
                    />
                  </div>
                </div>
              </div>
              
              <Separator />
              
              {/* Content Guidance Section */}
              <div className="space-y-4">
                <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                  <Shield className="h-4 w-4" />
                  Content Guidance
                </div>
                
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <Label className="text-xs">Nudity Status</Label>
                    {fields.nudity_status.isModified && (
                      <Badge variant="secondary" className="text-[10px] px-1.5 py-0 bg-primary/20 text-primary">
                        Modified
                      </Badge>
                    )}
                  </div>
                  <Select
                    value={fields.nudity_status.value}
                    onValueChange={(value) => updateField('nudity_status', value)}
                  >
                    <SelectTrigger className={cn(
                      'rounded-xl',
                      fields.nudity_status.isModified && 'border-primary/50 bg-primary/5'
                    )}>
                      <SelectValue placeholder="Select nudity status" />
                    </SelectTrigger>
                    <SelectContent>
                      {NUDITY_STATUS_OPTIONS.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <p className="text-xs text-muted-foreground">
                    Used for content filtering and parental controls
                  </p>
                </div>
              </div>
              
              <Separator />
              
              {/* Relationships Section */}
              <div className="space-y-4">
                <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                  <Tag className="h-4 w-4" />
                  Categories & Genres
                </div>
                
                <div className="space-y-3">
                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between">
                      <Label className="text-xs">Genres</Label>
                      {genresModified && (
                        <Badge variant="secondary" className="text-[10px] px-1.5 py-0 bg-primary/20 text-primary">
                          Modified
                        </Badge>
                      )}
                    </div>
                    <MultiSelect
                      options={genreOptions}
                      selected={selectedGenres}
                      onChange={setSelectedGenres}
                      placeholder="Select genres..."
                      searchPlaceholder="Search genres..."
                      allowCustom
                      isLoading={genresLoading}
                    />
                  </div>
                  
                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between">
                      <Label className="text-xs">Catalogs</Label>
                      {catalogsModified && (
                        <Badge variant="secondary" className="text-[10px] px-1.5 py-0 bg-primary/20 text-primary">
                          Modified
                        </Badge>
                      )}
                    </div>
                    <MultiSelect
                      options={catalogOptions}
                      selected={selectedCatalogs}
                      onChange={setSelectedCatalogs}
                      placeholder="Select catalogs..."
                      searchPlaceholder="Search catalogs..."
                      isLoading={catalogsLoading}
                    />
                  </div>
                </div>
              </div>
              
              <Separator />
              
              {/* Credits Section */}
              <div className="space-y-4">
                <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                  <Users className="h-4 w-4" />
                  Credits
                </div>
                
                <div className="space-y-3">
                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between">
                      <Label className="text-xs">Cast</Label>
                      {castModified && (
                        <Badge variant="secondary" className="text-[10px] px-1.5 py-0 bg-primary/20 text-primary">
                          Modified
                        </Badge>
                      )}
                    </div>
                    <MultiSelect
                      options={starOptions}
                      selected={selectedCast}
                      onChange={setSelectedCast}
                      placeholder="Select cast members..."
                      searchPlaceholder="Search people..."
                      allowCustom
                      isLoading={starsLoading}
                      maxDisplayed={5}
                    />
                  </div>
                  
                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between">
                      <Label className="text-xs">Directors</Label>
                      {directorsModified && (
                        <Badge variant="secondary" className="text-[10px] px-1.5 py-0 bg-primary/20 text-primary">
                          Modified
                        </Badge>
                      )}
                    </div>
                    <MultiSelect
                      options={starOptions}
                      selected={selectedDirectors}
                      onChange={setSelectedDirectors}
                      placeholder="Select directors..."
                      searchPlaceholder="Search people..."
                      allowCustom
                      isLoading={starsLoading}
                    />
                  </div>
                  
                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between">
                      <Label className="text-xs">Writers</Label>
                      {writersModified && (
                        <Badge variant="secondary" className="text-[10px] px-1.5 py-0 bg-primary/20 text-primary">
                          Modified
                        </Badge>
                      )}
                    </div>
                    <MultiSelect
                      options={starOptions}
                      selected={selectedWriters}
                      onChange={setSelectedWriters}
                      placeholder="Select writers..."
                      searchPlaceholder="Search people..."
                      allowCustom
                      isLoading={starsLoading}
                    />
                  </div>
                </div>
              </div>
              
              <Separator />
              
              {/* Alternative Titles Section */}
              <div className="space-y-4">
                <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                  <Folder className="h-4 w-4" />
                  Alternative Titles
                </div>
                
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <Label className="text-xs">AKA Titles</Label>
                    {akaTitlesModified && (
                      <Badge variant="secondary" className="text-[10px] px-1.5 py-0 bg-primary/20 text-primary">
                        Modified
                      </Badge>
                    )}
                  </div>
                  <TagInput
                    value={akaTitles}
                    onChange={setAkaTitles}
                    placeholder="Add alternative title..."
                  />
                </div>
              </div>
              
              <Separator />
              
              {/* Reason Section */}
              <div className="space-y-1.5">
                <Label className="text-xs">Reason for changes (optional)</Label>
                <Textarea
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="Explain why these changes are needed..."
                  rows={2}
                  className="rounded-xl resize-none"
                />
              </div>
              
              {/* Submit Results */}
              {submitResults.length > 0 && (
                <div className="p-4 rounded-xl bg-muted/50 space-y-2">
                  <p className="text-sm font-medium">Results</p>
                  {submitResults.map(({ field, success }) => (
                    <div key={field} className="flex items-center gap-2 text-sm">
                      {success ? (
                        <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                      ) : (
                        <AlertCircle className="h-4 w-4 text-red-500" />
                      )}
                      <span className="capitalize">{field.replace('_', ' ')}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </ScrollArea>
        
        <SheetFooter className="px-6 py-4 border-t">
          <div className="flex items-center justify-between w-full">
            <div className="text-sm text-muted-foreground">
              {modifiedCount > 0 ? (
                <span className="text-primary font-medium">{modifiedCount} change{modifiedCount !== 1 ? 's' : ''}</span>
              ) : (
                'No changes'
              )}
            </div>
            <Button
              onClick={handleSubmit}
              disabled={modifiedCount === 0 || isSubmitting}
              className="bg-gradient-to-r from-primary to-primary/80 hover:from-primary/90 hover:to-primary/70 rounded-xl"
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Submitting...
                </>
              ) : (
                `Submit ${modifiedCount} Edit${modifiedCount !== 1 ? 's' : ''}`
              )}
            </Button>
          </div>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
