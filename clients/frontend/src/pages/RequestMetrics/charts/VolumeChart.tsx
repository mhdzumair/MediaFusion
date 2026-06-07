import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { TimeseriesPoint } from '@/hooks/useRequestMetrics'

interface Props {
  points: TimeseriesPoint[]
}

function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export function VolumeChart({ points }: Props) {
  if (points.length === 0) {
    return <EmptyChart label="No volume data" />
  }

  return (
    <ResponsiveContainer width="100%" height={200}>
      <AreaChart data={points} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
        <defs>
          <linearGradient id="gradCount" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="hsl(var(--primary))" stopOpacity={0.3} />
            <stop offset="95%" stopColor="hsl(var(--primary))" stopOpacity={0} />
          </linearGradient>
          <linearGradient id="gradErrors" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="hsl(var(--destructive))" stopOpacity={0.3} />
            <stop offset="95%" stopColor="hsl(var(--destructive))" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" opacity={0.5} />
        <XAxis
          dataKey="ts"
          tickFormatter={fmtTime}
          tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
          tickLine={false}
          axisLine={false}
          interval="preserveStartEnd"
        />
        <YAxis tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }} tickLine={false} axisLine={false} />
        <Tooltip
          contentStyle={{
            background: 'hsl(var(--popover))',
            border: '1px solid hsl(var(--border))',
            borderRadius: '6px',
            fontSize: '12px',
          }}
          labelFormatter={(v) => fmtTime(v as number)}
          formatter={(value, name) => [(value as number).toLocaleString(), name === 'count' ? 'Requests' : 'Errors']}
        />
        <Area
          type="monotone"
          dataKey="count"
          stroke="hsl(var(--primary))"
          strokeWidth={2}
          fill="url(#gradCount)"
          dot={false}
        />
        <Area
          type="monotone"
          dataKey="errors"
          stroke="hsl(var(--destructive))"
          strokeWidth={1.5}
          fill="url(#gradErrors)"
          dot={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}

function EmptyChart({ label }: { label: string }) {
  return <div className="flex h-[200px] items-center justify-center text-xs text-muted-foreground">{label}</div>
}
