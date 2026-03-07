import { useMemo, useState } from 'react'
import {
  addMonths,
  eachDayOfInterval,
  endOfMonth,
  endOfWeek,
  format,
  isSameDay,
  isSameMonth,
  isToday,
  parseISO,
  startOfMonth,
  startOfWeek,
  subMonths,
} from 'date-fns'
import { Calendar as CalendarIcon, ChevronLeft, ChevronRight } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { cn } from '@/lib/utils'

interface DatePickerInputProps {
  value?: string
  onChange: (value: string) => void
  placeholder?: string
  disabled?: boolean
  className?: string
}

const WEEK_DAYS = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su']

export function DatePickerInput({
  value,
  onChange,
  placeholder = 'Select date',
  disabled = false,
  className,
}: DatePickerInputProps) {
  const selectedDate = useMemo(() => {
    if (!value) return null
    const parsed = parseISO(value)
    return Number.isNaN(parsed.getTime()) ? null : parsed
  }, [value])

  const [open, setOpen] = useState(false)
  const [monthCursor, setMonthCursor] = useState<Date>(() => selectedDate || new Date())

  const monthStart = startOfMonth(monthCursor)
  const monthEnd = endOfMonth(monthCursor)
  const gridStart = startOfWeek(monthStart, { weekStartsOn: 1 })
  const gridEnd = endOfWeek(monthEnd, { weekStartsOn: 1 })
  const days = eachDayOfInterval({ start: gridStart, end: gridEnd })

  const handleSelect = (day: Date) => {
    onChange(format(day, 'yyyy-MM-dd'))
    setOpen(false)
  }

  const handleToday = () => {
    const today = new Date()
    onChange(format(today, 'yyyy-MM-dd'))
    setMonthCursor(today)
    setOpen(false)
  }

  const handleClear = () => {
    onChange('')
    setOpen(false)
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          disabled={disabled}
          className={cn(
            'w-full justify-between rounded-lg px-3 font-normal',
            !value && 'text-muted-foreground',
            className,
          )}
        >
          <span>{selectedDate ? format(selectedDate, 'yyyy-MM-dd') : placeholder}</span>
          <CalendarIcon className="h-4 w-4 opacity-70" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-auto p-3" align="start">
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              onClick={() => setMonthCursor((prev) => subMonths(prev, 1))}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <div className="text-sm font-medium">{format(monthCursor, 'MMMM yyyy')}</div>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              onClick={() => setMonthCursor((prev) => addMonths(prev, 1))}
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>

          <div className="grid grid-cols-7 gap-1">
            {WEEK_DAYS.map((label) => (
              <div key={label} className="h-7 text-center text-xs font-medium text-muted-foreground leading-7">
                {label}
              </div>
            ))}
            {days.map((day) => {
              const isSelected = selectedDate ? isSameDay(day, selectedDate) : false
              return (
                <Button
                  key={day.toISOString()}
                  type="button"
                  variant="ghost"
                  size="icon"
                  className={cn(
                    'h-8 w-8 text-xs',
                    !isSameMonth(day, monthCursor) && 'text-muted-foreground/40',
                    isToday(day) && !isSelected && 'border border-primary/40',
                    isSelected && 'bg-primary text-primary-foreground hover:bg-primary/90',
                  )}
                  onClick={() => handleSelect(day)}
                >
                  {format(day, 'd')}
                </Button>
              )
            })}
          </div>

          <div className="flex items-center justify-between">
            <Button type="button" variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={handleClear}>
              Clear
            </Button>
            <Button type="button" variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={handleToday}>
              Today
            </Button>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  )
}
