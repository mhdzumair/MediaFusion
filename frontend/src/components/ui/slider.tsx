import * as React from "react"
import { cn } from "@/lib/utils"

interface SliderProps {
  value?: number[]
  defaultValue?: number[]
  max?: number
  min?: number
  step?: number
  onValueChange?: (value: number[]) => void
  className?: string
  disabled?: boolean
}

const Slider = React.forwardRef<HTMLInputElement, SliderProps>(
  ({ value, defaultValue, max = 100, min = 0, step = 1, onValueChange, className, disabled }, ref) => {
    const currentValue = value?.[0] ?? defaultValue?.[0] ?? min
    const percentage = ((currentValue - min) / (max - min)) * 100

    return (
      <div className={cn("relative w-full touch-none select-none", className)}>
        <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-white/20">
          <div
            className="absolute h-full bg-primary rounded-full"
            style={{ width: `${percentage}%` }}
          />
        </div>
        <input
          ref={ref}
          type="range"
          min={min}
          max={max}
          step={step}
          value={currentValue}
          disabled={disabled}
          onChange={(e) => onValueChange?.([parseFloat(e.target.value)])}
          className={cn(
            "absolute inset-0 w-full h-full opacity-0 cursor-pointer",
            disabled && "cursor-not-allowed"
          )}
        />
        <div
          className="absolute top-1/2 -translate-y-1/2 h-3 w-3 rounded-full bg-white shadow-md border-2 border-primary pointer-events-none transition-transform hover:scale-110"
          style={{ left: `calc(${percentage}% - 6px)` }}
        />
      </div>
    )
  }
)
Slider.displayName = "Slider"

export { Slider }

