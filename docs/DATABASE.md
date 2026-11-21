# Database design

PostgreSQL 16 is the system of record. The schema is created and evolved only by Alembic
(`backend/migrations`). `alembic check` confirms the models and the migration agree, and the
test suite rebuilds the schema from scratch with `alembic upgrade head` at the start of every run.

## Entity relationships

```
users 1 ───< urls (owner_id, nullable → anonymous links)
urls  1 ───< daily_analytics / browser_analytics / os_analytics / device_analytics /
             country_analytics / referrer_analytics      (FK short_code, ON DELETE CASCADE)
click_events        standalone, short-lived dedupe table (no FK, pruned by retention)
id_allocator        one row per allocator name
```

## Tables

### `users`
| column | type | notes |
|---|---|---|
| id | bigint identity PK | |
| email | varchar(320) | **UNIQUE**; stored lower-cased |
| hashed_password | varchar(255) | Argon2id hash |
| created_at / updated_at | timestamptz | server default `now()` |

### `urls`
| column | type | notes |
|---|---|---|
| id | bigint identity PK | monotonic, used as the keyset-pagination cursor |
| short_code | varchar(32) | **UNIQUE** (`uq_urls_short_code`); CHECK length 3–32; case-sensitive |
| original_url | varchar(2048) | normalised http(s) URL |
| owner_id | bigint NULL | FK → users, `ON DELETE CASCADE` |
| created_at | timestamptz | |
| expires_at | timestamptz NULL | |
| is_active | boolean | default true |
| total_clicks | bigint | CHECK ≥ 0; maintained by the worker |

Indexes and the query each serves:

| index | serves |
|---|---|
| `uq_urls_short_code` (unique) | redirect lookup on a cache miss: `WHERE short_code = $1`; alias uniqueness |
| `ix_urls_owner_id_id (owner_id, id)` | `GET /urls` keyset pagination: `WHERE owner_id = $1 AND id < $2 ORDER BY id DESC LIMIT n`; ownership checks; dashboard top/recent |
| `ix_urls_expires_at` (partial, `WHERE expires_at IS NOT NULL`) | cleanup worker scans for recently / long expired links without indexing the (typically majority) of never-expiring rows |

Search (`q=`) uses `ILIKE` over the owner's rows; the owner index bounds the scan to one user's links.
A trigram index would be the next step for owners with very many links (see limitations in the README).

### `click_events`
`event_id` (varchar(64), **PK**), `short_code`, `timestamp` (indexed), `user_agent`, `ip_hash`, `referrer`.

The primary key is the **idempotency key** of the pipeline. By default only `event_id`, `short_code`
and `timestamp` carry data; `user_agent`, `ip_hash` and `referrer` are NULL unless
`STORE_EVENT_DETAILS=true`. Rows older than `CLICK_EVENT_RETENTION_DAYS` are deleted in batches.
No raw IP address is ever stored anywhere; `ip_hash` is a truncated HMAC-SHA256 under a server secret.

### Aggregates
All reference `urls.short_code` with `ON DELETE CASCADE`, so deleting a link removes its analytics.

| table | primary key / unique | counter |
|---|---|---|
| `daily_analytics` | `id` PK, **UNIQUE (short_code, date)**, index on `date` | `total_clicks` |
| `browser_analytics` | (short_code, browser_name) | `total_clicks` |
| `os_analytics` | (short_code, os_name) | `total_clicks` |
| `device_analytics` | (short_code, device_type) | `total_clicks` |
| `country_analytics` | (short_code, country_code) | `total_clicks` |
| `referrer_analytics` | (short_code, referrer) | `total_clicks` |

The composite primary keys double as the lookup index for "all rows for this short code", and are the
conflict targets of the worker's UPSERTs. Referrers are reduced to a bare lowercase host before storage
(paths and query strings can contain personal data).

### `id_allocator`
`allocator_name` (PK), `next_id` (bigint), `updated_at`. See below.

## ID allocation (range reservation)

```sql
INSERT INTO id_allocator (allocator_name, next_id, updated_at)
VALUES (:name, :size, now())
ON CONFLICT (allocator_name)
DO UPDATE SET next_id = id_allocator.next_id + :size, updated_at = now()
RETURNING next_id - :size AS start_id;
```

One statement, one row lock: concurrent reservations from any number of processes are serialised by
PostgreSQL and each receives a disjoint `[start, start + 1000)` block. A process keeps its block in memory
behind an `asyncio.Lock`. If it crashes the remainder of the block is simply never used; `next_id` only moves
forward, so IDs are never reused. The service refuses to issue an ID `>= 62^7`.

## Analytics write path (per worker batch, one transaction)

1. `SELECT short_code FROM urls WHERE short_code IN (…) ORDER BY short_code FOR NO KEY UPDATE`
   – drops events for deleted URLs, serialises concurrent workers per URL in a consistent order (no deadlocks),
   and blocks a concurrent `DELETE` until commit.
2. `INSERT INTO click_events … ON CONFLICT (event_id) DO NOTHING RETURNING event_id` – returns only new events.
3. Counters computed **from the returned (new) events only**, then
   `UPDATE urls SET total_clicks = total_clicks + v.n FROM unnest(…)` and
   `INSERT … ON CONFLICT (…) DO UPDATE SET total_clicks = t.total_clicks + excluded.total_clicks`
   for each aggregate table, with keys sorted for lock-order consistency.
4. `COMMIT`, and only then `XACK`.

A redelivered event conflicts in step 2, is absent from `RETURNING`, and therefore changes nothing.

## Read patterns

* Redirect: Redis first; PostgreSQL only on a miss, `short_code` unique-index lookup selecting three columns.
* Lists: keyset (`id < cursor`) – cost is independent of page depth, unlike `OFFSET`.
* Analytics: pre-aggregated tables read with `ORDER BY total_clicks DESC LIMIT n`; a window `SUM() OVER ()`
  provides the grand total for percentages in the same query. The daily series uses `generate_series` joined to
  `daily_analytics` so days with no clicks are returned as zero without the application filling gaps.
  Nothing loads a whole analytics table into application memory.

## Migrations

```bash
cd backend
alembic upgrade head        # apply
alembic downgrade base      # fully reversible
alembic check               # fail if models and migrations drift
```
Docker Compose runs `alembic upgrade head` once in a dedicated `migrate` service; the API, worker and cleanup
services start only after it completes successfully, so replicas never race to migrate.
