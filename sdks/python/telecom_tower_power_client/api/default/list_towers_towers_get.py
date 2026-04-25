from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    operator: None | str | Unset = UNSET,
    limit: int | Unset = 1000,
    offset: int | Unset = 0,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_operator: None | str | Unset
    if isinstance(operator, Unset):
        json_operator = UNSET
    else:
        json_operator = operator
    params["operator"] = json_operator

    params["limit"] = limit

    params["offset"] = offset

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/towers",
        "params": params,
    }

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
    operator: None | str | Unset = UNSET,
    limit: int | Unset = 1000,
    offset: int | Unset = 0,
) -> Response[Any | HTTPValidationError]:
    """List Towers

     List towers with pagination. Use *offset* and *limit* to page through results.

    Args:
        operator (None | str | Unset):
        limit (int | Unset):  Default: 1000.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        operator=operator,
        limit=limit,
        offset=offset,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient,
    operator: None | str | Unset = UNSET,
    limit: int | Unset = 1000,
    offset: int | Unset = 0,
) -> Any | HTTPValidationError | None:
    """List Towers

     List towers with pagination. Use *offset* and *limit* to page through results.

    Args:
        operator (None | str | Unset):
        limit (int | Unset):  Default: 1000.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return sync_detailed(
        client=client,
        operator=operator,
        limit=limit,
        offset=offset,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient,
    operator: None | str | Unset = UNSET,
    limit: int | Unset = 1000,
    offset: int | Unset = 0,
) -> Response[Any | HTTPValidationError]:
    """List Towers

     List towers with pagination. Use *offset* and *limit* to page through results.

    Args:
        operator (None | str | Unset):
        limit (int | Unset):  Default: 1000.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        operator=operator,
        limit=limit,
        offset=offset,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient,
    operator: None | str | Unset = UNSET,
    limit: int | Unset = 1000,
    offset: int | Unset = 0,
) -> Any | HTTPValidationError | None:
    """List Towers

     List towers with pagination. Use *offset* and *limit* to page through results.

    Args:
        operator (None | str | Unset):
        limit (int | Unset):  Default: 1000.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            client=client,
            operator=operator,
            limit=limit,
            offset=offset,
        )
    ).parsed
