"""Kubernetes resource utilities.

This module provides common utilities for working with Kubernetes resources,
including age calculation and byte formatting.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def calculate_resource_age(metadata: dict[str, Any]) -> float | None:
    """Calculate age in seconds from Kubernetes resource metadata.

    This function extracts the creationTimestamp from resource metadata
    and calculates the age in seconds. It handles both string timestamps
    and already-parsed datetime objects.

    Args:
        metadata: Kubernetes resource metadata dictionary containing
            'creationTimestamp' field.

    Returns:
        Age in seconds as a float, or None if timestamp is missing or invalid.

    Example:
        >>> metadata = {"creationTimestamp": "2024-01-15T10:30:00Z"}
        >>> age = calculate_resource_age(metadata)
        >>> isinstance(age, float)
        True
    """
    creation_ts = metadata.get("creationTimestamp")
    if not creation_ts:
        return None

    try:
        if isinstance(creation_ts, str):
            # Parse ISO format timestamp (handle Z suffix)
            created = datetime.fromisoformat(creation_ts.replace("Z", "+00:00"))
            return (datetime.now(created.tzinfo) - created).total_seconds()
        if isinstance(creation_ts, datetime):
            # Already a datetime object
            return (datetime.now(creation_ts.tzinfo) - creation_ts).total_seconds()
    except (ValueError, TypeError, AttributeError):
        pass

    return None


def format_age(seconds: float | None) -> str:
    """Format age in seconds to human-readable string.

    Args:
        seconds: Age in seconds, or None.

    Returns:
        Human-readable age string (e.g., "5d 3h", "2h 30m", "45m").

    Example:
        >>> format_age(86400)
        '1d 0h'
        >>> format_age(3700)
        '1h 1m'
        >>> format_age(None)
        'unknown'
    """
    if seconds is None:
        return "unknown"

    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)

    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def format_bytes(bytes_value: int) -> str:
    """Format bytes to human-readable string.

    Converts byte values to appropriate units (B, KB, MB, GB, TB, PB, EB)
    for human-readable display.

    Args:
        bytes_value: Number of bytes.

    Returns:
        Human-readable string with appropriate unit.

    Example:
        >>> format_bytes(1024)
        '1.00 KB'
        >>> format_bytes(1073741824)
        '1.00 GB'
        >>> format_bytes(500)
        '500 B'
    """
    if bytes_value < 0:
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB", "PB", "EB"]
    unit_index = 0
    value = float(bytes_value)

    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1

    if unit_index == 0:
        return f"{int(value)} {units[unit_index]}"
    return f"{value:.2f} {units[unit_index]}"


def parse_kubernetes_quantity(quantity: str | int | float) -> int:
    """Parse Kubernetes resource quantity to bytes/units.

    Handles Kubernetes quantity notation like "100Mi", "2Gi", "500m".

    Args:
        quantity: Kubernetes quantity string or numeric value.

    Returns:
        Value in base units (bytes for memory, millicores for CPU).

    Example:
        >>> parse_kubernetes_quantity("1Gi")
        1073741824
        >>> parse_kubernetes_quantity("500Mi")
        524288000
        >>> parse_kubernetes_quantity(1000)
        1000
    """
    if isinstance(quantity, (int, float)):
        return int(quantity)

    quantity = str(quantity).strip()
    if not quantity:
        return 0

    # Binary units (Ki, Mi, Gi, Ti, Pi, Ei)
    binary_units = {
        "Ki": 1024,
        "Mi": 1024**2,
        "Gi": 1024**3,
        "Ti": 1024**4,
        "Pi": 1024**5,
        "Ei": 1024**6,
    }

    # Decimal units (k, M, G, T, P, E)
    decimal_units = {
        "k": 1000,
        "M": 1000**2,
        "G": 1000**3,
        "T": 1000**4,
        "P": 1000**5,
        "E": 1000**6,
    }

    # Millicores (m suffix for CPU)
    if quantity.endswith("m"):
        try:
            return int(float(quantity[:-1]))
        except ValueError:
            return 0

    # Binary units
    for suffix, multiplier in binary_units.items():
        if quantity.endswith(suffix):
            try:
                return int(float(quantity[: -len(suffix)]) * multiplier)
            except ValueError:
                return 0

    # Decimal units
    for suffix, multiplier in decimal_units.items():
        if quantity.endswith(suffix):
            try:
                return int(float(quantity[: -len(suffix)]) * multiplier)
            except ValueError:
                return 0

    # Plain number
    try:
        return int(float(quantity))
    except ValueError:
        return 0
