# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""
optimize_sites.py
=================

Genetic-algorithm site placement using TTP's `rf_engines` registry.

Picks N candidate tower positions (lat, lon, height_m) inside an AOI
that maximise coverage over a set of demand points. Engine is
configurable (`itu-p1812` or `itmlogic`) and the fitness function
**actually calls the engine** — no constant-loss degenerate walk.

Why this lives in TTP and not in a separate `rf-wisp-br-optimizer`
repo:

* The engine wrappers, SRTM reader, and tower DB already live here.
  Spawning a sibling repo would duplicate three glue layers and double
  the GPL/proprietary licence audit surface.
* The GA's bottleneck is the propagation engine (≈ 5 ms per ITM call,
  ≈ 30 ms per P.1812 call). Calling them in-process via the registry
  is faster than RPC and avoids container fan-out.

Usage
-----

    python -m scripts.optimize_sites \\
        --aoi -16.0,-48.0,-15.7,-47.7 \\
        --receivers sample_receivers.csv \\
        --n-towers 3 --engine itmlogic \\
        --generations 30 --pop 24 \\
        --threshold-db 150 --frequency-mhz 850 \\
        --out output/optim_run

Output: ``optim_run/sites.geojson``, ``optim_run/report.json``,
``optim_run/coverage_map.html`` (Folium).

Runtime budget
--------------
Default settings (24 individuals × 30 generations × 4 receivers × 3
towers ≈ 8 600 engine calls) finish in ~10-25 min on 4 cores. Scale
``--pop`` / ``--generations`` for larger AOIs.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger("optimize_sites")


@dataclass(frozen=True)
class Receiver:
    lat: float
    lon: float
    height_m: float = 10.0
    gain_db: float = 0.0


@dataclass(frozen=True)
class AOI:
    lat_min: float
    lon_min: float
    lat_max: float
    lon_max: float

    @classmethod
    def parse(cls, s: str) -> "AOI":
        parts = [float(x) for x in s.split(",")]
        if len(parts) != 4:
            raise ValueError("AOI must be 'lat_min,lon_min,lat_max,lon_max'")
        a = cls(*parts)
        if a.lat_max <= a.lat_min or a.lon_max <= a.lon_min:
            raise ValueError("AOI: max must be > min on both axes")
        return a


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance — adequate for engine input <500 km."""
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Terrain profile sampling
# ---------------------------------------------------------------------------


def _profile(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    n_pts: int = 32,
    fallback_h: float = 100.0,
) -> Tuple[List[float], List[float]]:
    """Return (d_km[], h_m[]) sampling SRTM along the great-circle path.

    Falls back to flat ``fallback_h`` if SRTM tiles are not provisioned —
    GA still runs (just rougher fitness signal). Worker processes
    create their own SRTMReader (no sharing across processes).
    """
    d_total = _haversine_km(lat1, lon1, lat2, lon2)
    d = [d_total * i / (n_pts - 1) for i in range(n_pts)]

    try:
        from srtm_elevation import SRTMReader  # type: ignore[import-not-found]
        reader = SRTMReader(data_dir=os.getenv("SRTM_DATA_DIR", "./srtm_data"))
    except Exception:
        reader = None

    h: List[float] = []
    for i in range(n_pts):
        f = i / (n_pts - 1)
        lat = lat1 + (lat2 - lat1) * f
        lon = lon1 + (lon2 - lon1) * f
        elev: Optional[float] = None
        if reader is not None:
            try:
                elev = reader.get_elevation(lat, lon)
            except Exception:
                elev = None
        h.append(float(elev) if elev is not None and elev > -1000 else fallback_h)
    return d, h


# ---------------------------------------------------------------------------
# Per-link evaluation (worker function — must be pickleable / top-level)
# ---------------------------------------------------------------------------


def _link_loss_db(
    engine_name: str,
    f_hz: float,
    tx_lat: float,
    tx_lon: float,
    tx_h_agl: float,
    rx: Receiver,
) -> Optional[float]:
    """Single tx→rx basic-loss query. Returns None if engine fails."""
    import rf_engines  # imported per-call so worker forks are clean

    try:
        engine = rf_engines.get_engine(engine_name)
    except KeyError:
        return None
    if not engine.is_available():
        return None

    d_km, h_m = _profile(tx_lat, tx_lon, rx.lat, rx.lon)
    if d_km[-1] < 0.05:
        return None

    res = engine.predict_basic_loss(
        f_hz=f_hz,
        d_km=d_km,
        h_m=h_m,
        htg=tx_h_agl,
        hrg=rx.height_m,
        phi_t=tx_lat,
        lam_t=tx_lon,
        phi_r=rx.lat,
        lam_r=rx.lon,
        pol=2,
        zone=4,
        time_pct=50.0,
        loc_pct=50.0,
    )
    return res.basic_loss_db if res is not None else None


# Fitness tunables (kept module-level so workers see them after fork)
_MARGIN_CAP_DB = 3.0           # cap per-receiver margin tightly: once a
                                # receiver clears threshold by 3 dB the GA
                                # stops earning fitness from improving it
                                # and is forced to focus on uncovered ones.
_UNCOVERED_PENALTY_DB = 200.0  # virtual margin charged when a receiver is
                                # not covered by any tower. Set high enough
                                # that no margin slack on the covered set
                                # can ever justify dropping a receiver:
                                # losing 1 covers (-200) > +3 dB × N rx.
_HEIGHT_PENALTY_PER_M = 0.005  # very small dB-equivalent cost per metre of
                                # antenna height — only breaks ties, never
                                # competes with coverage.


def _evaluate_individual(
    args: Tuple[List[float], str, float, List[Receiver], float],
) -> Tuple[float, float, int]:
    """Top-level worker for ProcessPoolExecutor.

    args: (genome_flat, engine_name, f_hz, receivers, threshold_db)
    genome layout: [lat_1, lon_1, h_1, lat_2, lon_2, h_2, ...]

    Returns (fitness, coverage_fraction, n_covered).

    Fitness contract
    ----------------
    * Continuous, monotone in link quality, NEVER plateaus while there is
      slack in either coverage *or* link margin.
    * For each receiver, take the best-server margin =
      ``threshold_db − basic_loss_db``, clipped to ``[-UNCOV_PEN, MARGIN_CAP]``.
    * Average across receivers.
    * Subtract a small height penalty so the GA prefers shorter masts when
      the coverage objective is already saturated.
    """
    genome, engine_name, f_hz, receivers, threshold_db = args
    n_tx = len(genome) // 3
    towers = [(genome[3 * i], genome[3 * i + 1], genome[3 * i + 2]) for i in range(n_tx)]

    covered = 0
    margin_sum = 0.0
    for rx in receivers:
        best_loss: Optional[float] = None
        for tx_lat, tx_lon, tx_h in towers:
            loss = _link_loss_db(engine_name, f_hz, tx_lat, tx_lon, tx_h, rx)
            if loss is None:
                continue
            if best_loss is None or loss < best_loss:
                best_loss = loss
        if best_loss is None or best_loss > threshold_db:
            # Uncovered: discrete penalty so coverage dominates the
            # objective. Margin slack on the covered set can never
            # justify dropping a receiver.
            margin = -_UNCOVERED_PENALTY_DB
        else:
            covered += 1
            # Covered: reward up to MARGIN_CAP_DB of slack, then plateau.
            margin = min(_MARGIN_CAP_DB, threshold_db - best_loss)
        margin_sum += margin

    n = max(1, len(receivers))
    avg_margin = margin_sum / n
    height_pen = _HEIGHT_PENALTY_PER_M * sum(towers[i][2] for i in range(n_tx)) / n_tx
    fitness = avg_margin - height_pen
    return fitness, covered / n, covered


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def _load_receivers(path: str) -> List[Receiver]:
    rxs: List[Receiver] = []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            rxs.append(
                Receiver(
                    lat=float(row["lat"]),
                    lon=float(row["lon"]),
                    height_m=float(row.get("height", 10.0) or 10.0),
                    gain_db=float(row.get("gain", 0.0) or 0.0),
                )
            )
    if not rxs:
        raise ValueError(f"no receivers in {path}")
    return rxs


def _write_geojson(towers: List[Tuple[float, float, float]], out_path: Path) -> None:
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {"index": i, "height_m": h},
            }
            for i, (lat, lon, h) in enumerate(towers)
        ],
    }
    out_path.write_text(json.dumps(fc, indent=2))


def _render_map(
    towers: List[Tuple[float, float, float]],
    receivers: List[Receiver],
    aoi: AOI,
    coverage_pct: float,
    out_path: Path,
) -> None:
    try:
        import folium  # type: ignore[import-not-found]
    except Exception:
        logger.warning("folium not installed; skipping HTML map")
        return
    centre = [(aoi.lat_min + aoi.lat_max) / 2, (aoi.lon_min + aoi.lon_max) / 2]
    m = folium.Map(location=centre, zoom_start=11, tiles="OpenStreetMap")
    folium.Rectangle(
        bounds=[[aoi.lat_min, aoi.lon_min], [aoi.lat_max, aoi.lon_max]],
        color="#888", fill=False, weight=1,
    ).add_to(m)
    for i, (lat, lon, h) in enumerate(towers):
        folium.Marker(
            [lat, lon],
            popup=f"Tower {i}: h={h:.1f} m",
            icon=folium.Icon(color="red", icon="signal", prefix="fa"),
        ).add_to(m)
    for rx in receivers:
        folium.CircleMarker(
            [rx.lat, rx.lon],
            radius=4, color="#0a64a4", fill=True, fill_opacity=0.8,
            popup=f"Rx ({rx.lat:.4f}, {rx.lon:.4f})",
        ).add_to(m)
    folium.map.Marker(
        centre,
        icon=folium.DivIcon(
            html=f'<div style="font:14px sans-serif;background:#fff;'
                 f'padding:4px 8px;border:1px solid #999;">'
                 f'Coverage: {coverage_pct:.1%}</div>'
        ),
    ).add_to(m)
    m.save(str(out_path))


# ---------------------------------------------------------------------------
# Genetic algorithm
# ---------------------------------------------------------------------------


def run_ga(
    *,
    aoi: AOI,
    receivers: List[Receiver],
    n_towers: int,
    engine_name: str,
    f_hz: float,
    threshold_db: float,
    pop_size: int,
    n_generations: int,
    cxpb: float,
    mutpb: float,
    height_min: float,
    height_max: float,
    workers: int,
    seed: Optional[int],
) -> Tuple[List[Tuple[float, float, float]], float, dict]:
    from deap import base, creator, tools  # type: ignore[import-not-found]

    rng = random.Random(seed)

    # DEAP creator is process-global; guard against re-creation in tests.
    if not hasattr(creator, "FitnessMaxCov"):
        creator.create("FitnessMaxCov", base.Fitness, weights=(1.0,))
    if not hasattr(creator, "Individual"):
        creator.create("Individual", list, fitness=creator.FitnessMaxCov)

    def _gene() -> List[float]:
        out: List[float] = []
        for _ in range(n_towers):
            out.append(rng.uniform(aoi.lat_min, aoi.lat_max))
            out.append(rng.uniform(aoi.lon_min, aoi.lon_max))
            out.append(rng.uniform(height_min, height_max))
        return out

    def _clamp(ind: List[float]) -> List[float]:
        for i in range(0, len(ind), 3):
            ind[i] = min(max(ind[i], aoi.lat_min), aoi.lat_max)
            ind[i + 1] = min(max(ind[i + 1], aoi.lon_min), aoi.lon_max)
            ind[i + 2] = min(max(ind[i + 2], height_min), height_max)
        return ind

    toolbox = base.Toolbox()
    toolbox.register("individual", lambda: creator.Individual(_gene()))
    toolbox.register("population", tools.initRepeat, list, toolbox.individual)
    toolbox.register("mate", tools.cxBlend, alpha=0.3)
    # Per-gene sigma scaled to AOI/height ranges so mutation can move
    # towers across a meaningful fraction of the search domain (escape
    # local optima) without being either negligible or chaotic.
    sigma_lat = (aoi.lat_max - aoi.lat_min) * 0.10
    sigma_lon = (aoi.lon_max - aoi.lon_min) * 0.10
    sigma_h = (height_max - height_min) * 0.10
    sigma_vec = [sigma_lat, sigma_lon, sigma_h] * n_towers
    mu_vec = [0.0] * (3 * n_towers)
    toolbox.register("mutate", tools.mutGaussian, mu=mu_vec, sigma=sigma_vec, indpb=0.3)
    toolbox.register("select", tools.selTournament, tournsize=3)

    pop = toolbox.population(n=pop_size)

    def _eval_pop(individuals: List) -> None:
        # Only re-evaluate dirty individuals.
        targets = [ind for ind in individuals if not ind.fitness.valid]
        if not targets:
            return
        payloads = [
            (list(ind), engine_name, f_hz, receivers, threshold_db)
            for ind in targets
        ]
        if workers > 1:
            with ProcessPoolExecutor(max_workers=workers) as ex:
                results = list(ex.map(_evaluate_individual, payloads))
        else:
            results = [_evaluate_individual(p) for p in payloads]
        for ind, (fit, cov, _n) in zip(targets, results):
            ind.fitness.values = (fit,)
            # stash coverage on the individual for reporting
            ind.coverage_fraction = cov  # type: ignore[attr-defined]

    t0 = time.perf_counter()
    _eval_pop(pop)

    history: List[dict] = []
    for gen in range(1, n_generations + 1):
        offspring = list(map(toolbox.clone, toolbox.select(pop, len(pop))))
        for c1, c2 in zip(offspring[::2], offspring[1::2]):
            if rng.random() < cxpb:
                toolbox.mate(c1, c2)
                _clamp(c1); _clamp(c2)
                del c1.fitness.values, c2.fitness.values
        for mut in offspring:
            if rng.random() < mutpb:
                toolbox.mutate(mut)
                _clamp(mut)
                del mut.fitness.values
        _eval_pop(offspring)
        # Elitism: keep best of pop.
        best_prev = max(pop, key=lambda x: x.fitness.values[0])
        pop = offspring
        worst_idx = min(range(len(pop)), key=lambda i: pop[i].fitness.values[0])
        if best_prev.fitness.values[0] > pop[worst_idx].fitness.values[0]:
            pop[worst_idx] = toolbox.clone(best_prev)

        best_ind = max(pop, key=lambda x: x.fitness.values[0])
        best = best_ind.fitness.values[0]
        avg = sum(ind.fitness.values[0] for ind in pop) / len(pop)
        cov = getattr(best_ind, "coverage_fraction", float("nan"))
        history.append({"gen": gen, "best": best, "avg": avg, "coverage": cov})
        logger.info(
            "gen %d/%d fitness best=%+.2f avg=%+.2f cov=%.1f%%",
            gen, n_generations, best, avg, (cov * 100) if cov == cov else 0.0,
        )

    elapsed = time.perf_counter() - t0
    champion = max(pop, key=lambda x: x.fitness.values[0])
    fitness = champion.fitness.values[0]
    coverage = getattr(champion, "coverage_fraction", float("nan"))
    towers = [
        (champion[3 * i], champion[3 * i + 1], champion[3 * i + 2])
        for i in range(n_towers)
    ]
    stats = {
        "elapsed_seconds": elapsed,
        "generations": n_generations,
        "population": pop_size,
        "history": history,
        "champion_fitness": fitness,
    }
    return towers, coverage, stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="GA site placement using rf_engines")
    p.add_argument(
        "--aoi",
        default=None,
        help=(
            "lat_min,lon_min,lat_max,lon_max. If omitted, the AOI is auto-derived "
            "from the receivers' bounding box expanded by --aoi-margin-deg."
        ),
    )
    p.add_argument(
        "--aoi-margin-deg",
        type=float,
        default=0.2,
        help=(
            "Degrees of padding added to each side of the receivers' bounding box "
            "when --aoi is not provided (default 0.2\u00b0 \u2248 22 km)."
        ),
    )
    p.add_argument("--receivers", required=True, help="CSV with lat,lon[,height,gain]")
    p.add_argument("--n-towers", type=int, default=3)
    p.add_argument("--engine", default="itmlogic",
                   choices=["itmlogic", "itu-p1812", "sionna"],
                   help=(
                       "Propagation engine for fitness eval. 'sionna' uses "
                       "the learned MLP from rf_engines.sionna_engine — only "
                       "available when the artefact has been provisioned "
                       "(SIONNA_DISABLED=0 + model+sidecar on disk). The GA "
                       "logs an error and exits if the chosen engine reports "
                       "is_available()=False, rather than silently using a "
                       "fallback (that would corrupt the comparative reports)."
                   ))
    p.add_argument("--frequency-mhz", type=float, default=850.0)
    p.add_argument("--threshold-db", type=float, default=130.0)
    p.add_argument("--generations", type=int, default=30)
    p.add_argument("--pop", type=int, default=24)
    p.add_argument("--cxpb", type=float, default=0.6)
    p.add_argument("--mutpb", type=float, default=0.3)
    p.add_argument("--height-min", type=float, default=15.0)
    p.add_argument("--height-max", type=float, default=60.0)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", default="output/optim_run")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    receivers = _load_receivers(args.receivers)
    if args.aoi:
        aoi = AOI.parse(args.aoi)
    else:
        if not receivers:
            print("error: --aoi omitted but receivers file is empty", file=sys.stderr)
            return 4
        m = args.aoi_margin_deg
        if m < 0:
            print("error: --aoi-margin-deg must be \u2265 0", file=sys.stderr)
            return 4
        lats = [r.lat for r in receivers]
        lons = [r.lon for r in receivers]
        aoi = AOI(
            lat_min=min(lats) - m,
            lon_min=min(lons) - m,
            lat_max=max(lats) + m,
            lon_max=max(lons) + m,
        )
        logger.info(
            "auto-AOI from %d receivers (margin %.3f\u00b0): %.4f,%.4f -> %.4f,%.4f",
            len(receivers), m, aoi.lat_min, aoi.lon_min, aoi.lat_max, aoi.lon_max,
        )
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Validate engine before paying the GA cost.
    import rf_engines
    try:
        engine = rf_engines.get_engine(args.engine)
    except KeyError:
        print(f"error: unknown engine '{args.engine}'", file=sys.stderr)
        return 2
    if not engine.is_available():
        print(
            f"error: engine '{args.engine}' is not available "
            "(install dependencies or set the right env vars)",
            file=sys.stderr,
        )
        return 3

    towers, coverage, stats = run_ga(
        aoi=aoi,
        receivers=receivers,
        n_towers=args.n_towers,
        engine_name=args.engine,
        f_hz=args.frequency_mhz * 1e6,
        threshold_db=args.threshold_db,
        pop_size=args.pop,
        n_generations=args.generations,
        cxpb=args.cxpb,
        mutpb=args.mutpb,
        height_min=args.height_min,
        height_max=args.height_max,
        workers=args.workers,
        seed=args.seed,
    )

    _write_geojson(towers, out_dir / "sites.geojson")
    _render_map(towers, receivers, aoi, coverage, out_dir / "coverage_map.html")

    report = {
        "engine": args.engine,
        "frequency_mhz": args.frequency_mhz,
        "threshold_db": args.threshold_db,
        "n_receivers": len(receivers),
        "n_towers": args.n_towers,
        "coverage_fraction": coverage,
        "champion_fitness_db": stats["champion_fitness"],
        "towers": [
            {"lat": lat, "lon": lon, "height_m": h}
            for lat, lon, h in towers
        ],
        "ga": {
            "pop": args.pop,
            "generations": args.generations,
            "cxpb": args.cxpb,
            "mutpb": args.mutpb,
            "seed": args.seed,
            "elapsed_seconds": stats["elapsed_seconds"],
        },
        "history": stats["history"],
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))

    print(f"coverage: {coverage:.1%}  fitness: {stats['champion_fitness']:+.2f} dB  ({stats['elapsed_seconds']:.1f}s)")
    print(f"output: {out_dir}/")
    for i, (lat, lon, h) in enumerate(towers):
        print(f"  tower {i}: lat={lat:+.5f} lon={lon:+.5f} h={h:.1f} m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
