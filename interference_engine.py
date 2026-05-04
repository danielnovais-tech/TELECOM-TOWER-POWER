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
