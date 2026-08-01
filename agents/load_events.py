"""One-off loader: ad_events.parquet -> ClickHouse `ad_events` table.

Fallback for the P1 pipeline (see CONTEXT.md): loads the raw 9M-row event
file so Phase 2 tools have data. Inserts in 200k-row slices with retries
(1M-row bodies time out on this uplink) and resumes: re-running skips rows
already in the table.

Run:  uv run --with pyarrow python -m agents.load_events
"""

import time

import pyarrow.parquet as pq

from agents import db

PARQUET = "click-a-thon-2026/InMobi/data/ad_events.parquet"

DDL = """
CREATE TABLE ad_events (
    event_time    DateTime64(3),
    app_id        String,
    geo_device_id String,
    advertiser_id String,
    ad_format     LowCardinality(String),
    is_filled     UInt8,
    is_impression UInt8,
    is_click      UInt8,
    revenue       Float64
) ENGINE = MergeTree ORDER BY event_time
"""

# View the tools query. P1 teammate can later replace it with a real
# enriched table (dims joined in) without any tool code changing.
VIEW = "CREATE OR REPLACE VIEW events_enriched AS SELECT * FROM ad_events"


BATCH = 200_000
RETRIES = 4


def _insert(tbl) -> None:
    for attempt in range(RETRIES):
        try:
            db.client().insert_arrow("ad_events", tbl)
            return
        except Exception as e:
            if attempt == RETRIES - 1:
                raise
            print(f"  retry {attempt + 1} after {type(e).__name__}", flush=True)
            time.sleep(2 * (attempt + 1))


def main() -> None:
    if not db.q("SELECT count() AS n FROM system.tables "
                "WHERE database = currentDatabase() AND name = 'ad_events'")[0]["n"]:
        db.command(DDL)
    done = db.q("SELECT count() AS n FROM ad_events")[0]["n"]
    print(f"resuming: {done:,} rows already loaded", flush=True)
    f = pq.ParquetFile(PARQUET)
    total = 0
    t0 = time.time()
    for i in range(f.num_row_groups):
        group = f.read_row_group(i)
        if total + group.num_rows <= done:
            total += group.num_rows
            continue
        for lo in range(0, group.num_rows, BATCH):
            n = min(BATCH, group.num_rows - lo)
            if total + n <= done:
                total += n
                continue
            _insert(group.slice(lo, n))
            total += n
            print(f"group {i + 1}/{f.num_row_groups}: {total:,} rows "
                  f"({time.time() - t0:.0f}s)", flush=True)
    db.command(VIEW)
    n = db.q("SELECT count() AS n, min(event_time) AS lo, max(event_time) AS hi "
             "FROM events_enriched")[0]
    print(f"done: {n['n']:,} rows, {n['lo']} .. {n['hi']}")


if __name__ == "__main__":
    main()
