import { useState, useEffect, useCallback } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  Edit,
  Loader2,
  Film,
  Tv,
  Radio,
  Database,
  Image,
  Link2,
  Tag,
  Users,
  Globe,
  Folder,
  Star,
  Shield,
  Save,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  useMetadata,
  useUpdateMetadata,
  useCreateGenre,
  useCreateCatalog,
  useCreateStar,
  useCreateParentalCertificate,
} from '@/hooks'
import { adminApi, NUDITY_STATUS_OPTIONS, type MetadataUpdateRequest, type ReferenceItem } from '@/lib/api'
import { useToast } from '@/hooks/use-toast'
import { AsyncMultiSelect, type AsyncMultiSelectOption } from '@/components/ui/async-multi-select'
import { TagInput } from '@/components/ui/tag-input'

// ============================================
// Props
// ============================================

interface AdminMetadataEditSheetProps {
  metaId: number
  title?: string
  year?: number
  poster?: string
  genres?: string[]
  catalogType?: 'movie' | 'series' | 'tv'
  open: boolean
  onOpenChange: (open: boolean) => void
  onSuccess?: () => void
}

// ============================================
// Helper to convert ReferenceItem[] to options
// ============================================

function toOptions(items: ReferenceItem[]): AsyncMultiSelectOption[] {
  return items.map((item) => ({ value: item.name, label: item.name }))
}

// ============================================
// Component
// ============================================

export function AdminMetadataEditSheet({
  metaId,
  title: initialTitle,
  year: initialYear,
  poster: initialPoster,
  genres: initialGenres,
  catalogType = 'movie',
  open,
  onOpenChange,
  onSuccess,
}: AdminMetadataEditSheetProps) {
  const { toast } = useToast()

  // Fetch full metadata when sheet opens
  const { data: metadata, isLoading: metadataLoading } = useMetadata(open ? metaId : undefined)

  // Update mutation
  const updateMetadata = useUpdateMetadata()

  // Create mutations for new items
  const createGenre = useCreateGenre()
  const createCatalog = useCreateCatalog()
  const createStar = useCreateStar()
  const createCert = useCreateParentalCertificate()

  // Form state
  const [formData, setFormData] = useState<MetadataUpdateRequest>({})
  const [isDirty, setIsDirty] = useState(false)

  // Reset form when metadata loads
  useEffect(() => {
    if (metadata && open) {
      setFormData({
        title: metadata.title,
        year: metadata.year ?? undefined,
        end_date: metadata.end_date ?? undefined,
        description: metadata.description ?? undefined,
        poster: metadata.poster ?? undefined,
        background: metadata.background ?? undefined,
        logo: metadata.logo ?? undefined,
        runtime: metadata.runtime ?? undefined,
        website: metadata.website ?? undefined,
        is_poster_working: metadata.is_poster_working,
        is_add_title_to_poster: metadata.is_add_title_to_poster,
        imdb_rating: metadata.imdb_rating ?? undefined,
        tmdb_rating: metadata.tmdb_rating ?? undefined,
        parent_guide_nudity_status: metadata.parent_guide_nudity_status ?? undefined,
        country: metadata.country ?? undefined,
        tv_language: metadata.tv_language ?? undefined,
        genres: metadata.genres ?? [],
        catalogs: metadata.catalogs ?? [],
        stars: metadata.stars ?? [],
        parental_certificates: metadata.parental_certificates ?? [],
        aka_titles: metadata.aka_titles ?? [],
      })
      setIsDirty(false)
    }
  }, [metadata, open])

  // Update field helper
  const updateField = <K extends keyof MetadataUpdateRequest>(field: K, value: MetadataUpdateRequest[K]) => {
    setFormData((prev) => ({ ...prev, [field]: value }))
    setIsDirty(true)
  }

  // ============================================
  // Async Search Functions for MultiSelects
  // ============================================

  const searchGenres = useCallback(async (search: string): Promise<AsyncMultiSelectOption[]> => {
    const response = await adminApi.listGenres({ search, per_page: 50 })
    return toOptions(response.items)
  }, [])

  const searchCatalogs = useCallback(async (search: string): Promise<AsyncMultiSelectOption[]> => {
    const response = await adminApi.listCatalogs({ search, per_page: 50 })
    return toOptions(response.items)
  }, [])

  const searchStars = useCallback(async (search: string): Promise<AsyncMultiSelectOption[]> => {
    const response = await adminApi.listStars({ search, per_page: 50 })
    return toOptions(response.items)
  }, [])

  const searchCerts = useCallback(async (search: string): Promise<AsyncMultiSelectOption[]> => {
    const response = await adminApi.listParentalCertificates({ search, per_page: 50 })
    return toOptions(response.items)
  }, [])

  // ============================================
  // Create Handlers
  // ============================================

  const handleCreateGenre = useCallback(
    async (name: string) => {
      await createGenre.mutateAsync({ name })
    },
    [createGenre],
  )

  const handleCreateCatalog = useCallback(
    async (name: string) => {
      await createCatalog.mutateAsync({ name })
    },
    [createCatalog],
  )

  const handleCreateStar = useCallback(
    async (name: string) => {
      await createStar.mutateAsync({ name })
    },
    [createStar],
  )

  const handleCreateCert = useCallback(
    async (name: string) => {
      await createCert.mutateAsync({ name })
    },
    [createCert],
  )

  // ============================================
  // Submit Handler
  // ============================================

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()

    try {
      await updateMetadata.mutateAsync({ metaId, data: formData })
      toast({
        title: 'Metadata updated',
        description: `Successfully updated ${formData.title || metadata?.title}`,
      })
      setIsDirty(false)
      onSuccess?.()
      onOpenChange(false)
    } catch (error) {
      toast({
        title: 'Error',
        description: error instanceof Error ? error.message : 'Failed to update metadata',
        variant: 'destructive',
      })
    }
  }

  // ============================================
  // Computed values
  // ============================================

  const isMovieOrSeries = catalogType === 'movie' || catalogType === 'series'
  const isSeries = catalogType === 'series'
  const isTV = catalogType === 'tv'

  const getTypeIcon = () => {
    switch (catalogType) {
      case 'movie':
        return <Film className="h-4 w-4" />
      case 'series':
        return <Tv className="h-4 w-4" />
      case 'tv':
        return <Radio className="h-4 w-4" />
      default:
        return <Database className="h-4 w-4" />
    }
  }

  const getTypeBadgeColor = () => {
    switch (catalogType) {
      case 'movie':
        return 'bg-primary/10 text-primary'
      case 'series':
        return 'bg-blue-500/10 text-blue-500'
      case 'tv':
        return 'bg-primary/10 text-primary'
      default:
        return 'bg-gray-500/10 text-gray-500'
    }
  }

  const isLoading = metadataLoading

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full sm:max-w-[600px] p-0 flex flex-col">
        <SheetHeader className="px-6 py-4 border-b bg-gradient-to-r from-primary/5 to-primary/5">
          <SheetTitle className="flex items-center gap-2">
            <div className="p-1.5 rounded-lg bg-gradient-to-br from-primary to-primary/80">
              <Edit className="h-4 w-4 text-white" />
            </div>
            Edit Metadata
          </SheetTitle>
          <SheetDescription className="flex items-center gap-2">
            <Badge className={cn('text-[10px]', getTypeBadgeColor())}>
              {getTypeIcon()}
              <span className="ml-1 capitalize">{catalogType}</span>
            </Badge>
            <span className="text-xs font-mono truncate">{metaId}</span>
          </SheetDescription>
        </SheetHeader>

        <form onSubmit={handleSubmit} className="flex-1 flex flex-col overflow-hidden">
          <ScrollArea className="flex-1 px-6">
            {isLoading ? (
              <div className="py-6 space-y-6">
                {[...Array(6)].map((_, i) => (
                  <div key={i} className="space-y-2">
                    <Skeleton className="h-4 w-24" />
                    <Skeleton className="h-10 w-full" />
                  </div>
                ))}
              </div>
            ) : (
              <div className="py-6 space-y-6">
                {/* Preview Card */}
                <div className="flex gap-4 p-4 rounded-xl bg-gradient-to-br from-primary/5 to-primary/5 border border-border/50">
                  <div className="w-16 h-24 rounded-lg overflow-hidden bg-muted flex-shrink-0">
                    {formData.poster || initialPoster ? (
                      <img
                        src={formData.poster || initialPoster}
                        alt={formData.title || initialTitle}
                        className="w-full h-full object-cover"
                      />
                    ) : (
                      <div className="w-full h-full flex items-center justify-center">{getTypeIcon()}</div>
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <h3 className="font-semibold truncate">{formData.title || initialTitle || 'Untitled'}</h3>
                    <p className="text-sm text-muted-foreground">
                      {formData.year || initialYear || 'N/A'} • {catalogType}
                    </p>
                    <div className="flex flex-wrap gap-1 mt-2">
                      {(formData.genres || initialGenres || []).slice(0, 3).map((g) => (
                        <Badge key={g} variant="outline" className="text-[10px]">
                          {g}
                        </Badge>
                      ))}
                    </div>
                  </div>
                </div>

                {/* Basic Info Section */}
                <div className="space-y-4">
                  <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                    <Database className="h-4 w-4" />
                    Basic Information
                  </div>

                  <div className="grid gap-4">
                    <div className="grid gap-2">
                      <Label htmlFor="title">Title</Label>
                      <Input
                        id="title"
                        value={formData.title ?? ''}
                        onChange={(e) => updateField('title', e.target.value)}
                        className="rounded-xl"
                      />
                    </div>

                    <div className="grid grid-cols-2 gap-4">
                      <div className="grid gap-2">
                        <Label htmlFor="year">Year</Label>
                        <Input
                          id="year"
                          type="number"
                          value={formData.year ?? ''}
                          onChange={(e) => updateField('year', e.target.value ? parseInt(e.target.value) : undefined)}
                          className="rounded-xl"
                        />
                      </div>
                      {isSeries ? (
                        <div className="grid gap-2">
                          <Label htmlFor="end_date">End Date</Label>
                          <Input
                            id="end_date"
                            type="date"
                            value={formData.end_date ?? ''}
                            onChange={(e) => updateField('end_date', e.target.value || undefined)}
                            className="rounded-xl"
                          />
                        </div>
                      ) : (
                        <div className="grid gap-2">
                          <Label htmlFor="runtime">Runtime</Label>
                          <Input
                            id="runtime"
                            value={formData.runtime ?? ''}
                            onChange={(e) => updateField('runtime', e.target.value || undefined)}
                            placeholder="e.g., 2h 30m"
                            className="rounded-xl"
                          />
                        </div>
                      )}
                    </div>

                    <div className="grid gap-2">
                      <Label htmlFor="description">Description</Label>
                      <Textarea
                        id="description"
                        value={formData.description ?? ''}
                        onChange={(e) => updateField('description', e.target.value || undefined)}
                        className="rounded-xl min-h-[80px] resize-none"
                      />
                    </div>

                    <div className="grid gap-2">
                      <Label htmlFor="website">Website</Label>
                      <Input
                        id="website"
                        value={formData.website ?? ''}
                        onChange={(e) => updateField('website', e.target.value || undefined)}
                        placeholder="https://..."
                        className="rounded-xl"
                      />
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

                  <div className="grid gap-4">
                    <div className="grid gap-2">
                      <Label htmlFor="poster">Poster URL</Label>
                      <Input
                        id="poster"
                        value={formData.poster ?? ''}
                        onChange={(e) => updateField('poster', e.target.value || undefined)}
                        placeholder="https://..."
                        className="rounded-xl text-sm"
                      />
                    </div>

                    <div className="grid gap-2">
                      <Label htmlFor="background">Background URL</Label>
                      <Input
                        id="background"
                        value={formData.background ?? ''}
                        onChange={(e) => updateField('background', e.target.value || undefined)}
                        placeholder="https://..."
                        className="rounded-xl text-sm"
                      />
                    </div>

                    {isTV && (
                      <div className="grid gap-2">
                        <Label htmlFor="logo">Logo URL</Label>
                        <Input
                          id="logo"
                          value={formData.logo ?? ''}
                          onChange={(e) => updateField('logo', e.target.value || undefined)}
                          placeholder="https://..."
                          className="rounded-xl text-sm"
                        />
                      </div>
                    )}

                    <div className="flex gap-6">
                      <div className="flex items-center gap-2">
                        <Switch
                          id="is_poster_working"
                          checked={formData.is_poster_working ?? true}
                          onCheckedChange={(checked) => updateField('is_poster_working', checked)}
                        />
                        <Label htmlFor="is_poster_working" className="text-sm cursor-pointer">
                          Poster Working
                        </Label>
                      </div>
                      <div className="flex items-center gap-2">
                        <Switch
                          id="is_add_title_to_poster"
                          checked={formData.is_add_title_to_poster ?? false}
                          onCheckedChange={(checked) => updateField('is_add_title_to_poster', checked)}
                        />
                        <Label htmlFor="is_add_title_to_poster" className="text-sm cursor-pointer">
                          Add Title to Poster
                        </Label>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Ratings Section (Movie/Series only) */}
                {isMovieOrSeries && (
                  <>
                    <Separator />
                    <div className="space-y-4">
                      <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                        <Star className="h-4 w-4" />
                        Ratings & Content
                      </div>

                      <div className="grid grid-cols-3 gap-4">
                        <div className="grid gap-2">
                          <Label htmlFor="imdb_rating">IMDb Rating</Label>
                          <Input
                            id="imdb_rating"
                            type="number"
                            step="0.1"
                            min="0"
                            max="10"
                            value={formData.imdb_rating ?? ''}
                            onChange={(e) =>
                              updateField('imdb_rating', e.target.value ? parseFloat(e.target.value) : undefined)
                            }
                            className="rounded-xl"
                          />
                        </div>
                        <div className="grid gap-2">
                          <Label htmlFor="tmdb_rating">TMDB Rating</Label>
                          <Input
                            id="tmdb_rating"
                            type="number"
                            step="0.1"
                            min="0"
                            max="10"
                            value={formData.tmdb_rating ?? ''}
                            onChange={(e) =>
                              updateField('tmdb_rating', e.target.value ? parseFloat(e.target.value) : undefined)
                            }
                            className="rounded-xl"
                          />
                        </div>
                        <div className="grid gap-2">
                          <Label htmlFor="nudity_status">Nudity Status</Label>
                          <Select
                            value={formData.parent_guide_nudity_status ?? ''}
                            onValueChange={(value) => updateField('parent_guide_nudity_status', value || undefined)}
                          >
                            <SelectTrigger className="rounded-xl">
                              <SelectValue placeholder="Select..." />
                            </SelectTrigger>
                            <SelectContent>
                              {NUDITY_STATUS_OPTIONS.map((opt) => (
                                <SelectItem key={opt.value} value={opt.value}>
                                  {opt.label}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </div>
                      </div>
                    </div>
                  </>
                )}

                {/* TV-specific fields */}
                {isTV && (
                  <>
                    <Separator />
                    <div className="space-y-4">
                      <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                        <Globe className="h-4 w-4" />
                        Channel Information
                      </div>

                      <div className="grid grid-cols-2 gap-4">
                        <div className="grid gap-2">
                          <Label htmlFor="country">Country</Label>
                          <Input
                            id="country"
                            value={formData.country ?? ''}
                            onChange={(e) => updateField('country', e.target.value || undefined)}
                            className="rounded-xl"
                          />
                        </div>
                        <div className="grid gap-2">
                          <Label htmlFor="tv_language">Language</Label>
                          <Input
                            id="tv_language"
                            value={formData.tv_language ?? ''}
                            onChange={(e) => updateField('tv_language', e.target.value || undefined)}
                            className="rounded-xl"
                          />
                        </div>
                      </div>
                    </div>
                  </>
                )}

                <Separator />

                {/* Relationships Section - Genres & Catalogs */}
                <div className="space-y-4">
                  <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                    <Tag className="h-4 w-4" />
                    Categories & Genres
                  </div>

                  <div className="grid gap-4">
                    <div className="grid gap-2">
                      <Label>Genres</Label>
                      <AsyncMultiSelect
                        selected={formData.genres ?? []}
                        onChange={(values) => updateField('genres', values)}
                        onSearch={searchGenres}
                        onCreate={handleCreateGenre}
                        placeholder="Select genres..."
                        searchPlaceholder="Search or add genres..."
                        allowCustom
                      />
                    </div>

                    <div className="grid gap-2">
                      <Label>Catalogs</Label>
                      <AsyncMultiSelect
                        selected={formData.catalogs ?? []}
                        onChange={(values) => updateField('catalogs', values)}
                        onSearch={searchCatalogs}
                        onCreate={handleCreateCatalog}
                        placeholder="Select catalogs..."
                        searchPlaceholder="Search or add catalogs..."
                        allowCustom
                      />
                    </div>
                  </div>
                </div>

                {/* Credits Section (Movie/Series only) */}
                {isMovieOrSeries && (
                  <>
                    <Separator />
                    <div className="space-y-4">
                      <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                        <Users className="h-4 w-4" />
                        Cast & Crew
                      </div>

                      <div className="grid gap-4">
                        <div className="grid gap-2">
                          <Label>Stars/Cast</Label>
                          <AsyncMultiSelect
                            selected={formData.stars ?? []}
                            onChange={(values) => updateField('stars', values)}
                            onSearch={searchStars}
                            onCreate={handleCreateStar}
                            placeholder="Select cast members..."
                            searchPlaceholder="Search or add people..."
                            allowCustom
                            maxDisplayed={5}
                          />
                        </div>
                      </div>
                    </div>

                    <Separator />

                    <div className="space-y-4">
                      <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                        <Shield className="h-4 w-4" />
                        Parental Info
                      </div>

                      <div className="grid gap-2">
                        <Label>Parental Certificates</Label>
                        <AsyncMultiSelect
                          selected={formData.parental_certificates ?? []}
                          onChange={(values) => updateField('parental_certificates', values)}
                          onSearch={searchCerts}
                          onCreate={handleCreateCert}
                          placeholder="Select certificates..."
                          searchPlaceholder="Search or add certificates..."
                          allowCustom
                        />
                      </div>
                    </div>
                  </>
                )}

                <Separator />

                {/* Alternative Titles */}
                <div className="space-y-4">
                  <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                    <Folder className="h-4 w-4" />
                    Alternative Titles
                  </div>

                  <div className="grid gap-2">
                    <Label>AKA Titles</Label>
                    <TagInput
                      value={formData.aka_titles ?? []}
                      onChange={(values) => updateField('aka_titles', values)}
                      placeholder="Add alternative title..."
                    />
                  </div>
                </div>

                <Separator />

                {/* Read-only Info */}
                <div className="space-y-3">
                  <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                    <Link2 className="h-4 w-4" />
                    System Information
                  </div>

                  <div className="grid grid-cols-2 gap-2 text-sm text-muted-foreground bg-muted/30 p-3 rounded-xl">
                    <div>
                      <span className="text-muted-foreground/70">ID:</span>{' '}
                      <span className="font-mono text-xs">{metaId}</span>
                    </div>
                    <div>
                      <span className="text-muted-foreground/70">Type:</span>{' '}
                      <span className="capitalize">{catalogType}</span>
                    </div>
                    <div>
                      <span className="text-muted-foreground/70">Streams:</span> {metadata?.total_streams ?? 0}
                    </div>
                    <div>
                      <span className="text-muted-foreground/70">Created:</span>{' '}
                      {metadata?.created_at ? new Date(metadata.created_at).toLocaleDateString() : 'N/A'}
                    </div>
                  </div>
                </div>
              </div>
            )}
          </ScrollArea>

          <SheetFooter className="px-6 py-4 border-t bg-gradient-to-r from-primary/5 to-primary/5">
            <div className="flex items-center justify-between w-full">
              <div className="text-sm text-muted-foreground">
                {isDirty ? (
                  <Badge variant="secondary" className="bg-primary/10 text-primary">
                    Unsaved changes
                  </Badge>
                ) : (
                  <span className="text-muted-foreground/70">No changes</span>
                )}
              </div>
              <div className="flex gap-2">
                <Button type="button" variant="outline" onClick={() => onOpenChange(false)} className="rounded-xl">
                  Cancel
                </Button>
                <Button
                  type="submit"
                  disabled={!isDirty || updateMetadata.isPending}
                  className="rounded-xl bg-gradient-to-r from-primary to-primary/80 hover:from-primary/90 hover:to-primary/70"
                >
                  {updateMetadata.isPending ? (
                    <>
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      Saving...
                    </>
                  ) : (
                    <>
                      <Save className="mr-2 h-4 w-4" />
                      Save Changes
                    </>
                  )}
                </Button>
              </div>
            </div>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  )
}
