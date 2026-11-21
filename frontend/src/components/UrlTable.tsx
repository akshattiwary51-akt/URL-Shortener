import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useDeleteUrl, useUpdateUrl } from '../hooks/useUrls'
import { describeError } from '../services/api'
import type { ShortUrl } from '../types/api'
import { describeExpiry, formatDate, formatNumber, hostOf, pathOf, statusOf } from '../utils/format'
import { CopyButton } from './CopyButton'
import { Button, Notice, StatusBadge } from './ui'

function Row({ url, onError }: { url: ShortUrl; onError: (m: string) => void }) {
  const update = useUpdateUrl()
  const remove = useDeleteUrl()
  const [confirming, setConfirming] = useState(false)
  const status = statusOf(url)

  return (
    <tr className="border-t border-line align-top">
      <td className="py-4 pr-4">
        <Link to={`/links/${encodeURIComponent(url.short_code)}`} className="font-mono text-sm font-bold text-accent hover:underline">
          {url.short_url.replace(/^https?:\/\//, '')}
        </Link>
        <div className="mt-1 max-w-md truncate text-sm text-muted" title={url.original_url}>
          {hostOf(url.original_url)}
          <span className="text-muted/70">{pathOf(url.original_url)}</span>
        </div>
        <div className="mt-0.5 text-xs text-muted md:hidden">
          {formatNumber(url.total_clicks)} clicks · {formatDate(url.created_at)}
        </div>
      </td>
      <td className="hidden py-4 pr-4 tabular-nums md:table-cell">{formatNumber(url.total_clicks)}</td>
      <td className="hidden py-4 pr-4 md:table-cell">
        <StatusBadge status={status} />
        <div className="mt-1 text-xs text-muted">{describeExpiry(url.expires_at)}</div>
      </td>
      <td className="hidden py-4 pr-4 text-sm text-muted lg:table-cell">{formatDate(url.created_at)}</td>
      <td className="py-4">
        <div className="flex flex-wrap justify-end gap-2">
          {confirming ? (
            <>
              <Button
                size="sm"
                variant="danger"
                loading={remove.isPending}
                onClick={() => remove.mutate(url.short_code, { onError: (e) => onError(describeError(e)) })}
              >
                Delete link
              </Button>
              <Button size="sm" variant="secondary" onClick={() => setConfirming(false)}>
                Cancel
              </Button>
            </>
          ) : (
            <>
              <CopyButton text={url.short_url} />
              <Link
                to={`/links/${encodeURIComponent(url.short_code)}`}
                className="inline-flex h-8 items-center rounded-md border border-line bg-surface px-3 text-sm font-medium hover:border-muted"
              >
                Analytics
              </Link>
              <Button
                size="sm"
                variant="secondary"
                loading={update.isPending}
                onClick={() =>
                  update.mutate({ code: url.short_code, is_active: !url.is_active }, { onError: (e) => onError(describeError(e)) })
                }
              >
                {url.is_active ? 'Deactivate' : 'Activate'}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirming(true)} aria-label={`Delete ${url.short_code}`}>
                Delete
              </Button>
            </>
          )}
        </div>
      </td>
    </tr>
  )
}

export function UrlTable({ items }: { items: ShortUrl[] }) {
  const [error, setError] = useState<string | null>(null)
  return (
    <div>
      {error && (
        <div className="mb-3">
          <Notice>{error}</Notice>
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-left">
          <thead>
            <tr className="text-sm text-muted">
              <th scope="col" className="pb-3 pr-4 font-medium">
                Link
              </th>
              <th scope="col" className="hidden pb-3 pr-4 font-medium md:table-cell">
                Clicks
              </th>
              <th scope="col" className="hidden pb-3 pr-4 font-medium md:table-cell">
                Status
              </th>
              <th scope="col" className="hidden pb-3 pr-4 font-medium lg:table-cell">
                Created
              </th>
              <th scope="col" className="pb-3 text-right font-medium">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {items.map((u) => (
              <Row key={u.short_code} url={u} onError={setError} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
