# RF Engines — open-source propagation stack

Pluggable adapters that let TELECOM-TOWER-POWER run third-party
propagation models alongside the built-in physics + ridge model and
ITU-R P.1812 wrapper, without any commercial licence.

## Engines shipped

| Engine          | Source                                       | Licence    | Status            |
| --------------- | -------------------------------------------- | ---------- | ----------------- |
| `itu-p1812`     | `eeveetza/Py1812` (already in platform)      | ITU permissive | **available** |
| `itmlogic`      | [`edwardoughton/itmlogic`][itm] (NTIA ITM)   | MIT-style  | **available** (pip) |
| `rf-signals`    | clean-room (this repo, `rf_signals/`)        | LicenseRef-TTP-Proprietary | **available** — empirical models |
| `signal-server` | [`W3AXL/Signal-Server`][css] (active fork)   | **GPL-2.0**| operator-buildable (see note) |
| `sionna`        | NVIDIA Sionna (learned model)                | Apache 2.0 | scaffolding only  |
| `sionna-rt`     | NVIDIA Sionna 2.x RT (Mitsuba 3 / Dr.Jit)    | Apache 2.0 | **roadmap Q2/2026** — scaffold registered, GPU runtime pending |

[rfs]: https://github.com/thebracket/rf-signals
[css]: https://github.com/W3AXL/Signal-Server
[itm]: https://github.com/edwardoughton/itmlogic

### Copyleft posture (signal-server) and clean-room rf-signals

`signal-server` is **GPL-2.0** — viral copyleft. The platform stays
clean because the Python adapter talks to it via **subprocess**
(separate process boundary) — no static linking, no FFI inside the
API process — and the resulting `signalserverHD` binary is **never
bundled** in the proprietary container image. Operators build it on
the ECS host (or a build sidecar), upload to S3, and the task
downloads it at boot.

`rf-signals` (the legacy `thebracket/rf-signals` GPL-2.0 crate) was
**not** forked. Instead `rf_signals/` in this repo is a **clean-room
Rust crate** that re-implements only the *public-domain empirical
formulas* (FSPL/ITU-R P.525, Okumura-Hata, COST-231-Hata, ECC-33,
Egli, two-ray plane-earth). All citations are to open IEEE/ITU/ECC
papers (see `rf_signals/src/models.rs` headers). No GPL upstream
code is consulted, copied, or linked, so the binary inherits no
copyleft obligation. It is still launched as a subprocess for
consistency with the other engines, but the in-repo source itself
is under the proprietary licence.

### rf-signals status (clean-room, available)

The revival shipped as `rf_signals/` (Rust 2021, stable toolchain).
It builds with `bash scripts/build_rf_signals.sh ~/.local` and
produces a self-contained `rfsignals-cli` binary (~500 KB, no
runtime deps). Model dispatch is automatic by frequency unless the
caller pins one explicitly:

| Band              | Default model    |
| ----------------- | ---------------- |
| < 150 MHz         | Egli             |
| 150 – 1500 MHz    | Okumura-Hata     |
| 1500 – 2000 MHz   | COST-231-Hata    |
| 2000 – 3500 MHz   | ECC-33           |
| > 3500 MHz        | FSPL fallback    |

All outputs are floored at FSPL (no model can return a value below
free-space). Confidence is 0.75 for empirical models in their
calibrated band, 0.55 for FSPL/two-ray fallbacks. Validation against
the coverage-diff golden set vs ITU-R P.1812 over 7 nights is the
remaining gate before promoting it to the primary engine in
`/coverage/engines/compare`; until then it stays in the rotation as
a cross-check, not a reference.

### ⚠️ signal-server status (operator-buildable)

The original `Cloud-RF/Signal-Server` repo was **deleted by upstream
in 2023**; the GitHub URL now resolves to a historical README only.
The build script in this repo points at the active community fork
[`W3AXL/Signal-Server`](https://github.com/W3AXL/Signal-Server) (GPL-2.0,
last update 2025).

Upstream's CLI is flag-based and writes PPM bitmaps plus a
`<basename>-site_report.txt` rather than JSON to stdout. To make it
request-time-callable from the registry we ship
`scripts/signal_server_json.patch` (GPL-2.0-or-later, ~17 lines)
which adds a single `-json` flag: in PPA mode the binary then prints
one extra JSON line to stdout containing `basic_loss_db`,
`free_space_loss_db`, `distance`, `frequency_mhz`, and `model`.
Without `-json` the binary is byte-for-byte identical to upstream.

`scripts/build_signal_server.sh` clones W3AXL, applies the patch,
runs `cmake ../src && make`, and installs `signalserverHD` to the
operator's `$PREFIX/bin`. The patch is verified to apply cleanly
against W3AXL master `7f6242a`; if upstream HEAD drifts, pin the
clone to that SHA or re-roll the patch.

The adapter still self-disables (`is_available() = False`) until
`SIGNAL_SERVER_JSON_FORK=1` is set in the environment — this is the
operator's assertion that the binary on `$PATH` was built with the
patch applied. Realistic loss values also require
`SIGNAL_SERVER_SDF_DIR` pointing at an SRTM .sdf tile directory;
without it Signal-Server assumes flat sea-level terrain.

**Fidelity note:** unlike the ITU P.1812 / ITM adapters, Signal-Server
ignores any caller-supplied `d_km` / `h_m` profile — it reads SRTM
tiles itself. Use it for second-opinion checks against profile-based
engines, not as a primary source when you already have a high-quality
terrain profile from another path.

## Endpoints

After deploy the API exposes (auth required, same key as `/analyze`):

```
GET  /coverage/engines
POST /coverage/engines/predict   { engine, link... } → loss + metadata
POST /coverage/engines/compare   { link... }         → A/B table with dB deltas
```

`compare` is the headline endpoint — it runs every available engine on
the same Tx→Rx link and returns deltas against `itu-p1812` as the
reference. Same statistic the FCC / ANATEL use when validating new
propagation models.

## Build

```bash
# Signal-Server (C++, GPL-2.0) — ~3 minutes
bash scripts/build_signal_server.sh ~/.local

# Then on the ECS task:
export SIGNAL_SERVER_BIN=/usr/local/bin/signalserverHD

# rf-signals (clean-room Rust, this repo) — ~15 s
bash scripts/build_rf_signals.sh ~/.local
# Then on the ECS task:
export RF_SIGNALS_BIN=/usr/local/bin/rfsignals-cli
```

`GET /coverage/engines` will start reporting `available=true` for each
engine whose binary is on `$PATH` (or whose explicit env var resolves).

## Companion automation

* **QGIS → Atoll exporter**: [scripts/qgis_to_atoll.py](../scripts/qgis_to_atoll.py)
  emits Atoll-native `sites.txt` / `transmitters.txt` / `terrain.bil` /
  `clutter.bil` (with ENVI `.hdr` sidecars) plus a `.qgs` project for
  visual QA. Towers come from `--towers-csv` or `--towers-from-db`
  (live `TowerStore`); rasters are sampled from the same `SRTMReader`
  / `MapBiomasExtractor` the runtime uses, so Atoll users can validate
  TTP predictions side-by-side with their commercial licence — no
  lock-in. Tested in [test_qgis_to_atoll.py](../test_qgis_to_atoll.py).
* **Planetiler base maps**: [scripts/planetiler_build.sh](../scripts/planetiler_build.sh)
  renders Brazil vector tiles in <30 min. Drops a `brazil.pmtiles`
  next to Caddy for the SPA's MapLibre layer.
* **Nightly coverage-diff robot**:
  [.github/workflows/coverage-diff.yml](../.github/workflows/coverage-diff.yml)
  re-runs a golden link set through every available engine and opens
  a regression issue if any engine drifts > 3 dB vs. the previous run.
  Golden links live under [tests/data/coverage_diff_links.json](../tests/data/coverage_diff_links.json).
* **Weekly satellite-change robot**:
  [.github/workflows/satellite-change.yml](../.github/workflows/satellite-change.yml)
  uses [scripts/satellite_change_robot.py](../scripts/satellite_change_robot.py)
  to scan each tower's footprint against fresh Planet Labs PSScene
  imagery (Data API quick-search), flagging sites with cloud-free
  scenes since the last run so the operator knows to refresh
  predictions. Requires the `PLANET_API_KEY` repo secret; without it
  the robot still runs and emits an empty `no-api-key` report.
  Tested in [test_satellite_change_robot.py](../test_satellite_change_robot.py).

## Adding a new engine

1. Implement `RFEngine` in `rf_engines/<name>_engine.py`.
2. `register_engine(MyEngine())` at module top-level.
3. Append the module name to the `_autoregister` tuple in
   [rf_engines/__init__.py](../rf_engines/__init__.py).
4. Optionally add a row to `tests/data/coverage_diff_links.json`
   coverage tests.

The engine **must** return `None` from `predict_basic_loss` on any
failure (missing dep, out of domain, subprocess timeout) — the
registry never propagates engine errors to the API caller.

## itmlogic (Longley-Rice / ITM)

Pure-Python NTIA ITM v1.2.2 wrapper, installed via `pip install itmlogic`.
The adapter replicates the orchestration in upstream's `scripts/p2p.py`
(`qlrpfl` → `lrprop` → `avar`) so the engine is self-contained — we
do **not** depend on the upstream scripts/ folder.

Env config: `ITMLOGIC_CLIMATE` (default 5 = continental temperate),
`ITMLOGIC_NS0` (default 314 N-units), `ITMLOGIC_EPS` (default 15),
`ITMLOGIC_SGM` (default 0.005). Brazilian guidance: Amazon → 1
(equatorial), Cerrado/DF → 2, NE coast → 3.

The `scripts/optimize_sites.py` GA optimizer uses this engine by
default (configurable via `--engine`). See module docstring for
runtime budgets.

The optimizer accepts either an explicit `--aoi=lat_min,lon_min,lat_max,lon_max`
or, when `--aoi` is omitted, derives the AOI automatically from the
receivers' bounding box padded by `--aoi-margin-deg` (default 0.2°
≈ 22 km). For dense receiver sets this is the recommended path: it
removes the manual step of picking a search box and avoids the failure
mode where receivers near a corner of a tight AOI cannot be reached
because no candidate tower position is close enough.

## Sionna / IA engine

The `sionna` adapter starts disabled (`SIONNA_DISABLED=1`) and returns
`None` until ops provisions a model artefact. Training pipeline lives
in `scripts/train_sionna.py` (Keras MLP → TFLite) and runs weekly via
`.github/workflows/retrain-sionna.yml` against drive-test rows in
`link_observations`. Once `sionna_model.tflite` lands in
`s3://telecom-tower-power-results/models/sionna/current/` the ECS
entrypoint pulls it on boot and the engine registers itself — no API
code change required.

> ⚠️ **Synthetic baseline (since 2026-05-03).** The current artefact
> was trained on 2 000 rows generated by
> `.github/workflows/seed-synthetic-observations.yml`, all tagged
> `source='synthetic_p1812_v1'`. The labels come from the P.1812
> reference solver itself with σ=4 dB shadowing — so this model can,
> at best, *imitate* P.1812. Treat it as plumbing validation only:
>
> - **Do NOT** promote `sionna` over `itu-p1812` in `/coverage/engines/compare`
>   or pick it as the primary predictor anywhere.
> - The `compare` endpoint already pins `reference="itu-p1812"`.
> - Once real drive-test rows arrive (any `source` other than
>   `synthetic_p1812_v1`), retrain with
>   `--exclude-source synthetic_p1812_v1`. The retrain workflow does
>   this automatically when ≥`min_links` non-synthetic rows are present
>   (override with the `exclude_synthetic` dispatch input).
> - Synthetic rows can be dropped at any time with
>   `DELETE FROM link_observations WHERE source = 'synthetic_p1812_v1';`.

This caution is intentional: production traffic will not depend on a
learned predictor until it has been benchmarked against P.1812 on the
coverage-diff golden set for at least 14 consecutive nights.

### Real-data ingestion plan (RMSE < 5 dB target — roadmap Q3/2026)

The `cable_loss_db` column (commit `9a0796d`) plus the drive-test
source validator (commit `a64c014`) close the calibration gap that
otherwise caps achievable RMSE around 5-6 dB regardless of sample
count. With those two landed, the ingestion plan runs in three
gated phases (`source` prefix `drivetest_*` → validator enforced):

| Phase | Rows | Source tag | Goal | Promotion gate |
|------:|-----:|------------|------|----------------|
| 1 | ≤ 500 | `drivetest_pilot` | plumbing | residuals visible on `/metrics`, 14-night coverage-diff golden run green |
| 2 | ~5 k  | `drivetest_v2`    | MAE ≈ 4 dB | retrain `--exclude-source synthetic_p1812_v1`, MAE ≥ 1 dB better than baseline |
| 3 | ≥ 30 k | `drivetest_fleet` | RMSE < 5 dB | `exclude_synthetic=always`, no per-tier regression on residual gauges |

Bulk S3 → Lambda → `COPY link_observations FROM …` ingestion
(designed for ~10⁶ rows/day) is **deliberately deferred** until
Phase 1 schedules a real drive: the manual
`POST /coverage/observations/batch` path scales cleanly to 30 k
rows per request and avoids building infrastructure for a dataset
that does not yet exist. See `ROADMAP.md` § "Q3/2026" for the
full milestone.

## Sionna RT 2.x — full 3D ray-tracing (roadmap Q2/2026)

A second Sionna-branded adapter, **`sionna-rt`**, was scaffolded on
2026-05-03 to track the upgrade path to NVIDIA Sionna 2.x's
`sionna.rt` module — a Mitsuba 3 / Dr.Jit GPU ray-tracer that
launches deterministic rays against a textured 3D scene with
frequency-dependent material parameters from ITU-R P.2040.

### Why a second engine, not a replacement

The existing `sionna` adapter is a learned **MLP/TFLite** path-loss
predictor (Sionna 1.x era, CPU-only, drive-test trained). It stays.
`sionna-rt` is the **physics** path: deterministic, no labels needed,
mandatory at FR2/mmWave (24-100 GHz) where P.1812 over-predicts
coverage by 20+ dB indoors and in dense urban canyons because it
has no diffraction-free LOS-bounce model.

### Status — scaffold only

The `SionnaRTEngine` class is registered with the registry so it
shows up in `GET /coverage/engines`, but `is_available()` is
hard-coded to `False` until the GPU runtime lands. `predict_basic_loss`
returns `None` on every call — there is intentionally no CPU fallback
because path-loss extracted from a degenerate (no-bounce) ray trace
is indistinguishable from FSPL and would mislead `compare`.

Why ship the scaffold now (May 2026)?

* The env-var contract (`SIONNA_RT_*`) is frozen — ops can pre-create
  SSM parameters and IAM grants without waiting for the implementation.
* `GET /coverage/engines` already exposes the placeholder so the SPA
  can render a "coming Q2/2026" badge.
* The autoregister + compare plumbing is exercised in CI without any
  GPU dependency.

### Q2/2026 delivery checklist

- [x] **Dependencies (separate `requirements-gpu.txt`)** — landed
  2026-05-03. `sionna>=2.0,<2.2`, `mitsuba>=3.5,<3.7`,
  `drjit>=1.0,<2.0`, `torch>=2.4,<2.6`. Kept out of the API container —
  these add ~3 GB of CUDA wheels.
- [x] **GPU image variant** — `Dockerfile.gpu` landed 2026-05-03,
  based on `nvidia/cuda:12.4.1-runtime-ubuntu22.04` + Python 3.11
  via deadsnakes PPA, used only by the worker pool. Build-time
  `python -c 'import torch, mitsuba, drjit, sionna'` smoke-test
  fails the build if the CUDA wheel selection is wrong.
- [~] **Worker pool** — AWS Batch with a GPU job queue (or a single
  EC2 G5 instance behind SQS) consuming `coverage:rt` jobs. Single
  predictions stay an HTTP "kick + poll" — no inline GPU calls from
  the API container.
  **Tijolo 5 — SQS poll + S3 raster upload (landed 2026-05-04):**
  `scripts/sionna_rt_worker.py --poll` long-polls
  `$SIONNA_RT_QUEUE_URL`, validates the job message schema
  (`job_id`, `scene_s3_uri`, `tx`, `frequency_hz`, `raster_grid`,
  `result_s3_uri`), downloads the scene bundle to a fresh
  tempdir, re-validates `manifest.json`
  (`implementation_status='complete'`), computes the per-pixel
  loss raster, writes `.npz` (loss_db + bbox + frequency + tx
  metadata) and uploads it to `result_s3_uri`. Poison-pill messages
  (schema-invalid JSON) are deleted with an error log; transient
  failures (S3 missing, manifest stale) leave the message on the
  queue for the redrive policy. `--once` and `--idle-exit` are
  test-friendly knobs. The trace itself is still a stub returning
  an FSPL-shaped raster centred on the TX — tijolos 6+ swap
  `compute_raster_loss()` for the real Mitsuba `load_file` +
  Sionna `PathSolver` call without changing the surrounding
  plumbing.
- [x] **Scene builder** — `scripts/build_mitsuba_scene.py` CLI
  scaffold landed 2026-05-03 (manifest-only, refuses to write
  `scene.xml` without `--allow-stub`). Manifest schema fixed
  (`schema_version=1`) so the Batch infra can be provisioned ahead
  of the implementation phases.
  **Tijolo 2 — data sources (landed 2026-05-04):** `--fetch-data`
  flag now writes `buildings.geojson` (Overpass `way["building"]`
  with `height` / `building:levels` resolution) and `terrain.tif`
  (SRTM3 EPSG:4326 Float32 GeoTIFF, native 3″ grid) under
  `<out-dir>/<aoi>/`. Manifest gains `buildings_summary` /
  `terrain_summary` and `implementation_status='data-only'`. Live
  smoke-test on `sp-centro` (5 km²) returns ~16 k buildings with
  realistic height stats (mean 14.94 m, max 170 m). Mutually
  exclusive with `--allow-stub`; `--prefetch-srtm` opts in to USGS
  tile downloads.
  **Tijolo 4 — Mitsuba `.xml` emission (landed 2026-05-04):**
  `--emit-scene` (implies `--fetch-data`) extrudes building
  footprints into triangulated meshes via stdlib ear-clipping
  (lon/lat → ENU equirectangular projection at the AOI centroid),
  writes binary little-endian `buildings.ply` and a flat
  `terrain.ply` ground plane at AOI mean elevation, then emits
  `scene.xml` (Mitsuba 3.5.0) carrying four `<bsdf type="radio-material">`
  blocks (concrete / glass / metal / vegetation) stamped with the
  `--reference-frequency-hz` evaluation (default 28 GHz) from the
  P.2040 library. Manifest advances to `implementation_status='complete'`
  with `buildings_mesh_count`, `buildings_ply_sha256`,
  `terrain_ply_sha256`, `scene_xml_sha256`, and
  `reference_frequency_hz`. `sp-centro` smoke build: 16 151 buildings
  → 330 426 vertices, 596 248 faces, scene.xml 1.5 KB. All buildings
  currently tagged `mat_concrete`; per-building material attribution
  (e.g. `building=commercial` → `mat_glass`) is a follow-up.
  Heightfield terrain replacing the flat plane is also a follow-up.
- [x] **mmWave material library** — `data/materials_p2040.json`
  landed 2026-05-04 (Tijolo 3). Four materials parameterised by the
  P.2040-3 Annex 1 four-coefficient model (`a, b, c, d`):
  concrete (a=5.31, b=0, c=0.0326, d=0.8095), glass (a=6.27, b=0,
  c=0.0043, d=1.1925), metal (near-PEC: σ=10⁷ S/m, ε_r=1) and
  vegetation (engineering extension — P.2040 defers to P.833 for
  foliage; values tuned so tagged canopies produce ~0.4–0.6 dB/m at
  28 GHz). Loader/evaluator at `scripts/sources/p2040_materials.py`
  exposes `evaluate(material, f_hz)` → `{epsilon_r, sigma, epsilon_r_imag,
  loss_tangent, in_valid_range}`. The `--fetch-data` build now
  embeds `materials_p2040.library_sha256` plus per-material
  evaluations at the requested frequencies into the manifest, so
  the GPU worker can refuse a scene built against a stale table.
- [~] **Per-pixel loss raster API** — `POST /coverage/engines/sionna-rt/raster`
  → SQS job → S3 output → presigned-URL response. Single-link
  `predict_basic_loss` is implemented as a 1×1 raster crop. Worker
  entrypoint scaffold landed 2026-05-03
  (`scripts/sionna_rt_worker.py --probe` reports torch/mitsuba/drjit/sionna
  versions + CUDA visibility; `--poll` refuses unless
  `SIONNA_RT_DISABLED=0` AND a manifest with
  `implementation_status='complete'` is reachable).
  **Tijolo 6 — kick-and-poll API endpoint (landed 2026-05-04):**
  `POST /coverage/engines/sionna-rt/raster` enqueues a job onto
  `$SIONNA_RT_QUEUE_URL` and returns `202 Accepted` with
  `{job_id, poll_url, result_s3_uri}`. The SQS body matches the
  Tijolo 5 worker's `parse_job_message` schema verbatim — a
  round-trip test in `tests/test_sionna_rt_raster_endpoint.py`
  feeds the API-emitted body back through the worker parser to
  lock the contract end-to-end. `GET /coverage/engines/sionna-rt/raster/{job_id}`
  polls S3 for the worker-uploaded `.npz` and returns a presigned
  download URL (`$SIONNA_RT_PRESIGN_TTL_S`, default 1 h) once
  available; until then the job stays `queued`. Refuses to enqueue
  with `503` when ops haven't set `$SIONNA_RT_QUEUE_URL` /
  `$SIONNA_RT_RESULTS_BUCKET`. SQS send failures bubble up as
  `502`. The actual GPU trace is still the FSPL-shaped stub from
  Tijolo 5 — replacing `compute_raster_loss()` with the real
  Mitsuba/Sionna call is the remaining brick.
- [ ] **Validation gate** — must stay within 6 dB RMSE vs.
  `itu-p1812` on sub-6 GHz golden links (sanity baseline) and
  produce non-trivial deltas (> 10 dB) on the mmWave golden links.
  Only then promote to the rotation in `/coverage/engines/compare`.
- [ ] **Docs** — design doc, scene-build runbook, GPU cost model
  (a 1 km² 3D trace at 28 GHz is ~$0.05 on a single G5.2xlarge —
  worth quoting per request before the SPA exposes the button).

### Why Q2/2026 and not now

ETA pulled in from Q3 → Q2/2026 on 2026-05-03 — mmWave moved up the
priority list. Three blockers still gate immediate implementation,
all mechanical:

1. **GPU infrastructure** — the platform is currently CPU-only ECS
   Fargate. Adding a Batch GPU queue is a multi-week ops effort.
2. **Scene data** — Brazilian OSM building coverage is patchy outside
   capitals; the scene builder needs a fallback to LIDAR-derived
   building footprints from IBGE, which is itself a separate task.
3. **mmWave pilots landing** — Brazilian 5G NR rollout (3.5 GHz) was
   the live priority through 2026-Q1; the FR2/mmWave deployments
   (26 GHz auctioned 2024) reach pilot density in Q2/2026, which is
   when accurate coverage predictions stop being academic.
