import { useState, useMemo, useCallback } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Check, ChevronDown, Plus, X } from 'lucide-react'
import { cn } from '@/lib/utils'

interface TagSelectorProps {
  value: string[]
  onChange: (value: string[]) => void
  suggestions: string[]
  placeholder?: string
  allowCustom?: boolean
  badgeVariant?: 'default' | 'secondary' | 'outline' | 'destructive'
}

export function TagSelector({
  value,
  onChange,
  suggestions,
  placeholder = 'Search or add...',
  allowCustom = true,
  badgeVariant = 'secondary',
}: TagSelectorProps) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')

  // Filter suggestions based on search and exclude already selected
  const filteredSuggestions = useMemo(() => {
    const available = suggestions.filter((s) => !value.includes(s))
    if (!search.trim()) return available
    return available.filter((s) => s.toLowerCase().includes(search.toLowerCase()))
  }, [suggestions, value, search])

  // Check if we can add a custom value
  const canAddCustom = useMemo(() => {
    if (!allowCustom || !search.trim()) return false
    const trimmed = search.trim()
    return (
      !value.some((v) => v.toLowerCase() === trimmed.toLowerCase()) &&
      !suggestions.some((s) => s.toLowerCase() === trimmed.toLowerCase())
    )
  }, [allowCustom, search, value, suggestions])

  const handleAdd = useCallback(
    (item: string) => {
      if (!value.includes(item)) {
        onChange([...value, item])
      }
      setSearch('')
    },
    [value, onChange],
  )

  const handleAddCustom = useCallback(() => {
    const trimmed = search.trim()
    if (trimmed && !value.includes(trimmed)) {
      onChange([...value, trimmed])
      setSearch('')
    }
  }, [search, value, onChange])

  const handleRemove = useCallback(
    (item: string) => {
      onChange(value.filter((v) => v !== item))
    },
    [value, onChange],
  )

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter') {
        e.preventDefault()
        if (canAddCustom) {
          handleAddCustom()
        } else if (filteredSuggestions.length > 0) {
          handleAdd(filteredSuggestions[0])
        }
      }
    },
    [canAddCustom, handleAddCustom, filteredSuggestions, handleAdd],
  )

  return (
    <div className="space-y-3">
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button variant="outline" role="combobox" aria-expanded={open} className="w-full justify-between h-10">
            <span className="text-muted-foreground font-normal truncate">{placeholder}</span>
            <ChevronDown className="h-4 w-4 shrink-0 opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-[--radix-popover-trigger-width] p-0" align="start">
          <div className="p-2 border-b">
            <Input
              placeholder={placeholder}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={handleKeyDown}
              className="h-8"
              autoFocus
            />
          </div>
          <ScrollArea className="h-[200px]">
            <div className="p-1">
              {canAddCustom && (
                <button
                  type="button"
                  onClick={handleAddCustom}
                  className="flex items-center gap-2 w-full px-2 py-1.5 text-sm rounded-sm hover:bg-accent hover:text-accent-foreground"
                >
                  <Plus className="h-4 w-4" />
                  Add "{search.trim()}"
                </button>
              )}
              {filteredSuggestions.length === 0 && !canAddCustom ? (
                <p className="text-sm text-muted-foreground text-center py-4">
                  {search.trim() ? 'No matching items' : 'No items available'}
                </p>
              ) : (
                filteredSuggestions.map((item) => (
                  <button
                    key={item}
                    type="button"
                    onClick={() => handleAdd(item)}
                    className={cn(
                      'flex items-center gap-2 w-full px-2 py-1.5 text-sm rounded-sm hover:bg-accent hover:text-accent-foreground text-left',
                      value.includes(item) && 'bg-accent',
                    )}
                  >
                    <Check className={cn('h-4 w-4 shrink-0', value.includes(item) ? 'opacity-100' : 'opacity-0')} />
                    <span className="truncate">{item}</span>
                  </button>
                ))
              )}
            </div>
          </ScrollArea>
        </PopoverContent>
      </Popover>

      {value.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {value.map((item) => (
            <Badge key={item} variant={badgeVariant} className="gap-1 pr-1">
              {item}
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="h-4 w-4 hover:bg-transparent"
                onClick={() => handleRemove(item)}
              >
                <X className="h-3 w-3" />
              </Button>
            </Badge>
          ))}
        </div>
      )}
    </div>
  )
}
