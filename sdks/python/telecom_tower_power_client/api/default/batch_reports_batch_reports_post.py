# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.body_batch_reports_batch_reports_post import BodyBatchReportsBatchReportsPost
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    body: BodyBatchReportsBatchReportsPost,
    tower_id: str,
    receiver_height_m: float | Unset = 10.0,
    antenna_gain_dbi: float | Unset = 12.0,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    params: dict[str, Any] = {}

    params["tower_id"] = tower_id

    params["receiver_height_m"] = receiver_height_m

    params["antenna_gain_dbi"] = antenna_gain_dbi

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/batch_reports",
        "params": params,
    }

    _kwargs["files"] = body.to_multipart()

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
    body: BodyBatchReportsBatchReportsPost,
    tower_id: str,
    receiver_height_m: float | Unset = 10.0,
    antenna_gain_dbi: float | Unset = 12.0,
) -> Response[Any | HTTPValidationError]:
    """Batch Reports

     Upload a CSV of receiver points (columns: lat,lon  and optionally
    height, gain) and download a ZIP of PDF reports – one per receiver.

    Small batches ( <= 100 rows) are processed synchronously.
    Larger batches are queued for the background worker and return a job_id.

    Args:
        tower_id (str):
        receiver_height_m (float | Unset):  Default: 10.0.
        antenna_gain_dbi (float | Unset):  Default: 12.0.
        body (BodyBatchReportsBatchReportsPost):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        body=body,
        tower_id=tower_id,
        receiver_height_m=receiver_height_m,
        antenna_gain_dbi=antenna_gain_dbi,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient,
    body: BodyBatchReportsBatchReportsPost,
    tower_id: str,
    receiver_height_m: float | Unset = 10.0,
    antenna_gain_dbi: float | Unset = 12.0,
) -> Any | HTTPValidationError | None:
    """Batch Reports

     Upload a CSV of receiver points (columns: lat,lon  and optionally
    height, gain) and download a ZIP of PDF reports – one per receiver.

    Small batches ( <= 100 rows) are processed synchronously.
    Larger batches are queued for the background worker and return a job_id.

    Args:
        tower_id (str):
        receiver_height_m (float | Unset):  Default: 10.0.
        antenna_gain_dbi (float | Unset):  Default: 12.0.
        body (BodyBatchReportsBatchReportsPost):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return sync_detailed(
        client=client,
        body=body,
        tower_id=tower_id,
        receiver_height_m=receiver_height_m,
        antenna_gain_dbi=antenna_gain_dbi,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient,
    body: BodyBatchReportsBatchReportsPost,
    tower_id: str,
    receiver_height_m: float | Unset = 10.0,
    antenna_gain_dbi: float | Unset = 12.0,
) -> Response[Any | HTTPValidationError]:
    """Batch Reports

     Upload a CSV of receiver points (columns: lat,lon  and optionally
    height, gain) and download a ZIP of PDF reports – one per receiver.

    Small batches ( <= 100 rows) are processed synchronously.
    Larger batches are queued for the background worker and return a job_id.

    Args:
        tower_id (str):
        receiver_height_m (float | Unset):  Default: 10.0.
        antenna_gain_dbi (float | Unset):  Default: 12.0.
        body (BodyBatchReportsBatchReportsPost):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        body=body,
        tower_id=tower_id,
        receiver_height_m=receiver_height_m,
        antenna_gain_dbi=antenna_gain_dbi,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient,
    body: BodyBatchReportsBatchReportsPost,
    tower_id: str,
    receiver_height_m: float | Unset = 10.0,
    antenna_gain_dbi: float | Unset = 12.0,
) -> Any | HTTPValidationError | None:
    """Batch Reports

     Upload a CSV of receiver points (columns: lat,lon  and optionally
    height, gain) and download a ZIP of PDF reports – one per receiver.

    Small batches ( <= 100 rows) are processed synchronously.
    Larger batches are queued for the background worker and return a job_id.

    Args:
        tower_id (str):
        receiver_height_m (float | Unset):  Default: 10.0.
        antenna_gain_dbi (float | Unset):  Default: 12.0.
        body (BodyBatchReportsBatchReportsPost):

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
            tower_id=tower_id,
            receiver_height_m=receiver_height_m,
            antenna_gain_dbi=antenna_gain_dbi,
        )
    ).parsed
