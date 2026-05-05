# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""GPU AWS Batch worker for ``engine='sionna-rt'`` interference jobs (T19).

Runs as a single-shot container on an AWS Batch GPU job queue. The
API submits the job via :func:`telecom_tower_power_api._submit_gpu_batch_job`;
this script is the container's entrypoint.

Expected invocation
-------------------
    JOB_ID=<uuid> JOB_TIER=<tier> python -m batch_gpu_interference_worker

Or the Batch job definition can pass ``["python", "-m",
"batch_gpu_interference_worker", "<job_id>", "<tier>"]`` — the script
prefers ``argv`` and falls back to env vars.

What it does (mirrors :func:`sqs_lambda_worker._process_interference_job`
but runs Sionna RT instead of FSPL):

1. Fetches the job row (``batch_jobs`` table) using the same DB path
   the SQS Lambda uses, and parses the embedded request + candidate
   list (sentinel ``tower_id='__interference__'``).
2. Marks the job ``running``.
3. Builds ``rf_engines.interference_engine._Aggressor`` records for
   each candidate and runs :class:`SionnaRTInterferenceHandler` over
   the full set.
4. Aggregates I/N + SINR + top-N using the same pure-math helpers
   (``aggregate_interference_dbm``, ``thermal_noise_dbm``,
   ``i_over_n_db``, ``sinr_db``, ``top_n_contributions``) so the FSPL
   and sionna-rt response shapes are identical (only ``engine`` and
   ``n_path_loss_failures`` differ).
5. Uploads ``result.json`` to the same S3 prefix the Lambda uses
   (``{S3_PREFIX}{tier}/{job_id}/result.json``) and marks the job
   ``completed`` with ``result_path=s3://...``.

Failure modes:
  * Job missing → exit non-zero with ``'job not found'`` log; Batch
    surfaces the failure but the DB row is left untouched.
  * Engine unavailable (scene missing, mitsuba import failed) →
    job marked ``failed`` and the process exits 1 so Batch retry
    semantics kick in.
  * Per-aggressor ray-solver failures are *not* fatal — the handler
    counts them in ``n_path_loss_failures`` and the response surfaces
    that field for operator diagnostics.
"""
from __future__ import annotations

import json
import logging
import math
import os
import sys
import time
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, NoReturn, Optional, Protocol, cast

if TYPE_CHECKING:
    from interference_engine import InterferenceContribution

# Reuse all DB / S3 plumbing from the Lambda worker — same env vars,
# same fall-through (sqlite local, RDS Proxy in prod). Importing it
# does NOT trigger the Lambda handler.
import sqs_lambda_worker as _w  # noqa: E402


class _UpdateJobStatusFn(Protocol):
    def __call__(self, job_id: str, status: str, **kwargs: Any) -> None: ...


class _FetchJobFn(Protocol):
    def __call__(self, job_id: str) -> Optional[Dict[str, Any]]: ...


class _PutObjectClient(Protocol):
    def put_object(self, **kwargs: Any) -> Any: ...


_UPDATE_JOB_STATUS = cast(_UpdateJobStatusFn, getattr(_w, "_update_job_status"))
_FETCH_JOB = cast(_FetchJobFn, getattr(_w, "_fetch_job"))
_GET_S3 = cast(Callable[[], _PutObjectClient], getattr(_w, "_get_s3"))

logger = logging.getLogger("batch_gpu_interference_worker")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def _resolve_job_id_and_tier(argv: List[str]) -> tuple[str, str]:
    """Prefer argv, fall back to env vars.

    Batch job definitions vary — some pass parameters as command
    overrides, others as environment variables. Support both so the
    deployment template can pick whichever is convenient.
    """
    job_id = ""
    tier = ""
    if len(argv) >= 2:
        job_id = argv[1]
    if len(argv) >= 3:
        tier = argv[2]
    job_id = job_id or os.environ.get("JOB_ID", "")
    tier = tier or os.environ.get("JOB_TIER", "")
    if not job_id:
        raise SystemExit(
            "JOB_ID is required: pass as argv[1] or set $JOB_ID. "
            "Batch job submission must include one of these.")
    return job_id, tier


def _fail(job_id: str, message: str, *, exc_info: bool = False) -> NoReturn:
    """Mark job failed and exit non-zero so Batch records the failure."""
    logger.error("Interference job %s failed: %s", job_id,
                 message, exc_info=exc_info)
    try:
        _UPDATE_JOB_STATUS(job_id, "failed", error=message[:512])
    except Exception:  # pragma: no cover — DB outage shouldn't mask the real error
        logger.exception(
            "Could not mark job %s as failed in DB", job_id)
    sys.exit(1)


def _build_response(
    *,
    victim: Mapping[str, Any],
    request_body: Mapping[str, Any],
    contributions: List["InterferenceContribution"],
    operator_by_id: Dict[str, str],
    n_candidates: int,
    n_in_radius: int,
    n_path_loss_failures: int,
    n_filtered_by_plmn: int = 0,
    runtime_ms: float,
) -> Dict[str, Any]:
    """Format the same response shape as the FSPL worker.

    Pure-math; no AWS calls. Kept inline (rather than importing from
    sqs_lambda_worker) because the FSPL formatter takes a
    ``InterferenceComputation`` bundle while the sionna-rt path has
    raw contributions + an extra failure counter.
    """
    from interference_engine import (
        aggregate_interference_dbm,
        i_over_n_db as _i_over_n,
        sinr_db as _sinr,
        thermal_noise_dbm,
        top_n_contributions,
    )

    victim_f_hz = float(victim["freq_mhz"]) * 1e6
    victim_bw_hz = float(victim["bw_mhz"]) * 1e6
    noise_figure_db = float(victim.get("noise_figure_db", 5.0))

    co_count = sum(1 for c in contributions if c.aci_db == 0.0)
    adj_count = sum(
        1 for c in contributions
        if c.aci_db != 0.0 and math.isfinite(c.rx_power_dbm)
    )

    i_dbm = aggregate_interference_dbm(contributions)
    n_dbm = thermal_noise_dbm(victim_bw_hz, noise_figure_db)
    i_n = _i_over_n(i_dbm, n_dbm)
    sinr = _sinr(victim.get("victim_signal_dbm"), i_dbm, n_dbm)

    top_n = int(request_body.get("top_n", 10))
    top = top_n_contributions(contributions, n=top_n)
    top_out: List[Dict[str, Any]] = []
    for c in top:
        delta_mhz = (c.aggressor_f_hz - victim_f_hz) / 1e6
        top_out.append({
            "aggressor_id": c.aggressor_id,
            "operator": operator_by_id.get(c.aggressor_id, "unknown"),
            "distance_km": round(c.distance_km, 3),
            "aggressor_freq_mhz": round(c.aggressor_f_hz / 1e6, 3),
            "aggressor_bw_mhz": round(c.aggressor_bw_hz / 1e6, 3),
            "delta_f_mhz": round(delta_mhz, 3),
            "eirp_dbm": round(c.eirp_dbm, 2),
            "path_loss_db": round(c.path_loss_db, 2),
            "aci_db": round(c.aci_db, 2),
            "rx_power_dbm": round(c.rx_power_dbm, 2),
            "plmn": c.plmn,
            "mimo_gain_db": round(c.mimo_gain_db, 2),
        })

    n_contrib = sum(1 for c in contributions if math.isfinite(c.rx_power_dbm))

    # T20 — MOCN aggregation maps.
    def _aggregate_by_label(
        values: List[InterferenceContribution],
        label_fn: Callable[[InterferenceContribution], str],
    ) -> Dict[str, float]:
        sums_mw: Dict[str, float] = {}
        for contribution in values:
            if not math.isfinite(contribution.rx_power_dbm):
                continue
            label = label_fn(contribution)
            sums_mw[label] = sums_mw.get(label, 0.0) + 10 ** (contribution.rx_power_dbm / 10.0)
        return {
            label: 10.0 * math.log10(total_mw)
            for label, total_mw in sums_mw.items()
            if total_mw > 0.0
        }

    agg_by_op = _aggregate_by_label(
        contributions,
        lambda contribution: operator_by_id.get(contribution.aggressor_id, "unknown"),
    )
    agg_by_plmn = _aggregate_by_label(
        contributions,
        lambda contribution: contribution.plmn or "unknown",
    )

    return {
        "victim": victim,
        "engine": "sionna-rt",
        "n_candidates": n_candidates,
        "n_in_radius": n_in_radius,
        "n_contributing": n_contrib,
        "n_path_loss_failures": n_path_loss_failures,
        "co_channel_count": co_count,
        "adjacent_channel_count": adj_count,
        "aggregate_i_dbm": (round(i_dbm, 2) if i_dbm is not None else None),
        "noise_dbm": round(n_dbm, 2),
        "i_over_n_db": (round(i_n, 2) if i_n is not None else None),
        "sinr_db": (round(sinr, 2) if sinr is not None else None),
        "top_n_aggressors": top_out,
        "runtime_ms": round(runtime_ms, 1),
        # T20 MOCN fields
        "n_filtered_by_plmn": n_filtered_by_plmn,
        "aggregate_by_operator_dbm": {k: round(v, 2) for k, v in agg_by_op.items()},
        "aggregate_by_plmn_dbm": {k: round(v, 2) for k, v in agg_by_plmn.items()},
    }


def run(job_id: str, tier: str = "") -> Dict[str, Any]:
    """End-to-end processing of a single GPU interference job.

    Returned dict is the same payload uploaded to S3, useful for
    in-process tests. Exits the interpreter on terminal errors via
    :func:`_fail`.
    """
    from interference_engine import haversine_km
    from rf_engines import interference_engine as _rt_intf
    from rf_engines.base import EngineUnavailable

    logger.info("GPU interference job %s starting (tier=%s)", job_id, tier)
    start = time.monotonic()
    _UPDATE_JOB_STATUS(job_id, "running")

    job = _FETCH_JOB(job_id)
    if job is None:
        _fail(job_id, "job not found")

    job_receivers = job.get("receivers")
    if not isinstance(job_receivers, str):
        _fail(job_id, "invalid job payload: receivers must be a JSON string")

    try:
        payload_obj: Any = json.loads(job_receivers)
    except (json.JSONDecodeError, TypeError) as exc:
        _fail(job_id, f"invalid job payload: {exc}")
    if not isinstance(payload_obj, dict):
        _fail(job_id, "invalid job payload: expected JSON object")

    payload = cast(Dict[str, Any], payload_obj)
    request_raw = payload.get("request")
    request_body = cast(Dict[str, Any], request_raw) if isinstance(request_raw, dict) else {}
    victim_raw = request_body.get("victim")
    victim_dict = cast(Dict[str, Any], victim_raw) if isinstance(victim_raw, dict) else {}
    candidates_value = payload.get("candidates")
    candidates_raw: List[Dict[str, Any]] = []
    if isinstance(candidates_value, list):
        for item in cast(List[Any], candidates_value):
            if isinstance(item, dict):
                candidates_raw.append(cast(Dict[str, Any], item))

    missing_victim_keys = [
        key for key in ("lat", "lon", "freq_mhz", "bw_mhz")
        if key not in victim_dict
    ]
    if missing_victim_keys:
        _fail(job_id, f"invalid victim payload: missing {', '.join(missing_victim_keys)}")

    try:
        handler = _rt_intf.SionnaRTInterferenceHandler()
    except Exception as exc:  # pragma: no cover — defensive
        _fail(job_id, f"sionna-rt handler init failed: {exc}", exc_info=True)
    if not handler.is_available():
        _fail(
            job_id,
            "sionna-rt engine unavailable on Batch container; "
            "check SIONNA_RT_DISABLED, SIONNA_RT_SCENE_PATH, mitsuba/sionna_rt imports",
        )

    victim_ctor = cast(Callable[..., Any], getattr(_rt_intf, "_Victim"))
    aggressor_ctor = cast(Callable[..., Any], getattr(_rt_intf, "_Aggressor"))

    victim = victim_ctor(
        lat=float(victim_dict["lat"]),
        lon=float(victim_dict["lon"]),
        height_m=float(victim_dict.get("rx_height_m", 1.5)),
        f_hz=float(victim_dict["freq_mhz"]) * 1e6,
        bw_hz=float(victim_dict["bw_mhz"]) * 1e6,
        rx_gain_dbi=float(victim_dict.get("rx_gain_dbi", 12.0)),
        plmn=cast(Optional[str], victim_dict.get("plmn")),
        n_rx_antennas=int((victim_dict.get("rx_mimo") or 1) or 1),
    )

    search_radius_km = float(request_body.get("search_radius_km", 30.0))
    aggressors: List[Any] = []
    operator_by_id: Dict[str, str] = {}
    for c in candidates_raw:
        try:
            aid = str(c["aggressor_id"])
            lat = float(c["lat"])
            lon = float(c["lon"])
        except (KeyError, ValueError, TypeError) as exc:
            _fail(job_id, f"invalid candidate record: {exc}")
        d_km = haversine_km(victim.lat, victim.lon, lat, lon)
        if d_km > search_radius_km or d_km <= 0.001:
            continue
        operator_by_id[aid] = str(c.get("operator", "unknown"))
        aggressors.append(aggressor_ctor(
            aggressor_id=aid,
            lat=lat,
            lon=lon,
            height_m=float(c.get("height_m", 0.0)),
            f_hz=float(c["f_hz"]),
            bw_hz=float(c["bw_hz"]),
            eirp_dbm=float(c["eirp_dbm"]),
            plmn=c.get("plmn") or None,
            n_tx_antennas=int(c.get("n_tx_antennas", 1) or 1),
        ))

    n_in_radius = len(aggressors)
    logger.info(
        "Running Sionna RT for %d aggressors (of %d candidates)",
        n_in_radius, len(candidates_raw),
    )

    try:
        result = handler.compute_contributions(
            victim=victim,
            aggressors=aggressors,
            include_aci=bool(request_body.get("include_aci", True)),
            aci_floor_db=request_body.get("aci_floor_db"),
            aggressor_plmn=request_body.get("aggressor_plmn"),
        )
    except EngineUnavailable as exc:
        _fail(job_id, f"sionna-rt unavailable mid-run: {exc}")
    except Exception as exc:  # pragma: no cover — Sionna RT internal crash
        _fail(job_id, f"sionna-rt compute failed: {exc}", exc_info=True)

    response = _build_response(
        victim=victim_dict,
        request_body=request_body,
        contributions=list(result.contributions),
        operator_by_id=operator_by_id,
        n_candidates=len(candidates_raw),
        n_in_radius=n_in_radius,
        n_path_loss_failures=result.n_path_loss_failures,
        n_filtered_by_plmn=result.n_filtered_by_plmn,
        runtime_ms=result.runtime_ms,
    )

    tier_segment = f"{tier}/" if tier else ""
    s3_key = f"{_w.S3_PREFIX}{tier_segment}{job_id}/result.json"
    _GET_S3().put_object(
        Bucket=_w.S3_BUCKET,
        Key=s3_key,
        Body=json.dumps(response).encode("utf-8"),
        ContentType="application/json",
    )
    s3_path = f"s3://{_w.S3_BUCKET}/{s3_key}"
    _UPDATE_JOB_STATUS(job_id, "completed", result_path=s3_path)

    elapsed = time.monotonic() - start
    logger.info(
        "GPU interference job %s completed: %d/%d aggressors traced (%d failed), %.2fs, %s",
        job_id, n_in_radius - result.n_path_loss_failures, n_in_radius,
        result.n_path_loss_failures, elapsed, s3_path,
    )
    return response


def main(argv: Optional[List[str]] = None) -> int:
    job_id, tier = _resolve_job_id_and_tier(list(argv if argv is not None else sys.argv))
    run(job_id, tier=tier)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
