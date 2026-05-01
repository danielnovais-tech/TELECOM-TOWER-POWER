# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.bedrock_antenna_request_analysis import BedrockAntennaRequestAnalysis
    from ..models.bedrock_antenna_request_tower import BedrockAntennaRequestTower


T = TypeVar("T", bound="BedrockAntennaRequest")


@_attrs_define
class BedrockAntennaRequest:
    """
    Attributes:
        analysis (BedrockAntennaRequestAnalysis): Link analysis result
        tower (BedrockAntennaRequestTower): Tower information
        target_clearance (float | Unset): Target Fresnel zone clearance fraction Default: 0.6.
        model_id (None | str | Unset):
    """

    analysis: BedrockAntennaRequestAnalysis
    tower: BedrockAntennaRequestTower
    target_clearance: float | Unset = 0.6
    model_id: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        analysis = self.analysis.to_dict()

        tower = self.tower.to_dict()

        target_clearance = self.target_clearance

        model_id: None | str | Unset
        if isinstance(self.model_id, Unset):
            model_id = UNSET
        else:
            model_id = self.model_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "analysis": analysis,
                "tower": tower,
            }
        )
        if target_clearance is not UNSET:
            field_dict["target_clearance"] = target_clearance
        if model_id is not UNSET:
            field_dict["model_id"] = model_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.bedrock_antenna_request_analysis import BedrockAntennaRequestAnalysis
        from ..models.bedrock_antenna_request_tower import BedrockAntennaRequestTower

        d = dict(src_dict)
        analysis = BedrockAntennaRequestAnalysis.from_dict(d.pop("analysis"))

        tower = BedrockAntennaRequestTower.from_dict(d.pop("tower"))

        target_clearance = d.pop("target_clearance", UNSET)

        def _parse_model_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        model_id = _parse_model_id(d.pop("model_id", UNSET))

        bedrock_antenna_request = cls(
            analysis=analysis,
            tower=tower,
            target_clearance=target_clearance,
            model_id=model_id,
        )

        bedrock_antenna_request.additional_properties = d
        return bedrock_antenna_request

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
