import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip, Legend } from 'recharts'
import type { TimeseriesPoint } from '@/hooks/useRequestMetrics'

interface Props {
  points: TimeseriesPoint[]
}

const STATUS_COLORS = [
  { key: 'status_2xx', label: '2xx', color: 'hsl(142 71% 45%)' }, // success green
  { key: 'status_3xx', label: '3xx', color: 'hsl(215 20% 55%)' }, // muted blue-grey
  { key: 'status_4xx', label: '4xx', color: 'hsl(38 92% 50%)' }, // warning amber
  { key: 'status_5xx', label: '5xx', color: 'hsl(0 72% 51%)' }, // destructive red
]

export function StatusDistributionChart({ points }: Props) {
  // Sum totals across all time buckets
  const totals = STATUS_COLORS.map(({ key, label, color }) => ({
    name: label,
    value: points.reduce((sum, p) => sum + ((p as unknown as Record<string, number>)[key] ?? 0), 0),
    color,
  })).filter((d) => d.value > 0)

  if (totals.length === 0) {
    return <EmptyChart label="No status data" />
  }

  return (
    <ResponsiveContainer width="100%" height={200}>
      <PieChart>
        <Pie data={totals} cx="50%" cy="50%" innerRadius={55} outerRadius={80} paddingAngle={2} dataKey="value">
          {totals.map((entry) => (
            <Cell key={entry.name} fill={entry.color} />
          ))}
        </Pie>
        <Tooltip
          contentStyle={{
            background: 'hsl(var(--popover))',
            border: '1px solid hsl(var(--border))',
            borderRadius: '6px',
            fontSize: '12px',
          }}
          formatter={(value, name) => [(value as number).toLocaleString(), name as string]}
        />
        <Legend
          iconType="circle"
          iconSize={8}
          wrapperStyle={{ fontSize: '11px', color: 'hsl(var(--muted-foreground))' }}
        />
      </PieChart>
    </ResponsiveContainer>
  )
}

function EmptyChart({ label }: { label: string }) {
  return <div className="flex h-[200px] items-center justify-center text-xs text-muted-foreground">{label}</div>
}
