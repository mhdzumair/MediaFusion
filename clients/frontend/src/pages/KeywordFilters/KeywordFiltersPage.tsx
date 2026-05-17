import { Ban } from 'lucide-react'

import { KeywordFiltersTab } from '@/pages/Moderator/components'

export function KeywordFiltersPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
          <div className="p-2 rounded-xl bg-gradient-to-br from-red-500 to-red-500/80 shadow-lg shadow-red-500/20">
            <Ban className="h-5 w-5 text-white" />
          </div>
          Keyword Filters
        </h1>
        <p className="text-muted-foreground mt-1">
          Manage blocked keywords and whitelist phrases for content filtering
        </p>
      </div>

      <KeywordFiltersTab />
    </div>
  )
}
