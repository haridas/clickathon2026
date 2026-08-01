# RCA index

Append-only log of every investigation. Newest at the bottom. Context source for the part-5 chat agent.

## RCA 2026-08-01T09:49 · revenue · alert cli_e5810706
- window: 2026-06-18 14:00 – 17:00 UTC
- root cause: (stub) ([STUB] revenue drop of -12.3% vs baseline (41200 vs 47000). Agent pipeline not yet implemented — this row proves the ale)
- factor: unknown · confidence: low · status: ok
- ruled out: none recorded
- trace: (no trace)
- rca_id: rca_9660a3dd (full evidence in rca_results)

## RCA 2026-08-01T09:59 · revenue · alert cli_c3dc60ed
- window: 2026-06-18 14:00 – 17:00 UTC
- root cause: (stub) ([STUB] revenue drop of -12.3% vs baseline (41200 vs 47000). Agent pipeline not yet implemented — this row proves the ale)
- factor: unknown · confidence: low · status: ok
- ruled out: none recorded
- trace: (no trace)
- rca_id: rca_80f98678 (full evidence in rca_results)

## RCA 2026-08-01T10:37 · revenue · alert cli_9839742b
- window: 2026-07-02 14:00 – 17:00 UTC
- root cause: broad-based (RCA pipeline failed: AttributeError: 'Langfuse' object has no attribute 'start_as_current_span')
- factor: unknown · confidence: low · status: failed
- ruled out: none recorded
- trace: (no trace)
- rca_id: rca_90f44b90 (full evidence in rca_results)

## RCA 2026-08-01T10:37 · revenue · alert cli_dc2d278d
- window: 2026-07-02 14:00 – 17:00 UTC
- root cause: broad-based (RCA pipeline failed: AttributeError: 'LangfuseSpan' object has no attribute 'update_trace')
- factor: unknown · confidence: low · status: failed
- ruled out: none recorded
- trace: (no trace)
- rca_id: rca_2566f3bc (full evidence in rca_results)

## RCA 2026-08-01T10:38 · revenue · alert cli_f0217103
- window: 2026-07-02 14:00 – 17:00 UTC
- root cause: broad-based (Revenue spiked to $82.85 from a baseline of $77.30 on July 2, 2026 (14:00–17:00), a +7.19% increase confirmed as genuine)
- factor: requests · confidence: high · status: ok
- ruled out: Seasonality: observed revenue $82.85 exceeds 4-week baseline range [76.27, 78.75] · Single ad format concentration: banner growth (7.75%) is comparable to overall requests growth (6.51%), ruling out disproportionate format-driven spike · Single app concentration: top app_id only 21.1% of delta, with long tail distribution · Fill rate contributed only 11.8% of revenue change despite +0.82% growth · eCPM declined -0.23%, contributing -3.3% to revenue change
- trace: https://cloud.langfuse.com/project/cmsa8hetk0c4fad0e9746vlic/traces/48105801c63f78287c34643725387361
- rca_id: rca_52c95ef1 (full evidence in rca_results)

## RCA 2026-08-01T10:41 · revenue · alert cli_d4aa7a50
- window: 2026-07-02 14:00 – 17:00 UTC
- root cause: broad-based (Revenue spiked $77.30 to $82.85 (+7.19%, z=5.01) from 2026-07-02 14:00–17:00, driven almost entirely by broad-based requ)
- factor: requests · confidence: low · status: low_confidence
- ruled out: Seasonality: observed revenue $82.85 is 7.9% above the 4-week trailing max of $78.75 · Single dominant app anomaly: largest contributor app_00000 is only 21.1% of delta and growing at platform rate (5.69% vs 6.51% global) · Pricing/yield event: eCPM contribution to revenue change is -3.3%
- trace: https://cloud.langfuse.com/project/cmsa8hetk0c4fad0e9746vlic/traces/be703144b2b2130670bdcc1ba8a4d1dc
- rca_id: rca_778e6a4a (full evidence in rca_results)

## RCA 2026-08-01T10:49 · ecpm · alert cli_86f9f388
- window: 2026-06-20 09:00 – 12:00 UTC
- root cause: ad_format=interstitial (eCPM dropped 3.18% during the 3-hour window, driven primarily by interstitial ads. Interstitial eCPM fell from 2.7279 to)
- factor: ecpm · confidence: high · status: ok
- ruled out: Seasonality: observed eCPM 2.4037 falls outside 4-week baseline range [2.4776, 2.4876] · Request-volume decline: requests rose 4.01%, not fell · Fill rate as driver: −0.45% share of revenue change, near-flat · Render rate as driver: −0.04% share of revenue change, near-flat
- trace: https://cloud.langfuse.com/project/cmsa8hetk0c4fad0e9746vlic/traces/d59aabc65e8273e9ef42e1649b73a750
- rca_id: rca_64de6b55 (full evidence in rca_results)

## RCA 2026-08-01T11:10 · ecpm · alert webhook_test_001
- window: 2026-06-20 09:00 – 12:00 UTC
- root cause: ad_format=interstitial (Between 09:00 and 12:00 UTC on 2026-06-20, eCPM dropped 3.18% (2.4826 → 2.4037), a statistically significant decline (z=)
- factor: ecpm · confidence: high · status: ok
- ruled out: Seasonality — eCPM observed 2.4037 falls outside the normal 4-week range [2.4776, 2.4876]
- trace: https://cloud.langfuse.com/project/cmsa8hetk0c4fad0e9746vlic/traces/1317a52ec7a0750c03bd3f0e23241967
- rca_id: rca_bd7072b5 (full evidence in rca_results)
