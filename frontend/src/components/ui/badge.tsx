import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center rounded-md border px-2.5 py-0.5 text-xs font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2',
  {
    variants: {
      variant: {
        default: 'border-transparent bg-primary/90 text-primary-foreground shadow-sm',
        secondary: 'border-transparent bg-secondary text-secondary-foreground',
        destructive: 'border-transparent bg-destructive/90 text-destructive-foreground shadow-sm',
        outline: 'border-border text-foreground',
        // Scheme-aware variants
        gold: 'border-transparent bg-gradient-to-r from-primary to-primary/80 text-primary-foreground shadow-sm',
        success: 'border-transparent bg-emerald-600/90 text-white shadow-sm',
        warning: 'border-transparent bg-orange-500/90 text-white shadow-sm',
        info: 'border-transparent bg-sky-600/90 text-white shadow-sm',
        muted: 'border-border/50 bg-muted/50 text-muted-foreground',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  },
)

export interface BadgeProps extends React.HTMLAttributes<HTMLDivElement>, VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { Badge, badgeVariants }
