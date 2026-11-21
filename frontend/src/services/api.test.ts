import { AxiosError, AxiosHeaders, type AxiosResponse } from 'axios'
import { describe, expect, it } from 'vitest'
import { ApiError, describeError, toApiError } from './api'

function axiosError(status: number, body: unknown, headers: Record<string, string> = {}) {
  const config = { headers: new AxiosHeaders() }
  const response = { status, data: body, headers, statusText: '', config } as unknown as AxiosResponse
  return new AxiosError('failed', 'ERR_BAD_REQUEST', config, null, response)
}

describe('toApiError', () => {
  it('maps the backend error envelope, including per-field messages', () => {
    const err = toApiError(
      axiosError(422, {
        error: {
          code: 'validation_error',
          message: 'The request is invalid.',
          details: [{ loc: ['body', 'custom_alias'], message: 'Alias is reserved.', type: 'value_error' }],
        },
      }),
    )
    expect(err).toBeInstanceOf(ApiError)
    expect(err.status).toBe(422)
    expect(err.code).toBe('validation_error')
    expect(err.fieldErrors).toEqual({ custom_alias: 'Alias is reserved.' })
  })

  it('reads Retry-After on rate limiting and words it for people', () => {
    const e = axiosError(429, { error: { code: 'rate_limited', message: 'Too many requests.' } }, { 'retry-after': '42' })
    expect(toApiError(e).retryAfter).toBe(42)
    expect(describeError(e)).toBe('Too many requests. Try again in 42 seconds.')
  })

  it('describes network failures without leaking internals', () => {
    const e = new AxiosError('Network Error', 'ERR_NETWORK')
    const api = toApiError(e)
    expect(api.code).toBe('network_error')
    expect(api.message).toMatch(/Could not reach the server/)
  })

  it('falls back for non-JSON server errors and unknown throwables', () => {
    expect(toApiError(axiosError(502, '<html>Bad gateway</html>')).code).toBe('http_error')
    expect(toApiError('boom').message).toBe('Something went wrong.')
    expect(toApiError(new Error('x')).message).toBe('x')
  })

  it('is idempotent', () => {
    const once = toApiError(new AxiosError('Network Error', 'ERR_NETWORK'))
    expect(toApiError(once)).toBe(once)
  })

  it('has a friendly message for 503', () => {
    const e = axiosError(503, { error: { code: 'service_unavailable', message: 'x' } })
    expect(describeError(e)).toMatch(/temporarily unavailable/)
  })
})
