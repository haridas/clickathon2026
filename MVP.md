# Agentic RCA for Ad Platform Metrics

**From alert to answer.** A metric moves → the system investigates itself → a plain-language
diagnosis where every number is computed in ClickHouse, not written by an LLM.

## Principle

> **ClickHouse computes. The LLM narrates.**

Every figure in a diagnosis traces back to a SQL query we can replay. The agent chooses
*what to ask*; it never invents a number. This is the single design rule everything below follows.

## Pipeline

| # | Stage | What it does |
|---|-------|--------------|
| 1 | **Ingest** | Load `ad_events` (9M) + 3 dimension tables into ClickHouse. Materialized views roll up hourly metrics per dimension. Surface in ClickStack. |
| 2 | **Detect** | Compare each metric against a **like-for-like baseline** (same weekday/hour, trailing weeks) — not a static threshold. Flags deviation with a magnitude and a confidence. |
| 3 | **Trigger** | Alert fires a webhook to the RCA agent with `alert_id`, metric, window, and observed vs. expected. |
| 4 | **Decompose** | Walk the revenue identity in SQL: `Revenue = Requests × Fill rate × Render rate × eCPM/1000`. Which *factor* moved? |
| 5 | **Localize** | Drill the guilty factor by dimension (app, geo, device, OS, advertiser, format) using contribution analysis. Which *segment* moved? Recurse into the top contributors. |
| 6 | **Rule out** | Explicitly test and clear the alternatives: seasonality, volume shift, sibling metrics, adjacent segments. Record the cleared checks as evidence. |
| 7 | **Narrate** | LLM writes the diagnosis from the computed evidence table only. Sub-agents review for unsupported claims and render a clean HTML report. |
| 8 | **Publish** | Findings written back to ClickHouse against `alert_id`. Slack notification links to LibreChat, pre-loaded with the RCA for follow-up questions. |
| 9 | **Digest** | Daily agent summarizes trends, open incidents, and estimated revenue impact. |

## Deliberate design choices

- **Seasonality-aware baseline over static thresholds.** Weekends are genuinely lower;
  a flat threshold cries wolf on every Saturday. At least one planted movement is pure
  seasonality and must be *ruled out*, not alarmed on.
- **Decomposition before drill-down.** Finding the factor first (volume vs. fill vs. price)
  cuts the search space before we start slicing dimensions.
- **Evidence table as the contract.** The narrator receives a structured set of
  `(claim, value, query)` rows. It cannot cite what isn't there.
- **Full trace per investigation** (Langfuse): every query run, in order, with its result and
  the reason it was run. A judge can replay the reasoning. *No trace, no credit.*

## Target output

> Revenue fell **12.4%** on Jun 18 (₹X vs. ₹Y expected). Driven almost entirely by
> **fill rate** on **Galaxy S23 / region EU**, down 31% against its own trailing baseline —
> **68%** of the total gap. Request volume (+2%), CTR (−0.4%), and all other regions were
> checked and are within normal range. Seasonality ruled out: same weekday, prior 3 weeks.

Localized, quantified, and honest about what it cleared.

## Business value

1. **Business on-call** — catches revenue movement the dashboards would show only in hindsight.
2. **Prioritization** — RCAs ranked by revenue at risk, so engineering fixes what costs most.
3. **MTTR** — hours of manual dashboard drilling collapse to seconds.
4. **Opportunity discovery** — the same segment analysis surfaces outperformers, not just failures.

## Built for the unseen incident

The build targets the *investigation loop*, not the anomalies we found while developing.
Nothing in the detection or drill-down is tuned to a known segment or date.
