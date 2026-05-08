import { Button } from '@/components/ui/button'
import { AlertTriangle, CheckCircle, XCircle } from 'lucide-react'
import { Link } from 'react-router-dom'
import type { ImportResult } from './types'

interface ImportResultBannerProps {
  result: ImportResult
  onDismiss: () => void
}

export function ImportResultBanner({ result, onDismiss }: ImportResultBannerProps) {
  const severity = result.severity || (result.success ? 'success' : 'error')
  const containerClass =
    severity === 'success'
      ? 'bg-emerald-500/10 border border-emerald-500/20'
      : severity === 'warning'
        ? 'bg-amber-500/10 border border-amber-500/20'
        : 'bg-red-500/10 border border-red-500/20'
  const textClass =
    severity === 'success'
      ? 'text-emerald-600 dark:text-emerald-400'
      : severity === 'warning'
        ? 'text-amber-700 dark:text-amber-400'
        : 'text-red-600 dark:text-red-400'

  return (
    <div className={`p-4 rounded-xl flex items-start gap-3 ${containerClass}`}>
      {severity === 'success' ? (
        <CheckCircle className="h-5 w-5 text-emerald-500" />
      ) : severity === 'warning' ? (
        <AlertTriangle className="h-5 w-5 text-amber-500" />
      ) : (
        <XCircle className="h-5 w-5 text-red-500" />
      )}
      <div className="min-w-0 flex-1 space-y-2">
        <p className={textClass}>{result.message}</p>
        {result.links && result.links.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {result.links.map((link) => (
              <Link
                key={`${link.to}:${link.label}`}
                to={link.to}
                className="text-xs underline underline-offset-2 text-primary hover:text-primary/80"
              >
                {link.label}
              </Link>
            ))}
          </div>
        )}
      </div>
      <Button variant="ghost" size="sm" className="ml-auto" onClick={onDismiss}>
        Dismiss
      </Button>
    </div>
  )
}
