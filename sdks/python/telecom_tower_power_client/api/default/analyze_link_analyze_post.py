# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.link_analysis_response import LinkAnalysisResponse
from ...models.receiver_input import ReceiverInput
from ...types import UNSET, Response


def _get_kwargs(
    *,
    body: ReceiverInput,
    tower_id: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    params: dict[str, Any] = {}

    params["tower_id"] = tower_id

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/analyze",
        "params": params,
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | LinkAnalysisResponse | None:
    if response.status_code == 200:
        response_200 = LinkAnalysisResponse.from_dict(response.json())

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
) -> Response[HTTPValidationError | LinkAnalysisResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient,
    body: ReceiverInput,
    tower_id: str,
) -> Response[HTTPValidationError | LinkAnalysisResponse]:
    """Analyze Link

     Perform point-to-point link analysis between an existing tower and a receiver.
    Automatically fetches real terrain elevation along the path.

    Args:
        tower_id (str):
        body (ReceiverInput):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | LinkAnalysisResponse]
    """

    kwargs = _get_kwargs(
        body=body,
        tower_id=tower_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient,
    body: ReceiverInput,
    tower_id: str,
) -> HTTPValidationError | LinkAnalysisResponse | None:
    """Analyze Link

     Perform point-to-point link analysis between an existing tower and a receiver.
    Automatically fetches real terrain elevation along the path.

    Args:
        tower_id (str):
        body (ReceiverInput):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | LinkAnalysisResponse
    """

    return sync_detailed(
        client=client,
        body=body,
        tower_id=tower_id,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient,
    body: ReceiverInput,
    tower_id: str,
) -> Response[HTTPValidationError | LinkAnalysisResponse]:
    """Analyze Link

     Perform point-to-point link analysis between an existing tower and a receiver.
    Automatically fetches real terrain elevation along the path.

    Args:
        tower_id (str):
        body (ReceiverInput):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | LinkAnalysisResponse]
    """

    kwargs = _get_kwargs(
        body=body,
        tower_id=tower_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient,
    body: ReceiverInput,
    tower_id: str,
) -> HTTPValidationError | LinkAnalysisResponse | None:
    """Analyze Link

     Perform point-to-point link analysis between an existing tower and a receiver.
    Automatically fetches real terrain elevation along the path.

    Args:
        tower_id (str):
        body (ReceiverInput):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | LinkAnalysisResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
            tower_id=tower_id,
        )
    ).parsed
