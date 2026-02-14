"use client"

import * as React from "react"
import { X, Check, ChevronsUpDown, Plus, Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { ScrollArea } from "@/components/ui/scroll-area"

export interface MultiSelectOption {
  value: string
  label: string
}

interface MultiSelectProps {
  options: MultiSelectOption[]
  selected: string[]
  onChange: (selected: string[]) => void
  placeholder?: string
  searchPlaceholder?: string
  emptyMessage?: string
  allowCustom?: boolean
  isLoading?: boolean
  disabled?: boolean
  className?: string
  maxDisplayed?: number
}

export function MultiSelect({
  options,
  selected,
  onChange,
  placeholder = "Select items...",
  searchPlaceholder = "Search...",
  emptyMessage = "No results found.",
  allowCustom = false,
  isLoading = false,
  disabled = false,
  className,
  maxDisplayed = 3,
}: MultiSelectProps) {
  const [open, setOpen] = React.useState(false)
  const [search, setSearch] = React.useState("")

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

  const handleAddCustom = () => {
    const trimmed = search.trim()
    if (trimmed && !selected.includes(trimmed)) {
      onChange([...selected, trimmed])
      setSearch("")
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && allowCustom && search.trim()) {
      e.preventDefault()
      handleAddCustom()
    }
  }

  // Filter options based on search
  const filteredOptions = options.filter(
    (option) =>
      option.label.toLowerCase().includes(search.toLowerCase()) ||
      option.value.toLowerCase().includes(search.toLowerCase())
  )

  // Check if search term is already in options or selected
  const canAddCustom =
    allowCustom &&
    search.trim() !== "" &&
    !options.some((o) => o.value.toLowerCase() === search.toLowerCase()) &&
    !selected.some((s) => s.toLowerCase() === search.toLowerCase())

  const displayedItems = selected.slice(0, maxDisplayed)
  const remainingCount = selected.length - maxDisplayed

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          disabled={disabled}
          className={cn(
            "w-full justify-between min-h-10 h-auto py-2",
            selected.length > 0 ? "px-2" : "px-3",
            className
          )}
        >
          <div className="flex flex-wrap gap-1 flex-1">
            {selected.length === 0 ? (
              <span className="text-muted-foreground font-normal">
                {placeholder}
              </span>
            ) : (
              <>
                {displayedItems.map((value) => {
                  const option = options.find((o) => o.value === value)
                  return (
                    <Badge
                      key={value}
                      variant="secondary"
                      className="text-xs px-1.5 py-0.5 gap-1"
                    >
                      {option?.label || value}
                      <X
                        className="h-3 w-3 cursor-pointer hover:text-destructive"
                        onClick={(e) => handleRemove(value, e)}
                      />
                    </Badge>
                  )
                })}
                {remainingCount > 0 && (
                  <Badge variant="outline" className="text-xs px-1.5 py-0.5">
                    +{remainingCount} more
                  </Badge>
                )}
              </>
            )}
          </div>
          {isLoading ? (
            <Loader2 className="h-4 w-4 shrink-0 opacity-50 animate-spin" />
          ) : (
            <ChevronsUpDown className="h-4 w-4 shrink-0 opacity-50" />
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[--radix-popover-trigger-width] p-0" align="start">
        <div className="p-2 border-b">
          <Input
            placeholder={searchPlaceholder}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={handleKeyDown}
            className="h-8"
          />
        </div>
        {isLoading ? (
          <div className="flex items-center justify-center py-6">
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <ScrollArea className="max-h-[200px]">
            <div className="p-1">
              {canAddCustom && (
                <button
                  onClick={handleAddCustom}
                  className="flex items-center gap-2 w-full px-2 py-1.5 text-sm rounded-sm hover:bg-accent hover:text-accent-foreground"
                >
                  <Plus className="h-4 w-4" />
                  Add "{search}"
                </button>
              )}
              {filteredOptions.length === 0 && !canAddCustom ? (
                <p className="text-sm text-muted-foreground text-center py-4">
                  {emptyMessage}
                </p>
              ) : (
                filteredOptions.map((option) => (
                  <button
                    key={option.value}
                    onClick={() => handleSelect(option.value)}
                    className={cn(
                      "flex items-center gap-2 w-full px-2 py-1.5 text-sm rounded-sm hover:bg-accent hover:text-accent-foreground",
                      selected.includes(option.value) && "bg-accent"
                    )}
                  >
                    <Check
                      className={cn(
                        "h-4 w-4",
                        selected.includes(option.value)
                          ? "opacity-100"
                          : "opacity-0"
                      )}
                    />
                    {option.label}
                  </button>
                ))
              )}
            </div>
          </ScrollArea>
        )}
      </PopoverContent>
    </Popover>
  )
}
