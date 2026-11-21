import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, Cell } from 'recharts'
import type { BreakdownItem, DailyPoint } from '../types/api'
import { formatDay, formatNumber } from '../utils/format'

const axisTick = { fill: 'var(--muted)', fontSize: 12 }

function TooltipBox({ title, rows }: { title: string; rows: string[] }) {
  return (
    <div className="rounded-md border border-line bg-surface px-3 py-2 text-sm shadow-sm">
      <div className="font-medium">{title}</div>
      {rows.map((r) => (
        <div key={r} className="text-muted">
          {r}
        </div>
      ))}
    </div>
  )
}

export function DailyChart({ points, height = 260 }: { points: DailyPoint[]; height?: number }) {
  const total = points.reduce((s, p) => s + p.clicks, 0)
  return (
    <figure aria-label={`Clicks per day, ${formatNumber(total)} in total`} className="m-0">
      <div style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
            <CartesianGrid vertical={false} stroke="var(--line)" />
            <XAxis dataKey="date" tickFormatter={formatDay} tick={axisTick} tickLine={false} axisLine={{ stroke: 'var(--line)' }} minTickGap={28} />
            <YAxis allowDecimals={false} tick={axisTick} tickLine={false} axisLine={false} />
            <Tooltip
              cursor={{ stroke: 'var(--muted)', strokeDasharray: '3 3' }}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null
                const p = payload[0].payload as DailyPoint
                return <TooltipBox title={formatDay(p.date)} rows={[`${formatNumber(p.clicks)} ${p.clicks === 1 ? 'click' : 'clicks'}`]} />
              }}
            />
            <Area type="monotone" dataKey="clicks" stroke="var(--accent)" strokeWidth={2} fill="var(--accent)" fillOpacity={0.12} isAnimationActive={false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </figure>
  )
}

export function BreakdownChart({ items, label }: { items: BreakdownItem[]; label: string }) {
  const rowHeight = 34
  const height = Math.max(items.length * rowHeight + 8, 60)
  return (
    <figure aria-label={label} className="m-0">
      <div style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={items} layout="vertical" margin={{ top: 0, right: 40, bottom: 0, left: 0 }} barCategoryGap={8}>
            <XAxis type="number" hide />
            <YAxis
              type="category"
              dataKey="name"
              width={150}
              tick={{ ...axisTick, fill: 'var(--ink)', fontSize: 13 }}
              tickLine={false}
              axisLine={false}
              interval={0}
              tickFormatter={(v: string) => (v.length > 22 ? `${v.slice(0, 21)}…` : v)}
            />
            <Tooltip
              cursor={{ fill: 'var(--accent-soft)' }}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null
                const it = payload[0].payload as BreakdownItem
                return <TooltipBox title={it.name} rows={[`${formatNumber(it.clicks)} clicks`, `${it.percentage}% of total`]} />
              }}
            />
            <Bar
              dataKey="clicks"
              radius={[0, 3, 3, 0]}
              isAnimationActive={false}
              label={{ position: 'right', fill: 'var(--muted)', fontSize: 12, formatter: (v: unknown) => formatNumber(Number(v)) }}
            >
              {items.map((it, i) => (
                <Cell key={it.name} fill="var(--accent)" fillOpacity={i === 0 ? 1 : 0.55} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </figure>
  )
}
