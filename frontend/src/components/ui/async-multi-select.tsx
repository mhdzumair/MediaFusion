'use client'

import * as React from 'react'
import { X, Check, ChevronsUpDown, Plus, Loader2, ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { ScrollArea } from '@/components/ui/scroll-area'

export interface AsyncMultiSelectOption {
  value: string
  label: string
}

export interface AsyncMultiSelectProps {
  // Selected values (array of strings)
  selected: string[]
  onChange: (selected: string[]) => void

  // Async data loading
  onSearch: (search: string) => Promise<AsyncMultiSelectOption[]>
  onLoadMore?: () => Promise<AsyncMultiSelectOption[]>
  hasMore?: boolean

  // Optionally pre-load initial options
  initialOptions?: AsyncMultiSelectOption[]

  // UI customization
  placeholder?: string
  searchPlaceholder?: string
  emptyMessage?: string
  allowCustom?: boolean
  disabled?: boolean
  className?: string
  maxDisplayed?: number

  // Callbacks for creating new items
  onCreate?: (value: string) => Promise<void>

  // Debounce delay for search (ms)
  debounceMs?: number
}

export function AsyncMultiSelect({
  selected,
  onChange,
  onSearch,
  onLoadMore,
  hasMore = false,
  initialOptions = [],
  placeholder = 'Select items...',
  searchPlaceholder = 'Search...',
  emptyMessage = 'No results found.',
  allowCustom = false,
  disabled = false,
  className,
  maxDisplayed = 3,
  onCreate,
  debounceMs = 300,
}: AsyncMultiSelectProps) {
  const [open, setOpen] = React.useState(false)
  const [search, setSearch] = React.useState('')
  const [debouncedSearch, setDebouncedSearch] = React.useState('')
  const [options, setOptions] = React.useState<AsyncMultiSelectOption[]>(initialOptions)
  const [isLoading, setIsLoading] = React.useState(false)
  const [isLoadingMore, setIsLoadingMore] = React.useState(false)
  const [isCreating, setIsCreating] = React.useState(false)
  const [internalHasMore, setInternalHasMore] = React.useState(hasMore)

  const scrollRef = React.useRef<HTMLDivElement>(null)

  // Debounce search input
  React.useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(search)
    }, debounceMs)
    return () => clearTimeout(timer)
  }, [search, debounceMs])

  // Fetch options when search changes
  React.useEffect(() => {
    if (!open) return

    let cancelled = false

    const fetchOptions = async () => {
      setIsLoading(true)
      try {
        const results = await onSearch(debouncedSearch)
        if (!cancelled) {
          setOptions(results)
          // Reset hasMore when search changes
          setInternalHasMore(hasMore)
        }
      } catch (error) {
        console.error('Failed to fetch options:', error)
        if (!cancelled) {
          setOptions([])
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false)
        }
      }
    }

    fetchOptions()

    return () => {
      cancelled = true
    }
  }, [debouncedSearch, open, onSearch, hasMore])

  // Load initial options when popover opens
  React.useEffect(() => {
    if (open && options.length === 0 && initialOptions.length === 0) {
      onSearch('').then(setOptions).catch(console.error)
    }
  }, [open])

  const handleSelect = (value: string) => {
    if (selected.includes(value)) {
      onChange(selected.filter((item) => item !== value))
    } else {
      onChange([...selected, value])
    }
  }

  const handleRemove = (value: string, e?: React.MouseEvent) => {
    e?.stopPropagation()
    onChange(selected.filter((item) => item !== value))
  }

  const handleAddCustom = async () => {
    const trimmed = search.trim()
    if (!trimmed || selected.includes(trimmed)) return

    if (onCreate) {
      setIsCreating(true)
      try {
        await onCreate(trimmed)
        onChange([...selected, trimmed])
        setSearch('')
        // Refresh options to include the new item
        const results = await onSearch('')
        setOptions(results)
      } catch (error) {
        console.error('Failed to create item:', error)
      } finally {
        setIsCreating(false)
      }
    } else {
      onChange([...selected, trimmed])
      setSearch('')
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && allowCustom && search.trim()) {
      e.preventDefault()
      handleAddCustom()
    }
  }

  const handleLoadMore = async () => {
    if (!onLoadMore || isLoadingMore || !internalHasMore) return

    setIsLoadingMore(true)
    try {
      const moreOptions = await onLoadMore()
      setOptions((prev) => [...prev, ...moreOptions])
      if (moreOptions.length === 0) {
        setInternalHasMore(false)
      }
    } catch (error) {
      console.error('Failed to load more options:', error)
    } finally {
      setIsLoadingMore(false)
    }
  }

  // Check if search term can be added as custom
  const canAddCustom =
    allowCustom &&
    search.trim() !== '' &&
    !options.some((o) => o.value.toLowerCase() === search.toLowerCase()) &&
    !selected.some((s) => s.toLowerCase() === search.toLowerCase())

  const displayedItems = selected.slice(0, maxDisplayed)
  const remainingCount = selected.length - maxDisplayed

  // Get label for a selected value
  const getLabel = (value: string) => {
    const option = options.find((o) => o.value === value)
    return option?.label || value
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          disabled={disabled}
          className={cn(
            'w-full justify-between min-h-10 h-auto py-2 rounded-xl',
            selected.length > 0 ? 'px-2' : 'px-3',
            className,
          )}
        >
          <div className="flex flex-wrap gap-1 flex-1">
            {selected.length === 0 ? (
              <span className="text-muted-foreground font-normal">{placeholder}</span>
            ) : (
              <>
                {displayedItems.map((value) => (
                  <Badge key={value} variant="secondary" className="text-xs px-1.5 py-0.5 gap-1">
                    {getLabel(value)}
                    <X
                      className="h-3 w-3 cursor-pointer hover:text-destructive"
                      onClick={(e) => handleRemove(value, e)}
                    />
                  </Badge>
                ))}
                {remainingCount > 0 && (
                  <Badge variant="outline" className="text-xs px-1.5 py-0.5">
                    +{remainingCount} more
                  </Badge>
                )}
              </>
            )}
          </div>
          <ChevronsUpDown className="h-4 w-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[--radix-popover-trigger-width] p-0" align="start">
        <div className="p-2 border-b">
          <Input
            placeholder={searchPlaceholder}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={handleKeyDown}
            className="h-8 rounded-lg"
          />
        </div>

        <ScrollArea className="max-h-[250px]" ref={scrollRef}>
          <div className="p-1">
            {isLoading ? (
              <div className="flex items-center justify-center py-6">
                <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
              </div>
            ) : (
              <>
                {/* Add custom option */}
                {canAddCustom && (
                  <button
                    onClick={handleAddCustom}
                    disabled={isCreating}
                    className="flex items-center gap-2 w-full px-2 py-1.5 text-sm rounded-sm hover:bg-accent hover:text-accent-foreground disabled:opacity-50"
                  >
                    {isCreating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
                    Add "{search}"
                  </button>
                )}

                {/* Options list */}
                {options.length === 0 && !canAddCustom ? (
                  <p className="text-sm text-muted-foreground text-center py-4">{emptyMessage}</p>
                ) : (
                  options.map((option) => (
                    <button
                      key={option.value}
                      onClick={() => handleSelect(option.value)}
                      className={cn(
                        'flex items-center gap-2 w-full px-2 py-1.5 text-sm rounded-sm hover:bg-accent hover:text-accent-foreground',
                        selected.includes(option.value) && 'bg-accent',
                      )}
                    >
                      <Check className={cn('h-4 w-4', selected.includes(option.value) ? 'opacity-100' : 'opacity-0')} />
                      {option.label}
                    </button>
                  ))
                )}

                {/* Load more button */}
                {internalHasMore && onLoadMore && options.length > 0 && (
                  <button
                    onClick={handleLoadMore}
                    disabled={isLoadingMore}
                    className="flex items-center justify-center gap-2 w-full px-2 py-2 text-sm text-muted-foreground hover:text-foreground hover:bg-accent rounded-sm"
                  >
                    {isLoadingMore ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <>
                        <ChevronDown className="h-4 w-4" />
                        Load more
                      </>
                    )}
                  </button>
                )}
              </>
            )}
          </div>
        </ScrollArea>
      </PopoverContent>
    </Popover>
  )
}
