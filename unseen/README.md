# Onboarding the unseen slice into `inmobi-hari`

Findings are in [DIAGNOSIS.md](DIAGNOSIS.md). This file is the method: how the
data was loaded, and the one thing that will produce a wrong answer if you skip it.

## Run order

| file | what it does |
|---|---|
| `u01_schema.sql` | database `inmobi-hari`, 3 dimension tables, `ad_events` |
| `u02_pipeline.sql` | dictionaries, `rollup_totals_1m`, `rollup_marginal_1h`, `rollup_os_country_1h`, their MVs |
| `u03_baselines.sql` | June price book + fill book, and the expected-value tables |
| `u04_alerts.sql` | `alerts_unseen`, `v_incidents_unseen` |
| `u05_rca_scan.sql` | `rca_scan` / `rca_seg` — dimension ranking and rate/mix split over an explicit window pair |

Load, then backfill (MVs only see inserts that arrive *after* they exist):

```bash
D=click-a-thon-2026/InMobi/unseen_data
chc -q "INSERT INTO \`inmobi-hari\`.apps FORMAT CSVWithNames"       < $D/apps.csv
chc -q "INSERT INTO \`inmobi-hari\`.geo_device FORMAT CSVWithNames" < $D/geo_device.csv
chc -q "INSERT INTO \`inmobi-hari\`.advertisers FORMAT CSVWithNames"< $D/advertisers.csv
chc -q "INSERT INTO \`inmobi-hari\`.ad_events FORMAT Parquet"       < $D/ad_events.parquet
```

Dimensions must land **before** events, or every dictionary lookup in the MV
resolves to `unknown` and the marginal rollup is unrecoverable without a rebuild.

Hyphenated database names work in `dictGet` — `dictGetOrDefault('inmobi-hari.dict_apps', …)`
resolves correctly. They need backticks everywhere else.

### Making it self-contained: the main batch lives here too

`inmobi-hari.ad_events` now holds **both** batches — the original main dataset
(2026-06-01 → 2026-07-05, loaded from a separate `inmobi` database used during
development) and the unseen slice — as one unified 10.5M-row fact table, with no
cross-database dependency. Two more things had to exist for that to be correct:

1. **A snapshot of the main batch's own dimension labels** — `apps_v1`,
   `geo_device_v1`, `advertisers_v1`, plus `dict_apps_v1` / `dict_geo_v1` /
   `dict_adv_v1` on top. Necessary because "restate the main batch through the
   unseen dictionaries" is exactly the relabeling trap above, just applied in
   the other direction.
2. **The three materialized views dropped before the bulk backfill, and
   recreated after.** The rollup tables already held correctly-loaded main-batch
   data from an earlier manual step; inserting 9M raw events into `ad_events`
   while the MVs were still attached would have re-derived those same rows
   through the *current* (unseen) dictionaries and summed them on top of the
   correct ones — silently doubling and corrupting every rollup. Backfilling
   a MergeTree-target MV always needs this drop/insert/recreate sequence;
   there is no per-insert switch to suppress it.

Bulk-copying `inmobi.ad_events` into `inmobi-hari.ad_events` runs server-side
(`INSERT INTO ... SELECT * FROM ...` on the same cluster) — 9M rows in 3.3s,
no client round-trip.

### The batch-aware join

With two dimension snapshots live side by side, every dictionary lookup needs
to pick the snapshot that matches the event's own batch — `event_time < 2026-07-06`
routes to `dict_*_v1`, everything else to the current `dict_*`. Miss this in
even one direction and the relabeling artifact comes back with a vengeance:
the first attempt at rescoring the full window hard-coded the current
dictionaries for every event, and June's `country = AE` came back at
**z = +327, +162% eCPM, on 30 of 35 June days** — a spurious "incident" that
was really just June traffic being priced through July's country mix. The fix
is `if(event_time < '2026-07-06', dict_*_v1, dict_*)` at every lookup site in
`u03_baselines.sql`. There is no shortcut here: the join key has to be
recomputed per-event, not assumed constant for a whole backfill.

---

## The trap: the dimension tables were regenerated

`spec.md` says the dimension CSVs carry the same IDs with new attribute values.
They were not kidding — 82% of apps changed category, 92% of geo profiles changed
country, 85% of advertisers changed vertical.

The obvious reading is "restate history through the new tables and compare."
The other obvious reading is "the old rollups are still fine, just use them."
**Both are wrong, and each is wrong for a different half of the metrics.**

Measured on every dimension, error against the observed clean July days:

| metric class | old rollup as-is | restated through new dims |
|---|---|---|
| segment traffic share | 8–24 pp off | **≤1.2 pp off** |
| eCPM (country, region) | **1.73% off** | 67–73% off |

Read that as: **volume is a property of the `geo_device_id`. Price is a property
of the country label.** A device keeps its traffic when it is relabeled; it does
not keep its price.

The consequence, concretely: under old labels, `country = ID` carried 6.98% of
requests. Under new labels it carries 17.46%. That looks like an enormous traffic
shift and it is **entirely relabeling** — the restated June figure is 17.47%.
Meanwhile per-country eCPM barely moved across the boundary (US 3.633 → 3.559,
ES 2.946 → 2.890), which is only visible if you *don't* restate.

### What the pipeline does instead

Don't baseline the segment. Baseline the **driver**, then re-weight to the
observed July mix. Three drivers, discovered by holding one dimension fixed and
varying another:

| metric | driver | evidence |
|---|---|---|
| `fill_rate`, `render_rate` | `publisher_tier` × `ad_format` | tier as-is 0.81% error vs restated 14.26%; fill is flat across categories *within* a tier (tier_1 0.907–0.914) |
| `eCPM` | `country` × `ad_format` | price book residual ±0.025 pp on 7 held-out days |
| volume / mix | the ID | restated share matches to ≤1.2 pp |

Expected value for any segment on any day is then
`Σ (observed events in that segment for each driver cell) × (June rate for that cell)`.
The mix cancels by construction, so a segment is only flagged when its *rate*
moved — which is the thing an incident actually does.

Validation on the clean days: total fill expected 0.79206 vs actual 0.79296 on
Jul 6 (+0.11%) and 0.79239 vs 0.79345 on Jul 10 (+0.13%); total eCPM gap +0.27%,
+0.23%, +0.28% on Jul 6–8. Against the same books the incident days read −7.66%
and −7.55% (fill) and −5.01% and −5.11% (eCPM). Zero alerts fire on Jul 6 and Jul 7.

---

## Three other things that cost real debugging time

**A sum/sum June baseline is contaminated by June's own incidents.** June's
planted fill anomaly (Jun 23–25, fill 0.750 against a 0.785 norm) drags a
window-wide `sum(fills)/sum(requests)` baseline down to 0.7787. Every clean July
day then reads +1.9% and fires at z ≈ 12. The fix is the median of the *daily*
ratios, which is what `v_fillbook_jun` computes. This is the same reason the
scoring layer uses median + MAD rather than mean + stddev: a breach must not be
able to move its own threshold.

**`vertical` and `campaign_type` cannot explain `fill_rate`.** An unfilled
request has no `advertiser_id`, so it lands in an `UNFILLED` bucket whose fill
rate is 0 by construction. It scores z = −279 on a completely healthy day and
outranks the real cause. They are excluded from the rate-metric array join in
`u03_baselines.sql` — not filtered downstream, excluded at the source.

**A driver model that is missing a term fires every day.** The first fill book
was keyed on `publisher_tier` alone. `ad_format = video` then read −9% on all
five days including the clean ones, because fill rate also depends on format
(video 0.723 vs banner 0.831). A deviation that is present on *every* day is a
model gap, not an incident — the tell is that it does not have an onset hour.

---

## Reading the output

`v_incidents_unseen` ranks by |z| within each `(day, metric)` and reports the
rest as `bleed_through_alerts`. That column is not noise to be suppressed — a
40% drop in a 19%-share segment *must* move all 25 overlapping buckets by ~7.7%,
and if it doesn't, the attribution is wrong. Bleed-through count is a consistency
check on the root cause, not a failure of the detector.

Concentration, from `rca_scan`: ≈0 means the move is global to that dimension
(stop looking for a segment); >2 means one segment owns it; two dimensions both
>2 means an interaction — cross them.

## Do not trust the inherited detectors across the boundary

`v_scores_1h` / `v_incidents_1h` were carried over from the June build and still
work, but they score against a trailing raw baseline with no mix correction. Run
over the unseen window they flag **12 eCPM breach-hours on Jul 6 and 15 on Jul 7**
— both fully clean days. They are kept for hour-grain drill-down *after* a day has
been confirmed by `alerts_unseen`; they are not a detector for this window.

`v_attribution_1d` has the same issue: its `is_weekday` median baseline spans both
imports, so its eCPM contribution for July absorbs the relabelling shift. Use the
LMDI table in `DIAGNOSIS.md`, which is computed against the clean Jul 6–7 level.
