import axios, { AxiosError } from 'axios'
import type {
  AnalyticsOverview,
  ApiErrorBody,
  Breakdown,
  BreakdownKind,
  CreateUrlInput,
  DailySeries,
  DashboardSummary,
  ShortUrl,
  TokenResponse,
  UpdateUrlInput,
  UrlList,
  User,
} from '../types/api'

// Configured at build time (VITE_API_URL). Empty means "same origin".
export const API_URL: string = (import.meta.env.VITE_API_URL ?? '').replace(/\/$/, '')

export class ApiError extends Error {
  status: number
  code: string
  fieldErrors: Record<string, string>
  retryAfter: number | null

  constructor(
    message: string,
    opts: { status: number; code: string; fieldErrors?: Record<string, string>; retryAfter?: number | null },
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = opts.status
    this.code = opts.code
    this.fieldErrors = opts.fieldErrors ?? {}
    this.retryAfter = opts.retryAfter ?? null
  }
}

/** Normalises any thrown value (axios error, network failure, …) into an ApiError. */
export function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err
  if (axios.isAxiosError(err)) {
    const e = err as AxiosError<ApiErrorBody>
    const body = e.response?.data?.error
    if (e.response && body) {
      const fieldErrors: Record<string, string> = {}
      for (const d of body.details ?? []) {
        const field = d.loc[d.loc.length - 1]
        if (field && !(field in fieldErrors)) fieldErrors[field] = d.message
      }
      const retry = Number(e.response.headers?.['retry-after'])
      return new ApiError(body.message, {
        status: e.response.status,
        code: body.code,
        fieldErrors,
        retryAfter: Number.isFinite(retry) && retry > 0 ? retry : null,
      })
    }
    if (e.response) {
      return new ApiError('The server returned an unexpected response.', { status: e.response.status, code: 'http_error' })
    }
    if (e.code === 'ECONNABORTED') {
      return new ApiError('The request timed out. Try again.', { status: 0, code: 'timeout' })
    }
    return new ApiError('Could not reach the server. Check your connection and try again.', { status: 0, code: 'network_error' })
  }
  return new ApiError(err instanceof Error ? err.message : 'Something went wrong.', { status: 0, code: 'unknown' })
}

/** Human-readable message for rate limiting and common failure modes. */
export function describeError(err: unknown): string {
  const e = toApiError(err)
  if (e.status === 429) {
    return e.retryAfter ? `Too many requests. Try again in ${e.retryAfter} seconds.` : 'Too many requests. Try again shortly.'
  }
  if (e.status === 503) return 'The service is temporarily unavailable. Try again in a moment.'
  return e.message
}

let authToken: string | null = null
let onUnauthorized: (() => void) | null = null

export function setAuthToken(token: string | null) {
  authToken = token
}
export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn
}

export const http = axios.create({ baseURL: `${API_URL}/api/v1`, timeout: 15_000 })

http.interceptors.request.use((config) => {
  if (authToken) config.headers.set('Authorization', `Bearer ${authToken}`)
  return config
})

http.interceptors.response.use(
  (r) => r,
  (error) => {
    const apiErr = toApiError(error)
    // An expired/invalid session on a protected call: drop it so the UI returns to sign-in.
    if (authToken && apiErr.status === 401 && ['token_expired', 'invalid_token'].includes(apiErr.code)) {
      onUnauthorized?.()
    }
    return Promise.reject(apiErr)
  },
)

export const authApi = {
  register: (email: string, password: string) => http.post<User>('/auth/register', { email, password }).then((r) => r.data),
  login: (email: string, password: string) => http.post<TokenResponse>('/auth/login', { email, password }).then((r) => r.data),
  me: () => http.get<User>('/auth/me').then((r) => r.data),
  changePassword: (current_password: string, new_password: string) =>
    http.post('/auth/change-password', { current_password, new_password }).then(() => undefined),
  deleteAccount: () => http.delete('/auth/me').then(() => undefined),
}

export const urlApi = {
  create: (input: CreateUrlInput) => http.post<ShortUrl>('/urls', input).then((r) => r.data),
  list: (params: { limit?: number; cursor?: string | null; q?: string }) =>
    http
      .get<UrlList>('/urls', { params: { limit: params.limit ?? 10, cursor: params.cursor || undefined, q: params.q || undefined } })
      .then((r) => r.data),
  get: (code: string) => http.get<ShortUrl>(`/urls/${encodeURIComponent(code)}`).then((r) => r.data),
  update: (code: string, input: UpdateUrlInput) => http.patch<ShortUrl>(`/urls/${encodeURIComponent(code)}`, input).then((r) => r.data),
  remove: (code: string) => http.delete(`/urls/${encodeURIComponent(code)}`).then(() => undefined),
}

export const analyticsApi = {
  overview: (code: string) => http.get<AnalyticsOverview>(`/analytics/${encodeURIComponent(code)}`).then((r) => r.data),
  daily: (code: string, from: string, to: string) =>
    http.get<DailySeries>(`/analytics/${encodeURIComponent(code)}/daily`, { params: { from, to } }).then((r) => r.data),
  breakdown: (code: string, kind: BreakdownKind, limit = 8) =>
    http.get<Breakdown>(`/analytics/${encodeURIComponent(code)}/${kind}`, { params: { limit } }).then((r) => r.data),
}

export const dashboardApi = {
  summary: () => http.get<DashboardSummary>('/dashboard/summary').then((r) => r.data),
}
