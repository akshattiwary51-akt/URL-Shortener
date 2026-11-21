#!/usr/bin/env bash
# Reproducible native benchmark runner (no Docker). Starts PostgreSQL-backed API + worker in an ISOLATED
# database (shortener_bench) and Redis db 5, then runs the four comparison configurations from the spec
# plus URL creation. Everything is measured with ./loadgen.mjs (k6 binary was unavailable; see ../README.md).
#
# Requirements: PostgreSQL + Redis already running on localhost, Node 20+, a Python env with backend deps.
#   PYBIN=/path/to/venv/bin ./run_benchmarks.sh            (PYBIN must contain uvicorn, alembic, python)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
PYBIN="${PYBIN:?set PYBIN to the venv bin directory}"
RESULTS="${RESULTS:-$ROOT/load-testing/results}"
LOGS="${LOGS:-/tmp/bench-logs}"
DURATION="${DURATION:-20}"
LEVELS="${LEVELS:-4 32}"      # 4 ~ unloaded latency, 32 ~ saturation throughput
REPEATS="${REPEATS:-2}"
export BENCH_DB="${BENCH_DB:-shortener_bench}"
export PGPASSWORD="${PGPASSWORD:-shortener}"
PSQL="psql -h localhost -U shortener -q"
mkdir -p "$RESULTS" "$LOGS"

export DATABASE_URL="postgresql+asyncpg://shortener:shortener@localhost:5432/$BENCH_DB"
export REDIS_URL="redis://localhost:6379/5"
export SECRET_KEY="bench-only-secret-key-bench-only-secret-key"
export IP_HASH_SECRET="bench-only-ip-secret-bench-only-ip-secret"
export BASE_URL="http://localhost:8000"
# Benchmark traffic comes from one machine: spread it over many client IPs via a trusted proxy header,
# and lift the limits so the limiter is *exercised* (full Lua call per request) but never rejects.
export TRUSTED_PROXIES="127.0.0.1/32"
export RATE_LIMIT_REDIRECT_PER_WINDOW=1000000000
export RATE_LIMIT_CREATE_PER_WINDOW=1000000000
export RATE_LIMIT_CREATE_USER_PER_WINDOW=1000000000
export RATE_LIMIT_AUTH_PER_WINDOW=1000000
export WORKER_METRICS_PORT=9101
export PATH="$PYBIN:$PATH"

stop_all() { pkill -f "uvicorn app.main" 2>/dev/null || true; pkill -f click_worker 2>/dev/null || true; pkill -f cleanup_worker 2>/dev/null || true; sleep 1; }
start_worker() { (cd "$ROOT/backend" && nohup python -m app.workers.click_worker >"$LOGS/worker.log" 2>&1 &); }
start_api() {  # env for the configuration is passed through the caller's environment
  (cd "$ROOT/backend" && nohup uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log >"$LOGS/api.log" 2>&1 &)
  for _ in $(seq 1 40); do curl -sf localhost:8000/health/ready >/dev/null && return 0; sleep 0.5; done
  echo "API did not become ready" >&2; exit 1
}
drain_stream() { for _ in $(seq 1 120); do [ "$(redis-cli -n 5 xlen click_events)" = "0" ] && return 0; sleep 1; done; echo "warning: stream not drained" >&2; }
reset_redis_cache() { redis-cli -n 5 --scan --pattern 'url:*' | xargs -r redis-cli -n 5 del >/dev/null; redis-cli -n 5 --scan --pattern 'rl:*' | xargs -r redis-cli -n 5 del >/dev/null; }

run() {  # run <label> <scenario>  -> every concurrency level, REPEATS times
  for c in $LEVELS; do
    for r in $(seq 1 "$REPEATS"); do
      local label="$1_c${c}_r${r}"
      echo ">>> $label ($2, ${c} connections, ${DURATION}s)"
      node "$HERE/loadgen.mjs" --scenario "$2" --duration "$DURATION" --concurrency "$c" --label "$label" --out "$RESULTS/$label.json" >/dev/null
      python3 - "$RESULTS/$label.json" <<'PY'
import json,sys
r=json.load(open(sys.argv[1])); l=r["latency_ms"]
print(f'    {r["requests_per_second"]} req/s  p50={l["p50"]} p95={l["p95"]} p99={l["p99"]} ms  errors={r["error_rate"]}%  cache_hit={(r["cache"] or {}).get("hit_ratio_pct")}')
PY
      drain_stream
    done
  done
}

echo "== preparing isolated benchmark database $BENCH_DB"
stop_all
$PSQL -d postgres -c "DROP DATABASE IF EXISTS $BENCH_DB" -c "CREATE DATABASE $BENCH_DB OWNER shortener"
(cd "$ROOT/backend" && alembic upgrade head >/dev/null 2>&1)
$PSQL -d "$BENCH_DB" -f "$ROOT/load-testing/seed.sql"
redis-cli -n 5 flushdb >/dev/null

start_worker
declare -A CFG=( [1_pg_only]="false false" [2_cache]="true false" [3_cache_negative]="true true" )
for name in 1_pg_only 2_cache 3_cache_negative; do
  read -r c n <<<"${CFG[$name]}"
  pkill -f "uvicorn app.main" 2>/dev/null || true; sleep 1
  CACHE_ENABLED=$c NEGATIVE_CACHE_ENABLED=$n start_api
  reset_redis_cache
  run "redirect_$name" redirect
done

# Full architecture under mixed traffic (cache + negative cache + analytics pipeline + API reads + creation).
pkill -f "uvicorn app.main" 2>/dev/null || true; sleep 1
CACHE_ENABLED=true NEGATIVE_CACHE_ENABLED=true start_api
reset_redis_cache
run "4_mixed_full" mixed
run "creation" create

echo "== data integrity check (counted vs recorded clicks)"
$PSQL -d "$BENCH_DB" -Atc "select 'urls.total_clicks sum = ' || coalesce(sum(total_clicks),0) from urls" \
  -c "select 'daily_analytics sum   = ' || coalesce(sum(total_clicks),0) from daily_analytics" \
  -c "select 'click_events rows     = ' || count(*) from click_events"
stop_all
echo "results written to $RESULTS"
