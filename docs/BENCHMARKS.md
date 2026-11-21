# Benchmarks

All numbers on this page were produced by `load-testing/native/run_benchmarks.sh` and are stored as raw JSON in
`load-testing/results/`. Nothing is estimated or extrapolated. **They describe one small sandbox machine and say
little about production hardware.**

## What was and was not used

* **Load generator: `load-testing/native/loadgen.mjs` (Node 22, HTTP keep-alive). It is NOT k6.** k6 could not be
  downloaded in the sandbox, so the k6 scripts in `load-testing/*.js` were only syntax-checked and **never executed**.
  The Node generator implements the same scenarios; do not treat these results as k6 results.
* **No Docker.** API, worker, PostgreSQL and Redis ran as native processes.
* **No sub-10 ms p99 claim.** The brief lists it as an optimisation goal; it was **not met** in these measurements
  (best cached-redirect p99 at low concurrency ≈ 13 ms on this machine, and see the caveats below).

## Environment

| item | value |
|---|---|
| CPU / RAM | 2 vCPU, ~8 GB (Linux 6.18, virtualised sandbox) |
| Processes | 1 uvicorn worker (API), 1 click worker, PostgreSQL 16.15, Redis 7.0.15, **and the load generator — all sharing the same 2 vCPUs** |
| Python / Node | 3.13.18 / 22.23.5 |
| Database | isolated DB `shortener_bench`, Redis db 5, seeded by `load-testing/seed.sql` (200 hot codes, 5,000 cold codes) |
| Rate limiter | enabled and executed on every request, but with limits raised to 10⁹ so it never rejects; client IPs spread over ~15 M random values via `X-Forwarded-For` from a trusted proxy |
| Duration | 20 s measured per run after a 3 s warm-up; 2 repeats per concurrency level |
| Concurrency | 4 connections (≈ unloaded latency) and 32 connections (saturation) |

Because the load generator, API, worker, PostgreSQL and Redis compete for two cores, **absolute throughput is far lower
than the same software would reach on dedicated hardware**, and the API process (one Python event loop) is saturated at
~85–95 % of a core in nearly every run.

## Scenarios

| scenario | traffic |
|---|---|
| redirect | ≈ 90 % hot codes (cacheable), remainder cold codes and nonexistent codes |
| mixed | ≈ 80 % hot redirects, ~5 % cold, ~5 % nonexistent, ~8 % analytics reads, ~2 % link creation |
| create | `POST /api/v1/urls` only |

## Redirect comparison (requests/second and latency, ms)

Each row is one 20 s run; "r1/r2" are the two repeats. The spread between repeats is a rough indication of noise;
two runs is **not** enough for confidence intervals.

| configuration | conc. | run | req/s | p50 | p95 | p99 | cache hit % |
|---|---|---|---|---|---|---|---|
| 1. PostgreSQL only (cache off) | 4 | r1 | 465.1 | 8.15 | 12.27 | 15.08 | – |
| | 4 | r2 | 470.2 | 7.98 | 12.19 | 16.39 | – |
| | 32 | r1 | 473.0 | 64.10 | 95.41 | 130.92 | – |
| | 32 | r2 | 478.0 | 64.48 | 88.72 | 106.62 | – |
| 2. Redis cache | 4 | r1 | 853.3 | 3.82 | 10.47 | 15.02 | 90.3 |
| | 4 | r2 | 946.9 | 3.45 | 9.27 | 13.28 | 91.1 |
| | 32 | r1 | 981.5 | 27.16 | 79.76 | 103.26 | 91.8 |
| | 32 | r2 | 1074.2 | 24.95 | 70.20 | 95.71 | 92.7 |
| 3. Redis cache + negative cache | 4 | r1 | 921.0 | 3.61 | 9.59 | 14.26 | 93.7 |
| | 4 | r2 | 989.3 | 3.47 | 8.23 | 13.02 | 96.2 |
| | 32 | r1 | 996.5 | 27.38 | 64.43 | 111.74 | 95.7 |
| | 32 | r2 | 1081.3 | 26.20 | 46.23 | 97.93 | 97.3 |

Cache hit % counts positive and negative hits over all lookups. In config 1 the cache is disabled, so none is reported.

**Reading this honestly**

* Caching roughly doubled throughput (≈ 470 → ≈ 850–1,080 req/s) and cut median latency by about half at low
  concurrency (≈ 8 ms → ≈ 3.5 ms) and by more than half at saturation (≈ 64 ms → ≈ 25 ms).
* Adding the negative cache changed throughput by less than the repeat-to-repeat spread (r1 vs r2 differ by 8–10 %
  within each configuration), so **these runs cannot show a throughput benefit from negative caching**. It lowered the
  PostgreSQL transaction rate (≈ 165 → 60–130 tx/s) because unknown codes stopped reaching the database; that is the effect
  it is designed to have, but with only ~5 % unknown-code traffic here it is small.
* Cached redirects were limited by the API process's CPU (≈ 86–94 % of a core), not by PostgreSQL (≈ 5–6 %).
  Throughput is therefore not a statement about Redis or PostgreSQL capacity.
* At 32 connections latency is mostly queueing time (closed-loop generator, saturated API): compare p50 at 4 connections
  for service time, and req/s at 32 for capacity. Do not read the 32-connection latencies as per-request cost.

## Mixed workload and URL creation

| scenario | conc. | run | req/s | p50 | p95 | p99 | cache hit % |
|---|---|---|---|---|---|---|---|
| mixed (full architecture) | 4 | r1 | 610.5 | 4.77 | 16.99 | 25.95 | 92.8 |
| | 4 | r2 | 662.7 | 4.39 | 16.02 | 24.42 | 95.4 |
| | 32 | r1 | 707.5 | 33.22 | 116.63 | 168.35 | 94.5 |
| | 32 | r2 | 736.0 | 32.60 | 114.11 | 161.30 | 95.9 |
| create only | 4 | r1 | 223.3 | 17.08 | 24.97 | 32.03 | – |
| | 4 | r2 | 221.0 | 17.17 | 25.70 | 33.59 | – |
| | 32 | r1 | 224.0 | 141.01 | 200.82 | 240.24 | – |
| | 32 | r2 | 220.7 | 145.43 | 189.25 | 211.19 | – |

Creation throughput (~222/s) did not change between 4 and 32 connections: the API process was already at ~96 % CPU,
so the limit here is the API process, not the number of connections. Creation was not profiled, so no cause beyond
"API CPU-bound" is claimed.
Per-kind latencies for the mixed runs (hot redirect, cold redirect, unknown code, analytics read, create) are in the JSON
files under `latency_by_kind_ms`; at 32 connections the mixed hot-redirect p99 was ≈ 66 ms and cold/unknown/analytics/create
requests were slower, as expected.

## Analytics pipeline under load

* **Worker keeps up on this box.** In every run `stream_lag_at_end` was a few entries (0–3 in most; up to 227 in
  one run), and each run's backlog drained before the next began (the runner waits for an empty stream).
  Worker throughput of 450–1,025 events/s in the table of JSON results simply tracks the redirect rate; it is
  not a measured maximum.
* **Integrity after the full run:** `urls.total_clicks` = 259,727, `daily_analytics` = 259,727 and `click_events` rows
  = 259,727. The three independent stores agree exactly, i.e. no double counting and no loss *after* an event reached
  the stream.
* **Click events can be dropped by design, and some were.** The redirect path gives `XADD` a strict timeout (50 ms)
  and never delays or fails the redirect beyond it. At 32 connections on a saturated 2-core machine this timeout was
  hit. In the benchmark table above, `enqueue_failures` per run were 3-39 in most saturated runs and **1,513 in
  `4_mixed_full_c32_r2`** (about 10 % of that run's ~14,700 requests). At 4 connections there were none.
* **Follow-up investigation (separate probe runs, not part of the table above).** The 1,513 was a cascade, not slow
  Redis: only a few dozen requests actually timed out, but five consecutive timeouts opened the in-process circuit
  breaker, which then skipped every enqueue for its 2 s cooldown (`reason="circuit_open"`, about 1,100 events in one
  probe). A timeout here measures wall time including event-loop scheduling delay on the saturated API process, so it
  is not evidence that Redis was slow (this is the likely cause, supported by the 250 ms result below, not separately
  profiled).
  * With `STREAM_ENQUEUE_TIMEOUT_MS=250`, two 20 s probes of the same mixed 32-connection scenario lost **0** events
    with no change in throughput or p99 (about 650 req/s, p99 about 190 ms).
  * **Code change made after the benchmark table was recorded:** timeouts no longer count toward the breaker
    (`click_ingest.py`); connection errors still do. Three further 20 s probes at 50 ms then showed no `circuit_open`
    drops, only individual timeouts: about 120, 270 and 400 events in the measured window (roughly 1-3 % of requests),
    at 612-639 req/s. The table above predates this change and was not re-run.
  * Remaining trade-off: under CPU saturation with the strict 50 ms default, a small share of clicks is still dropped
    by design. Raising the timeout removed that loss in these probes, at the cost of a looser bound on how long a
    redirect can wait for analytics. Alert on `stream_enqueue_failures_total`; it was not tuned further.
  * The load generator's `enqueue_failures` field counts only the measured window, so it excludes the 3 s warm-up,
    while a plain `/metrics` scrape includes it.

## Caveats and measurement limits

* The load generator shares the CPUs with everything else; its own CPU use (~7–15 % of one core) is included in the
  contention. A real test needs separate machines.
* CPU percentages come from `/proc` sampling over the run. A few PostgreSQL samples are negative or implausible
  (e.g. `-36.8` in `creation_c32_r1`), which is a sampling artifact (PIDs of short-lived backends changing between
  samples). **PostgreSQL CPU figures are unreliable; API CPU figures are consistent across runs.**
* Only two repeats per point and a 20 s duration were used; run-to-run variation of 5–10 % is visible. A separate
  earlier, interrupted run of the same script (discarded; its results were deleted and are not reported here) gave
  similar numbers (e.g. 500 req/s for config 1 at 4 connections vs 465–470 here), which suggests ≈ 7 % variation between
  invocations on this shared machine.
* Single API process; horizontal scaling was not measured.
* Redirect targets are never fetched and Redis/PostgreSQL ran with default configuration, no tuning.
* Latency is measured by the Node client and includes its own scheduling delay.

## Reproducing

```bash
# PostgreSQL 16 + Redis 7 running locally; role/db as in the script header
cd load-testing/native
PYBIN=/path/to/backend/venv/bin DURATION=20 LEVELS="4 32" REPEATS=2 ./run_benchmarks.sh
```
Results are written to `load-testing/results/<label>.json` and a data-integrity check is printed at the end.
To run the k6 scripts instead (never executed during development), see the README.
