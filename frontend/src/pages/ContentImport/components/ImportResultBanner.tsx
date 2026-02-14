import { Button } from '@/components/ui/button'
import { CheckCircle, XCircle } from 'lucide-react'
import type { ImportResult } from './types'

interface ImportResultBannerProps {
  result: ImportResult
  onDismiss: () => void
}

export function ImportResultBanner({ result, onDismiss }: ImportResultBannerProps) {
  return (
    <div 
      className={`p-4 rounded-xl flex items-center gap-3 ${
        result.success 
          ? 'bg-emerald-500/10 border border-emerald-500/20' 
          : 'bg-red-500/10 border border-red-500/20'
      }`}
    >
      {result.success ? (
        <CheckCircle className="h-5 w-5 text-emerald-500" />
      ) : (
        <XCircle className="h-5 w-5 text-red-500" />
      )}
      <p className={result.success ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}>
        {result.message}
      </p>
      <Button 
        variant="ghost" 
        size="sm" 
        className="ml-auto"
        onClick={onDismiss}
      >
        Dismiss
      </Button>
    </div>
  )
}

