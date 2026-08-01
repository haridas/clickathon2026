"""Fetch aggregate Langfuse v4 token and cost usage via the Metrics API."""

import argparse
import base64
from datetime import datetime, timedelta, timezone
import json
import os
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(description="Report Langfuse token usage by model.")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    if args.days < 1:
        raise ValueError("--days must be positive.")
    host, public, secret = (os.getenv("LANGFUSE_HOST"), os.getenv("LANGFUSE_PUBLIC_KEY"), os.getenv("LANGFUSE_SECRET_KEY"))
    if not all((host, public, secret)):
        raise ValueError("LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, and LANGFUSE_SECRET_KEY are required in .env.")
    now = datetime.now(timezone.utc)
    query = {"view": "observations", "metrics": [
        {"measure": "inputUsage", "aggregation": "sum"},
        {"measure": "outputUsage", "aggregation": "sum"},
        {"measure": "totalUsage", "aggregation": "sum"},
        {"measure": "totalCost", "aggregation": "sum"},
    ], "dimensions": [{"field": "providedModelName"}], "filters": [],
        "fromTimestamp": (now - timedelta(days=args.days)).isoformat(), "toTimestamp": now.isoformat(),
        "orderBy": [{"field": "sum_totalUsage", "direction": "desc"}], "config": {"row_limit": 1000}}
    token = base64.b64encode(f"{public}:{secret}".encode()).decode()
    request = Request(f"{host.rstrip('/')}/api/public/v2/metrics?{urlencode({'query': json.dumps(query)})}", headers={"Authorization": f"Basic {token}"})
    try:
        with urlopen(request, timeout=30) as response:
            print(json.dumps(json.load(response), indent=2))
    except HTTPError as error:
        if error.code == 401:
            raise SystemExit("Langfuse authentication failed. Create/copy a public and secret API key from the local Langfuse project at LANGFUSE_HOST, then update .env.") from error
        raise


if __name__ == "__main__":
    main()
