# Docker revenue anomaly detection

This service detects material, sustained global revenue anomalies, identifies the segments that best explain the gap, stores the evidence in ClickHouse, and emits confirmed incidents to self-hosted HyperDX as OpenTelemetry logs.

```text
ad_events -> metrics_hourly -> global revenue Prophet forecast
                                  |
                         confirmed incident?
                           | no        | yes
                           v           v
                       store forecast  forecast segments and rank contributors
                                               |
                                               v
                                      HyperDX anomaly_detected log
```

## Detection rules

For every completed UTC day, Prophet is trained on the preceding 21–35 complete days and forecasts the target day’s 24 hours. A global revenue incident requires all of these conditions:

- at least 21 complete training days;
- actual revenue outside Prophet's 99% prediction interval;
- a gap of at least 10% of forecast revenue;
- two or more consecutive anomalous hours in the same direction; and
- at least 250 requests per affected hour.

Example: expected revenue is 100,000, the lower prediction bound is 90,000, and actual revenue is 82,000 for three consecutive hours. This is a confirmed 18% revenue-drop incident. A one-hour drop is saved as a forecast result but does not alert.

After confirmation, the service forecasts one-level segments—region, country, device model, OS version, format, category, publisher tier, vertical, and campaign type. It ranks contributors within each dimension family using `segment residual / global residual`. These families overlap, so category, country, and OS shares must not be summed.

## Docker deployment on the HyperDX VM

The service is designed to run on the same Linux VM as HyperDX. The Compose service uses host networking, allowing it to send OTLP logs to HyperDX’s loopback-only receiver at `127.0.0.1:4318`; do not publish that port to the internet.

1. Place this repository on the VM and create the Docker-only runtime configuration:

   ```bash
   cd ~/clickathon2026
   cp anomaly.env.example anomaly.env
   nano anomaly.env
   ```

2. Set the ClickHouse connection values and these HyperDX settings in `anomaly.env`. Keep the file private.

   ```dotenv
   HYPERDX_API_KEY=your_ingestion_key
   OTEL_SERVICE_NAME=ad-anomaly-detector
   OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318
   OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
   ```

3. The `ad_events` data and its historical hourly rollups must already be available in ClickHouse. Backfill rollups once, with an exclusive end date:

   ```bash
   docker compose -f compose.anomaly.yml run --rm --no-deps anomaly-detector \
     python -m anomaly_detection.cli \
     --backfill-start 2026-06-01 --backfill-end 2026-07-06
   ```

4. Start the long-running revenue detector:

   ```bash
   docker compose -f compose.anomaly.yml up -d --build
   docker compose -f compose.anomaly.yml logs -f anomaly-detector
   ```

It polls hourly, materializes only an unprocessed finalized day, and therefore does not double-count a daily rollup. The container restarts automatically unless explicitly stopped.

## Verify before enabling alerts

Run one container execution and inspect its logs:

```bash
docker compose -f compose.anomaly.yml run --rm --no-deps anomaly-detector \
  python -m anomaly_detection.service --once --metric revenue
```

To score a known historical day without modifying data:

```bash
docker compose -f compose.anomaly.yml run --rm --no-deps anomaly-detector \
  python -m anomaly_detection.cli \
  --metric revenue --target-day 2026-07-05 --days 35 --dry-run
```

The ClickHouse output tables are:

- `metrics_hourly` — hourly raw totals by dimension;
- `metric_baselines_hourly` — actual, forecast, prediction interval, residual, and flag; and
- `revenue_attribution_hourly` — contributor shares for confirmed global revenue incidents.

## HyperDX alert configuration

Open the HyperDX UI through an SSH tunnel from your computer:

```powershell
ssh -L 8080:127.0.0.1:8080 <user>@8.231.126.50
```

Browse to `http://localhost:8080`, create an ingestion API key, and add it to the VM’s `anomaly.env` as `HYPERDX_API_KEY`.

Search for the structured alert event and save this filtered search:

```text
event:anomaly_detected AND metric:revenue AND dim_name:global
```

Create a HyperDX alert on that saved search with a count above `0`, checked every hour. Connect Slack, email, or PagerDuty. The filter is important: it generates one notification for the global incident while the `contributors` field holds the ranked segment RCA.

## Operational commands

```bash
# Status and logs
docker compose -f compose.anomaly.yml ps
docker compose -f compose.anomaly.yml logs -f anomaly-detector

# Stop without deleting ClickHouse data
docker compose -f compose.anomaly.yml down

# Rebuild after a code or dependency change
docker compose -f compose.anomaly.yml up -d --build
```

Do not commit `anomaly.env`, `.env`, or `data/`.
