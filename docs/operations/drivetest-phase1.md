# Drive-test Phase 1 runbook

> **Scope**: pilot session — ≤ 500 rows, `source=drivetest_pilot`. Track in
> issue [#30](https://github.com/danielnovais-tech/TELECOM-TOWER-POWER/issues/30).
> See `ROADMAP.md` § "Q3/2026" for the broader 3-phase plan and
> `docs/rf-engines.md` § "Real-data ingestion plan" for the promotion gates.

This page is the authoritative procedure for the pilot drive-test. The
goal is **not** to collect a lot of data — it is to validate every link
in the calibration → ingest → metric chain so that subsequent phases
(5 k rows, 30 k rows) cannot be sabotaged by a silent calibration
mistake.

## 1. Hard pre-flight gates

Do not leave the office until **all** of these are checked. Any
shortcut here invalidates the labels for retraining.

- [ ] Test vehicle reserved (any sedan with a metal roof).
- [ ] GPS receiver verified to ≤ 3 m horizontal CEP (a static 5-min
      log on a known marker, e.g. a survey monument, must place the
      cluster centroid within 3 m of truth).
- [ ] UE / scanner chosen, **firmware version recorded** in the
      calibration sidecar metadata. Validated models: TEMS Pocket,
      G-NetTrack Pro, R&S TSMA6, QualiPoc Android.
- [ ] Antenna mag-mounted on the **roof centre** (not the pillar —
      the body cancels lobes asymmetrically off-centre). Height
      measured to ± 0.05 m to the radiating element, not the cab roof.
- [ ] `cable_loss_db` measured per band of acquisition (§ 2 below).
      Values transcribed into `scripts/drivetest_rx_calibration.json`
      replacing the null placeholders.
- [ ] `rx_gain_dbi` read from the antenna datasheet at the **band
      centre**, not the peak. Typical: 2-5 dBi for an omni mag-mount,
      6-9 dBi for a directional roof yagi.
- [ ] Tower metadata exported per cell under test (§ 3 below).
- [ ] Calibration sidecar `_calibration_metadata` block fully filled
      (operator, vehicle plate, antenna serial, scanner serial,
      signal-generator model + serial, calibration date, ambient °C).
      Missing serial numbers = no audit trail = pilot data discarded.
- [ ] One LTE / NR cell with **canonical** `tx_power_dbm` confirmed
      against ANATEL public registry **and** with the operator's
      published EIRP (when both agree, that's our trusted reference).

## 2. Calibração de `cable_loss_db` (CW injection)

The single line of code that hangs on this number is in
`scripts/train_sionna.py`:

```python
lb = (pt + gt + gr) - prx - cable_loss
```

If `cable_loss` is wrong by 3 dB, every label in the pilot is biased
by 3 dB. The model will dutifully learn that bias. Measure it, do not
estimate it.

### Required instruments

| Item | Spec | Why |
|---|---|---|
| Calibrated CW signal generator | 700-3700 MHz, ±0.3 dB amplitude accuracy, 50 Ω | Source of truth |
| 50 Ω terminator | DC-6 GHz | Bench stability |
| SMA torque wrench | 0.6 N·m | Reproducibility — mating force changes loss by 0.1-0.3 dB |
| Reference attenuator | 10 dB ± 0.1 dB | Sanity-check the generator output before each band |
| Spectrum analyser **or** the scanner itself in CW-power mode | ±0.5 dB | Read-out |

### Procedure (per band)

For each band in the JSON template (`700`, `800`, `900`, `1800`,
`1900`, `2100`, `2300`, `2500`, `2600`, `3500`):

1. Disconnect the antenna at the SMA closest to the antenna body. Do
   **not** add an adapter — every adapter is a 0.05-0.20 dB error.
2. Inject a CW tone at the band centre (e.g. 2630 MHz for the 2600
   band) at exactly **−30 dBm** measured at the generator output.
3. Read the level at the scanner / spectrum analyser RF input.
4. `cable_loss_db = -30 - read_level_dbm` (rounded to 0.1 dB).
5. Repeat 3× and average. If the spread is > 0.5 dB, the connector
   is dirty — clean with isopropyl + lint-free cloth, re-torque,
   re-measure.
6. Record in `scripts/drivetest_rx_calibration.json` under the
   matching band key.

> ⚠️ **Why the loop matters.** A loose SMA can drop 2 dB just from
> vibration during a drive. If you skip the 3× averaging you will
> notice the inconsistency in Phase 1 residuals weeks later, after
> the data is already in the training set.

### Acceptance worksheet (one row per band)

Copy this into your field notebook. Numbers in italics are typical
expected ranges for a 5 m LMR-400 jumper + lightning protector — if
your measurement is outside the range, something is wrong.

| Band (MHz) | Centre f (MHz) | Read-out 1 | Read-out 2 | Read-out 3 | Mean | σ | Accepted? | _Typical_ |
|-----------:|---------------:|-----------:|-----------:|-----------:|-----:|---:|:----------|----------:|
| 700        | 730            |            |            |            |      |    |           | _2.5-3.2_ |
| 800        | 830            |            |            |            |      |    |           | _2.7-3.4_ |
| 900        | 935            |            |            |            |      |    |           | _2.8-3.6_ |
| 1800       | 1842           |            |            |            |      |    |           | _3.0-3.8_ |
| 1900       | 1960           |            |            |            |      |    |           | _3.1-3.9_ |
| 2100       | 2140           |            |            |            |      |    |           | _3.3-4.1_ |
| 2300       | 2350           |            |            |            |      |    |           | _3.4-4.3_ |
| 2500       | 2550           |            |            |            |      |    |           | _3.5-4.4_ |
| 2600       | 2630           |            |            |            |      |    |           | _3.6-4.6_ |
| 3500       | 3550           |            |            |            |      |    |           | _4.0-5.2_ |

Accept only when σ ≤ 0.5 dB and the mean is inside the typical range
**or** you can explain the deviation (longer cable, different
connector type — record the explanation in the
`_calibration_metadata` block).

## 3. Tower metadata

For every cell that will appear in the CSV's `tower_id` column,
fetch:

```bash
curl -fsS -H "X-API-Key: $TTP_DRIVETEST_KEY" \
  https://api.telecomtowerpower.com.br/tower/<id> \
  | jq '{tx_lat, tx_lon, tx_height_m, tx_power_dbm, tx_gain_dbi, freq_hz}'
```

Paste the responses into a single
`scripts/drivetest_tower_meta.json` file keyed by your CSV's
`tower_id` value. The **key must match the CSV verbatim** (case-
sensitive). The example file
`scripts/drivetest_tower_meta.example.json` shows the shape.

## 4. Route plan

- One urban-dense cell (downtown São Paulo / BH / Rio).
- One suburban cell, ≥ 5 km from urban core, ideally a different
  operator for path-loss diversity.
- Drive radius **0.5-3 km** from each tower. Avoid keeping LOS the
  whole time — we need clutter variety so the model has signal to
  learn from.
- GPS fix at ≤ 1 Hz, RSSI averaged over 100 ms windows. At 30 km/h
  that's ≈ 8 m between samples → ≈ 125 samples/km. Budget: 4 km of
  useful track ≈ 500 rows.

## 5. Ingest

```bash
# 1. Sanity-check that calibration JSON has NO nulls left.
jq -e '
  .rx_gain_dbi != null and
  .rx_height_m != null and
  ([.cable_loss_db[] | select(. == null)] | length) == 0
' scripts/drivetest_rx_calibration.json \
  || { echo "Calibration incomplete — measure remaining bands"; exit 1; }

# 2. Dry-run first. This must print "Parsed N valid rows (skipped 0)".
python scripts/drivetest_to_observations.py \
  --csv scan.csv \
  --tower-meta scripts/drivetest_tower_meta.json \
  --rx-calibration scripts/drivetest_rx_calibration.json \
  --source drivetest_pilot \
  --dry-run \
  --out /tmp/dt_pilot_payload.json

# 3. Real upload (Pro-tier API key).
python scripts/drivetest_to_observations.py \
  --csv scan.csv \
  --tower-meta scripts/drivetest_tower_meta.json \
  --rx-calibration scripts/drivetest_rx_calibration.json \
  --source drivetest_pilot \
  --api https://api.telecomtowerpower.com.br \
  --api-key "$TTP_DRIVETEST_KEY" \
  --batch-size 500
```

Confirm rows landed:

```bash
curl -fsS -H "X-API-Key: $TTP_DRIVETEST_KEY" \
  https://api.telecomtowerpower.com.br/coverage/observations/stats | jq
```

You should see `link_observations` increase by exactly the row count
the uploader reported. Any discrepancy = silently dropped rows; do
not proceed.

## 6. Validation gate (Phase 1 → Phase 2)

The pilot is **only** valid if all three pass within 14 days of
ingest:

1. **Residual histograms visible** on `/metrics`:
   `coverage_observation_residual_db_*` shows non-zero residual mass
   at ±5-15 dB. Synthetic-only deploys sit near 0 by construction
   — non-zero is what proves real-world labels are reaching the
   metric pipeline.
2. **14-night automated coverage-diff golden run** stays within
   ≤ 2 dB MAE delta vs. the 2026-05-03 snapshot. Blocked if any
   single night exceeds ±5 dB on any tile.
3. **No PII / IMEI leakage** in the uploaded JSON. Run
   `jq '[.observations[] | keys[]] | unique'` against the dry-run
   payload — only the 14 documented fields plus optional `ts` /
   `tower_id` should appear.

When all three pass, comment "Phase 1 ✅" on issue #30 and we
schedule Phase 2 (5 k rows, `source=drivetest_v2`).

## 7. Cleanup of pilot data

Pilot rows are **kept** in production — they are the seed for
Phase 2 retrains. Do not delete `source=drivetest_pilot` unless a
calibration mistake is discovered after the fact. In that case:

```sql
-- via SSM tunnel only; never via the API
DELETE FROM link_observations
 WHERE source = 'drivetest_pilot'
   AND ts >= <unix_epoch_of_bad_session>;
```

Smoke-test rows (`source=drivetest_smoketest`,
`tower_id LIKE 'SMOKETEST-%'`) can be dropped at any time without
side effects.

## Appendix — Quick failure triage

| Symptom | Likely cause | Fix |
|---|---|---|
| Uploader: `cable_loss_db['2600'] is null` | Forgot to measure that band | Stop. Measure. Restart. |
| 422 with `requires explicit values for: rx_height_m` | Pydantic-side default-fill caught | Calibration JSON missing `rx_height_m` — fill it |
| 429 `Rate limit exceeded` | Demo / Free tier key | Use a Pro-tier key (`$TTP_DRIVETEST_KEY`) |
| 500 + `column "cable_loss_db" does not exist` | ECS task on pre-`e9a4f2b81c5d` image | Re-run `gh workflow run deploy-ecs.yml` to force migration |
| Stats endpoint shows 0 new rows after 200 OK | Schema drift between API and DB | Check `alembic current` via SSM tunnel |
