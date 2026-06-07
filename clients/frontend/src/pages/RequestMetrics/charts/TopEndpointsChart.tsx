import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { EndpointStatsSummary } from '@/lib/api/requestMetrics'

interface Props {
  items: EndpointStatsSummary[]
}

export function TopEndpointsChart({ items }: Props) {
  if (items.length === 0) {
    return <EmptyChart label="No endpoint data" />
  }

  // Take top 8 endpoints and shorten route for display
  const data = items.slice(0, 8).map((item) => {
    const label = item.route.length > 28 ? `…${item.route.slice(-27)}` : item.route
    const errorRate = item.total_requests > 0 ? item.error_count / item.total_requests : 0
    return {
      name: label,
      requests: item.total_requests,
      errorRate,
    }
  })

  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, left: 8, bottom: 0 }} barSize={12}>
        <XAxis
          type="number"
          tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
          tickLine={false}
          axisLine={false}
          tickFormatter={(v: number) => (v >= 1000 ? `${(v / 1000).toFixed(0)}k` : String(v))}
        />
        <YAxis
          type="category"
          dataKey="name"
          tick={{ fontSize: 9, fill: 'hsl(var(--muted-foreground))' }}
          tickLine={false}
          axisLine={false}
          width={110}
        />
        <Tooltip
          contentStyle={{
            background: 'hsl(var(--popover))',
            border: '1px solid hsl(var(--border))',
            borderRadius: '6px',
            fontSize: '12px',
          }}
          formatter={(value, name) => {
            const v = value as number
            const n = name as string
            return [
              n === 'requests' ? v.toLocaleString() : `${(v * 100).toFixed(1)}%`,
              n === 'requests' ? 'Requests' : 'Error Rate',
            ]
          }}
        />
        <Bar dataKey="requests" radius={[0, 3, 3, 0]}>
          {data.map((entry, index) => (
            <Cell
              key={index}
              fill={
                entry.errorRate > 0.1
                  ? 'hsl(0 72% 51%)'
                  : entry.errorRate > 0.03
                    ? 'hsl(38 92% 50%)'
                    : 'hsl(var(--primary))'
              }
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

function EmptyChart({ label }: { label: string }) {
  return <div className="flex h-[200px] items-center justify-center text-xs text-muted-foreground">{label}</div>
}
