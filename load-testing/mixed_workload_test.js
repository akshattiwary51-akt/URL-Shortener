// Scenario C: mixed workload - 90% redirects, 8% analytics reads, 2% URL creation.
// Run:  k6 run mixed_workload_test.js   (same server prerequisites as the other scenarios)
import http from 'k6/http'
import { check } from 'k6'
import { BASE, pickRedirect, summaryStats, jsonSummary } from './common.js'

export const options = {
  summaryTrendStats: summaryStats,
  scenarios: { mixed: { executor: 'constant-vus', vus: Number(__ENV.VUS || 64), duration: __ENV.DURATION || '60s' } },
}

export function setup() {
  const email = `k6mixed-${Date.now()}@example.com`
  const body = JSON.stringify({ email, password: 'k6-bench-password' })
  const h = { headers: { 'Content-Type': 'application/json' } }
  http.post(`${BASE}/api/v1/auth/register`, body, h)
  const token = http.post(`${BASE}/api/v1/auth/login`, body, h).json('access_token')
  const auth = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }
  const codes = []
  for (let i = 0; i < 20; i++) {
    const r = http.post(`${BASE}/api/v1/urls`, JSON.stringify({ original_url: `https://example.com/mixed/${i}` }), { headers: auth })
    if (r.status === 201) codes.push(r.json('short_code'))
  }
  if (!codes.length) throw new Error('could not create owned links (raise RATE_LIMIT_CREATE_USER_PER_WINDOW)')
  return { auth, codes }
}

export default function (data) {
  const r = Math.random()
  const xff = `10.${Math.floor(Math.random() * 250)}.${Math.floor(Math.random() * 250)}.${1 + Math.floor(Math.random() * 250)}`
  if (r < 0.9) {
    const t = pickRedirect()
    const res = http.get(`${BASE}/${t.code}`, { redirects: 0, headers: { 'X-Forwarded-For': xff }, tags: { op: 'redirect' } })
    check(res, { redirect: (x) => x.status === t.expect })
  } else if (r < 0.98) {
    const code = data.codes[Math.floor(Math.random() * data.codes.length)]
    const path = Math.random() < 0.3 ? '/api/v1/dashboard/summary' : `/api/v1/analytics/${code}${['', '/daily', '/browsers', '/referrers'][Math.floor(Math.random() * 4)]}`
    const res = http.get(`${BASE}${path}`, { headers: data.auth, tags: { op: 'analytics' } })
    check(res, { analytics: (x) => x.status === 200 })
  } else {
    const res = http.post(`${BASE}/api/v1/urls`, JSON.stringify({ original_url: `https://example.com/mixed/${__VU}/${__ITER}` }), { headers: data.auth, tags: { op: 'create' } })
    check(res, { create: (x) => x.status === 201 })
  }
}

export const handleSummary = jsonSummary('mixed_k6')
