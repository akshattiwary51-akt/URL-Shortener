import { describe, expect, it } from 'vitest'
import {
  ALIAS_PATTERN,
  describeExpiry,
  formatDay,
  formatNumber,
  hostOf,
  isHttpUrl,
  localInputMin,
  pathOf,
  splitShortUrl,
  statusOf,
} from './format'

describe('isHttpUrl', () => {
  it.each(['https://example.com', 'http://example.com/a?b=1', ' https://example.com '])('accepts %s', (v) => {
    expect(isHttpUrl(v)).toBe(true)
  })
  it.each(['javascript:alert(1)', 'ftp://example.com', 'example.com', '', 'data:text/html,x', 'file:///etc/passwd'])('rejects %s', (v) => {
    expect(isHttpUrl(v)).toBe(false)
  })
})

describe('alias pattern', () => {
  it.each(['abc', 'My-Alias_01', 'a'.repeat(32)])('accepts %s', (v) => expect(ALIAS_PATTERN.test(v)).toBe(true))
  it.each(['ab', 'a'.repeat(33), 'has space', 'dot.dot', ''])('rejects %s', (v) => expect(ALIAS_PATTERN.test(v)).toBe(false))
})

describe('statusOf', () => {
  it('prefers disabled over expired', () => {
    expect(statusOf({ is_active: false, is_expired: true })).toBe('disabled')
    expect(statusOf({ is_active: true, is_expired: true })).toBe('expired')
    expect(statusOf({ is_active: true, is_expired: false })).toBe('active')
  })
})

describe('describeExpiry', () => {
  const now = new Date('2026-06-01T12:00:00Z')
  it('handles never, minutes, hours and days', () => {
    expect(describeExpiry(null, now)).toBe('Never expires')
    expect(describeExpiry('2026-06-01T12:30:00Z', now)).toBe('Expires in 30 min')
    expect(describeExpiry('2026-06-01T18:00:00Z', now)).toBe('Expires in 6 h')
    expect(describeExpiry('2026-06-11T12:00:00Z', now)).toBe('Expires in 10 days')
  })
  it('reports past dates as expired', () => {
    expect(describeExpiry('2026-05-01T00:00:00Z', now)).toMatch(/^Expired/)
  })
})

describe('formatting helpers', () => {
  it('formats numbers with separators', () => expect(formatNumber(1234567)).toBe('1,234,567'))
  it('formats calendar days without timezone drift', () => expect(formatDay('2026-03-01')).toBe('Mar 1'))
  it('splits short URLs into host and code', () => {
    expect(splitShortUrl('http://localhost:8000/aB3xK9q')).toEqual({ prefix: 'localhost:8000/', code: 'aB3xK9q' })
  })
  it('extracts host and path safely', () => {
    expect(hostOf('https://example.com/a/b?x=1')).toBe('example.com')
    expect(pathOf('https://example.com/a/b?x=1')).toBe('/a/b')
    expect(pathOf('https://example.com/')).toBe('')
    expect(hostOf('not a url')).toBe('not a url')
    expect(pathOf('not a url')).toBe('')
  })
  it('computes a future minimum for datetime-local', () => {
    const min = localInputMin(new Date('2026-06-01T12:00:00'))
    expect(min).toMatch(/^2026-06-01T12:01$/)
  })
})
