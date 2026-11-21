// Scenario A: redirect performance.
//   90% requests for hot (cached) links, 5% cold valid links (cache misses -> PostgreSQL),
//   5% unknown codes (negative-cache candidates). Redirects are NOT followed.
//
// Prerequisites: psql -f seed.sql (fixtures) and a server started with a high redirect limit
// (RATE_LIMIT_REDIRECT_PER_WINDOW=100000000) and TRUSTED_PROXIES covering the k6 host, so the
// per-IP limiter does not throttle the benchmark. Run:  k6 run redirect_test.js
import http from 'k6/http'
import { check } from 'k6'
import { Trend, Rate } from 'k6/metrics'
import { BASE, pickRedirect, summaryStats, jsonSummary } from './common.js'

const latency = { hot: new Trend('redirect_hot_ms', true), cold: new Trend('redirect_cold_ms', true), invalid: new Trend('redirect_invalid_ms', true) }
const wrongStatus = new Rate('redirect_wrong_status')

export const options = {
  summaryTrendStats: summaryStats,
  scenarios: {
    redirects: {
      executor: 'constant-vus',
      vus: Number(__ENV.VUS || 64),
      duration: __ENV.DURATION || '60s',
    },
  },
  thresholds: {
    redirect_wrong_status: ['rate<0.001'],
    // Optimisation goal, NOT a claim: fail the run if p99 exceeds 10 ms.
    ...(__ENV.STRICT ? { http_req_duration: ['p(99)<10'] } : {}),
  },
}

export default function () {
  const t = pickRedirect()
  const xff = `10.${Math.floor(Math.random() * 250)}.${Math.floor(Math.random() * 250)}.${1 + Math.floor(Math.random() * 250)}`
  const res = http.get(`${BASE}/${t.code}`, { redirects: 0, headers: { 'X-Forwarded-For': xff, 'User-Agent': 'k6-bench' }, tags: { kind: t.kind } })
  latency[t.kind].add(res.timings.duration)
  const ok = check(res, { [`${t.kind} -> ${t.expect}`]: (r) => r.status === t.expect })
  wrongStatus.add(!ok)
}

export const handleSummary = jsonSummary('redirect_k6')
