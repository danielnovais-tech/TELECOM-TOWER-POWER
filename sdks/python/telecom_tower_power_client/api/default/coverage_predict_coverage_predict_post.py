# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.coverage_predict_request import CoveragePredictRequest
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    *,
    body: CoveragePredictRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/coverage/predict",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = response.json()
        return response_200

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Any | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient,
    body: CoveragePredictRequest,
) -> Response[Any | HTTPValidationError]:
    """Coverage Predict

     ML-based signal coverage prediction.

    Uses a terrain-aware regression model trained on SRTM elevation
    features. Routes to a SageMaker endpoint when
    ``SAGEMAKER_COVERAGE_ENDPOINT`` is configured, otherwise serves the
    locally-trained model, with a deterministic physics fallback when
    no model artefact is available.

    Modes:
    - **point** — provide ``rx_lat``/``rx_lon`` for a single prediction.
    - **grid**  — provide ``bbox`` and ``grid_size`` for a coverage map.

    Restricted to Pro / Business / Enterprise tiers.

    Args:
        body (CoveragePredictRequest): Request body for /coverage/predict.

            Provide either ``tower_id`` (existing tower) **or** the explicit
            ``tx_lat`` / ``tx_lon`` / ``tx_height_m`` / ``band`` quartet.
            Provide either a single receiver (``rx_lat``/``rx_lon``) **or** a
            bounding box (``bbox``) to compute a coverage grid.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient,
    body: CoveragePredictRequest,
) -> Any | HTTPValidationError | None:
    """Coverage Predict

     ML-based signal coverage prediction.

    Uses a terrain-aware regression model trained on SRTM elevation
    features. Routes to a SageMaker endpoint when
    ``SAGEMAKER_COVERAGE_ENDPOINT`` is configured, otherwise serves the
    locally-trained model, with a deterministic physics fallback when
    no model artefact is available.

    Modes:
    - **point** — provide ``rx_lat``/``rx_lon`` for a single prediction.
    - **grid**  — provide ``bbox`` and ``grid_size`` for a coverage map.

    Restricted to Pro / Business / Enterprise tiers.

    Args:
        body (CoveragePredictRequest): Request body for /coverage/predict.

            Provide either ``tower_id`` (existing tower) **or** the explicit
            ``tx_lat`` / ``tx_lon`` / ``tx_height_m`` / ``band`` quartet.
            Provide either a single receiver (``rx_lat``/``rx_lon``) **or** a
            bounding box (``bbox``) to compute a coverage grid.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient,
    body: CoveragePredictRequest,
) -> Response[Any | HTTPValidationError]:
    """Coverage Predict

     ML-based signal coverage prediction.

    Uses a terrain-aware regression model trained on SRTM elevation
    features. Routes to a SageMaker endpoint when
    ``SAGEMAKER_COVERAGE_ENDPOINT`` is configured, otherwise serves the
    locally-trained model, with a deterministic physics fallback when
    no model artefact is available.

    Modes:
    - **point** — provide ``rx_lat``/``rx_lon`` for a single prediction.
    - **grid**  — provide ``bbox`` and ``grid_size`` for a coverage map.

    Restricted to Pro / Business / Enterprise tiers.

    Args:
        body (CoveragePredictRequest): Request body for /coverage/predict.

            Provide either ``tower_id`` (existing tower) **or** the explicit
            ``tx_lat`` / ``tx_lon`` / ``tx_height_m`` / ``band`` quartet.
            Provide either a single receiver (``rx_lat``/``rx_lon``) **or** a
            bounding box (``bbox``) to compute a coverage grid.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient,
    body: CoveragePredictRequest,
) -> Any | HTTPValidationError | None:
    """Coverage Predict

     ML-based signal coverage prediction.

    Uses a terrain-aware regression model trained on SRTM elevation
    features. Routes to a SageMaker endpoint when
    ``SAGEMAKER_COVERAGE_ENDPOINT`` is configured, otherwise serves the
    locally-trained model, with a deterministic physics fallback when
    no model artefact is available.

    Modes:
    - **point** — provide ``rx_lat``/``rx_lon`` for a single prediction.
    - **grid**  — provide ``bbox`` and ``grid_size`` for a coverage map.

    Restricted to Pro / Business / Enterprise tiers.

    Args:
        body (CoveragePredictRequest): Request body for /coverage/predict.

            Provide either ``tower_id`` (existing tower) **or** the explicit
            ``tx_lat`` / ``tx_lon`` / ``tx_height_m`` / ``band`` quartet.
            Provide either a single receiver (``rx_lat``/``rx_lon``) **or** a
            bounding box (``bbox``) to compute a coverage grid.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
