from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.band import Band
from ..types import UNSET, Unset

T = TypeVar("T", bound="CoveragePredictRequest")


@_attrs_define
class CoveragePredictRequest:
    """Request body for /coverage/predict.

    Provide either ``tower_id`` (existing tower) **or** the explicit
    ``tx_lat`` / ``tx_lon`` / ``tx_height_m`` / ``band`` quartet.
    Provide either a single receiver (``rx_lat``/``rx_lon``) **or** a
    bounding box (``bbox``) to compute a coverage grid.

        Attributes:
            tower_id (None | str | Unset):
            tx_lat (float | None | Unset):
            tx_lon (float | None | Unset):
            tx_height_m (float | None | Unset):
            tx_power_dbm (float | Unset):  Default: 43.0.
            tx_gain_dbi (float | Unset):  Default: 17.0.
            band (Band | None | Unset):
            rx_lat (float | None | Unset):
            rx_lon (float | None | Unset):
            rx_height_m (float | Unset):  Default: 10.0.
            rx_gain_dbi (float | Unset):  Default: 12.0.
            bbox (list[float] | None | Unset): [min_lat, min_lon, max_lat, max_lon] for grid mode
            grid_size (int | Unset):  Default: 20.
            feasibility_threshold_dbm (float | Unset):  Default: -95.0.
            explain (bool | Unset):  Default: False.
    """

    tower_id: None | str | Unset = UNSET
    tx_lat: float | None | Unset = UNSET
    tx_lon: float | None | Unset = UNSET
    tx_height_m: float | None | Unset = UNSET
    tx_power_dbm: float | Unset = 43.0
    tx_gain_dbi: float | Unset = 17.0
    band: Band | None | Unset = UNSET
    rx_lat: float | None | Unset = UNSET
    rx_lon: float | None | Unset = UNSET
    rx_height_m: float | Unset = 10.0
    rx_gain_dbi: float | Unset = 12.0
    bbox: list[float] | None | Unset = UNSET
    grid_size: int | Unset = 20
    feasibility_threshold_dbm: float | Unset = -95.0
    explain: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        tower_id: None | str | Unset
        if isinstance(self.tower_id, Unset):
            tower_id = UNSET
        else:
            tower_id = self.tower_id

        tx_lat: float | None | Unset
        if isinstance(self.tx_lat, Unset):
            tx_lat = UNSET
        else:
            tx_lat = self.tx_lat

        tx_lon: float | None | Unset
        if isinstance(self.tx_lon, Unset):
            tx_lon = UNSET
        else:
            tx_lon = self.tx_lon

        tx_height_m: float | None | Unset
        if isinstance(self.tx_height_m, Unset):
            tx_height_m = UNSET
        else:
            tx_height_m = self.tx_height_m

        tx_power_dbm = self.tx_power_dbm

        tx_gain_dbi = self.tx_gain_dbi

        band: None | str | Unset
        if isinstance(self.band, Unset):
            band = UNSET
        elif isinstance(self.band, Band):
            band = self.band.value
        else:
            band = self.band

        rx_lat: float | None | Unset
        if isinstance(self.rx_lat, Unset):
            rx_lat = UNSET
        else:
            rx_lat = self.rx_lat

        rx_lon: float | None | Unset
        if isinstance(self.rx_lon, Unset):
            rx_lon = UNSET
        else:
            rx_lon = self.rx_lon

        rx_height_m = self.rx_height_m

        rx_gain_dbi = self.rx_gain_dbi

        bbox: list[float] | None | Unset
        if isinstance(self.bbox, Unset):
            bbox = UNSET
        elif isinstance(self.bbox, list):
            bbox = self.bbox

        else:
            bbox = self.bbox

        grid_size = self.grid_size

        feasibility_threshold_dbm = self.feasibility_threshold_dbm

        explain = self.explain

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if tower_id is not UNSET:
            field_dict["tower_id"] = tower_id
        if tx_lat is not UNSET:
            field_dict["tx_lat"] = tx_lat
        if tx_lon is not UNSET:
            field_dict["tx_lon"] = tx_lon
        if tx_height_m is not UNSET:
            field_dict["tx_height_m"] = tx_height_m
        if tx_power_dbm is not UNSET:
            field_dict["tx_power_dbm"] = tx_power_dbm
        if tx_gain_dbi is not UNSET:
            field_dict["tx_gain_dbi"] = tx_gain_dbi
        if band is not UNSET:
            field_dict["band"] = band
        if rx_lat is not UNSET:
            field_dict["rx_lat"] = rx_lat
        if rx_lon is not UNSET:
            field_dict["rx_lon"] = rx_lon
        if rx_height_m is not UNSET:
            field_dict["rx_height_m"] = rx_height_m
        if rx_gain_dbi is not UNSET:
            field_dict["rx_gain_dbi"] = rx_gain_dbi
        if bbox is not UNSET:
            field_dict["bbox"] = bbox
        if grid_size is not UNSET:
            field_dict["grid_size"] = grid_size
        if feasibility_threshold_dbm is not UNSET:
            field_dict["feasibility_threshold_dbm"] = feasibility_threshold_dbm
        if explain is not UNSET:
            field_dict["explain"] = explain

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_tower_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        tower_id = _parse_tower_id(d.pop("tower_id", UNSET))

        def _parse_tx_lat(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        tx_lat = _parse_tx_lat(d.pop("tx_lat", UNSET))

        def _parse_tx_lon(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        tx_lon = _parse_tx_lon(d.pop("tx_lon", UNSET))

        def _parse_tx_height_m(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        tx_height_m = _parse_tx_height_m(d.pop("tx_height_m", UNSET))

        tx_power_dbm = d.pop("tx_power_dbm", UNSET)

        tx_gain_dbi = d.pop("tx_gain_dbi", UNSET)

        def _parse_band(data: object) -> Band | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                band_type_0 = Band(data)

                return band_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(Band | None | Unset, data)

        band = _parse_band(d.pop("band", UNSET))

        def _parse_rx_lat(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        rx_lat = _parse_rx_lat(d.pop("rx_lat", UNSET))

        def _parse_rx_lon(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        rx_lon = _parse_rx_lon(d.pop("rx_lon", UNSET))

        rx_height_m = d.pop("rx_height_m", UNSET)

        rx_gain_dbi = d.pop("rx_gain_dbi", UNSET)

        def _parse_bbox(data: object) -> list[float] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                bbox_type_0 = cast(list[float], data)

                return bbox_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[float] | None | Unset, data)

        bbox = _parse_bbox(d.pop("bbox", UNSET))

        grid_size = d.pop("grid_size", UNSET)

        feasibility_threshold_dbm = d.pop("feasibility_threshold_dbm", UNSET)

        explain = d.pop("explain", UNSET)

        coverage_predict_request = cls(
            tower_id=tower_id,
            tx_lat=tx_lat,
            tx_lon=tx_lon,
            tx_height_m=tx_height_m,
            tx_power_dbm=tx_power_dbm,
            tx_gain_dbi=tx_gain_dbi,
            band=band,
            rx_lat=rx_lat,
            rx_lon=rx_lon,
            rx_height_m=rx_height_m,
            rx_gain_dbi=rx_gain_dbi,
            bbox=bbox,
            grid_size=grid_size,
            feasibility_threshold_dbm=feasibility_threshold_dbm,
            explain=explain,
        )

        coverage_predict_request.additional_properties = d
        return coverage_predict_request

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
