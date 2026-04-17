from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.band import Band
from ..types import UNSET, Unset

T = TypeVar("T", bound="TowerInput")


@_attrs_define
class TowerInput:
    """
    Attributes:
        id (str):
        lat (float):
        lon (float):
        height_m (float):
        operator (str):
        bands (list[Band]):
        power_dbm (float | Unset):  Default: 43.0.
    """

    id: str
    lat: float
    lon: float
    height_m: float
    operator: str
    bands: list[Band]
    power_dbm: float | Unset = 43.0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        lat = self.lat

        lon = self.lon

        height_m = self.height_m

        operator = self.operator

        bands = []
        for bands_item_data in self.bands:
            bands_item = bands_item_data.value
            bands.append(bands_item)

        power_dbm = self.power_dbm

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "lat": lat,
                "lon": lon,
                "height_m": height_m,
                "operator": operator,
                "bands": bands,
            }
        )
        if power_dbm is not UNSET:
            field_dict["power_dbm"] = power_dbm

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = d.pop("id")

        lat = d.pop("lat")

        lon = d.pop("lon")

        height_m = d.pop("height_m")

        operator = d.pop("operator")

        bands = []
        _bands = d.pop("bands")
        for bands_item_data in _bands:
            bands_item = Band(bands_item_data)

            bands.append(bands_item)

        power_dbm = d.pop("power_dbm", UNSET)

        tower_input = cls(
            id=id,
            lat=lat,
            lon=lon,
            height_m=height_m,
            operator=operator,
            bands=bands,
            power_dbm=power_dbm,
        )

        tower_input.additional_properties = d
        return tower_input

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
