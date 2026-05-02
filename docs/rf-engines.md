# RF Engines — open-source propagation stack

Pluggable adapters that let TELECOM-TOWER-POWER run third-party
propagation models alongside the built-in physics + ridge model and
ITU-R P.1812 wrapper, without any commercial licence.

## Engines shipped

| Engine          | Source                                       | Licence    | Status            |
| --------------- | -------------------------------------------- | ---------- | ----------------- |
| `itu-p1812`     | `eeveetza/Py1812` (already in platform)      | MIT        | **available**     |
| `rf-signals`    | [`thebracket/rf-signals`][rfs]               | MIT        | needs build (Rust)|
| `signal-server` | [`Cloud-RF/Signal-Server`][css]              | **GPLv3**  | needs build (C++) |
| `sionna`        | NVIDIA Sionna (learned model)                | Apache 2.0 | scaffolding only  |

[rfs]: https://github.com/thebracket/rf-signals
[css]: https://github.com/Cloud-RF/Signal-Server

GPLv3 reminder: the Signal-Server binary **must not** be bundled into
the platform's container images. Operators build it on the host (or a
sidecar) via [scripts/build_signal_server.sh](../scripts/build_signal_server.sh)
and provision it via S3 — same posture as the ITU digital maps and
MapBiomas raster.

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
# rf-signals (Rust) — ~5 minutes, no root needed
bash scripts/build_rf_signals.sh ~/.local

# Signal-Server (C++, GPLv3) — ~3 minutes
bash scripts/build_signal_server.sh ~/.local

# Then on the ECS task:
export RF_SIGNALS_BIN=/usr/local/bin/rfsignals-cli
export SIGNAL_SERVER_BIN=/usr/local/bin/signalserverHD
```

`GET /coverage/engines` will start reporting `available=true` for each
engine whose binary is on `$PATH` (or whose explicit env var resolves).

## Companion automation

* **QGIS → Atoll exporter**: [scripts/qgis_to_atoll.py](../scripts/qgis_to_atoll.py)
  emits Atoll-native `sites.txt` / `transmitters.txt` / `terrain.bil` /
  `clutter.bil` plus a `.qgs` project for visual QA. Lets Atoll users
  validate TTP predictions side-by-side with their commercial licence.
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
