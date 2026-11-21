import type { ShortUrl } from '../types/api'

const numberFmt = new Intl.NumberFormat('en-US')
export const formatNumber = (n: number): string => numberFmt.format(n)

export function formatDate(iso: string): string {
  return new Intl.DateTimeFormat('en-US', { year: 'numeric', month: 'short', day: 'numeric' }).format(new Date(iso))
}

export function formatDateTime(iso: string): string {
  return new Intl.DateTimeFormat('en-US', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(iso))
}

/** "May 3" style label for chart axes; the input is a YYYY-MM-DD calendar date (no timezone shift). */
export function formatDay(day: string): string {
  const [y, m, d] = day.split('-').map(Number)
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }).format(new Date(Date.UTC(y, m - 1, d)))
}

export type LinkStatus = 'active' | 'disabled' | 'expired'

export function statusOf(url: Pick<ShortUrl, 'is_active' | 'is_expired'>): LinkStatus {
  if (!url.is_active) return 'disabled'
  if (url.is_expired) return 'expired'
  return 'active'
}

export function describeExpiry(expiresAt: string | null, now: Date = new Date()): string {
  if (!expiresAt) return 'Never expires'
  const ms = new Date(expiresAt).getTime() - now.getTime()
  if (ms <= 0) return `Expired ${formatDate(expiresAt)}`
  const minutes = Math.round(ms / 60_000)
  if (minutes < 60) return `Expires in ${Math.max(minutes, 1)} min`
  const hours = Math.round(minutes / 60)
  if (hours < 48) return `Expires in ${hours} h`
  return `Expires in ${Math.round(hours / 24)} days`
}

/** Splits a short URL into its host prefix and code so the code can be emphasised. */
export function splitShortUrl(shortUrl: string): { prefix: string; code: string } {
  const i = shortUrl.lastIndexOf('/')
  const prefix = shortUrl.slice(0, i + 1).replace(/^https?:\/\//, '')
  return { prefix, code: shortUrl.slice(i + 1) }
}

export function pathOf(url: string): string {
  try {
    const p = new URL(url).pathname
    return p === '/' ? '' : p
  } catch {
    return ''
  }
}

export function hostOf(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

/** Converts a <input type="datetime-local"> value (local time) to an ISO-8601 string with timezone. */
export function localInputToIso(value: string): string {
  return new Date(value).toISOString()
}

/** Minimum value for a datetime-local input: now, rounded up to the next minute, in local time. */
export function localInputMin(now: Date = new Date()): string {
  const d = new Date(now.getTime() - now.getTimezoneOffset() * 60_000 + 60_000)
  return d.toISOString().slice(0, 16)
}

export function isHttpUrl(value: string): boolean {
  try {
    const u = new URL(value.trim())
    return u.protocol === 'http:' || u.protocol === 'https:'
  } catch {
    return false
  }
}

export const ALIAS_PATTERN = /^[A-Za-z0-9_-]{3,32}$/
export const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    // Fallback for insecure contexts (plain http on a non-localhost origin).
    const ta = document.createElement('textarea')
    ta.value = text
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    try {
      return document.execCommand('copy')
    } finally {
      document.body.removeChild(ta)
    }
  }
}
