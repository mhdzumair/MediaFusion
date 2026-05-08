import { useState } from 'react'
import { AlertTriangle, Loader2, RotateCcw, Save, Settings, ThumbsUp, Zap } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { useContributionSettings, useResetContributionSettings, useUpdateContributionSettings } from '@/hooks'

export function ContributionSettingsTab() {
  const { data: settings, isLoading } = useContributionSettings()
  const updateSettings = useUpdateContributionSettings()
  const resetSettings = useResetContributionSettings()

  const [formData, setFormData] = useState({
    auto_approval_threshold: 25,
    contributor_threshold: 10,
    trusted_threshold: 50,
    expert_threshold: 200,
    points_per_metadata_edit: 5,
    points_per_stream_edit: 3,
    points_for_rejection_penalty: -2,
    max_pending_suggestions_per_user: 20,
    allow_auto_approval: true,
    require_reason_for_edits: false,
  })
  const [hasChanges, setHasChanges] = useState(false)

  useState(() => {
    if (settings) {
      setFormData({
        auto_approval_threshold: settings.auto_approval_threshold,
        contributor_threshold: settings.contributor_threshold,
        trusted_threshold: settings.trusted_threshold,
        expert_threshold: settings.expert_threshold,
        points_per_metadata_edit: settings.points_per_metadata_edit,
        points_per_stream_edit: settings.points_per_stream_edit,
        points_for_rejection_penalty: settings.points_for_rejection_penalty,
        max_pending_suggestions_per_user: settings.max_pending_suggestions_per_user,
        allow_auto_approval: settings.allow_auto_approval,
        require_reason_for_edits: settings.require_reason_for_edits,
      })
    }
  })

  if (settings && !hasChanges) {
    const needsUpdate =
      formData.auto_approval_threshold !== settings.auto_approval_threshold ||
      formData.contributor_threshold !== settings.contributor_threshold ||
      formData.trusted_threshold !== settings.trusted_threshold ||
      formData.expert_threshold !== settings.expert_threshold ||
      formData.points_per_metadata_edit !== settings.points_per_metadata_edit ||
      formData.points_per_stream_edit !== settings.points_per_stream_edit ||
      formData.points_for_rejection_penalty !== settings.points_for_rejection_penalty ||
      formData.max_pending_suggestions_per_user !== settings.max_pending_suggestions_per_user ||
      formData.allow_auto_approval !== settings.allow_auto_approval ||
      formData.require_reason_for_edits !== settings.require_reason_for_edits

    if (needsUpdate) {
      setFormData({
        auto_approval_threshold: settings.auto_approval_threshold,
        contributor_threshold: settings.contributor_threshold,
        trusted_threshold: settings.trusted_threshold,
        expert_threshold: settings.expert_threshold,
        points_per_metadata_edit: settings.points_per_metadata_edit,
        points_per_stream_edit: settings.points_per_stream_edit,
        points_for_rejection_penalty: settings.points_for_rejection_penalty,
        max_pending_suggestions_per_user: settings.max_pending_suggestions_per_user,
        allow_auto_approval: settings.allow_auto_approval,
        require_reason_for_edits: settings.require_reason_for_edits,
      })
    }
  }

  const handleChange = (field: string, value: number | boolean) => {
    setFormData((prev) => ({ ...prev, [field]: value }))
    setHasChanges(true)
  }

  const handleSave = async () => {
    await updateSettings.mutateAsync(formData)
    setHasChanges(false)
  }

  const handleReset = async () => {
    await resetSettings.mutateAsync()
    setHasChanges(false)
  }

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Card className="glass border-border/50">
          <CardContent className="p-6">
            <div className="space-y-4">
              {[...Array(4)].map((_, i) => (
                <Skeleton key={i} className="h-12 rounded-lg" />
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <Card className="glass border-border/50">
        <CardContent className="p-6">
          <div className="flex items-center justify-between mb-6">
            <div className="flex items-center gap-3">
              <Settings className="h-5 w-5 text-primary" />
              <div>
                <h3 className="font-semibold">Contribution Settings</h3>
                <p className="text-sm text-muted-foreground">
                  Configure auto-approval thresholds and contribution points
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={handleReset}
                disabled={resetSettings.isPending}
                className="rounded-xl"
              >
                {resetSettings.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <RotateCcw className="h-4 w-4 mr-2" />
                )}
                Reset to Defaults
              </Button>
              <Button
                size="sm"
                onClick={handleSave}
                disabled={!hasChanges || updateSettings.isPending}
                className="rounded-xl bg-gradient-to-r from-primary to-primary/80"
              >
                {updateSettings.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Save className="h-4 w-4 mr-2" />
                )}
                Save Changes
              </Button>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="space-y-4">
              <h4 className="font-medium flex items-center gap-2">
                <Zap className="h-4 w-4 text-primary" />
                Level Thresholds
              </h4>
              <div className="space-y-3">
                <div className="space-y-2">
                  <Label htmlFor="auto_approval_threshold" className="text-sm">
                    Auto-Approval Points Threshold
                  </Label>
                  <Input
                    id="auto_approval_threshold"
                    type="number"
                    value={formData.auto_approval_threshold}
                    onChange={(e) => handleChange('auto_approval_threshold', parseInt(e.target.value) || 0)}
                    className="rounded-xl"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="contributor_threshold" className="text-sm">
                    Contributor Level (points)
                  </Label>
                  <Input
                    id="contributor_threshold"
                    type="number"
                    value={formData.contributor_threshold}
                    onChange={(e) => handleChange('contributor_threshold', parseInt(e.target.value) || 0)}
                    className="rounded-xl"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="trusted_threshold" className="text-sm">
                    Trusted Level (points)
                  </Label>
                  <Input
                    id="trusted_threshold"
                    type="number"
                    value={formData.trusted_threshold}
                    onChange={(e) => handleChange('trusted_threshold', parseInt(e.target.value) || 0)}
                    className="rounded-xl"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="expert_threshold" className="text-sm">
                    Expert Level (points)
                  </Label>
                  <Input
                    id="expert_threshold"
                    type="number"
                    value={formData.expert_threshold}
                    onChange={(e) => handleChange('expert_threshold', parseInt(e.target.value) || 0)}
                    className="rounded-xl"
                  />
                </div>
              </div>
            </div>

            <div className="space-y-4">
              <h4 className="font-medium flex items-center gap-2">
                <ThumbsUp className="h-4 w-4 text-emerald-500" />
                Points Configuration
              </h4>
              <div className="space-y-3">
                <div className="space-y-2">
                  <Label htmlFor="points_per_metadata_edit" className="text-sm">
                    Points per Metadata Edit
                  </Label>
                  <Input
                    id="points_per_metadata_edit"
                    type="number"
                    value={formData.points_per_metadata_edit}
                    onChange={(e) => handleChange('points_per_metadata_edit', parseInt(e.target.value) || 0)}
                    className="rounded-xl"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="points_per_stream_edit" className="text-sm">
                    Points per Stream Edit
                  </Label>
                  <Input
                    id="points_per_stream_edit"
                    type="number"
                    value={formData.points_per_stream_edit}
                    onChange={(e) => handleChange('points_per_stream_edit', parseInt(e.target.value) || 0)}
                    className="rounded-xl"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="points_for_rejection_penalty" className="text-sm text-red-500">
                    Rejection Penalty (negative)
                  </Label>
                  <Input
                    id="points_for_rejection_penalty"
                    type="number"
                    max={0}
                    value={formData.points_for_rejection_penalty}
                    onChange={(e) => handleChange('points_for_rejection_penalty', parseInt(e.target.value) || 0)}
                    className="rounded-xl"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="max_pending_suggestions_per_user" className="text-sm">
                    Max Pending per User
                  </Label>
                  <Input
                    id="max_pending_suggestions_per_user"
                    type="number"
                    min={1}
                    value={formData.max_pending_suggestions_per_user}
                    onChange={(e) => handleChange('max_pending_suggestions_per_user', parseInt(e.target.value) || 1)}
                    className="rounded-xl"
                  />
                </div>
              </div>
            </div>
          </div>

          <div className="mt-6 pt-6 border-t border-border/50">
            <h4 className="font-medium flex items-center gap-2 mb-4">
              <Settings className="h-4 w-4 text-blue-500" />
              Feature Flags
            </h4>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="flex items-center justify-between p-4 bg-muted/50 rounded-xl">
                <div>
                  <Label htmlFor="allow_auto_approval" className="font-medium">
                    Auto-Approval Enabled
                  </Label>
                  <p className="text-xs text-muted-foreground mt-1">Allow trusted users to auto-approve their edits</p>
                </div>
                <Switch
                  id="allow_auto_approval"
                  checked={formData.allow_auto_approval}
                  onCheckedChange={(checked) => handleChange('allow_auto_approval', checked)}
                />
              </div>
              <div className="flex items-center justify-between p-4 bg-muted/50 rounded-xl">
                <div>
                  <Label htmlFor="require_reason_for_edits" className="font-medium">
                    Require Reason for Edits
                  </Label>
                  <p className="text-xs text-muted-foreground mt-1">
                    Users must provide a reason for their suggestions
                  </p>
                </div>
                <Switch
                  id="require_reason_for_edits"
                  checked={formData.require_reason_for_edits}
                  onCheckedChange={(checked) => handleChange('require_reason_for_edits', checked)}
                />
              </div>
            </div>
          </div>

          {hasChanges && (
            <div className="mt-6 p-4 bg-primary/10 border border-primary/30 rounded-lg">
              <p className="text-sm text-primary dark:text-primary">
                <AlertTriangle className="inline h-4 w-4 mr-2" />
                You have unsaved changes. Click "Save Changes" to apply them.
              </p>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
