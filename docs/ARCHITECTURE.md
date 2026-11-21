# Architecture

This document records the design decisions behind the URL shortener. It is the
Phase 1 deliverable and is kept in sync with the implementation; where the
implementation intentionally deviates from the original brief, the deviation and
its reason are called out in **Deviations**.

## 1. Components

```
Browser ──► React SPA (Vite)            static files (nginx in Docker)
   │
   │ XHR /api/v1/*                      short-link visits: GET /{code}
   ▼                                           │
┌────────────────────── FastAPI (stateless, N replicas) ─────────────────────┐
│ middleware: request-id, body limit, security headers, access log, CORS      │
│                                                                             │
│  Auth ── URL service ── Redirect service ── Rate limiter ── Click ingestor  │
└──────┬────────┬──────────────┬─────────────────┬───────────────┬───────────┘
       │        │              │                 │               │
       │        ▼              ▼                 ▼               ▼
       │   ID allocator    Redis (cache)    Redis (ZSET+Lua)  Redis Stream
       │   (PG row, 1000/range)                              click_events
       ▼                                                          │
  PostgreSQL ◄──── batch UPSERT (one txn, then XACK) ◄── Click worker (consumer group)
       ▲                                                          ▲
       └───────────── Cleanup worker (batched deletes) ───────────┘
```

* **backend** – FastAPI app, fully async (asyncpg, redis-py asyncio). Stateless;
  scale by adding replicas. One OS process per container (Prometheus metrics are
  per-process; scrape each replica).
* **worker** – Redis Streams consumer-group process that batches click events
  into PostgreSQL. Any number of workers can run concurrently.
* **cleanup** – batched maintenance: evicts expired entries from Redis, prunes
  old raw click events, optionally hard-deletes long-expired URLs.
* **postgres** – source of truth. **redis** – cache, rate-limit windows, stream.

Three *independent* redis-py clients (separate connection pools, and separately
configurable URLs) are used for cache, rate limiting and the event stream so a
slow or failing concern cannot starve the others, and so each can later be
pointed at its own Redis instance.

## 2. Request flows

### 2.1 Redirect (`GET /{short_code}`) – hot path

1. Validate the code against `^[A-Za-z0-9_-]{3,32}$` (else 404, no I/O).
2. Sliding-window rate limit per client IP (Redis, one Lua `EVALSHA`).
3. `GET url:{code}` on the cache client.
   * record → check embedded expiry → go to 6
   * `__NOT_FOUND__` → 404 (negative cache hit)
   * `__GONE__` → 410
   * corrupt value → delete key, count it, treat as miss
4. Miss → indexed lookup `urls.short_code` in PostgreSQL.
5. Not found → `SET url:{code} __NOT_FOUND__ NX EX 60`, 404.
   Inactive/expired → `__GONE__` tombstone (60 s), 410.
   Active → `SET url:{code} {json} NX EX min(24h, time-to-expiry)`.
6. `XADD click_events` with a strict timeout (default 50 ms). Failure is logged,
   counted and **never** fails the redirect.
7. `302 Found`, `Location: …`, `Cache-Control: no-store`.

PostgreSQL is touched only on a cache miss, and never for analytics.

### 2.2 URL creation

Validate → normalise URL → allocate code (or insert the custom alias) → insert
row → commit → write-through the cache record (this also overwrites any stale
`__NOT_FOUND__`). Unique violations on a custom alias map to HTTP 409.

### 2.3 Click analytics

Redirect → `XADD` → consumer group → worker batch (≤500 events or 5 s) → one
PostgreSQL transaction (dedupe insert + aggregate UPSERTs) → `COMMIT` → `XACK`.

## 3. Key design decisions

### 3.1 Collision-free ID generation
* `id_allocator(allocator_name, next_id)` is the durable source of truth. A
  single `INSERT … ON CONFLICT DO UPDATE SET next_id = next_id + 1000 RETURNING`
  reserves a block atomically (row lock serialises concurrent reservations).
* Each process holds `[next, end)` in memory behind an `asyncio.Lock`.
  A crash loses at most the unused remainder of a block; blocks are never reissued.
* The integer is mapped through a **bijective** affine permutation modulo 62⁷
  (`(n·M + c) mod 62⁷`, `gcd(M, 62) = 1`) before Base62 encoding, so codes are
  unique *and* not enumerable in creation order. A bijection cannot introduce
  collisions. Encoding is fixed-length (7 chars, `a` is the zero digit).
* Capacity is enforced at reservation time (ID must be < 62⁷).
* Custom aliases share the `urls.short_code` UNIQUE constraint. A custom alias
  could coincide with a future generated code; the insert then fails the unique
  constraint and the service transparently takes the next allocated ID (bounded
  retries). No random-generate-and-probe loop is involved.

### 3.2 Caching and consistency
* Cache-aside with `SET … NX` on *population* and plain `SET` on *write-through*
  (create/update/delete). This ordering closes the classic race where a reader
  that loaded the old row repopulates the cache after a writer invalidated it:
  the writer's value/tombstone is already present, so the stale `NX` write is a no-op.
* TTL is `min(24h, expires_at − now)`; an expiry timestamp is also embedded in the
  value and re-checked on every hit, so an expired link can never redirect even
  if the key outlives its TTL.
* Redis failures degrade to PostgreSQL lookups. A small in-process circuit
  breaker avoids paying a connect timeout on every request during an outage.

### 3.3 Rate limiting
Sliding-window **log** in a ZSET, executed as a single Lua script using Redis
`TIME`: trim → count → conditionally add → set `PEXPIRE`. The whole script is
atomic so concurrent requests cannot oversubscribe the limit. Keys are
namespaced `rl:{category}:{identifier}`. Fail-open on Redis errors by default
(configurable).

### 3.4 Event pipeline
* Producer: compact event, `XADD … MAXLEN ~ 1_000_000`, strict timeout.
* Consumer: `XREADGROUP` + `XAUTOCLAIM`; ack only after DB commit.
* Idempotency: `click_events.event_id` is the primary key. The batch inserts
  with `ON CONFLICT DO NOTHING RETURNING event_id` and aggregates **only the rows
  that were actually inserted**, in the same transaction. Redelivery therefore
  cannot double count.
* Poison messages: malformed events and events that repeatedly fail in isolation
  are moved to `click_events_dead` and acknowledged.
* Privacy: IPs are HMAC-SHA256 hashed with a server secret before they enter the
  stream; raw event details (UA, ip_hash, referrer) are only persisted when
  `STORE_EVENT_DETAILS=true`; raw rows are pruned after
  `CLICK_EVENT_RETENTION_DAYS`.

### 3.5 Data model
See `DATABASE.md`. Aggregate tables reference `urls.short_code` with
`ON DELETE CASCADE`; `click_events` is a short-lived dedupe/audit table with no FK.

## 4. Failure behaviour

| Failure | Behaviour |
|---|---|
| Redis down | Redirects served from PostgreSQL; rate limiting fails open (configurable); click events dropped and counted (`stream_enqueue_failures_total`); `/health/ready` reports `degraded`. |
| Redis slow | Enqueue bounded by `STREAM_ENQUEUE_TIMEOUT_MS`; cache/limiter bounded by socket timeout + breaker. |
| PostgreSQL down | Cached redirects keep working; cache misses and API calls return 503; `/health/ready` returns 503. |
| Worker crash | Unacked entries stay in the PEL and are reclaimed via `XAUTOCLAIM` after `WORKER_CLAIM_IDLE_MS`. |
| Worker retry | Deduplicated by `event_id`; no double counting. |
| Corrupt cache value | Deleted, counted, served from PostgreSQL. |

## 5. Security summary
Argon2id passwords (hashing off the event loop), pinned-algorithm JWT with expiry
and issuer, ownership checks returning 404 (no existence leak), strict
http/https URL validation, `extra="forbid"` request models (mass-assignment
protection), body-size limit, security headers, explicit CORS origins, forwarded
headers trusted only from configured proxies, the server never fetches
destinations.

## 6. Deviations from the original brief
* **GeoIP lookup happens at enqueue time**, not in the worker. Resolving a
  country in the worker would require putting raw IPs on the stream (which may
  be persisted by Redis AOF), violating the "never persist raw IPs" requirement.
  The event carries only a 2-letter country code (empty when no GeoIP DB is
  configured).
* **Additional endpoints** beyond the brief: `PATCH /api/v1/urls/{code}`
  (deactivate / change expiry), `/analytics/{code}/os`, `/analytics/{code}/countries`,
  `POST /auth/change-password`, `DELETE /auth/me`. They are needed by the
  required UI (deactivate action, country chart, account management).
* **Per-process metrics**: one uvicorn process per container instead of a
  multi-process gunicorn setup, so Prometheus counters stay correct without
  multiprocess-mode files. Scale with replicas.

## 7. Milestones
1. Architecture & docs · 2. Infra (Compose, PG, Redis, health) · 3. Backend
foundation (models, migrations, errors, logging) · 4. IDs, auth, URL CRUD ·
5. Redirect + cache · 6. Rate limiting · 7. Analytics pipeline · 8. Frontend ·
9. Metrics + load tests · 10. Hardening + docs. Progress is tracked in
`DEVELOPMENT_PROGRESS.md`.
