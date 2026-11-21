# Brief — URL shortener with private, real-time analytics

Shorten links, set custom aliases and expiry dates, and see clicks by day, browser, device, operating system,
referrer and country, without ever storing a visitor's IP address.

* **Backend**: FastAPI · SQLAlchemy 2 (async) · asyncpg · Alembic · PostgreSQL 16
* **Redis**: redirect cache, atomic sliding-window rate limiter (Lua), Redis Streams click pipeline
* **Workers**: a Streams consumer-group analytics worker and a batched cleanup worker
* **Frontend**: React 19 · Vite · TypeScript · Tailwind CSS 4 · TanStack Query · Recharts · React Router
* **Ops**: Docker Compose, Prometheus metrics, structured JSON logs with request IDs, k6 load tests

Documentation: [Architecture](docs/ARCHITECTURE.md) · [Database](docs/DATABASE.md) · [API](docs/API.md) ·
[Benchmarks](docs/BENCHMARKS.md) · [Development progress & verification status](DEVELOPMENT_PROGRESS.md)

---

## Quick start

```bash
cp .env.example .env          # safe development defaults; change every secret before deploying
docker compose up --build
```

| URL | what |
|---|---|
| http://localhost:3000 | web app |
| http://localhost:8000/docs | interactive API documentation |
| http://localhost:8000/metrics | Prometheus metrics (API) |
| http://localhost:9101/metrics | Prometheus metrics (analytics worker, inside the `worker` container) |

Compose starts PostgreSQL and Redis, runs `alembic upgrade head` once in a one-shot `migrate` service, then starts
`backend`, `worker`, `cleanup` and `frontend`. Stop with `docker compose down` (add `-v` to delete data).

> **Verification status.** The Compose file and Dockerfiles were written and statically validated
> (`docker compose config`), but the sandbox this was built in has no Docker daemon, so **the images were never built
> and `docker compose up` was never run here**. Everything else was run natively against real PostgreSQL 16 and
> Redis 7 as separate processes (API, worker, cleanup, static frontend). See DEVELOPMENT_PROGRESS.md.

### Run without Docker (what was used to develop and verify this)

```bash
# prerequisites: PostgreSQL 16 and Redis 7 listening on localhost
cd backend
python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
export DATABASE_URL=postgresql+asyncpg://USER:PASS@localhost:5432/shortener
export REDIS_URL=redis://localhost:6379/0
alembic upgrade head
uvicorn app.main:create_app --factory --port 8000 &
python -m app.workers.click_worker &          # analytics
python -m app.workers.cleanup_worker &        # maintenance
cd ../frontend && npm ci && VITE_API_URL=http://localhost:8000 npm run build && npx vite preview --port 3000
```

---

## What the application does

* Anyone can shorten a URL on the landing page. Accounts add a dashboard, link management and analytics.
* Links get a generated 7-character code or a custom alias (3–32 of `A–Z a–z 0–9 - _`, case-sensitive).
* Links can expire (`410 Gone` afterwards), be deactivated/reactivated, or deleted.
* Visiting `/{code}` answers `302 Found` with `Cache-Control: no-store` so every click is counted.
* Analytics show total/today/7-day/30-day clicks, a daily series, browsers, devices, OS, referrers and countries
  (countries need an optional local GeoIP database).

## Why this architecture

The workload is extremely read-heavy and latency-sensitive on one path (redirects) while everything else can
tolerate delay. The design separates those concerns so the hot path touches as little as possible:

| Concern | Choice | Reason |
|---|---|---|
| Redirect lookup | Redis cache-aside, PostgreSQL on miss | A cache hit costs one `GET`; the database is only consulted for cold or unknown codes |
| Unknown codes | Negative cache (`__NOT_FOUND__`, 60 s) | Repeated lookups of nonexistent codes (typos, scanners) never reach PostgreSQL |
| Code generation | Range allocation + bijective scramble | No collision probing; no coordination on the hot path; codes not guessable by counting |
| Rate limiting | ZSET + one Lua script | Exact sliding window, atomic across all API processes, no GET-then-INCR race |
| Analytics | Redis Streams → batch worker → aggregates | Redirects never wait on PostgreSQL; writes are batched and idempotent |
| State | PostgreSQL + Redis only | API processes are stateless and scale horizontally |

## How Base62 range allocation works

1. The `id_allocator` row holds `next_id`. One statement (`INSERT … ON CONFLICT DO UPDATE SET next_id = next_id + 1000
   RETURNING`) atomically reserves a block of 1,000 numbers; PostgreSQL's row lock serialises reservations from all
   processes, so blocks never overlap.
2. Each API process hands out numbers from its in-memory block under an `asyncio.Lock`, and fetches a new block when
   it runs out. A crash loses only the unused remainder of a block, and `next_id` never moves backwards, so numbers are
   **never reused**.
3. Each number `n < 62⁷` is mapped through the permutation `(n·M + C) mod 62⁷` (with `gcd(M, 62) = 1`, so it is a
   bijection) and encoded as a fixed 7-character Base62 string (alphabet `a–z A–Z 0–9`, 62⁷ ≈ 3.52 × 10¹² codes).
   A bijection cannot create collisions; it only stops codes from being sequential. The service refuses to issue
   numbers outside the 7-character range.
4. `urls.short_code` is `UNIQUE`. Custom aliases share that constraint; if an alias happens to equal a generated
   code the generator transparently takes the next number (inside a savepoint) rather than failing.

> Never change `ID_SCRAMBLE_MULTIPLIER` / `ID_SCRAMBLE_OFFSET` on a database that already has generated codes.

## How Redis caching works

`GET /{code}` → validate shape → rate limit → `GET url:{code}`:
* record `{"u": url, "e": expiry}` → check expiry again → redirect
* `__NOT_FOUND__` → 404 · `__GONE__` → 410 · malformed value → deleted and treated as a miss
* miss → indexed PostgreSQL lookup, then populate (`SET … NX`, TTL = `min(24 h, time to expiry)`)

Writers (create, deactivate, expiry change, delete) update the cache with a plain `SET` *after* committing, while
readers populate with `NX`. A slow reader that read the old row can therefore never overwrite a newer value.
If Redis is down, redirects fall back to PostgreSQL and a small in-process circuit breaker avoids paying a timeout on
every request.

## How distributed rate limiting works

`rl:{category}:{identifier}` is a sorted set of request timestamps. One Lua script (executed atomically by Redis)
reads the **server's** clock with `TIME`, removes entries older than the window, counts what remains, adds the new
request only if under the limit, and refreshes the key's expiry. Because check-and-add is a single atomic step,
concurrent requests from any number of API processes cannot exceed the limit — the test suite fires 200 simultaneous
requests across four independent clients at a limit of 25 and asserts that exactly 25 pass (a naive GET-then-ADD
implementation lets all 200 through under the same race). Rejections return `429` with `Retry-After` and
`X-RateLimit-*` headers. Behaviour when Redis is unavailable is configurable (`RATE_LIMIT_FAIL_OPEN`, default open).

## How Redis Streams decouple analytics

A successful redirect does one bounded `XADD` (50 ms timeout by default) of a compact event
`{event_id, short_code, ts, ua, ip_hash, ref[, country]}`; failure is logged, counted and ignored. The client IP is
HMAC-SHA256-hashed with a secret **before** it enters the stream; the raw address is not stored anywhere.
The worker reads through a consumer group (`XREADGROUP`), collects up to 500 events or ~5 s, and writes them in one
transaction (see below), acknowledging with `XACK` only after the commit. Entries left pending by a crashed worker are
taken over with `XAUTOCLAIM`. Malformed or persistently failing events are moved to `click_events_dead`.

## How batching improves database efficiency

One transaction per *batch* instead of per click: a single dedupe `INSERT … ON CONFLICT DO NOTHING RETURNING`, one
`UPDATE … FROM unnest(…)` for URL totals, and one multi-row UPSERT per aggregate table, with counts pre-summed in
memory. 500 clicks become ~8 statements instead of thousands of round trips, and hot rows (a viral link's daily
counter) are updated once per batch with `+N` rather than once per click. Because the dedupe insert and the counters
share a transaction and counters use only the rows that were newly inserted, redelivery can never double-count.

---

## Configuration

Everything is an environment variable; `.env.example` lists all of them with development defaults. With
`ENVIRONMENT=production` the app refuses to start unless `SECRET_KEY` and `IP_HASH_SECRET` are random, 32+ characters.
Key settings: `RATE_LIMIT_*`, `CACHE_TTL_SECONDS`, `NEGATIVE_CACHE_TTL_SECONDS`, `WORKER_BATCH_SIZE`,
`WORKER_FLUSH_INTERVAL_SECONDS`, `CLICK_EVENT_RETENTION_DAYS`, `STORE_EVENT_DETAILS`, `TRUSTED_PROXIES`,
`GEOIP_DB_PATH` (optional MaxMind GeoLite2-Country `.mmdb`), `EXPIRED_URL_PURGE_DAYS`.

Behind a reverse proxy, set `TRUSTED_PROXIES` to the proxy's CIDR; otherwise every client appears to share the
proxy's IP and rate limits would apply to all of them together.

## Running the tests

```bash
# Backend: needs PostgreSQL and Redis. Defaults assume localhost, DB shortener_test, Redis db 15.
cd backend
export TEST_DATABASE_URL=postgresql+asyncpg://USER:PASS@localhost:5432/shortener_test
export TEST_REDIS_URL=redis://localhost:6379/15
pytest -q                     # unit + integration + concurrency + failure tests

# Frontend
cd frontend
npm test                      # Vitest
npx tsc --noEmit              # type check
npm run test:e2e              # Playwright; needs the full stack running (see e2e/app.spec.ts)
```
The backend suite drops and rebuilds the test schema with Alembic on every run (so it doubles as a "migrations work
on a fresh database" check) and uses real PostgreSQL and Redis — no mocks for distributed behaviour.
If your Playwright browsers do not match the pinned version, set `E2E_CHROMIUM_PATH` to a Chromium binary.

## Running load tests

```bash
psql "$DATABASE_URL_PLAIN" -f load-testing/seed.sql       # 200 hot + 5,000 cold fixture links
cd load-testing
k6 run redirect_test.js           # Scenario A: 90% cached, 10% misses/unknown codes
k6 run creation_test.js           # Scenario B: creation + rate-limit enforcement
k6 run mixed_workload_test.js     # Scenario C: 90% redirects / 8% analytics / 2% creation
```
Start the server for load tests with the redirect/create limits raised (see the header of each script) and
`TRUSTED_PROXIES` covering the load generator so synthetic client IPs are honoured.

A dependency-free Node load generator with the same scenarios, and a script that reproduces the four-way
comparison natively, live in `load-testing/native/` (`PYBIN=… ./run_benchmarks.sh`). It exists because k6 could not
be installed where the recorded benchmarks were produced — see [docs/BENCHMARKS.md](docs/BENCHMARKS.md).

## How to interpret benchmark results

* **Throughput vs latency.** Runs are closed-loop (a fixed number of connections each sending as fast as it can).
  At saturation, latency is mostly queueing time, so judge *service time* from the low-concurrency runs and
  *capacity* from the high-concurrency runs.
* **Where the time goes.** Compare the CPU columns: in this single-box setup the API process is the bottleneck for
  cached redirects, PostgreSQL for uncached ones.
* **Cache hit ratio** counts positive and negative hits over all lookups in the measured window.
* **Stream backlog / worker throughput** show whether analytics keeps up; a growing `worker_stream_lag` means more
  worker replicas are needed.
* Numbers depend entirely on the hardware and configuration recorded next to them; never compare across machines.

## Security notes

Argon2id passwords (hashing off the event loop); pinned-algorithm, expiring JWTs; ownership enforced in every
URL/analytics query (other users' links return 404); strict http/https destination validation (no `javascript:`,
`data:`, credentials, or links back to this service); `extra="forbid"` on request models (mass-assignment safe);
64 KiB body limit; explicit CORS origins; security headers and a locked-down CSP on API responses; forwarded headers
trusted only from configured proxies; the server never fetches destinations; raw IPs never stored or logged.
`/metrics` is unauthenticated — expose it only on an internal network.

## Current limitations and future improvements

* **Session storage.** The SPA keeps the bearer token in `localStorage` (no refresh tokens or httpOnly cookies);
  moving to short-lived tokens plus rotating httpOnly refresh cookies would reduce XSS exposure.
* **Redis durability.** Cache, limiter and stream share one Redis by default. The three clients are separately
  configurable (`CACHE_REDIS_URL`, …) so the stream can live on a persistent instance and the cache on an evictable one.
  A hard Redis loss can drop up to ~1 s of unacknowledged click events (AOF `everysec`).
* **Counters are eventually consistent** (worker flush interval, ~5 s) and raw-event retention is minimal by design.
* **Search** is `ILIKE`; a trigram index would help owners with very large link counts.
* **Expired-click race.** Events are deduplicated by `event_id` for `CLICK_EVENT_RETENTION_DAYS`; a message redelivered
  after that window could be counted again (redelivery normally happens within seconds).
* **No Bloom filter** for unknown codes (optional in the brief); negative caching plus rate limiting is used instead.
* **GeoIP** is resolved at enqueue time in the API process so raw IPs never enter Redis; it requires the optional database.
* **Docker images were not built** in the authoring environment (see Quick start).
* **k6 was not executed** in the authoring environment; its scripts are syntax-checked only.
* Single-process uvicorn per container; scale with replicas behind a load balancer.
