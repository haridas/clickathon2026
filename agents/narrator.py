"""The Narrator pipeline (Part 3, second entry point) — one LLM call, no
tools, per the team's "Agents — InMobi Root-Cause Analyst" spec.

Part 2's adapter (~/inmobi_agent/diagnose.py on the VM) already IS the
Detector + Attributor: deterministic per-factor z-score screening, gated
drill-down by dimension, native ClickHouse Tukey/STL cross-validation. It
sends us its raw evidence dict verbatim (no reshaping on their side). Our
only job:

  1. build_findings() — pure Python, no LLM. Condenses the adapter's rich
     evidence into the spec's `findings` shape (found[]/ruled_out[]). Every
     number here is copied straight out of their computed evidence, so it's
     correct by construction — nothing here can hallucinate.
  2. narrate() — exactly one LLM call, given ONLY the `findings` JSON (no
     table access, no tool calls), producing a plain-language paragraph. A
     regex numeral guardrail checks every number the model wrote against
     `findings`; on a mismatch it retries once with the bad token(s) named,
     then degrades to low_confidence rather than silently shipping an
     unverifiable number.

No `create_agent`, no tool-calling loop, no RECURSION_LIMIT — structurally
immune to the occasional GraphRecursionError seen in pipeline.py.
"""

import os
import re
from datetime import datetime, timezone

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage

from agents.pipeline import _model
from agents.schemas import Findings, FoundSegment, NarratorRCA, RuledOutEntry

load_dotenv()

# A dimension's top segment must explain at least this much of the total
# deviation to count as a primary "found" driver rather than a correlated
# symptom (e.g. publisher_tier moving because it's downstream of the real
# os_version-concentrated incident, not an independent cause).
FOUND_THRESHOLD_PCT = 50.0


def _derive_alert_id(evidence: dict) -> str:
    """Same formula ~/inmobi_agent/alert_client.py::build_alert() already
    uses for the /alert webhook, so both rows for one incident correlate by
    alert_id without requiring any change on the adapter's side."""
    factor = evidence["flagged_factor"]
    w = evidence["window"]
    return f"inmobi-{factor}-{w['start']}-{w['end']}".replace(":", "")


def build_findings(evidence: dict) -> Findings:
    """Deterministic transform of the adapter's diagnose() evidence into
    the spec's `findings` shape. No LLM involved — every value here is
    copied verbatim from `evidence`."""
    metric = evidence["flagged_factor"]
    topline = evidence["topline"]
    actual = topline["actual"][metric]
    baseline = topline["baseline"][metric]
    deviation_pct = round(100 * (actual - baseline) / baseline, 2) if baseline else 0.0

    seq_by_factor = {e["factor"]: e for e in evidence["diagnostic_sequence"]}
    flagged_entry = seq_by_factor.get(metric, {})
    z = flagged_entry.get("z_score")
    trailing_weeks = evidence.get("trailing_weeks")
    detector = (
        f"trailing z-score vs weekday baseline (z={z:.2f}, "
        f"trailing {trailing_weeks}wk, threshold {evidence.get('z_threshold')})"
        if z is not None else
        f"trailing z-score vs weekday baseline (undefined variance, "
        f"trailing {trailing_weeks}wk)"
    )

    found: list[FoundSegment] = []
    ruled_out: list[RuledOutEntry] = []

    # Sibling factors in the diagnostic sequence that were checked and
    # didn't move — the top-line "what we ruled out" entries.
    for factor, entry in seq_by_factor.items():
        if factor == metric or entry.get("moved"):
            continue
        ez = entry.get("z_score")
        ruled_out.append(RuledOutEntry(
            dimension=factor,
            note=(f"z={ez:.2f}, no significant deviation" if ez is not None
                  else "no significant deviation (baseline variance ~0)"),
            value=ez,
        ))

    rr = evidence.get("render_rate_check")
    if rr and not rr.get("moved"):
        rz = rr.get("z_score")
        ruled_out.append(RuledOutEntry(
            dimension="render_rate",
            note=(f"z={rz:.2f}, stable — not a technical serving issue"
                  if rz is not None else "stable — not a technical serving issue"),
            value=rz,
        ))

    drill = evidence.get("drill_down")
    if drill:
        total_delta = drill.get("total_delta") or 0.0
        for dim, d in (drill.get("by_dimension") or {}).items():
            responsible = d.get("responsible") or []
            top = responsible[0] if responsible else None
            top_pct = (round(100 * top["contribution"] / total_delta, 1)
                       if top and total_delta else None)
            if top is not None and top_pct is not None and abs(top_pct) >= FOUND_THRESHOLD_PCT:
                found.append(FoundSegment(
                    dimension=dim, segment=top["segment"],
                    contribution_pct=top_pct,
                    actual=top["actual"], baseline=top["baseline"],
                ))
            elif top is not None:
                ruled_out.append(RuledOutEntry(
                    dimension=dim, segment=top["segment"],
                    note=(f"top segment explains only {top_pct:g}% of the "
                          f"deviation — not the dominant driver" if top_pct is not None
                          else "no dominant segment in this dimension"),
                    value=top_pct,
                ))
            for r in d.get("ruled_out") or []:
                r_pct = (round(100 * r["contribution"] / total_delta, 1)
                          if total_delta else None)
                ruled_out.append(RuledOutEntry(
                    dimension=dim, segment=r.get("segment"),
                    note=(f"contributes only {r_pct:g}% of the deviation"
                          if r_pct is not None else "negligible contribution"),
                    value=r_pct,
                ))

    corroboration = evidence.get("native_validation")
    if corroboration and corroboration.get("status") == "insufficient_history":
        corroboration = None

    return Findings(
        metric=metric,
        window_start=evidence["window"]["start"],
        window_end=evidence["window"]["end"],
        baseline=baseline, actual=actual, deviation_pct=deviation_pct,
        detector=detector, found=found, ruled_out=ruled_out,
        corroboration=corroboration,
    )


NARRATOR_SYSTEM_PROMPT = """You are writing a one-paragraph incident diagnosis for an ad-tech dashboard.

You will receive a JSON object called `findings`. Every number in your
response must appear verbatim in `findings` — do not calculate, round
differently, or infer a number that isn't already there. If a claim would
need a number you don't have, drop the claim instead of estimating it.

Structure: (1) what moved and by how much, (2) which segment(s) explain it,
citing their contribution, (3) what you checked and ruled out, from
findings.ruled_out. Keep it to 3-4 sentences."""

_NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*%?")


def _extract_numbers(text: str) -> list[str]:
    return _NUM_RE.findall(text)


def _narrative_guardrail(narrative: str, findings_json: str) -> list[str]:
    """Every number-looking token in the narrative must appear (in some
    reasonable formatting) somewhere in the findings JSON it was given.
    Returns the offending tokens."""
    bad = []
    for tok in _extract_numbers(narrative):
        core = tok.rstrip("%").replace(",", "")
        try:
            v = float(core)
        except ValueError:
            continue
        candidates = {f"{v:g}", f"{v:.1f}", f"{v:.2f}", f"{v:.4f}",
                      f"{v:,.1f}", f"{v:,.2f}", tok, core}
        if not any(c in findings_json for c in candidates):
            bad.append(tok)
    return bad


def narrate(findings: Findings, callbacks: list | None = None) -> tuple[str, bool]:
    """One LLM call, no tools. Returns (narrative, guardrail_failed) —
    guardrail_failed is True only if a number was still unverifiable after
    one retry."""
    findings_json = findings.model_dump_json()
    model = _model("FAST")
    config = {"callbacks": callbacks or [], "run_name": "narrator"}
    messages = [
        SystemMessage(content=NARRATOR_SYSTEM_PROMPT),
        HumanMessage(content=f"findings:\n{findings_json}"),
    ]
    resp = model.invoke(messages, config=config)
    narrative = resp.text if isinstance(resp.text, str) else str(resp.content)
    bad = _narrative_guardrail(narrative, findings_json)
    if bad:
        messages.append(resp)
        messages.append(HumanMessage(content=(
            f"Your previous answer used number(s) not found in findings: "
            f"{bad}. Rewrite the narrative using ONLY numbers that appear "
            f"verbatim in findings. Drop any claim you can't support."
        )))
        resp = model.invoke(messages, config=config)
        narrative = resp.text if isinstance(resp.text, str) else str(resp.content)
        bad = _narrative_guardrail(narrative, findings_json)
    return narrative, bool(bad)


def run_narrator(evidence: dict, alert_id: str | None = None) -> NarratorRCA:
    if evidence.get("flagged_factor") is None:
        raise ValueError("evidence.flagged_factor is None — nothing to summarize")

    alert_id = alert_id or _derive_alert_id(evidence)

    callbacks, trace_url, lf = [], "", None
    if os.getenv("LANGFUSE_PUBLIC_KEY"):
        from langfuse import get_client
        from langfuse.langchain import CallbackHandler
        lf = get_client()
        callbacks = [CallbackHandler()]

    def build() -> NarratorRCA:
        findings = build_findings(evidence)
        narrative, guardrail_failed = narrate(findings, callbacks=callbacks)
        corroborated = bool(findings.corroboration and findings.corroboration.get("corroborated"))
        confidence = "high" if corroborated else "medium"
        status = "ok"
        if guardrail_failed:
            confidence = "low"
            status = "low_confidence"
            narrative += (" [guardrail: some numbers could not be matched "
                          "to findings and should not be trusted]")
        return NarratorRCA(
            alert_id=alert_id, findings=findings, narrative=narrative,
            confidence=confidence, status=status,
        )

    try:
        if lf is not None:
            with lf.start_as_current_observation(
                    name=f"narrator {alert_id}", as_type="span") as root:
                rca = build()
                try:
                    trace_url = lf.get_trace_url(trace_id=lf.get_current_trace_id())
                except Exception:
                    trace_url = f"trace_id={lf.get_current_trace_id()}"
                root.set_trace_io(input=evidence, output=rca.model_dump(mode="json"))
            lf.flush()
        else:
            rca = build()
        rca.trace_url = trace_url
        return rca
    except Exception as e:  # noqa: BLE001 — an alert must always yield a row
        now = datetime.now(timezone.utc)
        w = evidence.get("window") or {}
        return NarratorRCA(
            alert_id=alert_id,
            findings=Findings(
                metric=evidence.get("flagged_factor") or "revenue",
                window_start=w.get("start", now),
                window_end=w.get("end", now),
                baseline=0.0, actual=0.0, deviation_pct=0.0,
                detector="error", found=[], ruled_out=[],
            ),
            narrative=f"Narrator pipeline failed: {type(e).__name__}: {e}",
            confidence="low", status="failed", trace_url=trace_url,
        )
