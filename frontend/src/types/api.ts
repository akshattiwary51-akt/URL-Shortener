// Mirrors the backend response schemas (backend/app/schemas).

export interface User {
  id: number
  email: string
  created_at: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
  expires_in: number
  user: User
}

export interface ShortUrl {
  short_code: string
  short_url: string
  original_url: string
  created_at: string
  expires_at: string | null
  is_active: boolean
  is_expired: boolean
  total_clicks: number
  is_owned: boolean
}

export interface UrlList {
  items: ShortUrl[]
  next_cursor: string | null
}

export interface CreateUrlInput {
  original_url: string
  custom_alias?: string
  expires_at?: string
}

export interface UpdateUrlInput {
  is_active?: boolean
  expires_at?: string | null
}

export interface DailyPoint {
  date: string
  clicks: number
}

export interface AnalyticsOverview {
  short_code: string
  total_clicks: number
  clicks_today: number
  clicks_last_7_days: number
  clicks_last_30_days: number
}

export interface DailySeries {
  short_code: string
  from: string
  to: string
  total: number
  points: DailyPoint[]
}

export interface BreakdownItem {
  name: string
  clicks: number
  percentage: number
}

export interface Breakdown {
  short_code: string
  total: number
  items: BreakdownItem[]
}

export type BreakdownKind = 'browsers' | 'devices' | 'os' | 'referrers' | 'countries'

export interface DashboardSummary {
  total_urls: number
  active_urls: number
  total_clicks: number
  average_clicks_per_url: number
  clicks_last_14_days: DailyPoint[]
  top_links: ShortUrl[]
  recent_urls: ShortUrl[]
}

export interface ApiErrorBody {
  error: {
    code: string
    message: string
    request_id?: string | null
    details?: { loc: string[]; message: string; type: string }[]
  }
}
