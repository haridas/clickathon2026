"""Dev/demo entrypoint: build an Alert by hand and run the pipeline.

Usage:
  uv run python -m agents.cli investigate \
      --metric revenue --from 2026-06-18T14:00 --to 2026-06-18T17:00 \
      [--direction drop --observed 41200 --baseline 47000]
"""

import argparse
import json
from datetime import datetime, timezone
from uuid import uuid4

from agents.outputs import deliver
from agents.pipeline import run_rca
from agents.schemas import Alert


def parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def main() -> None:
    p = argparse.ArgumentParser(prog="agents.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    inv = sub.add_parser("investigate", help="run the RCA pipeline on a window")
    inv.add_argument("--metric", required=True,
                     choices=["revenue", "fill_rate", "requests", "ctr", "ecpm"])
    inv.add_argument("--from", dest="t_from", required=True)
    inv.add_argument("--to", dest="t_to", required=True)
    inv.add_argument("--direction", choices=["drop", "spike"], default="drop")
    inv.add_argument("--observed", type=float, default=0.0)
    inv.add_argument("--baseline", type=float, default=0.0)
    inv.add_argument("--detection-method", dest="detection_method", default=None,
                     help="e.g. seriesDecomposeSTL_residual, tukey, trailing_zscore "
                          "(simulates upstream detection; omit for the dev "
                          "significance-screen fallback)")
    inv.add_argument("--detection-score", dest="detection_score", type=float,
                     default=None)
    inv.add_argument("--detection-params", dest="detection_params", default=None,
                     help='JSON string, e.g. \'{"period": 7}\'')
    args = p.parse_args()

    alert = Alert(
        alert_id=f"cli_{uuid4().hex[:8]}",
        metric=args.metric,
        window_start=parse_dt(args.t_from),
        window_end=parse_dt(args.t_to),
        direction=args.direction,
        observed=args.observed,
        baseline=args.baseline,
        source="cli",
        detection_method=args.detection_method,
        detection_score=args.detection_score,
        detection_params=(json.loads(args.detection_params)
                          if args.detection_params else None),
    )
    rca = run_rca(alert)
    deliver(rca)
    print(json.dumps(rca.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    main()
