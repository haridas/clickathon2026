"""The constrained tool belt (Phase 2).

Six @tool functions — the ONLY way agents touch ClickHouse. Rules:

- Agents never write SQL. They pick a template; values are bound
  server-side ({name:Type}); the dimension name is validated against an
  allowlist and substituted from OUR mapping, never from agent text.
- Every result is compact JSON: top-10 rows max. The executed SQL is
  captured for reproducibility (evidence[]/Langfuse trace) via LangChain's
  response_format="content_and_artifact" — attached to the ToolMessage
  separately, never resent as part of the LLM-facing text.
- Nothing raises — errors come back as {"error": ...} so the agent can
  recover.

Baseline everywhere: same hour-of-day × day-of-week — the identical
window shifted back 1..4 weeks, median across those 4 weeks.
"""

import functools
import json
import statistics
from typing import Optional

from langchain_core.tools import tool

from agents import db

TABLE = "ad_events"

# Dimensions the agent may group by, mapped to qualified column expressions
# against the aliases used in WINDOWED_JOIN below (e = ad_events, g =
# geo_device, a = apps, d = advertisers).
DIMENSIONS = {
    "region": "g.region",
    "country": "g.country",
    "device_model": "g.device_model",
    "os_version": "g.os_version",
    "app_category": "a.category",
    "publisher_tier": "a.publisher_tier",
    "ad_format": "e.ad_format",
    "vertical": "d.vertical",
    "campaign_type": "d.campaign_type",
}

METRICS = ["revenue", "requests", "fill_rate", "ctr", "ecpm"]

# Additive column used to rank/share segment contributions per metric.
PROXY = {
    "revenue": "revenue",
    "requests": "requests",
    "fill_rate": "filled",
    "ctr": "clicks",
    "ecpm": "revenue",
}

# ---------------------------------------------------------------- SQL --
# One constants block, judge-readable. k = weeks back (0 = the alert
# window itself, 1..4 = the like-for-like baseline weeks).
#
# WINDOWED_JOIN filters ad_events down to just the alert's window (+ the 4
# baseline weeks) *before* joining the dimension tables — the inner
# subquery forces that ordering regardless of the optimizer's own
# join-pushdown decisions, so every tool query touches a few thousand rows
# of ad_events, never all 9M, however deep in a 5-week table it lives.

_WINDOW_K = ("(event_time >= {{ws:DateTime}} - INTERVAL {k} WEEK "
             "AND event_time < {{we:DateTime}} - INTERVAL {k} WEEK)")
WINDOWS = " OR ".join(_WINDOW_K.format(k=k) for k in range(5))
K_EXPR = "multiIf(" + ", ".join(
    _WINDOW_K.format(k=k) + f", {k}" for k in range(4)) + ", 4)"

WINDOWED_JOIN = f"""
(SELECT * FROM {TABLE} WHERE ({WINDOWS})) e
LEFT JOIN geo_device g ON e.geo_device_id = g.geo_device_id
LEFT JOIN apps a ON e.app_id = a.app_id
LEFT JOIN advertisers d ON e.advertiser_id = d.advertiser_id
"""

SUMS_SQL = f"""
SELECT {K_EXPR} AS k,
       count()            AS requests,
       sum(is_filled)     AS filled,
       sum(is_impression) AS imps,
       sum(is_click)      AS clicks,
       sum(revenue)       AS revenue
FROM {WINDOWED_JOIN}
WHERE 1=1__EXTRA__
GROUP BY k ORDER BY k
"""

CONTRIB_SQL = f"""
SELECT segment,
       maxIf(requests, k = 0)          AS o_requests,
       quantileExactInclusiveIf(0.5)(requests, k > 0)  AS b_requests,
       maxIf(filled, k = 0)            AS o_filled,
       quantileExactInclusiveIf(0.5)(filled, k > 0)    AS b_filled,
       maxIf(imps, k = 0)              AS o_imps,
       quantileExactInclusiveIf(0.5)(imps, k > 0)      AS b_imps,
       maxIf(clicks, k = 0)            AS o_clicks,
       quantileExactInclusiveIf(0.5)(clicks, k > 0)    AS b_clicks,
       maxIf(revenue, k = 0)           AS o_revenue,
       quantileExactInclusiveIf(0.5)(revenue, k > 0)   AS b_revenue
FROM (
    SELECT __DIM__ AS segment, {K_EXPR} AS k,
           count()            AS requests,
           sum(is_filled)     AS filled,
           sum(is_impression) AS imps,
           sum(is_click)      AS clicks,
           sum(revenue)       AS revenue
    FROM {WINDOWED_JOIN}
    WHERE 1=1__EXTRA__
    GROUP BY segment, k
)
GROUP BY segment
ORDER BY abs(maxIf(__PROXY__, k = 0) - quantileExactInclusiveIf(0.5)(__PROXY__, k > 0)) DESC
LIMIT 10
"""

# ------------------------------------------------------------ helpers --


def _norm(ts: str) -> str:
    """'2026-06-18T14:00' -> '2026-06-18 14:00:00' (ClickHouse DateTime)."""
    ts = ts.strip().replace("T", " ")
    if len(ts) == 16:
        ts += ":00"
    return ts


def _metric(sums: dict) -> dict:
    """All 5 metric values from one row of raw sums."""
    req, filled = sums["requests"], sums["filled"]
    imps, clicks, rev = sums["imps"], sums["clicks"], sums["revenue"]
    return {
        "revenue": round(rev, 2),
        "requests": req,
        "fill_rate": round(filled / req, 4) if req else None,
        "ctr": round(clicks / imps, 4) if imps else None,
        "ecpm": round(1000 * rev / imps, 4) if imps else None,
    }


def _sums_by_k(ws: str, we: str, extra_sql: str = "",
               extra_params: dict | None = None) -> tuple[dict, str]:
    """Raw sums per week-offset k. Returns ({k: row}, executed_sql)."""
    sql = SUMS_SQL.replace("__EXTRA__", extra_sql)
    params = {"ws": _norm(ws), "we": _norm(we), **(extra_params or {})}
    rows = db.q(sql, params)
    return {r["k"]: r for r in rows}, sql.strip()


def _ok(payload: dict, sql: str, params: dict) -> tuple[str, dict]:
    """Returns (compact_content, full_artifact). The agent only ever reads
    `compact_content` — no sql/sql_params — since that text gets resent in
    full on every subsequent turn of the agent's own tool-calling loop
    (standard chat-API behavior) and the query text itself does nothing
    for the model's reasoning. `full_artifact` (with sql/sql_params) is
    attached to the ToolMessage separately via LangChain's
    response_format="content_and_artifact" — it's what lands in `evidence`
    for guardrail matching and reproducibility, and it's what Langfuse
    traces, without ever being paid for as LLM input tokens."""
    compact = json.dumps(payload, default=str)
    full = {**payload, "sql": sql,
            "sql_params": {k: str(v) for k, v in params.items()}}
    return compact, full


def _err(msg: str) -> tuple[str, dict]:
    err = {"error": msg}
    return json.dumps(err), err


def _guard(fn):
    """Tools never raise — the agent gets {"error": ...} and can recover."""
    @functools.wraps(fn)
    def wrapped(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as e:  # noqa: BLE001 — deliberate catch-all
            return _err(f"{type(e).__name__}: {e}")
    return wrapped


# -------------------------------------------------------------- tools --


@tool(response_format="content_and_artifact")
@_guard
def metric_overview(window_start: str, window_end: str) -> tuple[str, dict]:
    """Compare ALL five metrics (revenue, requests, fill_rate, ctr, ecpm)
    in the window against the like-for-like baseline (same weekday+hours,
    trailing 4 weeks, median). Returns pct deviation and z-score per
    metric. Call this FIRST to confirm the alert and spot sibling metrics
    moving together. Timestamps like '2026-06-18T14:00'."""
    by_k, sql = _sums_by_k(window_start, window_end)
    if 0 not in by_k:
        return _err("no events in the window — check the timestamps")
    obs = _metric(by_k[0])
    weeks = {k: _metric(v) for k, v in by_k.items() if k > 0}
    out = {}
    for m in METRICS:
        vals = [w[m] for w in weeks.values() if w[m] is not None]
        if not vals or obs[m] is None:
            out[m] = {"observed": obs[m], "baseline": None}
            continue
        base = statistics.median(vals)
        std = statistics.stdev(vals) if len(vals) > 1 else 0.0
        out[m] = {
            "observed": obs[m],
            "baseline": round(base, 4),
            "pct_change": round(100 * (obs[m] - base) / base, 2) if base else None,
            "z": round((obs[m] - base) / std, 2) if std else None,
            "baseline_weeks": vals,
        }
    return _ok({"window": [window_start, window_end], "metrics": out},
               sql, {"ws": window_start, "we": window_end})


@tool(response_format="content_and_artifact")
@_guard
def factor_decompose(window_start: str, window_end: str) -> tuple[str, dict]:
    """Split a revenue change into its identity factors:
    revenue = requests × fill_rate × render_rate × ecpm/1000.
    Log-ratio decomposition vs the baseline — each factor's share of the
    revenue change sums to ~100%. Use this to pick the guilty factor
    BEFORE drilling into dimensions."""
    import math
    by_k, sql = _sums_by_k(window_start, window_end)
    if 0 not in by_k or len(by_k) < 2:
        return _err("need the window plus at least one baseline week")
    o = by_k[0]
    base = {c: statistics.median([by_k[k][c] for k in by_k if k > 0])
            for c in ("requests", "filled", "imps", "clicks", "revenue")}
    factors = {
        "requests": (o["requests"], base["requests"]),
        "fill_rate": (o["filled"] / o["requests"], base["filled"] / base["requests"]),
        "render_rate": (o["imps"] / o["filled"], base["imps"] / base["filled"]),
        "ecpm": (1000 * o["revenue"] / o["imps"], 1000 * base["revenue"] / base["imps"]),
    }
    dlog_rev = math.log(o["revenue"] / base["revenue"])
    out = {}
    for name, (ov, bv) in factors.items():
        dlog = math.log(ov / bv)
        out[name] = {
            "observed": round(ov, 4), "baseline": round(bv, 4),
            "pct_change": round(100 * (ov / bv - 1), 2),
            "share_of_revenue_change": round(100 * dlog / dlog_rev, 1)
            if dlog_rev else None,
        }
    payload = {
        "revenue": {"observed": round(o["revenue"], 2),
                    "baseline": round(base["revenue"], 2),
                    "pct_change": round(100 * (o["revenue"] / base["revenue"] - 1), 2)},
        "factors": out,
    }
    return _ok(payload, sql, {"ws": window_start, "we": window_end})


def _contribution(dimension: str, window_start: str, window_end: str,
                  metric: str, extra_sql: str = "",
                  extra_params: dict | None = None) -> tuple[str, dict]:
    if dimension not in DIMENSIONS:
        return _err(f"unknown dimension {dimension!r}, use one of "
                    f"{sorted(DIMENSIONS)}")
    if metric not in METRICS:
        return _err(f"unknown metric {metric!r}, use one of {METRICS}")
    proxy = PROXY[metric]
    sql = (CONTRIB_SQL.replace("__DIM__", DIMENSIONS[dimension])
           .replace("__PROXY__", proxy).replace("__EXTRA__", extra_sql))
    params = {"ws": _norm(window_start), "we": _norm(window_end),
              **(extra_params or {})}
    rows = db.q(sql, params)
    total_delta = sum(r[f"o_{proxy}"] - r[f"b_{proxy}"] for r in rows)
    top = []
    for r in rows:
        o = _metric({"requests": r["o_requests"], "filled": r["o_filled"],
                     "imps": r["o_imps"], "clicks": r["o_clicks"],
                     "revenue": r["o_revenue"]})
        b = _metric({"requests": r["b_requests"], "filled": r["b_filled"],
                     "imps": r["b_imps"], "clicks": r["b_clicks"],
                     "revenue": r["b_revenue"]})
        delta = r[f"o_{proxy}"] - r[f"b_{proxy}"]
        top.append({
            "segment": f"{dimension}={r['segment']}",
            "observed": o[metric], "baseline": b[metric],
            "delta_" + proxy: round(delta, 2),
            "share_of_total_delta_pct":
                round(100 * delta / total_delta, 1) if total_delta else None,
        })
    return _ok({"dimension": dimension, "metric": metric,
                "ranking_by": f"abs delta of {proxy}", "top": top},
               sql, params)


@tool(response_format="content_and_artifact")
@_guard
def contribution_by_dimension(dimension: str, window_start: str,
                              window_end: str, metric: str = "revenue") -> tuple[str, dict]:
    """Rank segments of ONE dimension by contribution to the metric's
    change vs baseline. Call factor_decompose FIRST to know which metric
    to pass. dimension: one of region, country, device_model, os_version,
    app_category, publisher_tier, ad_format, vertical, campaign_type.
    Top 10 segments, ranked by absolute delta."""
    return _contribution(dimension, window_start, window_end, metric)


@tool(response_format="content_and_artifact")
@_guard
def drilldown_filtered(dimension: str, parent_dimension: str,
                       parent_value: str, window_start: str,
                       window_end: str, metric: str = "revenue") -> tuple[str, dict]:
    """Second-level drill-down: contribution_by_dimension for `dimension`
    but only inside rows where parent_dimension = parent_value (e.g. find
    which app drives the drop inside ad_format=video). Use after
    contribution_by_dimension names a top segment."""
    if parent_dimension not in DIMENSIONS:
        return _err(f"unknown parent_dimension {parent_dimension!r}, use "
                    f"one of {sorted(DIMENSIONS)}")
    extra = f" AND {DIMENSIONS[parent_dimension]} = {{pv:String}}"
    return _contribution(dimension, window_start, window_end, metric,
                         extra_sql=extra, extra_params={"pv": parent_value})


@tool(response_format="content_and_artifact")
@_guard
def seasonality_check(window_start: str, window_end: str,
                      metric: str = "revenue") -> tuple[str, dict]:
    """Is the observed value just seasonality? Shows the metric for the
    SAME weekday+hours in each of the trailing 4 weeks individually, and
    whether the observed value sits inside their min–max range. If it
    does, the 'anomaly' is likely normal weekly variation — rule it out."""
    if metric not in METRICS:
        return _err(f"unknown metric {metric!r}, use one of {METRICS}")
    by_k, sql = _sums_by_k(window_start, window_end)
    if 0 not in by_k:
        return _err("no events in the window — check the timestamps")
    obs = _metric(by_k[0])[metric]
    weeks = {f"{k}w_ago": _metric(by_k[k])[metric]
             for k in sorted(by_k) if k > 0}
    vals = [v for v in weeks.values() if v is not None]
    lo, hi = min(vals), max(vals)
    med = statistics.median(vals)
    return _ok({
        "metric": metric, "observed": obs, "baseline_weeks": weeks,
        "baseline_range": [lo, hi], "baseline_median": med,
        "within_normal_range": lo <= obs <= hi,
        "pct_vs_median": round(100 * (obs - med) / med, 2) if med else None,
    }, sql, {"ws": window_start, "we": window_end})


@tool(response_format="content_and_artifact")
@_guard
def verify_claim(metric: str, window_start: str, window_end: str,
                 dimension: Optional[str] = None,
                 value: Optional[str] = None) -> tuple[str, dict]:
    """Recompute ONE metric for ONE exact segment and window, vs its
    baseline. Use to re-verify a specific claim (e.g. metric='fill_rate',
    dimension='ad_format', value='video'). Omit dimension/value to check
    the whole window."""
    if metric not in METRICS:
        return _err(f"unknown metric {metric!r}, use one of {METRICS}")
    extra, extra_params = "", {}
    if dimension is not None:
        if dimension not in DIMENSIONS:
            return _err(f"unknown dimension {dimension!r}, use one of "
                        f"{sorted(DIMENSIONS)}")
        if value is None:
            return _err("value is required when dimension is given")
        extra = f" AND {DIMENSIONS[dimension]} = {{v:String}}"
        extra_params = {"v": value}
    by_k, sql = _sums_by_k(window_start, window_end, extra, extra_params)
    if 0 not in by_k:
        return _err("no events match that segment+window")
    obs = _metric(by_k[0])[metric]
    vals = [_metric(by_k[k])[metric] for k in by_k if k > 0]
    vals = [v for v in vals if v is not None]
    base = statistics.median(vals) if vals else None
    return _ok({
        "metric": metric,
        "segment": f"{dimension}={value}" if dimension else "ALL",
        "observed": obs, "baseline": base,
        "pct_change": round(100 * (obs - base) / base, 2)
        if base else None,
    }, sql, {"ws": window_start, "we": window_end, **extra_params})


ALL_TOOLS = [metric_overview, factor_decompose, contribution_by_dimension,
             drilldown_filtered, seasonality_check, verify_claim]
