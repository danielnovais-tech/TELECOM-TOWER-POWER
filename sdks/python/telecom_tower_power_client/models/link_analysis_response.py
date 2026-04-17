from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="LinkAnalysisResponse")


@_attrs_define
class LinkAnalysisResponse:
    """
    Attributes:
        feasible (bool):
        signal_dbm (float):
        fresnel_clearance (float):
        los_ok (bool):
        distance_km (float):
        recommendation (str):
        terrain_profile (list[float] | None | Unset):
        tx_height_asl (float | None | Unset):
        rx_height_asl (float | None | Unset):
    """

    feasible: bool
    signal_dbm: float
    fresnel_clearance: float
    los_ok: bool
    distance_km: float
    recommendation: str
    terrain_profile: list[float] | None | Unset = UNSET
    tx_height_asl: float | None | Unset = UNSET
    rx_height_asl: float | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        feasible = self.feasible

        signal_dbm = self.signal_dbm

        fresnel_clearance = self.fresnel_clearance

        los_ok = self.los_ok

        distance_km = self.distance_km

        recommendation = self.recommendation

        terrain_profile: list[float] | None | Unset
        if isinstance(self.terrain_profile, Unset):
            terrain_profile = UNSET
        elif isinstance(self.terrain_profile, list):
            terrain_profile = self.terrain_profile

        else:
            terrain_profile = self.terrain_profile

        tx_height_asl: float | None | Unset
        if isinstance(self.tx_height_asl, Unset):
            tx_height_asl = UNSET
        else:
            tx_height_asl = self.tx_height_asl

        rx_height_asl: float | None | Unset
        if isinstance(self.rx_height_asl, Unset):
            rx_height_asl = UNSET
        else:
            rx_height_asl = self.rx_height_asl

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "feasible": feasible,
                "signal_dbm": signal_dbm,
                "fresnel_clearance": fresnel_clearance,
                "los_ok": los_ok,
                "distance_km": distance_km,
                "recommendation": recommendation,
            }
        )
        if terrain_profile is not UNSET:
            field_dict["terrain_profile"] = terrain_profile
        if tx_height_asl is not UNSET:
            field_dict["tx_height_asl"] = tx_height_asl
        if rx_height_asl is not UNSET:
            field_dict["rx_height_asl"] = rx_height_asl

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        feasible = d.pop("feasible")

        signal_dbm = d.pop("signal_dbm")

        fresnel_clearance = d.pop("fresnel_clearance")

        los_ok = d.pop("los_ok")

        distance_km = d.pop("distance_km")

        recommendation = d.pop("recommendation")

        def _parse_terrain_profile(data: object) -> list[float] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                terrain_profile_type_0 = cast(list[float], data)

                return terrain_profile_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[float] | None | Unset, data)

        terrain_profile = _parse_terrain_profile(d.pop("terrain_profile", UNSET))

        def _parse_tx_height_asl(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        tx_height_asl = _parse_tx_height_asl(d.pop("tx_height_asl", UNSET))

        def _parse_rx_height_asl(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        rx_height_asl = _parse_rx_height_asl(d.pop("rx_height_asl", UNSET))

        link_analysis_response = cls(
            feasible=feasible,
            signal_dbm=signal_dbm,
            fresnel_clearance=fresnel_clearance,
            los_ok=los_ok,
            distance_km=distance_km,
            recommendation=recommendation,
            terrain_profile=terrain_profile,
            tx_height_asl=tx_height_asl,
            rx_height_asl=rx_height_asl,
        )

        link_analysis_response.additional_properties = d
        return link_analysis_response

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
