import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { BreakdownChart, DailyChart } from '../components/Charts'
import { CopyButton } from '../components/CopyButton'
import { StatStrip } from '../components/StatStrip'
import { Button, ErrorState, Notice, PageLoading, Spinner, StatusBadge } from '../components/ui'
import { useBreakdown, useDaily, useOverview } from '../hooks/useAnalytics'
import { useDeleteUrl, useUpdateUrl, useUrl } from '../hooks/useUrls'
import { ApiError, describeError } from '../services/api'
import type { BreakdownKind } from '../types/api'
import { describeExpiry, formatNumber, statusOf } from '../utils/format'

function BreakdownSection({ code, kind, title, empty }: { code: string; kind: BreakdownKind; title: string; empty: string }) {
  const { data, isLoading, error } = useBreakdown(code, kind)
  return (
    <section aria-label={title}>
      <h2 className="mb-3 text-lg font-semibold">{title}</h2>
      {isLoading ? (
        <Spinner />
      ) : error || !data ? (
        <Notice>{describeError(error)}</Notice>
      ) : data.items.length === 0 ? (
        <p className="border-y border-line py-6 text-muted">{empty}</p>
      ) : (
        <BreakdownChart items={data.items} label={`${title}: ${data.items.map((i) => `${i.name} ${i.percentage}%`).join(', ')}`} />
      )}
    </section>
  )
}

const RANGES = [7, 30, 90] as const

export default function UrlDetail() {
  const { code = '' } = useParams()
  const navigate = useNavigate()
  const [days, setDays] = useState<(typeof RANGES)[number]>(30)
  const [confirming, setConfirming] = useState(false)
  const url = useUrl(code)
  const overview = useOverview(code)
  const daily = useDaily(code, days)
  const update = useUpdateUrl()
  const remove = useDeleteUrl()

  if (url.isLoading) return <PageLoading label="Loading link" />
  if (url.error || !url.data) {
    const notFound = url.error instanceof ApiError && url.error.status === 404
    return (
      <div>
        <Link to="/links" className="text-sm font-medium text-accent hover:underline">
          Back to links
        </Link>
        <ErrorState message={notFound ? 'This link does not exist or belongs to another account.' : describeError(url.error)} />
      </div>
    )
  }

  const u = url.data
  const o = overview.data
  return (
    <>
      <Link to="/links" className="text-sm font-medium text-accent hover:underline">
        Back to links
      </Link>

      <div className="mb-8 mt-4 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="break-all font-mono text-2xl font-bold sm:text-3xl">{u.short_url.replace(/^https?:\/\//, '')}</h1>
          <p className="mt-2 max-w-2xl break-all text-muted">{u.original_url}</p>
          <p className="mt-2 flex flex-wrap items-center gap-3 text-sm text-muted">
            <StatusBadge status={statusOf(u)} />
            {describeExpiry(u.expires_at)}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <CopyButton text={u.short_url} label="Copy link" size="md" variant="primary" />
          <Button variant="secondary" loading={update.isPending} onClick={() => update.mutate({ code: u.short_code, is_active: !u.is_active })}>
            {u.is_active ? 'Deactivate' : 'Activate'}
          </Button>
          {confirming ? (
            <>
              <Button
                variant="danger"
                loading={remove.isPending}
                onClick={() => remove.mutate(u.short_code, { onSuccess: () => navigate('/links', { replace: true }) })}
              >
                Delete link
              </Button>
              <Button variant="secondary" onClick={() => setConfirming(false)}>
                Cancel
              </Button>
            </>
          ) : (
            <Button variant="ghost" onClick={() => setConfirming(true)}>
              Delete
            </Button>
          )}
        </div>
      </div>
      {(update.error || remove.error) && (
        <div className="mb-6">
          <Notice>{describeError(update.error ?? remove.error)}</Notice>
        </div>
      )}

      <StatStrip
        stats={[
          { label: 'Total clicks', value: formatNumber(o?.total_clicks ?? u.total_clicks) },
          { label: 'Today', value: o ? formatNumber(o.clicks_today) : '–', note: 'UTC day' },
          { label: 'Last 7 days', value: o ? formatNumber(o.clicks_last_7_days) : '–' },
          { label: 'Last 30 days', value: o ? formatNumber(o.clicks_last_30_days) : '–' },
        ]}
      />

      <section className="mt-10" aria-labelledby="h-over-time">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <h2 id="h-over-time" className="text-lg font-semibold">
            Clicks over time
          </h2>
          <div role="group" aria-label="Date range" className="inline-flex overflow-hidden rounded-md border border-line">
            {RANGES.map((r) => (
              <button
                key={r}
                type="button"
                aria-pressed={days === r}
                onClick={() => setDays(r)}
                className={`h-8 px-3 text-sm font-medium ${days === r ? 'bg-accent text-accent-ink' : 'bg-surface text-muted hover:text-ink'}`}
              >
                {r} days
              </button>
            ))}
          </div>
        </div>
        {daily.isLoading ? <Spinner /> : daily.error || !daily.data ? <Notice>{describeError(daily.error)}</Notice> : <DailyChart points={daily.data.points} />}
      </section>

      <div className="mt-12 grid gap-x-12 gap-y-10 md:grid-cols-2">
        <BreakdownSection code={code} kind="browsers" title="Browsers" empty="No browser data yet." />
        <BreakdownSection code={code} kind="devices" title="Devices" empty="No device data yet." />
        <BreakdownSection code={code} kind="referrers" title="Top referrers" empty="No referrer data yet." />
        <BreakdownSection code={code} kind="os" title="Operating systems" empty="No operating system data yet." />
        <BreakdownSection
          code={code}
          kind="countries"
          title="Countries"
          empty="No country data. Country stats need a GeoIP database configured on the server."
        />
      </div>
    </>
  )
}
