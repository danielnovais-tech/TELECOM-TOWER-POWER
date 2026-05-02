# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""A/B comparison helper for RF engines.

Used at runtime by ``/coverage/engines/compare`` and offline by the
``coverage-diff`` GitHub Actions robot. The comparator runs N engines
on identical inputs and returns dB deltas relative to a reference
engine (default: ``itu-p1812``) — the same statistic the FCC and
ANATEL use when validating new propagation models against P.1812.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence

from . import get_engine, list_engines
from .base import LossEstimate, RFEngine


@dataclass
class ComparisonRow:
    engine: str
    available: bool
    basic_loss_db: Optional[float]
    confidence: Optional[float]
    runtime_ms: Optional[float]
    delta_db: Optional[float]
    """Difference vs. reference engine. Positive = more pessimistic
    (higher loss) than reference."""
    extra: dict = field(default_factory=dict)


@dataclass
class ComparisonResult:
    reference: str
    rows: List[ComparisonRow]

    def to_dict(self) -> dict:
        return {"reference": self.reference, "rows": [asdict(r) for r in self.rows]}


def _run_one(
    engine: RFEngine, kwargs: dict
) -> tuple[Optional[LossEstimate], float]:
    t0 = time.perf_counter()
    try:
        est = engine.predict_basic_loss(**kwargs)
    except Exception:  # pragma: no cover — engines must fail closed
        est = None
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return est, elapsed_ms


def compare(
    *,
    engine_names: Optional[Sequence[str]] = None,
    reference: str = "itu-p1812",
    **link_kwargs,
) -> ComparisonResult:
    """Run ``engine_names`` (default: all available) on the same link.

    ``link_kwargs`` is forwarded verbatim to
    :meth:`RFEngine.predict_basic_loss`.
    """
    engines: List[RFEngine]
    if engine_names is None:
        engines = list_engines()
    else:
        engines = [get_engine(n) for n in engine_names]

    # Reference must always be in the result set so deltas are defined.
    if reference and not any(e.name == reference for e in engines):
        try:
            engines.append(get_engine(reference))
        except KeyError:
            pass

    results: Dict[str, tuple[Optional[LossEstimate], float, bool]] = {}
    for eng in engines:
        avail = eng.is_available()
        if not avail:
            results[eng.name] = (None, 0.0, False)
            continue
        est, ms = _run_one(eng, link_kwargs)
        results[eng.name] = (est, ms, True)

    ref_loss: Optional[float] = None
    ref_entry = results.get(reference)
    if ref_entry and ref_entry[0] is not None:
        ref_loss = ref_entry[0].basic_loss_db

    rows: List[ComparisonRow] = []
    for name, (est, ms, avail) in results.items():
        loss = est.basic_loss_db if est else None
        delta = (loss - ref_loss) if (loss is not None and ref_loss is not None) else None
        rows.append(ComparisonRow(
            engine=name,
            available=avail,
            basic_loss_db=loss,
            confidence=est.confidence if est else None,
            runtime_ms=round(ms, 3) if avail else None,
            delta_db=delta,
            extra=est.extra if est else {},
        ))
    # Stable order: reference first, then alphabetical.
    rows.sort(key=lambda r: (r.engine != reference, r.engine))
    return ComparisonResult(reference=reference, rows=rows)
