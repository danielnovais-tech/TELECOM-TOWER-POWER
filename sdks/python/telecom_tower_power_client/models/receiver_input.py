# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ReceiverInput")


@_attrs_define
class ReceiverInput:
    """
    Attributes:
        lat (float):
        lon (float):
        height_m (float | Unset):  Default: 10.0.
        antenna_gain_dbi (float | Unset):  Default: 12.0.
    """

    lat: float
    lon: float
    height_m: float | Unset = 10.0
    antenna_gain_dbi: float | Unset = 12.0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        lat = self.lat

        lon = self.lon

        height_m = self.height_m

        antenna_gain_dbi = self.antenna_gain_dbi

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "lat": lat,
                "lon": lon,
            }
        )
        if height_m is not UNSET:
            field_dict["height_m"] = height_m
        if antenna_gain_dbi is not UNSET:
            field_dict["antenna_gain_dbi"] = antenna_gain_dbi

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        lat = d.pop("lat")

        lon = d.pop("lon")

        height_m = d.pop("height_m", UNSET)

        antenna_gain_dbi = d.pop("antenna_gain_dbi", UNSET)

        receiver_input = cls(
            lat=lat,
            lon=lon,
            height_m=height_m,
            antenna_gain_dbi=antenna_gain_dbi,
        )

        receiver_input.additional_properties = d
        return receiver_input

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
