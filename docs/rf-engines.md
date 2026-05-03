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
