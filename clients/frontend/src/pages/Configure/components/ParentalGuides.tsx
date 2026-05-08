import { Check, AlertTriangle } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { cn } from '@/lib/utils'
import { CERTIFICATION_LEVELS, NUDITY_LEVELS } from './constants'
import type { ConfigSectionProps } from './types'

export function ParentalGuides({ config, onChange }: ConfigSectionProps) {
  const selectedCertifications = config.cf || ['Adults+']
  const selectedNudity = config.nf || ['Severe']

  const toggleCertification = (value: string) => {
    if (value === 'Disable') {
      onChange({ ...config, cf: ['Disable'] })
      return
    }

    // If Disable was selected, remove it when selecting other options
    let newCerts = selectedCertifications.filter((c) => c !== 'Disable')

    if (newCerts.includes(value)) {
      newCerts = newCerts.filter((c) => c !== value)
    } else {
      newCerts = [...newCerts, value]
    }

    // Ensure at least one option is selected
    if (newCerts.length === 0) {
      newCerts = ['Adults+']
    }

    onChange({ ...config, cf: newCerts })
  }

  const toggleNudity = (value: string) => {
    if (value === 'Disable') {
      onChange({ ...config, nf: ['Disable'] })
      return
    }

    // If Disable was selected, remove it when selecting other options
    let newNudity = selectedNudity.filter((n) => n !== 'Disable')

    if (newNudity.includes(value)) {
      newNudity = newNudity.filter((n) => n !== value)
    } else {
      newNudity = [...newNudity, value]
    }

    // Ensure at least one option is selected
    if (newNudity.length === 0) {
      newNudity = ['Severe']
    }

    onChange({ ...config, nf: newNudity })
  }

  const isNudityDisabled = selectedNudity.includes('Disable')

  const isDisabled = selectedCertifications.includes('Disable')

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">👨‍👩‍👧‍👦 Parental Guides</CardTitle>
        <CardDescription>Filter content based on age ratings and nudity levels</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <Alert variant="default" className="border-primary/50 bg-primary/10">
          <AlertTriangle className="h-4 w-4 text-primary" />
          <AlertDescription className="text-sm">
            Content that exceeds selected levels will be hidden from catalogs and search results.
          </AlertDescription>
        </Alert>

        {/* Certification Filter */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <Label>Certification Filter</Label>
            <Badge variant="secondary">{isDisabled ? 'Disabled' : `${selectedCertifications.length} selected`}</Badge>
          </div>
          <p className="text-xs text-muted-foreground">Block content with certifications above the selected levels</p>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            {CERTIFICATION_LEVELS.map((cert) => {
              const isSelected = selectedCertifications.includes(cert.value)
              const isDisableOption = cert.value === 'Disable'

              return (
                <button
                  key={cert.value}
                  onClick={() => toggleCertification(cert.value)}
                  className={cn(
                    'flex items-center gap-2 p-3 rounded-lg border text-left transition-colors',
                    isSelected
                      ? isDisableOption
                        ? 'border-amber-500 bg-primary/10'
                        : 'border-red-500 bg-red-500/10'
                      : 'border-border hover:border-muted-foreground/50',
                    isDisabled && !isDisableOption && 'opacity-50',
                  )}
                  disabled={isDisabled && !isDisableOption}
                >
                  <Check
                    className={cn(
                      'h-4 w-4 shrink-0',
                      isSelected ? (isDisableOption ? 'text-primary' : 'text-red-500') : 'text-transparent',
                    )}
                  />
                  <span className="text-sm">{cert.label}</span>
                </button>
              )
            })}
          </div>
        </div>

        {/* Nudity Filter */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <Label>Nudity Filter</Label>
            <Badge variant="secondary">{isNudityDisabled ? 'Disabled' : `${selectedNudity.length} selected`}</Badge>
          </div>
          <p className="text-xs text-muted-foreground">Block content with nudity levels at or above selected options</p>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
            {NUDITY_LEVELS.map((nudity) => {
              const isSelected = selectedNudity.includes(nudity.value)
              const isDisableOption = nudity.value === 'Disable'

              return (
                <button
                  key={nudity.value}
                  onClick={() => toggleNudity(nudity.value)}
                  className={cn(
                    'flex items-center gap-2 p-3 rounded-lg border text-left transition-colors',
                    isSelected
                      ? isDisableOption
                        ? 'border-amber-500 bg-primary/10'
                        : 'border-red-500 bg-red-500/10'
                      : 'border-border hover:border-muted-foreground/50',
                    isNudityDisabled && !isDisableOption && 'opacity-50',
                  )}
                  disabled={isNudityDisabled && !isDisableOption}
                >
                  <Check
                    className={cn(
                      'h-4 w-4 shrink-0',
                      isSelected ? (isDisableOption ? 'text-primary' : 'text-red-500') : 'text-transparent',
                    )}
                  />
                  <span className="text-sm">{nudity.label}</span>
                </button>
              )
            })}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
