import { useState, useCallback, useMemo } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Switch } from '@/components/ui/switch'
import { Separator } from '@/components/ui/separator'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Tv,
  Save,
  Loader2,
  Globe,
  Lock,
  Calendar,
  Tag,
  FolderOpen,
  Layers,
  Link2,
  Users,
  FileText,
  Languages,
  Shield,
} from 'lucide-react'
import {
  useCreateUserMetadata,
  useUpdateUserMetadata,
  useUserMetadata,
  useDeleteSeason,
  useDeleteEpisode,
  useAvailableCatalogs,
  useGenres,
} from '@/hooks'
import { useToast } from '@/hooks/use-toast'
import type { UserMediaResponse, UserMediaCreate, UserMediaUpdate, UserSeasonCreate } from '@/lib/api'
import { ImageUrlInput } from './ImageUrlInput'
import { SeasonEpisodeBuilder } from './SeasonEpisodeBuilder'
import { TagSelector } from './TagSelector'

const NUDITY_STATUS_OPTIONS = [
  { value: 'Unknown', label: 'Unknown' },
  { value: 'None', label: 'None' },
  { value: 'Mild', label: 'Mild' },
  { value: 'Moderate', label: 'Moderate' },
  { value: 'Severe', label: 'Severe' },
  { value: 'Disable', label: 'Disable' },
]

interface SeriesMetadataFormProps {
  initialData?: UserMediaResponse
  onSuccess: () => void
  onCancel: () => void
}

export function SeriesMetadataForm({ initialData, onSuccess, onCancel }: SeriesMetadataFormProps) {
  const isEditing = !!initialData

  // Fetch full data if editing (to get seasons/episodes)
  const { data: fullData } = useUserMetadata(isEditing ? initialData.id : undefined)
  const mediaData = fullData || initialData

  // Fetch available genres and catalogs from DB
  const { data: availableCatalogs } = useAvailableCatalogs()
  const { data: availableGenres } = useGenres('series')

  // Form state - Basic fields
  const [title, setTitle] = useState(initialData?.title || '')
  const [originalTitle, setOriginalTitle] = useState(initialData?.original_title || '')
  const [year, setYear] = useState(initialData?.year?.toString() || '')
  const [releaseDate, setReleaseDate] = useState(initialData?.release_date || '')
  const [description, setDescription] = useState(initialData?.description || '')
  const [tagline, setTagline] = useState(initialData?.tagline || '')
  const [status, setStatus] = useState(initialData?.status || '')
  const [website, setWebsite] = useState(initialData?.website || '')
  const [originalLanguage, setOriginalLanguage] = useState(initialData?.original_language || '')
  const [nudityStatus, setNudityStatus] = useState(initialData?.nudity_status || 'Unknown')

  // Images
  const [posterUrl, setPosterUrl] = useState(initialData?.poster_url || '')
  const [backgroundUrl, setBackgroundUrl] = useState(initialData?.background_url || '')
  const [logoUrl, setLogoUrl] = useState(initialData?.logo_url || '')

  // Lists
  const [genres, setGenres] = useState<string[]>(initialData?.genres || [])
  const [catalogs, setCatalogs] = useState<string[]>(initialData?.catalogs || [])
  const [akaTitles, setAkaTitles] = useState<string[]>(initialData?.aka_titles || [])
  const [cast, setCast] = useState<string[]>(initialData?.cast || [])
  const [directors, setDirectors] = useState<string[]>(initialData?.directors || [])
  const [writers, setWriters] = useState<string[]>(initialData?.writers || [])

  // Visibility
  const [isPublic, setIsPublic] = useState(initialData?.is_public ?? true)

  // Seasons (for new series or new seasons on existing series)
  const [seasons, setSeasons] = useState<UserSeasonCreate[]>([])

  // External IDs
  const [imdbId, setImdbId] = useState(initialData?.external_ids?.imdb || '')
  const [tmdbId, setTmdbId] = useState(initialData?.external_ids?.tmdb || '')
  const [tvdbId, setTvdbId] = useState(initialData?.external_ids?.tvdb || '')
  const [malId, setMalId] = useState(initialData?.external_ids?.mal || '')
  const [kitsuId, setKitsuId] = useState(initialData?.external_ids?.kitsu || '')

  const { toast } = useToast()
  const createMetadata = useCreateUserMetadata()
  const updateMetadata = useUpdateUserMetadata()
  const deleteSeason = useDeleteSeason()
  const deleteEpisode = useDeleteEpisode()

  // Get all available suggestions
  const genreSuggestions = useMemo(() => {
    return availableGenres?.map(g => g.name) || []
  }, [availableGenres])

  const catalogSuggestions = useMemo(() => {
    return availableCatalogs?.series.map(c => c.name) || []
  }, [availableCatalogs])

  const handleDeleteExistingSeason = useCallback(async (seasonNumber: number) => {
    if (!initialData) return
    try {
      await deleteSeason.mutateAsync({
        mediaId: initialData.id,
        seasonNumber,
      })
      toast({
        title: 'Season Deleted',
        description: `Season ${seasonNumber} has been deleted`,
      })
    } catch (error) {
      toast({
        title: 'Error',
        description: error instanceof Error ? error.message : 'Failed to delete season',
        variant: 'destructive',
      })
    }
  }, [initialData, deleteSeason, toast])

  const handleDeleteExistingEpisode = useCallback(async (episodeId: number) => {
    if (!initialData) return
    try {
      await deleteEpisode.mutateAsync({
        mediaId: initialData.id,
        episodeId,
      })
      toast({
        title: 'Episode Deleted',
        description: 'Episode has been deleted',
      })
    } catch (error) {
      toast({
        title: 'Error',
        description: error instanceof Error ? error.message : 'Failed to delete episode',
        variant: 'destructive',
      })
    }
  }, [initialData, deleteEpisode, toast])

  const handleSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault()

    if (!title.trim()) {
      toast({
        title: 'Validation Error',
        description: 'Title is required',
        variant: 'destructive',
      })
      return
    }

    try {
      const externalIds: Record<string, string> = {}
      if (imdbId.trim()) externalIds.imdb = imdbId.trim()
      if (tmdbId.trim()) externalIds.tmdb = tmdbId.trim()
      if (tvdbId.trim()) externalIds.tvdb = tvdbId.trim()
      if (malId.trim()) externalIds.mal = malId.trim()
      if (kitsuId.trim()) externalIds.kitsu = kitsuId.trim()

      if (isEditing && initialData) {
        const updateData: UserMediaUpdate = {
          title: title.trim(),
          original_title: originalTitle.trim() || undefined,
          year: year ? parseInt(year) : undefined,
          release_date: releaseDate || undefined,
          description: description.trim() || undefined,
          tagline: tagline.trim() || undefined,
          poster_url: posterUrl.trim() || undefined,
          background_url: backgroundUrl.trim() || undefined,
          logo_url: logoUrl.trim() || undefined,
          status: status.trim() || undefined,
          website: website.trim() || undefined,
          original_language: originalLanguage.trim() || undefined,
          nudity_status: nudityStatus || undefined,
          genres: genres.length > 0 ? genres : undefined,
          catalogs: catalogs.length > 0 ? catalogs : undefined,
          aka_titles: akaTitles.length > 0 ? akaTitles : undefined,
          cast: cast.length > 0 ? cast : undefined,
          directors: directors.length > 0 ? directors : undefined,
          writers: writers.length > 0 ? writers : undefined,
          is_public: isPublic,
          external_ids: Object.keys(externalIds).length > 0 ? externalIds : undefined,
        }

        await updateMetadata.mutateAsync({
          mediaId: initialData.id,
          data: updateData,
        })

        // Note: New seasons are added separately via the API
        // The SeasonEpisodeBuilder handles this through the hooks
      } else {
        const createData: UserMediaCreate = {
          type: 'series',
          title: title.trim(),
          year: year ? parseInt(year) : undefined,
          description: description.trim() || undefined,
          poster_url: posterUrl.trim() || undefined,
          background_url: backgroundUrl.trim() || undefined,
          logo_url: logoUrl.trim() || undefined,
          genres: genres.length > 0 ? genres : undefined,
          catalogs: catalogs.length > 0 ? catalogs : undefined,
          external_ids: Object.keys(externalIds).length > 0 ? externalIds : undefined,
          is_public: isPublic,
          seasons: seasons.length > 0 ? seasons : undefined,
        }

        await createMetadata.mutateAsync(createData)
      }

      onSuccess()
    } catch (error) {
      toast({
        title: 'Error',
        description: error instanceof Error ? error.message : 'Failed to save metadata',
        variant: 'destructive',
      })
    }
  }, [
    title, originalTitle, year, releaseDate, description, tagline, posterUrl, backgroundUrl, logoUrl,
    status, website, originalLanguage, nudityStatus,
    genres, catalogs, akaTitles, cast, directors, writers, isPublic, seasons,
    imdbId, tmdbId, tvdbId, malId, kitsuId,
    isEditing, initialData, createMetadata, updateMetadata, toast, onSuccess
  ])

  const isPending = createMetadata.isPending || updateMetadata.isPending

  return (
    <form onSubmit={handleSubmit}>
      <div className="grid gap-6 lg:grid-cols-3">
        {/* Main Content */}
        <div className="lg:col-span-2 space-y-6">
          {/* Basic Info */}
          <Card className="border-border/50 bg-card/50 backdrop-blur">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Tv className="h-5 w-5 text-green-500" />
                Basic Information
              </CardTitle>
              <CardDescription>
                Enter the series' core details
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2 sm:col-span-2">
                  <Label htmlFor="title">Title *</Label>
                  <Input
                    id="title"
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    placeholder="Enter series title"
                    required
                  />
                </div>

                <div className="space-y-2 sm:col-span-2">
                  <Label htmlFor="originalTitle">Original Title</Label>
                  <Input
                    id="originalTitle"
                    value={originalTitle}
                    onChange={(e) => setOriginalTitle(e.target.value)}
                    placeholder="Original title (if different)"
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="year" className="flex items-center gap-1.5">
                    <Calendar className="h-3.5 w-3.5" />
                    Start Year
                  </Label>
                  <Input
                    id="year"
                    type="number"
                    min="1800"
                    max="2100"
                    value={year}
                    onChange={(e) => setYear(e.target.value)}
                    placeholder="2024"
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="releaseDate" className="flex items-center gap-1.5">
                    <Calendar className="h-3.5 w-3.5" />
                    First Air Date
                  </Label>
                  <Input
                    id="releaseDate"
                    type="date"
                    value={releaseDate}
                    onChange={(e) => setReleaseDate(e.target.value)}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="status">Status</Label>
                  <Input
                    id="status"
                    value={status}
                    onChange={(e) => setStatus(e.target.value)}
                    placeholder="Returning, Ended, Canceled, etc."
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="originalLanguage" className="flex items-center gap-1.5">
                    <Languages className="h-3.5 w-3.5" />
                    Original Language
                  </Label>
                  <Input
                    id="originalLanguage"
                    value={originalLanguage}
                    onChange={(e) => setOriginalLanguage(e.target.value)}
                    placeholder="en, ja, ko, etc."
                    maxLength={10}
                  />
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="tagline">Tagline</Label>
                <Input
                  id="tagline"
                  value={tagline}
                  onChange={(e) => setTagline(e.target.value)}
                  placeholder="Enter a tagline..."
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="description">Description</Label>
                <Textarea
                  id="description"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Enter a description for the series..."
                  rows={4}
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="website">Website</Label>
                <Input
                  id="website"
                  type="url"
                  value={website}
                  onChange={(e) => setWebsite(e.target.value)}
                  placeholder="https://..."
                />
              </div>
            </CardContent>
          </Card>

          {/* Seasons & Episodes */}
          <Card className="border-border/50 bg-card/50 backdrop-blur">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Layers className="h-5 w-5 text-primary" />
                Seasons & Episodes
              </CardTitle>
              <CardDescription>
                {isEditing
                  ? 'Manage seasons and episodes for this series'
                  : 'Add seasons and episodes to your series'}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <SeasonEpisodeBuilder
                seasons={seasons}
                onChange={setSeasons}
                existingSeasons={mediaData?.seasons}
                onDeleteExistingSeason={isEditing ? handleDeleteExistingSeason : undefined}
                onDeleteExistingEpisode={isEditing ? handleDeleteExistingEpisode : undefined}
                isLoading={deleteSeason.isPending || deleteEpisode.isPending}
              />
            </CardContent>
          </Card>

          {/* Content Guidance */}
          <Card className="border-border/50 bg-card/50 backdrop-blur">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Shield className="h-5 w-5 text-orange-500" />
                Content Guidance
              </CardTitle>
              <CardDescription>
                Content ratings and warnings
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="nudityStatus">Nudity Status</Label>
                <Select value={nudityStatus} onValueChange={setNudityStatus}>
                  <SelectTrigger>
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
              </div>
            </CardContent>
          </Card>

          {/* Genres & Catalogs */}
          <Card className="border-border/50 bg-card/50 backdrop-blur">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Tag className="h-5 w-5 text-primary" />
                Genres & Catalogs
              </CardTitle>
              <CardDescription>
                Select from available options or add custom ones
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              {/* Genres */}
              <div className="space-y-2">
                <Label>Genres</Label>
                <TagSelector
                  value={genres}
                  onChange={setGenres}
                  suggestions={genreSuggestions}
                  placeholder="Search or add a genre..."
                  badgeVariant="secondary"
                />
              </div>

              <Separator />

              {/* Catalogs */}
              <div className="space-y-2">
                <Label className="flex items-center gap-1.5">
                  <FolderOpen className="h-3.5 w-3.5" />
                  Catalogs
                </Label>
                <TagSelector
                  value={catalogs}
                  onChange={setCatalogs}
                  suggestions={catalogSuggestions}
                  placeholder="Search or add a catalog..."
                  badgeVariant="outline"
                />
              </div>

              <Separator />

              {/* AKA Titles */}
              <div className="space-y-2">
                <Label className="flex items-center gap-1.5">
                  <FileText className="h-3.5 w-3.5" />
                  Alternative Titles (AKA)
                </Label>
                <TagSelector
                  value={akaTitles}
                  onChange={setAkaTitles}
                  suggestions={[]}
                  placeholder="Add alternative titles..."
                  badgeVariant="outline"
                />
              </div>
            </CardContent>
          </Card>

          {/* Cast & Crew */}
          <Card className="border-border/50 bg-card/50 backdrop-blur">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Users className="h-5 w-5 text-green-500" />
                Cast & Crew
              </CardTitle>
              <CardDescription>
                Add cast members and crew
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              <div className="space-y-2">
                <Label>Cast</Label>
                <TagSelector
                  value={cast}
                  onChange={setCast}
                  suggestions={[]}
                  placeholder="Add cast members..."
                  badgeVariant="secondary"
                />
              </div>

              <Separator />

              <div className="space-y-2">
                <Label>Directors</Label>
                <TagSelector
                  value={directors}
                  onChange={setDirectors}
                  suggestions={[]}
                  placeholder="Add directors..."
                  badgeVariant="outline"
                />
              </div>

              <Separator />

              <div className="space-y-2">
                <Label>Writers</Label>
                <TagSelector
                  value={writers}
                  onChange={setWriters}
                  suggestions={[]}
                  placeholder="Add writers..."
                  badgeVariant="outline"
                />
              </div>
            </CardContent>
          </Card>

          {/* External IDs */}
          <Card className="border-border/50 bg-card/50 backdrop-blur">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Link2 className="h-5 w-5 text-blue-500" />
                External IDs
              </CardTitle>
              <CardDescription>
                Link to existing metadata from external sources. Supports IMDb, TMDB, TVDB, MyAnimeList, and Kitsu.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                <div className="space-y-2">
                  <Label htmlFor="imdb" className="text-xs text-muted-foreground">
                    IMDb ID
                  </Label>
                  <Input
                    id="imdb"
                    value={imdbId}
                    onChange={(e) => setImdbId(e.target.value)}
                    placeholder="tt1234567"
                    className="font-mono text-sm"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="tmdb" className="text-xs text-muted-foreground">
                    TMDB ID
                  </Label>
                  <Input
                    id="tmdb"
                    value={tmdbId}
                    onChange={(e) => setTmdbId(e.target.value)}
                    placeholder="12345"
                    className="font-mono text-sm"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="tvdb" className="text-xs text-muted-foreground">
                    TVDB ID
                  </Label>
                  <Input
                    id="tvdb"
                    value={tvdbId}
                    onChange={(e) => setTvdbId(e.target.value)}
                    placeholder="12345"
                    className="font-mono text-sm"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="mal" className="text-xs text-muted-foreground">
                    MyAnimeList ID
                  </Label>
                  <Input
                    id="mal"
                    value={malId}
                    onChange={(e) => setMalId(e.target.value)}
                    placeholder="12345"
                    className="font-mono text-sm"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="kitsu" className="text-xs text-muted-foreground">
                    Kitsu ID
                  </Label>
                  <Input
                    id="kitsu"
                    value={kitsuId}
                    onChange={(e) => setKitsuId(e.target.value)}
                    placeholder="12345"
                    className="font-mono text-sm"
                  />
                </div>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Sidebar */}
        <div className="space-y-6">
          {/* Images */}
          <Card className="border-border/50 bg-card/50 backdrop-blur">
            <CardHeader>
              <CardTitle className="text-base">Images</CardTitle>
              <CardDescription>
                Add poster, background, and logo images
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <ImageUrlInput
                label="Poster URL"
                value={posterUrl}
                onChange={setPosterUrl}
                aspectRatio="poster"
              />
              <ImageUrlInput
                label="Background URL"
                value={backgroundUrl}
                onChange={setBackgroundUrl}
                aspectRatio="backdrop"
              />
              <ImageUrlInput
                label="Logo URL"
                value={logoUrl}
                onChange={setLogoUrl}
                aspectRatio="logo"
              />
            </CardContent>
          </Card>

          {/* Visibility */}
          <Card className="border-border/50 bg-card/50 backdrop-blur">
            <CardHeader>
              <CardTitle className="text-base">Visibility</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  {isPublic ? (
                    <Globe className="h-4 w-4 text-green-500" />
                  ) : (
                    <Lock className="h-4 w-4 text-primary" />
                  )}
                  <span className="text-sm">
                    {isPublic ? 'Public' : 'Private'}
                  </span>
                </div>
                <Switch
                  checked={isPublic}
                  onCheckedChange={setIsPublic}
                />
              </div>
              <p className="text-xs text-muted-foreground mt-2">
                {isPublic
                  ? 'Anyone can see and link to this metadata'
                  : 'Only you can see and use this metadata'}
              </p>
            </CardContent>
          </Card>

          {/* Actions */}
          <Card className="border-border/50 bg-card/50 backdrop-blur">
            <CardContent className="pt-6">
              <div className="flex flex-col gap-2">
                <Button
                  type="submit"
                  disabled={isPending || !title.trim()}
                  className="w-full bg-gradient-to-r from-primary to-primary/80 hover:from-primary/90 hover:to-primary/70"
                >
                  {isPending ? (
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  ) : (
                    <Save className="h-4 w-4 mr-2" />
                  )}
                  {isEditing ? 'Save Changes' : 'Create Series'}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={onCancel}
                  disabled={isPending}
                >
                  Cancel
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </form>
  )
}
