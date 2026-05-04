# TELECOM-TOWER-POWER — Public Roadmap

This file tracks larger feature directions that span more than a single
sprint. Day-to-day work continues to live in GitHub issues / Dependabot;
items here are the architectural moves that need cross-component
planning (deps, infra, data, validation gates).

## Q2 / 2026 — Ray-tracing + mmWave (Sionna 2 full 3D)

**Status:** scaffold landed 2026-05-03 (commit pending).
**Tracking:** `rf_engines/sionna_rt_engine.py`, env vars `SIONNA_RT_*`,
docs/rf-engines.md § "Sionna RT 2.x — full 3D ray-tracing".

Goal: integrate NVIDIA Sionna 2.x's `sionna.rt` module
(Mitsuba 3 / Dr.Jit GPU ray-tracer) as a first-class RF engine, so
the platform can produce physically meaningful path-loss predictions
at FR2 / mmWave bands (24-100 GHz) where ITU-R P.1812 over-predicts
coverage by 20+ dB.

Deliverables — see the checklist in `docs/rf-engines.md` (§ "Q2/2026
delivery checklist"). Headline items:

- GPU runtime via AWS Batch (`Dockerfile.gpu`, CUDA 12.x).
- `scripts/build_mitsuba_scene.py` — OSM + SRTM + clutter → Mitsuba scene.
- mmWave material library (ITU-R P.2040-3, 28 / 39 / 60 GHz).
- `POST /coverage/engines/sionna-rt/raster` async endpoint backed by SQS.
- Validation: ≤ 6 dB RMSE vs. P.1812 sub-6 GHz; > 10 dB delta on
  mmWave golden links before promotion to `/coverage/engines/compare`.

Non-goals for Q2/2026:

- Replacing the existing learned `sionna` (TFLite) engine — both stay.
- Inline GPU calls from the API container — ray-tracing is async only.
- Indoor in-building modelling (separate Q4/2026 item).


## Q3 / 2026 — Real drive-test ML retrain (RMSE < 5 dB)

**Status:** ingestion plumbing landed 2026-05-03 (commits 9a0796d,
a64c014). Awaiting first scheduled drive-test session to graduate
beyond Phase 1.
**Tracking:** `observation_store.py`, `scripts/train_sionna.py`,
`POST /coverage/observations`, `s3://telecom-tower-power-results/models/sionna/`.

Goal: replace the 100% synthetic training set (P.1812 + σ=4 dB
log-normal shadowing — fundamental floor that caps test_mae at
~6.5 dB) with calibrated real-world labels and graduate the
production model below RMSE = 5 dB on the held-out city-pair fold.

Phased ingestion plan (sequential gates — each must pass before
green-lighting the next phase to avoid spending field hours on
poisoned plumbing):

- **Phase 1 — plumbing validation (≤ 500 rows, manual upload).**
  One-day pilot drive (≈ 30 km, 1-2 cells, source `drivetest_pilot`).
  Verify: rows persist with `cable_loss_db != 0`, validator rejects
  default-fill payloads, residual histogram on `/metrics` shifts
  visibly. 14-night automated coverage-diff golden run before the
  data is allowed near a training pass.
- **Phase 2 — calibration scaling (≈ 5 k rows, target MAE ≈ 4 dB).**
  Multi-cell weekend drive across two morphologies (urban dense +
  suburban). Re-train with `--exclude-sources synthetic_p1812_v1`
  on a 50/50 mix; promote only if held-out MAE improves ≥ 1 dB
  vs. the synthetic baseline.
- **Phase 3 — production retrain (≥ 30 k rows, target RMSE < 5 dB).**
  Fleet-mounted continuous logging across ≥ 5 cities. Train with
  `exclude_synthetic=always`. Promotion gate: RMSE < 5 dB on the
  city-pair fold AND no per-tier regression on the residual gauges.

Bulk S3 + Lambda + COPY ingestion (sized for ~10⁶ rows/day) is
deliberately deferred until Phase 1 schedules a real drive: the
manual `POST /coverage/observations/batch` path scales easily to
30 k rows and avoids premature infrastructure on a dataset that
does not yet exist.
