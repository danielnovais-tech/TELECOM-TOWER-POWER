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
