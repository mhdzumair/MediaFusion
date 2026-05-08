import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle, CardFooter } from '@/components/ui/card'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { AlertTriangle, X, RefreshCw, Upload, AlertCircle } from 'lucide-react'

interface ValidationError {
  type: string
  message: string
}

interface ValidationFailedDialogProps {
  errors: ValidationError[]
  onCancel: () => void
  onReanalyze: () => void
  onForceImport: () => void
}

export function ValidationFailedDialog({
  errors,
  onCancel,
  onReanalyze,
  onForceImport,
}: ValidationFailedDialogProps) {
  // Check error types
  const errorTypes = errors.map(e => e.type)
  const hasTitleMismatch = errorTypes.includes('title_mismatch')
  const hasEpisodeIssues = errorTypes.includes('episodes_not_found') || errorTypes.includes('seasons_not_found')

  // Get dialog title based on error type
  const getTitle = () => {
    if (hasTitleMismatch) return 'Title Mismatch Detected'
    if (hasEpisodeIssues) return 'Episode Information Required'
    return 'Validation Failed'
  }

  // Get explanation based on error type
  const getExplanation = () => {
    if (hasTitleMismatch) {
      return (
        <>
          <p className="text-xs text-muted-foreground mb-2">
            The torrent title doesn't match the expected content. This might happen if:
          </p>
          <ul className="text-xs text-muted-foreground list-disc pl-4 space-y-0.5 mb-3">
            <li>The torrent contains different content than expected</li>
            <li>The title format is unusual or has extra information</li>
            <li>There's a typo in the torrent name</li>
          </ul>
        </>
      )
    }
    if (hasEpisodeIssues) {
      return (
        <p className="text-xs text-muted-foreground mb-3">
          The torrent requires episode or season information that couldn't be detected automatically.
          Please re-analyze and annotate the episodes.
        </p>
      )
    }
    return (
      <p className="text-xs text-muted-foreground mb-3">
        The import validation found issues that need to be addressed before the torrent can be imported.
      </p>
    )
  }

  return (
    <Card className="border-yellow-500/50">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2 text-yellow-500">
          <AlertTriangle className="h-4 w-4" />
          {getTitle()}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Error messages */}
        <div className="space-y-2">
          {errors.map((error, index) => (
            <Alert key={index} variant="destructive" className="py-2">
              <AlertCircle className="h-3 w-3" />
              <AlertDescription className="text-xs">
                <Badge variant="outline" className="text-[9px] mr-1.5">
                  {error.type.replace(/_/g, ' ')}
                </Badge>
                {error.message}
              </AlertDescription>
            </Alert>
          ))}
        </div>

        {/* Explanation */}
        {getExplanation()}

        {/* Options */}
        <div className="space-y-2 text-xs">
          <div className="p-2 rounded border bg-primary/5 border-primary/20">
            <span className="font-semibold text-primary">Recommended:</span>
            {' '}Re-analyze the torrent and manually select the correct metadata.
          </div>
          <div className="p-2 rounded border bg-yellow-500/5 border-yellow-500/20">
            <span className="font-semibold text-yellow-600 dark:text-yellow-400">Alternative:</span>
            {' '}Force import with current metadata (may result in incorrect matching).
          </div>
        </div>
      </CardContent>
      <CardFooter className="flex gap-2 pt-0">
        <Button
          variant="ghost"
          size="sm"
          onClick={onCancel}
          className="h-7 text-xs"
        >
          <X className="h-3 w-3 mr-1" />
          Cancel
        </Button>
        <Button
          variant="default"
          size="sm"
          onClick={onReanalyze}
          className="h-7 text-xs flex-1"
        >
          <RefreshCw className="h-3 w-3 mr-1" />
          Re-analyze & Fix
        </Button>
        <Button
          variant="secondary"
          size="sm"
          onClick={onForceImport}
          className="h-7 text-xs bg-yellow-500/20 hover:bg-yellow-500/30 text-yellow-600 dark:text-yellow-400"
        >
          <Upload className="h-3 w-3 mr-1" />
          Force Import
        </Button>
      </CardFooter>
    </Card>
  )
}
