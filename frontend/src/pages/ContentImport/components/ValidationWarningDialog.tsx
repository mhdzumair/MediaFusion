import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'
import { 
  AlertTriangle, 
  Search, 
  ArrowRight,
  XCircle,
} from 'lucide-react'
import { useState } from 'react'

interface ValidationError {
  type: string
  message: string
}

interface ValidationWarningDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  errors: ValidationError[]
  onCancel: () => void
  onReanalyze: () => void
  onForceImport: () => void
}

export function ValidationWarningDialog({
  open,
  onOpenChange,
  errors,
  onCancel,
  onReanalyze,
  onForceImport,
}: ValidationWarningDialogProps) {
  const [guidelinesAcknowledged, setGuidelinesAcknowledged] = useState(false)

  const handleOpenChange = (isOpen: boolean) => {
    if (!isOpen) {
      setGuidelinesAcknowledged(false)
    }
    onOpenChange(isOpen)
  }

  const handleForceImport = () => {
    if (guidelinesAcknowledged) {
      setGuidelinesAcknowledged(false)
      onForceImport()
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={handleOpenChange}>
      <AlertDialogContent className="sm:max-w-[500px]">
        <AlertDialogHeader>
          <AlertDialogTitle className="flex items-center gap-2 text-primary">
            <AlertTriangle className="h-5 w-5" />
            Validation Warning
          </AlertDialogTitle>
          <AlertDialogDescription className="text-left">
            The torrent metadata didn&apos;t match our validation checks. This could mean:
          </AlertDialogDescription>
        </AlertDialogHeader>
        
        <div className="space-y-4 py-4">
          {/* Possible Reasons */}
          <div className="p-3 rounded-lg bg-muted/50 space-y-2">
            <p className="text-sm font-medium">Possible reasons:</p>
            <ul className="text-sm text-muted-foreground space-y-1 ml-4 list-disc">
              <li>The torrent contains different content than expected</li>
              <li>The title format is unusual or contains extra information</li>
              <li>There&apos;s a typo in the torrent name</li>
              <li>The content type (movie/series) was incorrectly detected</li>
            </ul>
          </div>

          {/* Error Messages */}
          <div className="space-y-2">
            <p className="text-sm font-medium">Issues found:</p>
            <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20">
              <ul className="text-sm text-red-500 dark:text-red-400 space-y-1">
                {errors.map((error, index) => (
                  <li key={index} className="flex items-start gap-2">
                    <XCircle className="h-4 w-4 mt-0.5 flex-shrink-0" />
                    <span>{error.message}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>

          {/* Guidelines */}
          <div className="p-3 rounded-lg bg-primary/10 border border-primary/20">
            <p className="text-sm font-medium text-primary dark:text-primary mb-2">
              Community Guidelines Reminder:
            </p>
            <ul className="text-xs text-muted-foreground space-y-1 ml-4 list-disc">
              <li>Do not upload adult or inappropriate content</li>
              <li>Only upload content that matches the metadata</li>
              <li>Avoid spamming or uploading duplicates</li>
            </ul>
          </div>

          {/* Acknowledgement */}
          <div className="flex items-start gap-2">
            <Checkbox
              id="guidelines"
              checked={guidelinesAcknowledged}
              onCheckedChange={(checked) => setGuidelinesAcknowledged(checked === true)}
            />
            <Label 
              htmlFor="guidelines" 
              className="text-sm font-normal leading-tight cursor-pointer"
            >
              I confirm this content follows community guidelines and the metadata is correct
            </Label>
          </div>
        </div>

        <AlertDialogFooter className="flex-col sm:flex-row gap-2">
          <Button
            variant="outline"
            onClick={onCancel}
            className="sm:order-1"
          >
            Cancel
          </Button>
          <Button
            variant="secondary"
            onClick={onReanalyze}
            className="sm:order-2"
          >
            <Search className="h-4 w-4 mr-2" />
            Re-analyze & Select Correct Match
          </Button>
          <Button
            onClick={handleForceImport}
            disabled={!guidelinesAcknowledged}
            className="sm:order-3 bg-primary hover:bg-primary/90"
          >
            <ArrowRight className="h-4 w-4 mr-2" />
            Force Import Anyway
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

