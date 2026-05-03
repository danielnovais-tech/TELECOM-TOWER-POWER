# RF Engines — open-source propagation stack

Pluggable adapters that let TELECOM-TOWER-POWER run third-party
propagation models alongside the built-in physics + ridge model and
ITU-R P.1812 wrapper, without any commercial licence.

## Engines shipped

| Engine          | Source                                       | Licence    | Status            |
| --------------- | -------------------------------------------- | ---------- | ----------------- |
| `itu-p1812`     | `eeveetza/Py1812` (already in platform)      | ITU permissive | **available** |
| `itmlogic`      | [`edwardoughton/itmlogic`][itm] (NTIA ITM)   | MIT-style  | **available** (pip) |
| `rf-signals`    | [`thebracket/rf-signals`][rfs]               | **GPL-2.0**| placeholder — see note |
| `signal-server` | [`W3AXL/Signal-Server`][css] (active fork)   | **GPL-2.0**| operator-buildable (see note) |
| `sionna`        | NVIDIA Sionna (learned model)                | Apache 2.0 | scaffolding only  |

[rfs]: https://github.com/thebracket/rf-signals
[css]: https://github.com/W3AXL/Signal-Server
[itm]: https://github.com/edwardoughton/itmlogic

### Copyleft posture (rf-signals + signal-server)

Both `rf-signals` and `signal-server` are **GPL-2.0** — viral
copyleft. The platform stays clean because:

* the Python adapter talks to each engine via **subprocess** (separate
  process boundary) — no static linking, no FFI inside the API process;
* the resulting binaries (`rfsignals-cli`, `signalserverHD`) are
  **never bundled** in the proprietary container image. Operators build
  them on the ECS host (or a build sidecar), upload to S3, and the
  task downloads them at boot — same posture as ITU digital maps and
  MapBiomas rasters.

### ⚠️ rf-signals status (placeholder)

The upstream repo `thebracket/rf-signals` has been **unmaintained for
~5 years**, requires nightly Rust from 2020, an old Rocket release,
Google Maps API key, and LiDAR conversion via PROJ. The adapter in
this repo is wired into the registry for future revival but reports
`is_available() = False` until someone:

1. Forks `thebracket/rf-signals` and pins a buildable Rust toolchain.
2. Maps the actual public API in `rf-signal-algorithms/src/rfcalc/`
   (ITWOM3, HATA, COST/HATA, ECC33, EGLI, FSPL) onto the JSON shim
   schema documented in `rf_engines/rf_signals_engine.py` (`_call_subprocess`).
3. Validates outputs against ITU-R P.1812 on the coverage-diff golden
   set for at least 7 nights before flipping the engine on.

Until step 3 lands, treat any rf-signals output as research-only —
the `coverage-diff` workflow already excludes it via `is_available()`.

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

# rf-signals: NOT shipped — see "rf-signals status" above. To revive,
# fork the upstream crate, pin the toolchain, fix the JSON shim, then
# re-add a build_rf_signals.sh that actually compiles.
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

The `sionna` adapter ships disabled (`SIONNA_DISABLED=1`) and returns
`None`. Training pipeline lives under `scripts/train_sionna.py` (TBD)
and runs on a g5.xlarge spot fleet against SRTM + ANATEL drive-tests
+ MapBiomas clutter. Once a `sionna_model.tflite` artefact is uploaded
to S3 and `SIONNA_DISABLED=0` is set on the ECS task, the engine
registers itself automatically — no API code change required.

This is intentional: production traffic will not depend on a learned
predictor until it has been benchmarked against P.1812 on the
coverage-diff golden set for at least 14 consecutive nights.
