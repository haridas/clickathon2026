"""Execute the same candidate query used by a HyperDX anomaly alert."""

import argparse
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clickhouse_client import ClickHouseClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview gated ClickHouse anomaly alerts.")
    parser.add_argument("--day", type=date.fromisoformat, required=True, help="UTC day, YYYY-MM-DD")
    args = parser.parse_args()
    query = Path(__file__).with_name("anomaly_alert.sql").read_text(encoding="utf-8").strip().rstrip(";")
    candidates = ClickHouseClient().client.query_df(query, parameters={"target": args.day})
    print(candidates.to_string(index=False) if not candidates.empty else "No alert candidates.")


if __name__ == "__main__":
    main()
