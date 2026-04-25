from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="CheckoutRequest")


@_attrs_define
class CheckoutRequest:
    """
    Attributes:
        email (str):
        tier (str):
        billing_cycle (str | Unset):  Default: 'monthly'.
        country (None | str | Unset): ISO 3166-1 alpha-2 country code for SRTM tile pre-download (enterprise only)
    """

    email: str
    tier: str
    billing_cycle: str | Unset = "monthly"
    country: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        email = self.email

        tier = self.tier

        billing_cycle = self.billing_cycle

        country: None | str | Unset
        if isinstance(self.country, Unset):
            country = UNSET
        else:
            country = self.country

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "email": email,
                "tier": tier,
            }
        )
        if billing_cycle is not UNSET:
            field_dict["billing_cycle"] = billing_cycle
        if country is not UNSET:
            field_dict["country"] = country

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        email = d.pop("email")

        tier = d.pop("tier")

        billing_cycle = d.pop("billing_cycle", UNSET)

        def _parse_country(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        country = _parse_country(d.pop("country", UNSET))

        checkout_request = cls(
            email=email,
            tier=tier,
            billing_cycle=billing_cycle,
            country=country,
        )

        checkout_request.additional_properties = d
        return checkout_request

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
