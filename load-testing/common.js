// Shared helpers for the k6 scripts. Configure through environment variables:
//   BASE_URL (default http://localhost:8000)   HOT_LINKS (200)   COLD_LINKS (5000)   INVALID_POOL (500)
import http from 'k6/http'

export const BASE = __ENV.BASE_URL || 'http://localhost:8000'
export const HOT = Number(__ENV.HOT_LINKS || 200)
export const COLD = Number(__ENV.COLD_LINKS || 5000)
export const INVALID_POOL = Number(__ENV.INVALID_POOL || 500)

const pad = (n, w = 5) => String(n).padStart(w, '0')
const rand = (n) => 1 + Math.floor(Math.random() * n)

export const hotCode = () => `bh${pad(rand(HOT))}`
export const coldCode = () => `bc${pad(rand(COLD))}`
// A bounded pool of unknown codes: repeated misses are exactly what negative caching absorbs.
export const invalidCode = () => `zz${pad(rand(INVALID_POOL))}`

/** Pick a redirect target: 90% hot (cached), 5% cold valid (cache miss), 5% unknown (negative cache). */
export function pickRedirect() {
  const r = Math.random()
  if (r < 0.9) return { kind: 'hot', code: hotCode(), expect: 302 }
  if (r < 0.95) return { kind: 'cold', code: coldCode(), expect: 302 }
  return { kind: 'invalid', code: invalidCode(), expect: 404 }
}

// 302 and 404 are expected outcomes in these tests, not failures. 429 is counted separately.
http.setResponseCallback(http.expectedStatuses(302, 404, 201, 200, 409, 429))

export const summaryStats = ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max']

export function jsonSummary(name) {
  return (data) => ({ [`results/${name}.json`]: JSON.stringify(data, null, 2), stdout: textSummary(data) })
}

function textSummary(data) {
  const d = data.metrics.http_req_duration
  const lines = [`\n== ${data.state ? 'summary' : ''} ==`]
  if (d) {
    lines.push(`requests: ${data.metrics.http_reqs.values.count}  rate: ${data.metrics.http_reqs.values.rate.toFixed(1)}/s`)
    lines.push(`latency ms  p50=${d.values.med.toFixed(2)} p95=${d.values['p(95)'].toFixed(2)} p99=${d.values['p(99)'].toFixed(2)} max=${d.values.max.toFixed(2)}`)
  }
  return lines.join('\n') + '\n'
}
