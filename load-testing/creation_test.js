// Scenario B: URL creation.
//   * "authenticated": signed-in users creating links (generated codes + ~20% custom aliases).
//   * "anonymous": a single client creating links without an account; the per-IP limit (default 10/min)
//     must start answering 429 with Retry-After, proving rate-limit enforcement.
//
// For the throughput scenario raise the per-user limit on the server, e.g.
//   RATE_LIMIT_CREATE_USER_PER_WINDOW=1000000 RATE_LIMIT_AUTH_PER_WINDOW=1000
// Run:  k6 run creation_test.js
import http from 'k6/http'
import { check } from 'k6'
import { Counter } from 'k6/metrics'
import { BASE, summaryStats, jsonSummary } from './common.js'

const created = new Counter('created_total')
const aliasConflicts = new Counter('alias_conflicts_total')
const limited = new Counter('anonymous_429_total')
const limitedWithRetryAfter = new Counter('anonymous_429_with_retry_after')

export const options = {
  summaryTrendStats: summaryStats,
  scenarios: {
    authenticated: { executor: 'constant-vus', vus: Number(__ENV.VUS || 16), duration: __ENV.DURATION || '30s', exec: 'authenticated' },
    anonymous: { executor: 'per-vu-iterations', vus: 1, iterations: 30, maxDuration: '30s', exec: 'anonymous' },
  },
  thresholds: { anonymous_429_total: ['count>0'], created_total: ['count>0'] },
}

export function setup() {
  const email = `k6-${Date.now()}@example.com`
  const body = JSON.stringify({ email, password: 'k6-bench-password' })
  const h = { headers: { 'Content-Type': 'application/json' } }
  const reg = http.post(`${BASE}/api/v1/auth/register`, body, h)
  if (reg.status !== 201) throw new Error(`register failed: ${reg.status} ${reg.body}`)
  const login = http.post(`${BASE}/api/v1/auth/login`, body, h)
  return { token: login.json('access_token') }
}

export function authenticated(data) {
  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${data.token}` }
  const payload = { original_url: `https://example.com/bench/${__VU}/${__ITER}` }
  if (__ITER % 5 === 0) payload.custom_alias = `k6-${__VU}-${__ITER}-${Date.now().toString(36)}`
  const res = http.post(`${BASE}/api/v1/urls`, JSON.stringify(payload), { headers, tags: { kind: payload.custom_alias ? 'alias' : 'generated' } })
  check(res, { 'created or limited': (r) => r.status === 201 || r.status === 429 })
  if (res.status === 201) created.add(1)
  if (res.status === 409) aliasConflicts.add(1)
}

export function anonymous() {
  const res = http.post(`${BASE}/api/v1/urls`, JSON.stringify({ original_url: `https://example.com/anon/${__ITER}` }), {
    headers: { 'Content-Type': 'application/json' },
  })
  check(res, { 'anonymous 201 or 429': (r) => r.status === 201 || r.status === 429 })
  if (res.status === 429) {
    limited.add(1)
    if (res.headers['Retry-After']) limitedWithRetryAfter.add(1)
  }
}

export const handleSummary = jsonSummary('creation_k6')
