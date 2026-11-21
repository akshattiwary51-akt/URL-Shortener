#!/usr/bin/env node
// Native (Node) load generator with the same scenarios and traffic mix as the k6 scripts.
//
// Why it exists: k6 is the intended tool (see ../*.js) but its binary could not be installed in the
// sandbox where the recorded benchmarks were produced. Numbers from this tool are therefore labelled
// "loadgen", never "k6". It uses keep-alive HTTP/1.1 connections and exact (sorted-sample) percentiles.
//
//   node loadgen.mjs --scenario redirect|create|mixed --duration 20 --concurrency 32 --label name --out file.json
import http from 'node:http'
import fs from 'node:fs'
import { execSync } from 'node:child_process'
import { performance } from 'node:perf_hooks'

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, a, i, all) => (a.startsWith('--') ? [...acc, [a.slice(2), all[i + 1]]] : acc), []),
)
const BASE = new URL(args.base ?? 'http://localhost:8000')
const SCENARIO = args.scenario ?? 'redirect'
const DURATION = Number(args.duration ?? 20) * 1000
const WARMUP = Number(args.warmup ?? 3) * 1000
const CONCURRENCY = Number(args.concurrency ?? 32)
const LABEL = args.label ?? SCENARIO
const WORKER_METRICS = args['worker-metrics'] ?? 'http://localhost:9101/metrics'
const HOT = 200
const COLD = 5000
const INVALID_POOL = 500

const agent = new http.Agent({ keepAlive: true, maxSockets: CONCURRENCY, maxFreeSockets: CONCURRENCY })
const pad = (n) => String(n).padStart(5, '0')
const rand = (n) => 1 + Math.floor(Math.random() * n)
const randomIp = () => `10.${rand(250)}.${rand(250)}.${rand(250)}`

function request(method, path, { headers = {}, body } = {}) {
  return new Promise((resolve, reject) => {
    const started = performance.now()
    const req = http.request(
      { host: BASE.hostname, port: BASE.port, path, method, agent, headers: { ...headers, ...(body ? { 'Content-Length': Buffer.byteLength(body) } : {}) } },
      (res) => {
        const chunks = []
        res.on('data', (c) => chunks.push(c))
        res.on('end', () => resolve({ status: res.statusCode, ms: performance.now() - started, headers: res.headers, body: Buffer.concat(chunks).toString() }))
      },
    )
    req.on('error', reject)
    req.setTimeout(10_000, () => req.destroy(new Error('timeout')))
    if (body) req.write(body)
    req.end()
  })
}

const json = (token) => ({ 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) })

function pickRedirect() {
  const r = Math.random()
  if (r < 0.9) return { kind: 'hot', code: `bh${pad(rand(HOT))}`, expect: 302 }
  if (r < 0.95) return { kind: 'cold', code: `bc${pad(rand(COLD))}`, expect: 302 }
  return { kind: 'invalid', code: `zz${pad(rand(INVALID_POOL))}`, expect: 404 }
}

async function setupUser(links = 0) {
  const email = `bench-${Date.now()}-${Math.random().toString(36).slice(2, 6)}@example.com`
  const creds = JSON.stringify({ email, password: 'bench-password-123' })
  const reg = await request('POST', '/api/v1/auth/register', { headers: json(), body: creds })
  if (reg.status !== 201) throw new Error(`register failed ${reg.status}: ${reg.body}`)
  const login = await request('POST', '/api/v1/auth/login', { headers: json(), body: creds })
  const token = JSON.parse(login.body).access_token
  const codes = []
  for (let i = 0; i < links; i++) {
    const r = await request('POST', '/api/v1/urls', { headers: json(token), body: JSON.stringify({ original_url: `https://example.com/mixed/${i}` }) })
    if (r.status === 201) codes.push(JSON.parse(r.body).short_code)
  }
  return { token, codes }
}

// ------------------------------------------------------------------ workloads
const scenarios = {
  async redirect() {
    return () => {
      const t = pickRedirect()
      return { kind: t.kind, expect: [t.expect], run: () => request('GET', `/${t.code}`, { headers: { 'X-Forwarded-For': randomIp(), 'User-Agent': 'loadgen' } }) }
    }
  },
  async create() {
    const { token } = await setupUser()
    let n = 0
    return () => {
      const i = n++
      const alias = i % 5 === 0
      const payload = { original_url: `https://example.com/bench/${Date.now()}/${i}`, ...(alias ? { custom_alias: `lg-${Date.now().toString(36)}-${i}-${Math.random().toString(36).slice(2, 6)}` } : {}) }
      return { kind: alias ? 'create_alias' : 'create_generated', expect: [201], run: () => request('POST', '/api/v1/urls', { headers: json(token), body: JSON.stringify(payload) }) }
    }
  },
  async mixed() {
    const { token, codes } = await setupUser(20)
    if (!codes.length) throw new Error('no owned links could be created')
    let n = 0
    return () => {
      const r = Math.random()
      if (r < 0.9) {
        const t = pickRedirect()
        return { kind: `redirect_${t.kind}`, expect: [t.expect], run: () => request('GET', `/${t.code}`, { headers: { 'X-Forwarded-For': randomIp() } }) }
      }
      if (r < 0.98) {
        const code = codes[rand(codes.length) - 1]
        const path = Math.random() < 0.3 ? '/api/v1/dashboard/summary' : `/api/v1/analytics/${code}${['', '/daily', '/browsers', '/referrers'][Math.floor(Math.random() * 4)]}`
        return { kind: 'analytics', expect: [200], run: () => request('GET', path, { headers: json(token) }) }
      }
      const i = n++
      return { kind: 'create', expect: [201], run: () => request('POST', '/api/v1/urls', { headers: json(token), body: JSON.stringify({ original_url: `https://example.com/mixed/${Date.now()}/${i}` }) }) }
    }
  },
}

// ------------------------------------------------------------ observability
function parseMetrics(text) {
  const out = {}
  for (const line of text.split('\n')) {
    if (!line || line.startsWith('#')) continue
    const m = line.match(/^([a-zA-Z_:][\w:]*)(\{[^}]*\})?\s+([-+0-9.eE]+|NaN)$/)
    if (m) out[m[1] + (m[2] ?? '')] = Number(m[3])
  }
  return out
}

async function scrape(url) {
  try {
    const u = new URL(url)
    const res = await new Promise((resolve, reject) => {
      http.get({ host: u.hostname, port: u.port, path: u.pathname }, (r) => {
        const c = []
        r.on('data', (d) => c.push(d))
        r.on('end', () => resolve(Buffer.concat(c).toString()))
      }).on('error', reject)
    })
    return parseMetrics(res)
  } catch {
    return null
  }
}

/** Cumulative CPU seconds per component, read from /proc (clock ticks at 100 Hz). */
function cpuSample() {
  const groups = { postgres: 0, redis: 0, api: 0, worker: 0, loadgen: 0 }
  for (const pid of fs.readdirSync('/proc').filter((p) => /^\d+$/.test(p))) {
    try {
      const cmd = fs.readFileSync(`/proc/${pid}/cmdline`, 'utf8').replace(/\0/g, ' ')
      const stat = fs.readFileSync(`/proc/${pid}/stat`, 'utf8')
      const f = stat.slice(stat.lastIndexOf(')') + 2).split(' ')
      const secs = (Number(f[11]) + Number(f[12])) / 100
      const key = /postgres/.test(cmd) ? 'postgres' : /redis-server/.test(cmd) ? 'redis' : /uvicorn app\.main/.test(cmd) ? 'api' : /click_worker/.test(cmd) ? 'worker' : /loadgen\.mjs/.test(cmd) ? 'loadgen' : null
      if (key) groups[key] += secs
    } catch {
      /* process exited */
    }
  }
  return groups
}

function pgStats() {
  try {
    const row = execSync(`psql -h localhost -U shortener -d ${process.env.BENCH_DB ?? 'shortener'} -Atc "select xact_commit + xact_rollback, tup_returned + tup_fetched from pg_stat_database where datname = '${process.env.BENCH_DB ?? 'shortener'}'"`, { env: { ...process.env, PGPASSWORD: 'shortener' } }).toString().trim().split('|')
    return { transactions: Number(row[0]), tuples: Number(row[1]) }
  } catch {
    return null
  }
}

function sumPrefix(m, prefix) {
  return m ? Object.entries(m).filter(([k]) => k.startsWith(prefix)).reduce((a, [, v]) => a + v, 0) : 0
}

function percentile(sorted, p) {
  if (!sorted.length) return null
  return sorted[Math.min(sorted.length - 1, Math.ceil((p / 100) * sorted.length) - 1)]
}

// --------------------------------------------------------------------- run
async function main() {
  const next = await scenarios[SCENARIO]()
  const lat = []
  const perKind = {}
  const statusCounts = {}
  let unexpected = 0
  let errors = 0
  let recording = false
  let stop = false

  async function worker() {
    while (!stop) {
      const job = next()
      try {
        const res = await job.run()
        if (!recording) continue
        lat.push(res.ms)
        ;(perKind[job.kind] ??= []).push(res.ms)
        statusCounts[res.status] = (statusCounts[res.status] ?? 0) + 1
        if (!job.expect.includes(res.status)) unexpected++
      } catch {
        if (recording) errors++
      }
    }
  }

  const workers = Array.from({ length: CONCURRENCY }, worker)
  await new Promise((r) => setTimeout(r, WARMUP))

  const before = { app: await scrape(`${BASE.origin}/metrics`), worker: await scrape(WORKER_METRICS), cpu: cpuSample(), pg: pgStats() }
  const t0 = performance.now()
  recording = true
  await new Promise((r) => setTimeout(r, DURATION))
  recording = false
  const elapsed = (performance.now() - t0) / 1000
  const after = { app: await scrape(`${BASE.origin}/metrics`), worker: await scrape(WORKER_METRICS), cpu: cpuSample(), pg: pgStats() }
  stop = true
  await Promise.all(workers)
  agent.destroy()

  const summarize = (arr) => {
    const s = [...arr].sort((a, b) => a - b)
    return { count: s.length, p50: percentile(s, 50), p95: percentile(s, 95), p99: percentile(s, 99), max: s.at(-1) ?? null, mean: s.reduce((a, b) => a + b, 0) / (s.length || 1) }
  }
  const delta = (a, b, key) => (a && b && key in b ? b[key] - (a[key] ?? 0) : null)
  const hits = delta(before.app, after.app, 'cache_hits_total')
  const negHits = delta(before.app, after.app, 'cache_negative_hits_total')
  const misses = delta(before.app, after.app, 'cache_misses_total')
  const lookups = hits != null ? hits + negHits + misses : null
  const cpu = Object.fromEntries(Object.keys(after.cpu).map((k) => [k, +((after.cpu[k] - before.cpu[k]) / elapsed * 100).toFixed(1)]))

  const result = {
    label: LABEL,
    tool: 'loadgen (Node http, keep-alive) - NOT k6',
    scenario: SCENARIO,
    config: { concurrency: CONCURRENCY, duration_s: DURATION / 1000, warmup_s: WARMUP / 1000 },
    requests: lat.length,
    elapsed_s: +elapsed.toFixed(2),
    requests_per_second: +(lat.length / elapsed).toFixed(1),
    latency_ms: Object.fromEntries(Object.entries(summarize(lat)).map(([k, v]) => [k, v == null ? v : +v.toFixed(3)])),
    latency_by_kind_ms: Object.fromEntries(Object.entries(perKind).map(([k, v]) => [k, Object.fromEntries(Object.entries(summarize(v)).map(([a, b]) => [a, b == null ? b : +b.toFixed(3)]))])),
    status_counts: statusCounts,
    error_rate: lat.length + errors ? +(((unexpected + errors) / (lat.length + errors)) * 100).toFixed(3) : 0,
    transport_errors: errors,
    unexpected_status: unexpected,
    cache: lookups ? { hits: hits, negative_hits: negHits, misses, lookups, hit_ratio_pct: +(((hits + negHits) / lookups) * 100).toFixed(2) } : null,
    cpu_percent_of_one_core: cpu,
    postgres: before.pg && after.pg ? { transactions_per_second: +((after.pg.transactions - before.pg.transactions) / elapsed).toFixed(1), tuples_per_second: +((after.pg.tuples - before.pg.tuples) / elapsed).toFixed(0) } : null,
    worker: after.worker
      ? {
          events_processed: delta(before.worker, after.worker, 'worker_events_processed_total'),
          throughput_events_per_second: +(delta(before.worker, after.worker, 'worker_events_processed_total') / elapsed).toFixed(1),
          stream_lag_at_end: after.worker.worker_stream_lag ?? null,
          stream_pending_at_end: after.worker.worker_stream_pending ?? null,
          stream_length_at_end: after.worker.worker_stream_length ?? null,
        }
      : null,
    enqueue_failures: sumPrefix(after.app, 'stream_enqueue_failures_total') - sumPrefix(before.app, 'stream_enqueue_failures_total'),
  }
  const text = JSON.stringify(result, null, 2)
  if (args.out) fs.writeFileSync(args.out, text)
  console.log(text)
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})
