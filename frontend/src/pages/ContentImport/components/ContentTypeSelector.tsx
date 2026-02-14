import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Film, Tv, Trophy, Layers, FolderOpen } from 'lucide-react'
import { 
  CONTENT_TYPE_OPTIONS, 
  SPORTS_CATEGORY_OPTIONS, 
  IMPORT_MODE_OPTIONS,
  type ContentType, 
  type SportsCategory,
  type ImportMode,
} from '@/lib/constants'
import { cn } from '@/lib/utils'

interface ContentTypeSelectorProps {
  value: ContentType
  sportsCategory?: SportsCategory
  importMode?: ImportMode
  onChange: (value: ContentType) => void
  onSportsCategoryChange?: (value: SportsCategory) => void
  onImportModeChange?: (value: ImportMode) => void
  className?: string
  compact?: boolean
  showImportMode?: boolean
  /** Content types to exclude from the selector (e.g. ['tv'] for non-live import tabs) */
  excludeTypes?: ContentType[]
}

const CONTENT_TYPE_ICONS: Record<ContentType, typeof Film> = {
  movie: Film,
  series: Tv,
  sports: Trophy,
  tv: Tv,
}

export function ContentTypeSelector({
  value,
  sportsCategory,
  importMode = 'single',
  onChange,
  onSportsCategoryChange,
  onImportModeChange,
  className,
  compact = false,
  showImportMode = true,
  excludeTypes = [],
}: ContentTypeSelectorProps) {
  // Filter out excluded content types
  const availableOptions = excludeTypes.length > 0
    ? CONTENT_TYPE_OPTIONS.filter(opt => !excludeTypes.includes(opt.value))
    : CONTENT_TYPE_OPTIONS

  // Get import mode options for the current content type
  const modeOptions = value === 'movie' || value === 'series' 
    ? IMPORT_MODE_OPTIONS[value] 
    : null

  return (
    <div className={cn("space-y-3", className)}>
      <Label className="text-sm font-medium">Content Type</Label>
      
      {compact ? (
        // Compact select dropdown
        <Select value={value} onValueChange={(v) => onChange(v as ContentType)}>
          <SelectTrigger className="rounded-lg">
            <SelectValue placeholder="Select content type" />
          </SelectTrigger>
          <SelectContent>
            {availableOptions.map(option => {
              const Icon = CONTENT_TYPE_ICONS[option.value]
              return (
                <SelectItem key={option.value} value={option.value}>
                  <span className="flex items-center gap-2">
                    <Icon className="h-4 w-4" />
                    {option.label}
                  </span>
                </SelectItem>
              )
            })}
          </SelectContent>
        </Select>
      ) : (
        // Full card-style buttons
        <div className="grid grid-cols-3 gap-3">
          {availableOptions.map(option => {
            const Icon = CONTENT_TYPE_ICONS[option.value]
            const isSelected = value === option.value
            
            return (
              <Button
                key={option.value}
                type="button"
                variant="outline"
                className={cn(
                  "flex flex-col items-center gap-2 p-4 h-auto rounded-xl border-2 transition-all",
                  isSelected 
                    ? "border-primary bg-primary/10" 
                    : "border-border/50 hover:border-primary/50 hover:bg-muted/30"
                )}
                onClick={() => onChange(option.value)}
              >
                <div className={cn(
                  "p-2 rounded-lg",
                  isSelected ? "bg-primary/20" : "bg-muted"
                )}>
                  <Icon className={cn(
                    "h-5 w-5",
                    isSelected ? "text-primary" : "text-muted-foreground"
                  )} />
                </div>
                <span className={cn(
                  "font-medium text-sm",
                  isSelected && "text-primary"
                )}>
                  {option.label}
                </span>
                <span className="text-xs text-muted-foreground text-center font-normal">
                  {option.description}
                </span>
              </Button>
            )
          })}
        </div>
      )}

      {/* Import Mode Selector for Movie/Series */}
      {showImportMode && modeOptions && onImportModeChange && (
        <div className="space-y-2 pt-2 border-t border-border/50 mt-3">
          <Label className="text-sm font-medium">Import Mode</Label>
          <div className="grid grid-cols-2 gap-2">
            {modeOptions.map(option => {
              const isSelected = importMode === option.value
              const Icon = option.value === 'single' ? Film : option.value === 'collection' ? Layers : FolderOpen
              
              return (
                <Button
                  key={option.value}
                  type="button"
                  variant="outline"
                  className={cn(
                    "flex items-center gap-3 p-3 h-auto rounded-lg border-2 transition-all justify-start",
                    isSelected 
                      ? "border-primary bg-primary/10" 
                      : "border-border/50 hover:border-primary/50 hover:bg-muted/30"
                  )}
                  onClick={() => onImportModeChange(option.value as ImportMode)}
                >
                  <div className={cn(
                    "p-1.5 rounded-md",
                    isSelected ? "bg-primary/20" : "bg-muted"
                  )}>
                    <Icon className={cn(
                      "h-4 w-4",
                      isSelected ? "text-primary" : "text-muted-foreground"
                    )} />
                  </div>
                  <div className="flex flex-col items-start">
                    <span className={cn(
                      "font-medium text-sm",
                      isSelected && "text-primary"
                    )}>
                      {option.label}
                    </span>
                    <span className="text-xs text-muted-foreground font-normal">
                      {option.description}
                    </span>
                  </div>
                </Button>
              )
            })}
          </div>
        </div>
      )}

      {/* Sports Category Selector */}
      {value === 'sports' && onSportsCategoryChange && (
        <div className="space-y-2 pt-2 border-t border-border/50 mt-3">
          <Label className="text-sm font-medium">Sports Category</Label>
          <Select 
            value={sportsCategory || ''} 
            onValueChange={(v) => onSportsCategoryChange(v as SportsCategory)}
          >
            <SelectTrigger className="rounded-lg">
              <SelectValue placeholder="Select sport category" />
            </SelectTrigger>
            <SelectContent>
              {SPORTS_CATEGORY_OPTIONS.map(option => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
    </div>
  )
}
