from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.bedrock_batch_analysis_request_batch_results_item import BedrockBatchAnalysisRequestBatchResultsItem


T = TypeVar("T", bound="BedrockBatchAnalysisRequest")


@_attrs_define
class BedrockBatchAnalysisRequest:
    """
    Attributes:
        batch_results (list[BedrockBatchAnalysisRequestBatchResultsItem]): Link analysis results to analyze
        question (None | str | Unset):
        model_id (None | str | Unset):
        max_tokens (int | None | Unset):
        temperature (float | None | Unset):
    """

    batch_results: list[BedrockBatchAnalysisRequestBatchResultsItem]
    question: None | str | Unset = UNSET
    model_id: None | str | Unset = UNSET
    max_tokens: int | None | Unset = UNSET
    temperature: float | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        batch_results = []
        for batch_results_item_data in self.batch_results:
            batch_results_item = batch_results_item_data.to_dict()
            batch_results.append(batch_results_item)

        question: None | str | Unset
        if isinstance(self.question, Unset):
            question = UNSET
        else:
            question = self.question

        model_id: None | str | Unset
        if isinstance(self.model_id, Unset):
            model_id = UNSET
        else:
            model_id = self.model_id

        max_tokens: int | None | Unset
        if isinstance(self.max_tokens, Unset):
            max_tokens = UNSET
        else:
            max_tokens = self.max_tokens

        temperature: float | None | Unset
        if isinstance(self.temperature, Unset):
            temperature = UNSET
        else:
            temperature = self.temperature

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "batch_results": batch_results,
            }
        )
        if question is not UNSET:
            field_dict["question"] = question
        if model_id is not UNSET:
            field_dict["model_id"] = model_id
        if max_tokens is not UNSET:
            field_dict["max_tokens"] = max_tokens
        if temperature is not UNSET:
            field_dict["temperature"] = temperature

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.bedrock_batch_analysis_request_batch_results_item import (
            BedrockBatchAnalysisRequestBatchResultsItem,
        )

        d = dict(src_dict)
        batch_results = []
        _batch_results = d.pop("batch_results")
        for batch_results_item_data in _batch_results:
            batch_results_item = BedrockBatchAnalysisRequestBatchResultsItem.from_dict(batch_results_item_data)

            batch_results.append(batch_results_item)

        def _parse_question(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        question = _parse_question(d.pop("question", UNSET))

        def _parse_model_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        model_id = _parse_model_id(d.pop("model_id", UNSET))

        def _parse_max_tokens(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        max_tokens = _parse_max_tokens(d.pop("max_tokens", UNSET))

        def _parse_temperature(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        temperature = _parse_temperature(d.pop("temperature", UNSET))

        bedrock_batch_analysis_request = cls(
            batch_results=batch_results,
            question=question,
            model_id=model_id,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        bedrock_batch_analysis_request.additional_properties = d
        return bedrock_batch_analysis_request

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
