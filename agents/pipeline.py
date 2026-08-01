"""run_rca(alert) -> RCA — the part-3 core (Phase 3: the four-agent pipeline).

Plain Python sequence of four agents sharing an evidence list:

  1. Triage       FAST   metric_overview             ack upstream detection, siblings
  2. Investigator STRONG decompose/contribution/drill names factor + segment(s)
  3. Skeptic      STRONG seasonality/verify_claim     re-verifies, builds checks ledger
  4. Writer       FAST   no tools, structured output  narrative from evidence only

Every tool output is appended to `evidence` with an id (t1, t2, ...); the
Writer may only cite those ids, so every number in the RCA is a ClickHouse
result by construction. One Langfuse trace spans the whole run.

Detection lives upstream now (part-2 alert generation runs deterministic
ClickHouse-native functions — seriesDecomposeSTL residual, Tukey fences,
trailing z-score — and only fires an alert when one of them flags a real
deviation). When `alert.detection_method` is set, this pipeline trusts that
call outright and always runs the full investigation — see `screen()`.

Cost control (fallback path only): for alerts WITHOUT upstream detection
metadata (CLI/dev alerts today), `_screen()` calls metric_overview directly
(plain Python, zero tokens) and checks the alerted metric's z-score. If
it's within normal statistical noise (|z| < RCA_SIGNIFICANCE_Z, default
2.0), the pipeline returns immediately with a short "no investigation
needed" RCA — skipping Triage, Investigator, Skeptic, and Writer entirely.

No `temperature` anywhere — current Claude models reject the parameter.
"""

import json
import os
from typing import Literal

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.messages import SystemMessage, ToolMessage
from pydantic import BaseModel, Field

from agents import tools as T
from agents.schemas import RCA, Alert, Check, Claim

load_dotenv()

RECURSION_LIMIT = 15  # ~7 tool calls + model turns per stage

# Below this |z|, the alerted metric is within normal statistical noise vs
# its 4-week baseline — not worth spending LLM tokens investigating.
# Tunable per event without a code change.
SIGNIFICANCE_Z = float(os.getenv("RCA_SIGNIFICANCE_Z", "2.0"))


def _model(kind: str):
    default = {"STRONG": "anthropic:claude-sonnet-5",
               "FAST": "anthropic:claude-haiku-4-5"}[kind]
    return init_chat_model(os.getenv(f"RCA_MODEL_{kind}", default))


def _fmt(dt) -> str:
    return dt.replace(tzinfo=None).isoformat(timespec="minutes")


def _harvest(result, evidence: list[dict]) -> str:
    """Append this stage's tool outputs to the shared evidence list and
    return the agent's final text answer.

    Tools return (content, artifact) via response_format="content_and_
    artifact": `content` is the compact string the LLM actually reads (and
    which gets resent on every subsequent turn of the loop); `artifact`
    carries the full payload including sql/sql_params. We store the full
    artifact in `evidence` — that's what the guardrail matches claims
    against and what's available for reproducibility — without it ever
    having been paid for as LLM input tokens."""
    tool_calls = {}  # tool_call_id -> (name, args)
    for m in result["messages"]:
        for tc in getattr(m, "tool_calls", None) or []:
            tool_calls[tc["id"]] = (tc["name"], tc["args"])
        if isinstance(m, ToolMessage):
            name, args = tool_calls.get(m.tool_call_id, (m.name, {}))
            artifact = getattr(m, "artifact", None)
            output = (json.dumps(artifact, default=str) if isinstance(artifact, dict)
                      else m.content if isinstance(m.content, str) else str(m.content))
            evidence.append({"id": f"t{len(evidence) + 1}", "tool": name,
                             "args": args, "output": output})
    final = result["messages"][-1]
    return final.text if isinstance(final.text, str) else str(final.content)


def _evidence_block(evidence: list[dict], max_chars: int = 1800) -> str:
    """Cross-stage handoff text (Investigator -> Skeptic -> Writer). Drops
    sql/sql_params — later stages need the numbers, never the query text —
    keeping this handoff compact even though `evidence[]` itself (used by
    the guardrail) still holds the full artifact."""
    parts = []
    for e in evidence:
        out = e["output"]
        try:
            d = json.loads(out)
            d.pop("sql", None)
            d.pop("sql_params", None)
            out = json.dumps(d, default=str)
        except (json.JSONDecodeError, TypeError):
            pass
        if len(out) > max_chars:
            out = out[:max_chars] + "...[truncated]"
        parts.append(f'[{e["id"]}] {e["tool"]}({json.dumps(e["args"])}) -> {out}')
    return "\n".join(parts)


def _guardrail(out: "WriterOutput", evidence: list[dict]) -> list[str]:
    """Numeral guardrail: every claim's value must literally appear in the
    evidence entry it cites. Returns the texts of claims that fail.
    Checks with tool_call_id=None (e.g. the upstream-detection entry, which
    is alert-sourced, not an LLM claim over tool evidence) are skipped."""
    by_id = {e["id"]: e["output"] for e in evidence}
    bad = []
    numbered = [*out.claims,
                *[c for c in out.checks if c.value is not None and c.tool_call_id]]
    for c in numbered:
        c.tool_call_id = c.tool_call_id.strip("[]")
        src = by_id.get(c.tool_call_id, "")
        v = abs(c.value)
        candidates = {f"{v:g}", f"{v:.1f}", f"{v:.2f}", f"{v:.4f}",
                      f"{v:,.1f}", f"{v:,.2f}"}
        text = c.text if isinstance(c, Claim) else c.result
        if not any(s in src for s in candidates):
            bad.append(text)
    return bad


class WriterOutput(BaseModel):
    """What the Writer must produce; merged into the full RCA by run_rca."""
    factor: Literal["requests", "fill_rate", "render_rate", "ecpm", "unknown"]
    segments: list[str] = Field(description='e.g. ["ad_format=video"]')
    narrative: str = Field(description="<=150 words, plain language")
    claims: list[Claim]
    checks: list[Check]
    confidence: Literal["high", "medium", "low"]


TRIAGE_PROMPT = """You are the Triage stage of an ad-metrics RCA pipeline.
If the alert names an upstream detection method (e.g. seriesDecomposeSTL
residual, Tukey fences, trailing z-score) and score, that deviation is
ALREADY CONFIRMED — a deterministic function flagged it before this pipeline
ran. Do not re-litigate whether it's real. Your job: call metric_overview
ONCE on the alert window, then summarize in <=100 words: (1) restate what
upstream found (method + score) alongside our own observed/baseline/pct/z
for the same metric, (2) which sibling metrics moved with it (context for
the Investigator), (3) anything metric_overview shows that's inconsistent
with the upstream call, if anything. Use only numbers from the tool output
or literally present in the alert."""

INVESTIGATOR_PROMPT = """You are the Investigator stage of an ad-metrics RCA
pipeline. Find WHY the metric moved. Method, in order:
1. factor_decompose on the window -> which factor of
   revenue = requests x fill_rate x render_rate x ecpm/1000 moved most.
2. contribution_by_dimension for the guilty factor's metric across the
   available dimensions (ad_format, app_id, advertiser_id, geo_device_id)
   -> which segment(s) drive the change.
3. If one segment dominates, drilldown_filtered inside it for a second level.
Use AT MOST 8 tool calls, but stop as soon as you're confident — if the
factor and segment are already clear after 2-3 calls, conclude immediately
rather than using the full budget just because it's available. Finish with
a <=120-word conclusion naming the factor and the guilty segment(s) with
their numbers. Every number must come from a tool output — never estimate."""

SKEPTIC_PROMPT = """You are the Skeptic stage of an ad-metrics RCA pipeline.
You receive the Investigator's conclusion and the evidence so far. Your job:
1. seasonality_check on the alerted metric — is the 'anomaly' inside the
   normal weekly range? If yes, the RCA should say so and confidence drops.
2. verify_claim to independently recompute the 1-3 most load-bearing numbers
   in the Investigator's conclusion (exact segment + window).
Finish with: VERDICT (confirmed / partly confirmed / seasonality / refuted),
then a per-check ledger — for EACH check you ran (seasonality, each
verify_claim call, and any dimension the Investigator explored but that
turned out not to matter) state: what was checked, the verdict (confirmed /
ruled_out / inconclusive), the plain-language result, and the number(s)
backing it. This ledger is what the final report shows as "what we checked
and ruled out" — be complete, not just the misses. End with a confidence
grade high/medium/low with one-line reasoning."""

WRITER_PROMPT = """You are the Writer stage of an ad-metrics RCA pipeline.
You get the alert, the three stage conclusions, and the full evidence list.
Write the final RCA. Rules:
- narrative: <=150 words, plain language a PM can read.
- claims: the numbers backing the guilty factor/segment. Each: text with
  the number, value = that number, tool_call_id = the evidence id
  ([t1], [t2], ...) it came from. Only cite numbers that literally appear
  in the evidence — never invent or derive.
- checks: the compact ledger of everything examined — from Triage's sibling
  metrics, the Investigator's ruled-out dimensions, and the Skeptic's
  seasonality/verify_claim re-checks. Each entry: check (what was
  examined), verdict (confirmed / ruled_out / inconclusive), result
  (plain-language finding), value + tool_call_id when a number backs it
  (omit both if the check is qualitative). Include confirmed checks too,
  not just ruled-out ones — this is the "what we looked at" record, not
  just a miss list.
- factor: the guilty factor per the Investigator (or "unknown").
- segments: the guilty segment(s) like "ad_format=video" (empty if none).
- confidence: follow the Skeptic's grade.
- If the Skeptic found the move is seasonality, say so plainly in the
  narrative and put the seasonality numbers in checks with verdict
  "ruled_out" (meaning: ruled out as a real anomaly)."""


def run_rca(alert: Alert) -> RCA:
    ws, we = _fmt(alert.window_start), _fmt(alert.window_end)
    alert_text = (
        f"ALERT {alert.alert_id}: {alert.metric} {alert.direction}, "
        f"window {ws} -> {we}, observed={alert.observed:g}, "
        f"baseline={alert.baseline:g}, source={alert.source}"
    )
    detection_check: Check | None = None
    if alert.detection_method:
        alert_text += (
            f"\nUpstream detection: method={alert.detection_method}, "
            f"score={alert.detection_score:g}"
            if alert.detection_score is not None else
            f"\nUpstream detection: method={alert.detection_method}"
        )
        if alert.detection_params:
            alert_text += f", params={alert.detection_params}"
        detection_check = Check(
            check=f"upstream detection: {alert.detection_method}",
            verdict="confirmed",
            result=(
                f"{alert.metric} flagged by {alert.detection_method} "
                f"(score={alert.detection_score:g})" if alert.detection_score is not None
                else f"{alert.metric} flagged by {alert.detection_method}"
            ),
            value=alert.detection_score,
            tool_call_id=None,
        )

    callbacks, trace_url, lf = [], "", None
    if os.getenv("LANGFUSE_PUBLIC_KEY"):
        from langfuse import get_client
        from langfuse.langchain import CallbackHandler
        lf = get_client()
        callbacks = [CallbackHandler()]

    evidence: list[dict] = []

    def screen() -> RCA | None:
        """Zero-LLM-token gate: pull metric_overview directly (plain
        Python, no agent) and check whether the alerted metric's z-score
        even clears the significance bar. If not, return a short RCA
        immediately instead of spending tokens on Triage/Investigator/
        Skeptic/Writer. Returns None to proceed with the full pipeline.

        Skipped entirely when the alert already carries upstream detection
        metadata (alert.detection_method) — a deterministic ClickHouse
        function already decided this is a real anomaly before firing the
        alert, so re-deriving significance here would just be redundant
        work. This gate only runs as a fallback for alerts without that
        info (CLI/dev alerts today)."""
        if alert.detection_method:
            return None
        raw = T.metric_overview.invoke({"window_start": ws, "window_end": we})
        data = json.loads(raw)
        evidence.append({"id": f"t{len(evidence) + 1}", "tool": "metric_overview",
                         "args": {"window_start": ws, "window_end": we},
                         "output": raw})
        m = data.get("metrics", {}).get(alert.metric)
        z = m.get("z") if m else None
        if m is None or z is None or abs(z) >= SIGNIFICANCE_Z:
            return None  # ambiguous or genuinely significant — investigate fully
        eid = evidence[-1]["id"]
        return RCA(
            alert_id=alert.alert_id, metric=alert.metric,
            window_start=alert.window_start, window_end=alert.window_end,
            factor="unknown", segments=[],
            narrative=(
                f"No investigation needed: {alert.metric} is within normal "
                f"statistical variation vs its 4-week baseline (z={z:g}, "
                f"threshold ±{SIGNIFICANCE_Z:g}). Observed {m['observed']:g} "
                f"vs baseline {m['baseline']:g} "
                f"({m.get('pct_change', 0) or 0:+g}%). Skipped the deep "
                f"investigation to avoid unnecessary cost on what looks "
                f"like noise, not a real anomaly."
            ),
            claims=[Claim(
                text=(f"{alert.metric} observed {m['observed']:g} vs "
                      f"baseline {m['baseline']:g}, z={z:g}"),
                value=z, tool_call_id=eid,
            )],
            checks=[Check(
                check="significance screen vs 4-week baseline",
                verdict="ruled_out",
                result=f"|z|={abs(z):g} below significance threshold {SIGNIFICANCE_Z:g}",
                value=z, tool_call_id=eid,
            )],
            confidence="high",
        )

    def stage(name: str, model_kind: str, tools: list, prompt: str, task: str) -> str:
        # cache_control on the system prompt: within one stage's own
        # multi-turn tool loop, the (system + tool schemas) prefix repeats
        # unchanged on every turn — caching lets Anthropic serve those
        # repeats at ~10% of input price instead of full price. Below the
        # per-model minimum (512-4096 tokens depending on model) this is a
        # silent no-op, not an error — see CONTEXT.md for what we measured.
        sys_msg = SystemMessage(content=[
            {"type": "text", "text": prompt,
             "cache_control": {"type": "ephemeral"}},
        ])
        agent = create_agent(_model(model_kind), tools=tools,
                             system_prompt=sys_msg, name=name)
        result = agent.invoke(
            {"messages": [{"role": "user", "content": task}]},
            {"callbacks": callbacks, "recursion_limit": RECURSION_LIMIT,
             "run_name": name},
        )
        return _harvest(result, evidence)

    def investigate() -> RCA:
        screened = screen()
        if screened is not None:
            return screened
        triage = stage("triage", "FAST", [T.metric_overview], TRIAGE_PROMPT,
                       alert_text)
        invest = stage(
            "investigator", "STRONG",
            [T.factor_decompose, T.contribution_by_dimension, T.drilldown_filtered],
            INVESTIGATOR_PROMPT,
            f"{alert_text}\n\nTriage said:\n{triage}",
        )
        # Skeptic's job is mechanical re-verification (rerun two tools,
        # compare numbers) rather than open-ended reasoning — FAST instead
        # of STRONG here, ~5x cheaper per token on the model that runs
        # longest after Investigator.
        skeptic = stage(
            "skeptic", "FAST", [T.seasonality_check, T.verify_claim],
            SKEPTIC_PROMPT,
            f"{alert_text}\n\nInvestigator concluded:\n{invest}\n\n"
            f"Evidence so far:\n{_evidence_block(evidence)}",
        )
        writer = _model("FAST").with_structured_output(WriterOutput)
        out: WriterOutput = writer.invoke(
            f"{WRITER_PROMPT}\n\n{alert_text}\n\nTriage:\n{triage}\n\n"
            f"Investigator:\n{invest}\n\nSkeptic:\n{skeptic}\n\n"
            f"EVIDENCE:\n{_evidence_block(evidence)}",
            config={"callbacks": callbacks, "run_name": "writer"},
        )
        unverified = _guardrail(out, evidence)
        dumped = out.model_dump()
        if detection_check is not None:
            dumped["checks"] = [detection_check.model_dump(), *dumped["checks"]]
        rca = RCA(
            alert_id=alert.alert_id,
            metric=alert.metric,
            window_start=alert.window_start,
            window_end=alert.window_end,
            **dumped,
        )
        if unverified:
            rca.confidence = "low"
            rca.status = "low_confidence"
            rca.narrative += (
                f" [guardrail: {len(unverified)} claim(s) could not be "
                f"matched to tool evidence and should not be trusted]"
            )
        return rca

    try:
        if lf is not None:
            with lf.start_as_current_observation(
                    name=f"rca {alert.alert_id}", as_type="span") as root:
                rca = investigate()
                try:
                    trace_url = lf.get_trace_url(trace_id=lf.get_current_trace_id())
                except Exception:
                    trace_url = f"trace_id={lf.get_current_trace_id()}"
                root.set_trace_io(input=alert.model_dump(mode="json"),
                                  output=rca.model_dump(mode="json"))
            lf.flush()
        else:
            rca = investigate()
        rca.trace_url = trace_url
        return rca
    except Exception as e:  # noqa: BLE001 — an alert must always yield an RCA row
        return RCA(
            alert_id=alert.alert_id,
            metric=alert.metric,
            window_start=alert.window_start,
            window_end=alert.window_end,
            factor="unknown",
            segments=[],
            narrative=f"RCA pipeline failed: {type(e).__name__}: {e}",
            claims=[],
            checks=[],
            confidence="low",
            trace_url=trace_url,
            status="failed",
        )
