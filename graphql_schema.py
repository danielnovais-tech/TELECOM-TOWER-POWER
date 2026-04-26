"""
graphql_schema.py — Strawberry GraphQL schema mounted alongside the REST API.

Why GraphQL?
    Web/dashboard clients today over-fetch from REST: e.g., to render a tower
    card with a single field from /towers/{id} they pull the whole row, then
    /analyze for each receiver. With GraphQL one request returns exactly the
    shape needed → ~60–70% fewer round-trips for the typical UI screen.

Scope (intentionally small to start):
    Query.tower(id)                       → TowerType
    Query.towers(operator, limit, offset) → [TowerType]
    Query.nearestTowers(lat, lon, limit)  → [TowerWithDistanceType]
    Query.analyzeLink(towerId, lat, lon, height_m, antenna_gain_dbi)
                                          → LinkResultType

Auth:
    Reuses the REST X-API-Key dependency. The router exposed by
    `get_graphql_router(verify_api_key)` injects key_data into the resolver
    context so every query is authenticated and tier-gated.
"""

from __future__ import annotations

import logging
from typing import List, Optional

import strawberry
from strawberry.fastapi import GraphQLRouter

logger = logging.getLogger(__name__)


# ── GraphQL types ─────────────────────────────────────────────────────────────

@strawberry.type
class TowerType:
    id: str
    lat: float
    lon: float
    height_m: float
    operator: str
    bands: List[str]
    owner: str
    power_dbm: float


@strawberry.type
class TowerWithDistanceType:
    tower: TowerType
    distance_km: float


@strawberry.type
class LinkResultType:
    feasible: bool
    signal_dbm: float
    fresnel_clearance: float
    los_ok: bool
    distance_km: float
    recommendation: str
    tx_height_asl: Optional[float] = None
    rx_height_asl: Optional[float] = None


# ── Helpers to convert dataclasses → GraphQL types ────────────────────────────

def _tower_to_gql(t) -> TowerType:
    return TowerType(
        id=t.id,
        lat=t.lat,
        lon=t.lon,
        height_m=t.height_m,
        operator=t.operator,
        bands=[b.value for b in t.bands],
        owner=t.owner,
        power_dbm=t.power_dbm,
    )


def _link_to_gql(r) -> LinkResultType:
    return LinkResultType(
        feasible=r.feasible,
        signal_dbm=r.signal_dbm,
        fresnel_clearance=r.fresnel_clearance,
        los_ok=r.los_ok,
        distance_km=r.distance_km,
        recommendation=r.recommendation,
        tx_height_asl=getattr(r, "tx_height_asl", None),
        rx_height_asl=getattr(r, "rx_height_asl", None),
    )


# ── Query root ────────────────────────────────────────────────────────────────

@strawberry.type
class Query:
    @strawberry.field
    def tower(self, info: strawberry.Info, id: str) -> Optional[TowerType]:
        ctx = info.context
        platform = ctx["platform"]
        owner = ctx["owner"]
        t = platform.get_tower(id)
        if not t:
            return None
        # Same cross-tenant rule as the REST endpoint.
        if t.owner not in (owner, "system"):
            return None
        return _tower_to_gql(t)

    @strawberry.field
    def towers(
        self,
        info: strawberry.Info,
        operator: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[TowerType]:
        # Cap limit defensively (REST allows up to 50000, but GraphQL
        # over-fetch is exactly what we are trying to prevent).
        limit = max(1, min(limit, 1000))
        offset = max(0, offset)
        ctx = info.context
        rows = ctx["platform"].list_towers(
            operator=operator, limit=limit, offset=offset, owner=ctx["owner"]
        )
        return [_tower_to_gql(t) for t in rows]

    @strawberry.field
    def nearest_towers(
        self,
        info: strawberry.Info,
        lat: float,
        lon: float,
        operator: Optional[str] = None,
        limit: int = 5,
    ) -> List[TowerWithDistanceType]:
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            raise ValueError("lat/lon out of range")
        limit = max(1, min(limit, 50))
        ctx = info.context
        platform = ctx["platform"]
        # Local import keeps this module importable without the platform pkg
        # at schema-build time (e.g., during unit tests of the schema only).
        from telecom_tower_power import LinkEngine
        rows = platform.find_nearest_towers(
            lat, lon, operator=operator, limit=limit, owner=ctx["owner"]
        )
        return [
            TowerWithDistanceType(
                tower=_tower_to_gql(t),
                distance_km=round(LinkEngine.haversine_km(lat, lon, t.lat, t.lon), 3),
            )
            for t in rows
        ]

    @strawberry.field
    async def analyze_link(
        self,
        info: strawberry.Info,
        tower_id: str,
        lat: float,
        lon: float,
        height_m: float = 10.0,
        antenna_gain_dbi: float = 12.0,
    ) -> LinkResultType:
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            raise ValueError("lat/lon out of range")
        if height_m < 0:
            raise ValueError("height_m must be >= 0")
        ctx = info.context
        platform = ctx["platform"]
        owner = ctx["owner"]
        tower = platform.get_tower(tower_id)
        if not tower or tower.owner not in (owner, "system"):
            raise ValueError(f"Tower {tower_id} not found")
        from telecom_tower_power import Receiver
        rx = Receiver(lat=lat, lon=lon, height_m=height_m,
                      antenna_gain_dbi=antenna_gain_dbi)
        result = await platform.analyze_link(tower, rx)
        return _link_to_gql(result)


schema = strawberry.Schema(query=Query)


# ── FastAPI integration ───────────────────────────────────────────────────────

def get_graphql_router(verify_api_key, platform):
    """Build a GraphQLRouter with auth + platform injected into context.

    `verify_api_key` is the same FastAPI dependency the REST endpoints use,
    so quota/tier rules apply identically. `platform` is the singleton
    TelecomTowerPower instance.
    """
    from fastapi import Depends

    async def get_context(
        key_data: dict = Depends(verify_api_key),
    ) -> dict:
        # Strawberry auto-merges {"request", "response", "background_tasks"}
        # into the returned dict, so resolvers can still access info.context["request"].
        owner = key_data.get("owner") or "system"
        return {
            "key_data": key_data,
            "owner": owner,
            "tier": key_data.get("tier"),
            "platform": platform,
        }

    return GraphQLRouter(
        schema,
        context_getter=get_context,
        # Disable GraphiQL in prod to avoid an unauthenticated browser UI
        # exposing the schema. Set GRAPHQL_GRAPHIQL=true to re-enable in dev.
        graphql_ide=None,
    )
