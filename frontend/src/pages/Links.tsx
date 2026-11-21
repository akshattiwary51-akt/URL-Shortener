import { useState } from 'react'
import { CreateLinkForm } from '../components/CreateLinkForm'
import { UrlTable } from '../components/UrlTable'
import { Button, EmptyState, ErrorState, PageHeader, PageLoading } from '../components/ui'
import { useDebounced } from '../hooks/useDebounced'
import { useUrlList } from '../hooks/useUrls'
import { describeError } from '../services/api'

export default function Links() {
  const [search, setSearch] = useState('')
  const q = useDebounced(search.trim(), 300)
  // Keyset pagination: the server only hands out "the page after this cursor", so we keep a stack of
  // the cursors we have visited to support going back.
  const [cursors, setCursors] = useState<(string | null)[]>([null])
  const cursor = cursors[cursors.length - 1]
  const { data, isLoading, isFetching, error, refetch } = useUrlList(q, cursor)

  function onSearch(value: string) {
    setSearch(value)
    setCursors([null])
  }

  return (
    <>
      <PageHeader title="Links" />

      <section aria-labelledby="h-create" className="mb-12 max-w-3xl">
        <h2 id="h-create" className="mb-4 text-lg font-semibold">
          Create a short link
        </h2>
        <CreateLinkForm />
      </section>

      <section aria-labelledby="h-all">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <h2 id="h-all" className="text-lg font-semibold">
            Your links
          </h2>
          <label className="block">
            <span className="sr-only">Search links</span>
            <input
              type="search"
              value={search}
              onChange={(e) => onSearch(e.target.value)}
              placeholder="Search by code or destination"
              className="h-10 w-72 max-w-full rounded-md border border-line bg-surface px-3 text-sm placeholder:text-muted/70 hover:border-muted"
            />
          </label>
        </div>

        {isLoading ? (
          <PageLoading label="Loading links" />
        ) : error || !data ? (
          <ErrorState message={describeError(error)} onRetry={() => void refetch()} />
        ) : data.items.length === 0 ? (
          q ? (
            <EmptyState title={`No links match "${q}"`}>Try part of the short code or the destination address.</EmptyState>
          ) : (
            <EmptyState title="No links yet">Create your first one above and it will show up here.</EmptyState>
          )
        ) : (
          <>
            <div className={isFetching ? 'opacity-70 transition-opacity' : ''}>
              <UrlTable items={data.items} />
            </div>
            <nav aria-label="Pagination" className="mt-6 flex items-center justify-between">
              <Button variant="secondary" size="sm" disabled={cursors.length === 1} onClick={() => setCursors((c) => c.slice(0, -1))}>
                Newer
              </Button>
              <span className="text-sm text-muted">Page {cursors.length}</span>
              <Button
                variant="secondary"
                size="sm"
                disabled={!data.next_cursor}
                onClick={() => data.next_cursor && setCursors((c) => [...c, data.next_cursor])}
              >
                Older
              </Button>
            </nav>
          </>
        )}
      </section>
    </>
  )
}
