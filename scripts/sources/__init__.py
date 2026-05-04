# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Data-source helpers for ``scripts/build_mitsuba_scene.py``.

Each submodule owns one external data source and is responsible for
producing exactly one provenance artefact under the AOI directory:

- ``overpass_buildings`` → ``buildings.geojson``
- ``srtm_terrain``       → ``terrain.tif``

The submodules do **not** know about Mitsuba, materials, or anything
else that lives downstream. Keeping the seams narrow lets a stale
data-source response be re-fetched without re-running the whole
scene build.
"""
