export interface Stat {
  label: string
  value: string
  note?: string
}

/** A single ruled row of figures; deliberately not a grid of cards. */
export function StatStrip({ stats }: { stats: Stat[] }) {
  return (
    <dl className="grid grid-cols-2 border-y border-line md:grid-cols-4 md:divide-x md:divide-line">
      {stats.map((s, i) => (
        <div key={s.label} className={`py-5 md:px-6 ${i === 0 ? 'md:pl-0' : ''} ${i % 2 === 1 ? 'pl-4 md:pl-6' : ''}`}>
          <dt className="text-sm text-muted">{s.label}</dt>
          <dd className="mt-1 text-3xl font-semibold tabular-nums tracking-tight">{s.value}</dd>
          {s.note && <dd className="mt-0.5 text-xs text-muted">{s.note}</dd>}
        </div>
      ))}
    </dl>
  )
}
