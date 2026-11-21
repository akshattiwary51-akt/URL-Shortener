-- Benchmark fixtures, inserted directly so seeding thousands of rows is not throttled by the API rate limiter.
--   bh00001..bh00200  "hot"  links: requested constantly, so they stay in the Redis cache.
--   bc00001..bc05000  "cold" links: spread over so many codes that most requests miss the cache.
-- Re-runnable: existing rows are left alone. Remove with load-testing/cleanup.sql.
INSERT INTO urls (short_code, original_url)
SELECT 'bh' || lpad(i::text, 5, '0'), 'https://example.com/bench/hot/' || i
FROM generate_series(1, 200) AS i
ON CONFLICT (short_code) DO NOTHING;

INSERT INTO urls (short_code, original_url)
SELECT 'bc' || lpad(i::text, 5, '0'), 'https://example.com/bench/cold/' || i
FROM generate_series(1, 5000) AS i
ON CONFLICT (short_code) DO NOTHING;
