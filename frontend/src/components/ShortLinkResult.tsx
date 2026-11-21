import { Link } from 'react-router-dom'
import type { ShortUrl } from '../types/api'
import { describeExpiry, hostOf, splitShortUrl } from '../utils/format'
import { CopyButton } from './CopyButton'

/**
 * The new link, shown large. In the hero variant the code itself is the headline element:
 * the host recedes, the seven characters carry the weight.
 */
export function ShortLinkResult({ url, variant = 'panel', signedIn }: { url: ShortUrl; variant?: 'hero' | 'panel'; signedIn: boolean }) {
  const { prefix, code } = splitShortUrl(url.short_url)
  const hero = variant === 'hero'
  return (
    <section aria-label="Your short link" className={`settle ${hero ? 'mt-10' : 'mt-6 border-t border-line pt-6'}`}>
      <p className="text-sm text-muted">Your short link</p>
      <p
        data-testid="short-link"
        className={`mt-1 break-all font-mono font-bold leading-tight ${hero ? 'text-4xl sm:text-6xl' : 'text-2xl sm:text-3xl'}`}
      >
        <span className="text-[0.5em] font-medium text-muted">{prefix}</span>
        <span className="text-accent">{code}</span>
      </p>
      <p className="mt-3 truncate text-sm text-muted" title={url.original_url}>
        Points to {hostOf(url.original_url)} · {describeExpiry(url.expires_at)}
      </p>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <CopyButton text={url.short_url} label="Copy link" size="md" variant="primary" />
        {signedIn ? (
          <Link to={`/links/${encodeURIComponent(url.short_code)}`} className="text-sm font-medium text-accent hover:underline">
            View analytics
          </Link>
        ) : (
          <Link to="/register" className="text-sm font-medium text-accent hover:underline">
            Create a free account to see clicks
          </Link>
        )}
      </div>
    </section>
  )
}
