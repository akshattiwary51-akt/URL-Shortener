# Development progress and verification status

Everything below was run in a single Linux sandbox (2 vCPU, 8 GB RAM) with **native** PostgreSQL 16 and Redis 7
processes. There was no Docker daemon and no k6 binary. Where something could not be run, it is listed under
"Not verified" rather than claimed.

## Phase 1 — Architecture and infrastructure
* **Features**: monorepo layout, `docker-compose.yml` (postgres, redis, one-shot migrate, backend, worker, cleanup,
  frontend), Dockerfiles, `.env.example`, `.gitignore`, architecture documentation.
* **Files**: `docker-compose.yml`, `backend/Dockerfile`, `frontend/Dockerfile`, `frontend/nginx.conf`,
  `.env.example`, `docs/ARCHITECTURE.md`.
* **Verified**: compose file parses; environment variables are consumed by `app/core/config.py`.
* **Not verified**: images were never built; `docker compose up` was never run.

## Phase 2 — Database
* **Features**: SQLAlchemy 2 async models, Alembic migration `0001_initial_schema`, constraints and indexes,
  ID-allocator table.
* **Files**: `backend/app/database/**`, `backend/migrations/**`, `docs/DATABASE.md`.
* **Verified**: migration applies to a fresh database on every test run; models and migration agree (`alembic check`).

## Phase 3 — Core backend
* **Features**: Base62 range allocator with bijective scramble, custom aliases (409 on conflict), URL validation,
  JWT auth with Argon2id, ownership checks, expiry, keyset pagination, error envelope, request IDs, security headers.
* **Verified by tests**: unit tests for Base62 and validation; integration tests for allocation under concurrency
  (no duplicate codes, no reuse), auth, URL CRUD and authorisation.

## Phase 4 — Redirect, cache, rate limiting
* **Features**: 302 + `Cache-Control: no-store`, cache-aside with negative caching, write-through invalidation,
  circuit breaker, atomic Lua sliding-window limiter with `X-RateLimit-*` / `Retry-After`, trusted-proxy IP resolution.
* **Verified by tests** (real Redis): hit/miss/negative/expired/gone paths, corrupt cache value handling, Redis-down
  fallback, 200 concurrent requests across four clients admit exactly the limit, fail-open behaviour. A naive
  GET-then-ADD limiter was run against the same race to confirm the test detects the bug.

## Phase 5 — Analytics pipeline
* **Features**: `XADD` producer with strict timeout, consumer-group worker (batch 500 / ~5 s, `XAUTOCLAIM`),
  idempotency by `event_id`, ack after commit, dead-letter stream, aggregate tables, cleanup worker, GeoIP at enqueue.
* **Verified by tests**: duplicate delivery never double-counts, crash-and-reclaim, malformed events dead-lettered,
  deleted URLs dropped, no raw IP persisted.
* **Verified end to end** (separate real processes): redirect → stream → worker → analytics API; the stream drained to 0
  and counts matched the clicks sent.

## Phase 6 — Frontend
* **Features**: landing page with inline shortener, register/login, dashboard, link management (search, pagination,
  create with alias/expiry, activate/deactivate, delete), analytics detail with charts, settings, light/dark theme,
  responsive layout.
* **Verified**: `tsc` clean, production build, Vitest unit/component tests, Playwright end-to-end tests against the
  running stack, and visual review of screenshots at desktop and mobile widths.

## Phase 7 — Testing and load testing
* Backend: 199 tests (unit + integration + concurrency + failure injection).
* Frontend: 44 Vitest tests, 6 Playwright tests.
* Load: k6 scripts written but **never executed** (k6 download was blocked). A Node load generator with the same
  scenarios was used instead for the recorded measurements; see `docs/BENCHMARKS.md`.

## Phase 8 — Documentation
`README.md`, `docs/ARCHITECTURE.md`, `docs/API.md`, `docs/DATABASE.md`, `docs/BENCHMARKS.md`, this file.

## Known issues and deviations from the brief
* **GeoIP is resolved at enqueue time**, not in the worker, so raw IPs never travel through Redis. Documented in ARCHITECTURE.md.
* **Metrics are per process**, and `/metrics` is unauthenticated: restrict it at the network layer.
* **JWT in `localStorage`** is simple but exposed to XSS; httpOnly refresh cookies would be stronger.
* **Search uses `ILIKE`**; add a trigram index for very large per-user link sets.
* **No Bloom filter** (optional in the brief); negative caching and rate limiting are used.
* **Google Fonts** could not be loaded in the sandbox, so screenshots used fallback fonts; production loads them via the stylesheet link.
* **GeoIP country data** requires the operator to supply a GeoLite2-Country `.mmdb`.

## Not verified
* Docker image builds and `docker compose up`.
* k6 scripts (syntax-checked only).
* Multi-replica behaviour across separate hosts (concurrency was exercised with multiple clients/processes on one host).
* Any performance figure on hardware other than the sandbox; no sub-10 ms p99 is claimed.

## Next milestones
1. Build and run the Compose stack on a machine with Docker; fix anything that surfaces.
2. Run the k6 scripts and compare with the native generator's results.
3. Authenticate or network-isolate `/metrics`; add Grafana dashboards.
4. httpOnly refresh-cookie auth; trigram search index; optional Bloom filter.
