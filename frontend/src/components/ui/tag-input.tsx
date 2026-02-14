'use client'

import * as React from 'react'
import { X, Plus } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'

interface TagInputProps {
  value: string[]
  onChange: (value: string[]) => void
  placeholder?: string
  disabled?: boolean
  className?: string
  maxTags?: number
}

export function TagInput({
  value,
  onChange,
  placeholder = 'Add tag...',
  disabled = false,
  className,
  maxTags,
}: TagInputProps) {
  const [inputValue, setInputValue] = React.useState('')
  const inputRef = React.useRef<HTMLInputElement>(null)

  const handleAdd = () => {
    const trimmed = inputValue.trim()
    if (trimmed && !value.includes(trimmed)) {
      if (maxTags && value.length >= maxTags) return
      onChange([...value, trimmed])
      setInputValue('')
    }
  }

  const handleRemove = (tag: string) => {
    onChange(value.filter((t) => t !== tag))
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      handleAdd()
    } else if (e.key === 'Backspace' && inputValue === '' && value.length > 0) {
      // Remove last tag if backspace is pressed with empty input
      onChange(value.slice(0, -1))
    }
  }

  const handleContainerClick = () => {
    inputRef.current?.focus()
  }

  const isMaxReached = maxTags !== undefined && value.length >= maxTags

  return (
    <div
      className={cn(
        'flex flex-wrap gap-1.5 min-h-10 w-full rounded-xl border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-2',
        disabled && 'cursor-not-allowed opacity-50',
        className,
      )}
      onClick={handleContainerClick}
    >
      {value.map((tag) => (
        <Badge key={tag} variant="secondary" className="text-xs px-2 py-0.5 gap-1">
          {tag}
          {!disabled && (
            <X
              className="h-3 w-3 cursor-pointer hover:text-destructive"
              onClick={(e) => {
                e.stopPropagation()
                handleRemove(tag)
              }}
            />
          )}
        </Badge>
      ))}
      {!disabled && !isMaxReached && (
        <div className="flex items-center gap-1 flex-1 min-w-[120px]">
          <Input
            ref={inputRef}
            type="text"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={value.length === 0 ? placeholder : ''}
            disabled={disabled}
            className="border-0 p-0 h-6 text-sm focus-visible:ring-0 focus-visible:ring-offset-0 bg-transparent"
          />
          {inputValue.trim() && (
            <Button type="button" variant="ghost" size="icon" className="h-6 w-6" onClick={handleAdd}>
              <Plus className="h-3 w-3" />
            </Button>
          )}
        </div>
      )}
      {isMaxReached && <span className="text-xs text-muted-foreground">Max {maxTags} tags</span>}
    </div>
  )
}
