"""Phase 2 gate: unit-test each tool as a plain function, no LLM.

Run:  uv run python tests/test_tools.py
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.tools import (contribution_by_dimension, drilldown_filtered,
                          factor_decompose, metric_overview,
                          seasonality_check, verify_claim)

# data spans 2026-06-01 .. 2026-07-05; July 2 has all 4 baseline weeks
WS, WE = "2026-07-02T14:00", "2026-07-02T17:00"
failures = []


def check(name, result, *required_keys):
    data = json.loads(result)  # every tool must return valid JSON
    missing = [k for k in required_keys if k not in data]
    if "error" in data:
        failures.append(f"{name}: unexpected error {data['error']}")
    elif missing:
        failures.append(f"{name}: missing keys {missing}")
    else:
        print(f"  ok  {name}")
    return data


def expect_error(name, result):
    data = json.loads(result)
    if "error" in data:
        print(f"  ok  {name} -> error JSON, nothing executed")
    else:
        failures.append(f"{name}: should have returned an error")


t0 = time.time()
over = check("metric_overview", metric_overview.invoke(
    {"window_start": WS, "window_end": WE}), "metrics", "sql")
deco = check("factor_decompose", factor_decompose.invoke(
    {"window_start": WS, "window_end": WE}), "factors", "sql")
contrib = check("contribution_by_dimension", contribution_by_dimension.invoke(
    {"dimension": "ad_format", "window_start": WS, "window_end": WE}),
    "top", "sql")
drill = check("drilldown_filtered", drilldown_filtered.invoke(
    {"dimension": "app_id", "parent_dimension": "ad_format",
     "parent_value": contrib["top"][0]["segment"].split("=", 1)[1],
     "window_start": WS, "window_end": WE}), "top", "sql")
seas = check("seasonality_check", seasonality_check.invoke(
    {"window_start": WS, "window_end": WE}),
    "within_normal_range", "baseline_weeks", "sql")
ver = check("verify_claim", verify_claim.invoke(
    {"metric": "revenue", "window_start": WS, "window_end": WE,
     "dimension": "ad_format",
     "value": contrib["top"][0]["segment"].split("=", 1)[1]}),
    "observed", "baseline", "sql")
print(f"  6 tools in {time.time() - t0:.1f}s total")

# factor shares should roughly sum to 100%
shares = [f["share_of_revenue_change"] for f in deco["factors"].values()]
if all(s is not None for s in shares) and abs(sum(shares) - 100) > 2:
    failures.append(f"factor shares sum to {sum(shares)}, expected ~100")
else:
    print(f"  ok  factor shares sum to {sum(shares):.1f}%")

# injection surface: junk dimension must return error JSON, execute nothing
expect_error("injection dimension", contribution_by_dimension.invoke(
    {"dimension": "drop table", "window_start": WS, "window_end": WE}))
expect_error("injection metric", verify_claim.invoke(
    {"metric": "1;DROP TABLE ad_events", "window_start": WS, "window_end": WE}))

# consistency: verify_claim on ALL must reproduce metric_overview's revenue
all_rev = json.loads(verify_claim.invoke(
    {"metric": "revenue", "window_start": WS, "window_end": WE}))
if all_rev["observed"] != over["metrics"]["revenue"]["observed"]:
    failures.append("verify_claim(ALL) revenue != metric_overview revenue")
else:
    print("  ok  verify_claim reproduces metric_overview revenue")

if failures:
    print("\nFAILURES:")
    for f in failures:
        print("  -", f)
    raise SystemExit(1)
print("\nall Phase 2 tool tests passed")
