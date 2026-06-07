import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { TimeseriesPoint } from '@/hooks/useRequestMetrics'

interface Props {
  points: TimeseriesPoint[]
}

function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function formatDuration(seconds: number): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds === 0) return '0ms'
  if (seconds < 0.001) return `${(seconds * 1_000_000).toFixed(0)}us`
  if (seconds < 1) return `${(seconds * 1000).toFixed(1)}ms`
  return `${seconds.toFixed(2)}s`
}

export function LatencyChart({ points }: Props) {
  if (points.length === 0) {
    return <EmptyChart label="No latency data" />
  }

  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={points} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" opacity={0.5} />
        <XAxis
          dataKey="ts"
          tickFormatter={fmtTime}
          tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
          tickLine={false}
          axisLine={false}
          interval="preserveStartEnd"
        />
        <YAxis
          tickFormatter={(v: number) => formatDuration(v)}
          tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
          tickLine={false}
          axisLine={false}
          width={48}
        />
        <Tooltip
          contentStyle={{
            background: 'hsl(var(--popover))',
            border: '1px solid hsl(var(--border))',
            borderRadius: '6px',
            fontSize: '12px',
          }}
          labelFormatter={(v) => fmtTime(v as number)}
          formatter={(value) => [formatDuration(value as number), 'Avg Latency']}
        />
        <Line
          type="monotone"
          dataKey="avg_time"
          stroke="hsl(var(--chart-2, 217 91% 60%))"
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4 }}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}

function EmptyChart({ label }: { label: string }) {
  return <div className="flex h-[200px] items-center justify-center text-xs text-muted-foreground">{label}</div>
}
