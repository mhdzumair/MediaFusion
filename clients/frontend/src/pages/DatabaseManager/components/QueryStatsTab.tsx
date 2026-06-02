import { SlowQueriesPanel } from './SlowQueriesPanel'

export function QueryStatsTab() {
  return (
    <div className="space-y-4">
      <SlowQueriesPanel variant="full" />
    </div>
  )
}
