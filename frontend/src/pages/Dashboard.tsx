import { Link } from 'react-router-dom'
import { DailyChart } from '../components/Charts'
import { StatStrip } from '../components/StatStrip'
import { ButtonLink, EmptyState, ErrorState, PageHeader, PageLoading } from '../components/ui'
import { useDashboardSummary } from '../hooks/useAnalytics'
import { describeError } from '../services/api'
import type { ShortUrl } from '../types/api'
import { formatDate, formatNumber, hostOf } from '../utils/format'

function LinkList({ title, items, empty, showClicks }: { title: string; items: ShortUrl[]; empty: string; showClicks?: boolean }) {
  return (
    <section aria-labelledby={`h-${title}`}>
      <h2 id={`h-${title}`} className="text-lg font-semibold">
        {title}
      </h2>
      {items.length === 0 ? (
        <p className="mt-3 text-muted">{empty}</p>
      ) : (
        <ul className="mt-3 divide-y divide-line border-y border-line">
          {items.map((u) => (
            <li key={u.short_code}>
              <Link to={`/links/${encodeURIComponent(u.short_code)}`} className="flex items-center justify-between gap-4 py-3 hover:bg-surface/60">
                <span className="min-w-0">
                  <span className="block font-mono text-sm font-bold text-accent">/{u.short_code}</span>
                  <span className="block truncate text-sm text-muted">{hostOf(u.original_url)}</span>
                </span>
                <span className="shrink-0 text-right text-sm tabular-nums text-muted">
                  {showClicks ? `${formatNumber(u.total_clicks)} ${u.total_clicks === 1 ? 'click' : 'clicks'}` : formatDate(u.created_at)}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

export default function Dashboard() {
  const { data, isLoading, error, refetch } = useDashboardSummary()

  if (isLoading) return <PageLoading label="Loading your dashboard" />
  if (error || !data) return <ErrorState message={describeError(error)} onRetry={() => void refetch()} />

  if (data.total_urls === 0) {
    return (
      <>
        <PageHeader title="Dashboard" />
        <EmptyState title="You haven't created any links yet" action={<ButtonLink to="/links">Create your first link</ButtonLink>}>
          Shorten a URL and its clicks will appear here within a few seconds of the first visit.
        </EmptyState>
      </>
    )
  }

  const last14 = data.clicks_last_14_days.reduce((s, p) => s + p.clicks, 0)
  return (
    <>
      <PageHeader title="Dashboard">
        <ButtonLink to="/links">New link</ButtonLink>
      </PageHeader>

      <StatStrip
        stats={[
          { label: 'Links created', value: formatNumber(data.total_urls), note: `${formatNumber(data.active_urls)} active` },
          { label: 'Total clicks', value: formatNumber(data.total_clicks) },
          { label: 'Clicks per link', value: data.average_clicks_per_url.toLocaleString('en-US', { maximumFractionDigits: 1 }), note: 'average' },
          { label: 'Last 14 days', value: formatNumber(last14), note: 'clicks' },
        ]}
      />

      <section className="mt-10" aria-labelledby="h-activity">
        <h2 id="h-activity" className="mb-4 text-lg font-semibold">
          Clicks across all links
        </h2>
        <DailyChart points={data.clicks_last_14_days} />
      </section>

      <div className="mt-12 grid gap-10 lg:grid-cols-2">
        <LinkList title="Top links" items={data.top_links} showClicks empty="No clicks recorded yet." />
        <LinkList title="Recent links" items={data.recent_urls} empty="No links yet." />
      </div>
    </>
  )
}
