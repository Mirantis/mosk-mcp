"""HTTP(S) URL validation using Pydantic's URL parsing."""

from __future__ import annotations

from pydantic import HttpUrl, TypeAdapter, ValidationError

_http_url_adapter = TypeAdapter(HttpUrl)


def validate_http_url(url: str) -> str:
    """Validate ``url`` as an ``http`` or ``https`` URL with a host.

    Returns the URL as a string with trailing ``/`` characters removed from the end.

    Raises:
        ValueError: If the string is not a valid HTTP(S) URL.
    """
    try:
        _http_url_adapter.validate_python(url)
    except ValidationError as exc:
        raise ValueError(
            f"Invalid URL format: '{url}'. "
            "URL must start with http:// or https:// and contain a valid hostname."
        ) from exc
    # Keep the input spelling (e.g. explicit :443); str(HttpUrl) drops default ports.
    return url.rstrip("/")
