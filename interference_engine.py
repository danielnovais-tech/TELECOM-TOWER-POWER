# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Aggregate co-channel + adjacent-channel interference helpers.

Pure functions — no I/O, no DB, no engine registry. Designed to be
called from the ``/coverage/interference`` endpoint and from offline
tooling (``scripts/coverage_diff_*`` and the Sionna RT validation
gate, T17+).

The path-loss to each aggressor is supplied by the caller (so the
endpoint can pick FSPL vs ITM vs ITU-R P.1812 vs Sionna RT without
this module knowing); we only do the spectral-mask attenuation,
linear-domain summation, thermal noise and SINR algebra.

ACI mask
--------
We use a piecewise mask inspired by 3GPP TS 36.104 §6.6.2.1 (E-UTRA
ACLR) and ITU-R M.2101 (Recommendation, IMT-2020 sharing studies).
Values are intentionally *conservative* — under-estimating aggressor
isolation so the service errs on the side of flagging interference.
The mask is parameterised in units of ``Δf / max(BW_v, BW_a)``
("normalised offset"):

* ``|Δ| < 0.5``      → 0 dB    (co-channel)
* ``0.5 ≤ |Δ| < 1.5`` → 30 dB   (1st adjacent)
* ``1.5 ≤ |Δ| < 2.5`` → 43 dB   (2nd adjacent)
* ``|Δ| ≥ 2.5``       → 60 dB   (far-out / oob)

These are basic-loss-equivalent attenuations applied **on top of**
the propagation loss. Operators with measured ACLR/ACS data should
override via the ``aci_floor_db`` argument.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, List, Optional


# kT in dBm/Hz at T = 290 K (standard reference temperature).
_KT_DBM_PER_HZ = -174.0


def thermal_noise_dbm(bw_hz: float, noise_figure_db: float = 5.0) -> float:
    """Receiver thermal noise floor.

    ``N = kTB + NF`` in dBm. ``noise_figure_db`` defaults to 5 dB which
    matches the typical 4G/5G UE/CPE budget (3GPP TS 36.101 §7.4 for
    Bands 1/3/41 give 9 dB, but consumer CPEs in the field usually
    measure 4-6 dB; we pick 5 as the median).
    """
    if bw_hz <= 0:
        raise ValueError(f"bw_hz must be > 0, got {bw_hz}")
    if noise_figure_db < 0:
        raise ValueError(f"noise_figure_db must be >= 0, got {noise_figure_db}")
    return _KT_DBM_PER_HZ + 10.0 * math.log10(bw_hz) + noise_figure_db


def aci_attenuation_db(
    victim_f_hz: float,
    victim_bw_hz: float,
    aggressor_f_hz: float,
    aggressor_bw_hz: float,
    *,
    aci_floor_db: Optional[float] = None,
) -> float:
    """Spectral-mask attenuation between a victim and an aggressor channel.

    Returns the attenuation in **dB** (a positive number) to apply on
    top of the path loss so that a co-channel aggressor returns 0 dB
    and a far-out aggressor returns the floor (default 60 dB).

    ``aci_floor_db`` overrides the far-out value (``|Δ| ≥ 2.5``) — useful
    for studies with measured RF filter rejection.
    """
    if victim_bw_hz <= 0 or aggressor_bw_hz <= 0:
        raise ValueError("bandwidths must be > 0")
    delta = abs(victim_f_hz - aggressor_f_hz)
    norm = max(victim_bw_hz, aggressor_bw_hz)
    x = delta / norm
    if x < 0.5:
        return 0.0
    if x < 1.5:
        return 30.0
    if x < 2.5:
        return 43.0
    return 60.0 if aci_floor_db is None else float(aci_floor_db)


@dataclass(frozen=True)
class InterferenceContribution:
    """One aggressor's per-pair contribution at the victim receiver."""

    aggressor_id: str
    distance_km: float
    aggressor_f_hz: float
    aggressor_bw_hz: float
    eirp_dbm: float
    path_loss_db: float
    aci_db: float
    rx_power_dbm: float


def aggregate_interference_dbm(
    contributions: Iterable[InterferenceContribution],
) -> Optional[float]:
    """Sum aggressor powers in linear domain and return the dBm result.

    Returns ``None`` when no contribution is finite (every aggressor was
    either out-of-mask floored or returned ``-inf``). The caller should
    treat ``None`` as "interference indistinguishable from noise" —
    ``I/N → -∞`` and SINR is just SNR.
    """
    lin_mw = 0.0
    n_finite = 0
    for c in contributions:
        if not math.isfinite(c.rx_power_dbm):
            continue
        lin_mw += 10.0 ** (c.rx_power_dbm / 10.0)
        n_finite += 1
    if n_finite == 0 or lin_mw <= 0:
        return None
    return 10.0 * math.log10(lin_mw)


def i_over_n_db(i_dbm: Optional[float], n_dbm: float) -> Optional[float]:
    """Return ``I/N`` in dB or ``None`` if ``I`` is undefined."""
    if i_dbm is None:
        return None
    return i_dbm - n_dbm


def sinr_db(
    s_dbm: Optional[float],
    i_dbm: Optional[float],
    n_dbm: float,
) -> Optional[float]:
    """Return SINR (dB) given victim signal, aggregate I and noise.

    SINR = S / (I + N), all linear, then 10·log10. ``None`` when the
    victim signal is unknown (caller didn't supply a ``victim_signal_dbm``).
    """
    if s_dbm is None:
        return None
    n_lin = 10.0 ** (n_dbm / 10.0)
    i_lin = 10.0 ** (i_dbm / 10.0) if i_dbm is not None else 0.0
    s_lin = 10.0 ** (s_dbm / 10.0)
    denom = i_lin + n_lin
    if denom <= 0:
        return None
    return 10.0 * math.log10(s_lin / denom)


def build_contribution(
    *,
    aggressor_id: str,
    distance_km: float,
    aggressor_f_hz: float,
    aggressor_bw_hz: float,
    aggressor_eirp_dbm: float,
    victim_f_hz: float,
    victim_bw_hz: float,
    rx_gain_dbi: float,
    path_loss_db: float,
    include_aci: bool = True,
    aci_floor_db: Optional[float] = None,
) -> InterferenceContribution:
    """Compose a single aggressor contribution at the victim receiver.

    ``Pr = EIRP - PL + Gr - ACI``  (all dB[m]).

    When ``include_aci=False`` the helper still drops adjacent-channel
    aggressors entirely (returns ``-inf`` rx_power for ``|Δf| ≥ BW/2``);
    set the flag if the caller already pre-filtered to co-channel only.
    """
    aci = aci_attenuation_db(
        victim_f_hz, victim_bw_hz, aggressor_f_hz, aggressor_bw_hz,
        aci_floor_db=aci_floor_db,
    )
    if not include_aci and aci > 0:
        # Caller wants co-channel only: hard-mute adjacent aggressors.
        rx_dbm = float("-inf")
    else:
        rx_dbm = aggressor_eirp_dbm - path_loss_db + rx_gain_dbi - aci
    return InterferenceContribution(
        aggressor_id=aggressor_id,
        distance_km=distance_km,
        aggressor_f_hz=aggressor_f_hz,
        aggressor_bw_hz=aggressor_bw_hz,
        eirp_dbm=aggressor_eirp_dbm,
        path_loss_db=path_loss_db,
        aci_db=aci,
        rx_power_dbm=rx_dbm,
    )


def top_n_contributions(
    contributions: List[InterferenceContribution],
    n: int = 10,
) -> List[InterferenceContribution]:
    """Return the ``n`` strongest-Rx aggressors, ordered descending."""
    finite = [c for c in contributions if math.isfinite(c.rx_power_dbm)]
    finite.sort(key=lambda c: c.rx_power_dbm, reverse=True)
    return finite[: max(0, int(n))]


# ─────────────────────────────────────────────────────────────────────
# Job-payload primitives (T18 SQS async path)
# ─────────────────────────────────────────────────────────────────────
#
# The synchronous endpoint resolves candidate towers from the DB and
# computes the contributions in-process. The async path (T18) splits
# that work in two:
#
#   1. The API submits the job — same DB lookup happens at submit-time
#      so the request payload is fully self-contained. The candidates
#      list is captured inline so the SQS worker doesn't need DB access
#      (RDS Proxy IAM is only attached to the PDF Lambda; an interference
#      worker can run on cheaper bare Lambda).
#   2. The worker dequeues, runs the math, uploads JSON to S3.
#
# The serialized job payload schema is intentionally minimal — every
# field the worker needs to recompute the response, nothing more.

@dataclass(frozen=True)
class CandidateAggressor:
    """Tower record reduced to the fields the math needs.

    Built at submit-time from ``models.Tower`` rows so the worker has
    no SQLAlchemy / DB dependency.
    """

    aggressor_id: str
    operator: str
    lat: float
    lon: float
    height_m: float
    f_hz: float
    bw_hz: float
    eirp_dbm: float

    def to_dict(self) -> dict:
        return {
            "aggressor_id": self.aggressor_id,
            "operator": self.operator,
            "lat": self.lat,
            "lon": self.lon,
            "height_m": self.height_m,
            "f_hz": self.f_hz,
            "bw_hz": self.bw_hz,
            "eirp_dbm": self.eirp_dbm,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CandidateAggressor":
        return cls(
            aggressor_id=str(d["aggressor_id"]),
            operator=str(d.get("operator", "unknown")),
            lat=float(d["lat"]),
            lon=float(d["lon"]),
            height_m=float(d.get("height_m", 0.0) or 0.0),
            f_hz=float(d["f_hz"]),
            bw_hz=float(d["bw_hz"]),
            eirp_dbm=float(d["eirp_dbm"]),
        )


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (km). Local copy so this module stays
    importable in worker contexts that don't ship the FastAPI app."""
    R = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return float(2 * R * math.asin(min(1.0, math.sqrt(a))))


def _free_space_path_loss_db(d_km: float, f_hz: float) -> float:
    """FSPL in dB. Mirrors ``LinkEngine.free_space_path_loss``."""
    d_m = d_km * 1000.0
    return 20.0 * math.log10(d_m) + 20.0 * math.log10(f_hz) - 147.55


@dataclass(frozen=True)
class InterferenceComputation:
    """Result bundle returned by :func:`compute_interference_fspl`.

    The endpoint / worker map this onto the public response schema.
    """

    contributions: List[InterferenceContribution]
    aggregate_i_dbm: Optional[float]
    noise_dbm: float
    i_over_n_db: Optional[float]
    sinr_db: Optional[float]
    co_channel_count: int
    adjacent_channel_count: int
    n_in_radius: int
    n_contributing: int
    operator_by_id: dict


def compute_interference_fspl(
    *,
    victim_lat: float,
    victim_lon: float,
    victim_f_hz: float,
    victim_bw_hz: float,
    victim_rx_gain_dbi: float,
    victim_signal_dbm: Optional[float],
    noise_figure_db: float,
    candidates: Iterable[CandidateAggressor],
    search_radius_km: float,
    include_aci: bool = True,
    aci_floor_db: Optional[float] = None,
) -> InterferenceComputation:
    """Run the FSPL pipeline for a victim + candidate fleet.

    Pure function — no DB, no I/O. Used by the synchronous
    ``/coverage/interference`` endpoint and by the SQS worker that
    handles the async path. Sionna RT path-loss is *not* computed here
    (the worker for that lives in ``rf_engines.interference_engine``);
    when the engine is FSPL the math collapses to closed-form which is
    fast enough that the async path exists mainly for very large
    ``search_radius_km`` sweeps where 200+ aggressors push the
    sync timeout budget.
    """
    contributions: List[InterferenceContribution] = []
    operator_by_id: dict = {}
    co_count = 0
    adj_count = 0
    in_radius = 0
    for c in candidates:
        d_km = haversine_km(victim_lat, victim_lon, c.lat, c.lon)
        if d_km > search_radius_km or d_km <= 0.001:
            continue
        in_radius += 1
        pl_db = _free_space_path_loss_db(d_km, c.f_hz)
        contrib = build_contribution(
            aggressor_id=c.aggressor_id,
            distance_km=d_km,
            aggressor_f_hz=c.f_hz,
            aggressor_bw_hz=c.bw_hz,
            aggressor_eirp_dbm=c.eirp_dbm,
            victim_f_hz=victim_f_hz,
            victim_bw_hz=victim_bw_hz,
            rx_gain_dbi=victim_rx_gain_dbi,
            path_loss_db=pl_db,
            include_aci=include_aci,
            aci_floor_db=aci_floor_db,
        )
        if contrib.aci_db == 0.0:
            co_count += 1
        elif math.isfinite(contrib.rx_power_dbm):
            adj_count += 1
        contributions.append(contrib)
        operator_by_id[contrib.aggressor_id] = c.operator

    i_dbm = aggregate_interference_dbm(contributions)
    n_dbm = thermal_noise_dbm(victim_bw_hz, noise_figure_db)
    i_n = i_over_n_db(i_dbm, n_dbm)
    sinr = sinr_db(victim_signal_dbm, i_dbm, n_dbm)
    n_contrib = sum(1 for c in contributions if math.isfinite(c.rx_power_dbm))

    return InterferenceComputation(
        contributions=contributions,
        aggregate_i_dbm=i_dbm,
        noise_dbm=n_dbm,
        i_over_n_db=i_n,
        sinr_db=sinr,
        co_channel_count=co_count,
        adjacent_channel_count=adj_count,
        n_in_radius=in_radius,
        n_contributing=n_contrib,
        operator_by_id=operator_by_id,
    )
