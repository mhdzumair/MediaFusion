import { useState } from 'react'
import { Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useRequestMetricsTimeseries, useEndpointStats } from '@/hooks/useRequestMetrics'
import { VolumeChart } from './charts/VolumeChart'
import { LatencyChart } from './charts/LatencyChart'
import { StatusDistributionChart } from './charts/StatusDistributionChart'
import { TopEndpointsChart } from './charts/TopEndpointsChart'

const WINDOWS = [
  { label: '1h', value: 3600 },
  { label: '6h', value: 21600 },
  { label: '24h', value: 86400 },
]

export function OverviewTab() {
  const [windowSecs, setWindowSecs] = useState(3600)
  const { data: tsData, isLoading: tsLoading } = useRequestMetricsTimeseries(windowSecs)
  const { data: epData } = useEndpointStats({ page: 1, per_page: 8, sort_by: 'total_requests', sort_order: 'desc' })

  const points = tsData?.points ?? []

  return (
    <div className="space-y-4">
      {/* Window selector */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-muted-foreground">Window:</span>
        {WINDOWS.map((w) => (
          <Button
            key={w.value}
            variant={windowSecs === w.value ? 'default' : 'outline'}
            size="sm"
            className="h-7 text-xs"
            onClick={() => setWindowSecs(w.value)}
          >
            {w.label}
          </Button>
        ))}
      </div>

      {tsLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Request Volume */}
          <Card>
            <CardHeader className="pb-2 pt-4 px-4">
              <CardTitle className="text-sm font-medium">Request Volume</CardTitle>
              <p className="text-xs text-muted-foreground">Requests &amp; errors per minute</p>
            </CardHeader>
            <CardContent className="px-4 pb-4">
              <VolumeChart points={points} />
            </CardContent>
          </Card>

          {/* Avg Latency */}
          <Card>
            <CardHeader className="pb-2 pt-4 px-4">
              <CardTitle className="text-sm font-medium">Avg Response Latency</CardTitle>
              <p className="text-xs text-muted-foreground">Average response time per minute</p>
            </CardHeader>
            <CardContent className="px-4 pb-4">
              <LatencyChart points={points} />
            </CardContent>
          </Card>

          {/* Status Distribution */}
          <Card>
            <CardHeader className="pb-2 pt-4 px-4">
              <CardTitle className="text-sm font-medium">Status Code Distribution</CardTitle>
              <p className="text-xs text-muted-foreground">Breakdown across the selected window</p>
            </CardHeader>
            <CardContent className="px-4 pb-4">
              <StatusDistributionChart points={points} />
            </CardContent>
          </Card>

          {/* Top Endpoints */}
          <Card>
            <CardHeader className="pb-2 pt-4 px-4">
              <CardTitle className="text-sm font-medium">Top Endpoints</CardTitle>
              <p className="text-xs text-muted-foreground">Busiest endpoints by total requests</p>
            </CardHeader>
            <CardContent className="px-4 pb-4">
              <TopEndpointsChart items={epData?.items ?? []} />
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  )
}
