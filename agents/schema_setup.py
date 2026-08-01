"""One-off: build events_enriched over the real dimension tables.

Run once against a ClickHouse instance that already has ad_events, apps,
advertisers, geo_device loaded (the VM's local `hackathon` database — see
CONTEXT.md). Joins in the full 9-dimension plan so agents/tools.py's
DIMENSIONS can use region/country/device_model/os_version/app_category/
publisher_tier/vertical/campaign_type alongside the raw ad_format column.

Run:  uv run python -m agents.schema_setup
"""

from agents import db

VIEW = """
CREATE OR REPLACE VIEW events_enriched AS
SELECT
    e.event_time    AS event_time,
    e.app_id         AS app_id,
    e.geo_device_id  AS geo_device_id,
    e.advertiser_id  AS advertiser_id,
    e.ad_format      AS ad_format,
    e.is_filled      AS is_filled,
    e.is_impression  AS is_impression,
    e.is_click       AS is_click,
    e.revenue        AS revenue,
    g.region         AS region,
    g.country        AS country,
    g.device_model   AS device_model,
    g.os_version     AS os_version,
    a.category       AS app_category,
    a.publisher_tier AS publisher_tier,
    d.vertical       AS vertical,
    d.campaign_type  AS campaign_type
FROM ad_events e
LEFT JOIN geo_device g ON e.geo_device_id = g.geo_device_id
LEFT JOIN apps a ON e.app_id = a.app_id
LEFT JOIN advertisers d ON e.advertiser_id = d.advertiser_id
"""


def main() -> None:
    db.command(VIEW)
    row = db.q("SELECT count() AS n FROM events_enriched")[0]
    print(f"events_enriched ready: {row['n']:,} rows")
    sample = db.q(
        "SELECT region, country, device_model, os_version, app_category, "
        "publisher_tier, ad_format, vertical, campaign_type "
        "FROM events_enriched LIMIT 1"
    )
    print("sample row:", sample[0] if sample else "(empty)")


if __name__ == "__main__":
    main()
