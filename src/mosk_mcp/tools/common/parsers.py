"""Common parsing utilities for MOSK MCP tools.

This module provides shared parsing functions to eliminate code duplication
across tools, particularly for Kubernetes resource parsing.

Example:
    from mosk_mcp.tools.common.parsers import parse_k8s_condition, parse_k8s_conditions

    # Parse a single condition
    condition = parse_k8s_condition(raw_condition_dict)

    # Parse multiple conditions
    conditions = parse_k8s_conditions(raw_conditions_list)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast


if TYPE_CHECKING:
    from mosk_mcp.tools.operations_visibility.models import Condition


def parse_k8s_condition(cond_data: dict[str, Any]) -> Condition:
    """Parse a Kubernetes condition from raw API data.

    Converts raw condition dictionaries from Kubernetes API responses
    into typed Condition objects with proper enum handling.

    Args:
        cond_data: Raw condition dictionary from Kubernetes API.

    Returns:
        Parsed Condition object.

    Example:
        >>> cond = parse_k8s_condition(
        ...     {
        ...         "type": "Ready",
        ...         "status": "True",
        ...         "reason": "NodeReady",
        ...         "message": "Node is ready",
        ...         "lastTransitionTime": "2024-01-15T10:30:00Z",
        ...     }
        ... )
        >>> cond.status
        <ConditionStatus.TRUE: 'True'>
    """
    # Lazy import to avoid circular import
    from mosk_mcp.tools.operations_visibility.models import (
        Condition,
        ConditionStatus,
    )

    status_str = cond_data.get("status", "Unknown")
    try:
        status = ConditionStatus(status_str)
    except ValueError:
        status = ConditionStatus.UNKNOWN

    return Condition(
        type=cond_data.get("type", "Unknown"),
        status=status,
        reason=cond_data.get("reason"),
        message=cond_data.get("message"),
        last_transition_time=cond_data.get("lastTransitionTime"),
        last_update_time=cond_data.get("lastUpdateTime") or cond_data.get("lastHeartbeatTime"),
    )


def parse_k8s_conditions(conditions_data: list[dict[str, Any]]) -> list[Condition]:
    """Parse a list of Kubernetes conditions.

    Convenience wrapper around parse_k8s_condition for parsing
    multiple conditions at once.

    Args:
        conditions_data: List of raw condition dictionaries.

    Returns:
        List of parsed Condition objects.
    """
    return [parse_k8s_condition(c) for c in conditions_data]


def utc_timestamp() -> str:
    """Get current UTC timestamp in ISO format.

    Returns a consistent timestamp format for all tool responses.

    Returns:
        ISO format timestamp string.

    Example:
        >>> ts = utc_timestamp()
        >>> ts  # '2024-01-15T10:30:00.123456+00:00'
    """
    return datetime.now(UTC).isoformat()


def parse_label_selector(labels: dict[str, str]) -> str:
    """Convert a labels dict to a Kubernetes label selector string.

    Args:
        labels: Dictionary of label key-value pairs.

    Returns:
        Label selector string (e.g., "app=nginx,env=prod").

    Example:
        >>> parse_label_selector({"app": "nginx", "env": "prod"})
        'app=nginx,env=prod'
    """
    return ",".join(f"{k}={v}" for k, v in sorted(labels.items()))


def safe_get_nested(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Safely get a nested value from a dictionary.

    Navigates through nested dictionaries, returning the default
    if any key is missing.

    Args:
        data: The dictionary to navigate.
        *keys: The sequence of keys to follow.
        default: Value to return if path not found.

    Returns:
        The value at the nested path, or the default.

    Example:
        >>> data = {"status": {"conditions": [{"type": "Ready"}]}}
        >>> safe_get_nested(data, "status", "conditions")
        [{'type': 'Ready'}]
        >>> safe_get_nested(data, "spec", "replicas", default=1)
        1
    """
    result: Any = data
    for key in keys:
        if isinstance(result, dict):
            result = result.get(key)
        else:
            return default
        if result is None:
            return default
    return result


def find_condition_by_type(
    conditions: list[dict[str, Any]],
    condition_type: str,
) -> dict[str, Any] | None:
    """Find a condition by its type.

    Common pattern for finding specific conditions like "Ready" from
    a list of Kubernetes conditions.

    Args:
        conditions: List of raw condition dictionaries.
        condition_type: The condition type to find (e.g., "Ready").

    Returns:
        The matching condition dictionary, or None if not found.

    Example:
        >>> conditions = [{"type": "Ready", "status": "True"}]
        >>> find_condition_by_type(conditions, "Ready")
        {'type': 'Ready', 'status': 'True'}
        >>> find_condition_by_type(conditions, "Unknown")
        None
    """
    return next((c for c in conditions if c.get("type") == condition_type), None)


def is_condition_true(
    conditions: list[dict[str, Any]],
    condition_type: str,
) -> bool:
    """Check if a condition is present and its status is True.

    Convenience function that combines finding a condition and checking
    its status in one call.

    Args:
        conditions: List of raw condition dictionaries.
        condition_type: The condition type to check (e.g., "Ready").

    Returns:
        True if condition exists and status is "True", False otherwise.

    Example:
        >>> conditions = [{"type": "Ready", "status": "True"}]
        >>> is_condition_true(conditions, "Ready")
        True
        >>> is_condition_true(conditions, "DiskPressure")
        False
    """
    cond = find_condition_by_type(conditions, condition_type)
    return cond is not None and cond.get("status") == "True"


def get_status_conditions(resource: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract conditions from a Kubernetes resource.

    Safely extracts the conditions array from a resource's status,
    returning an empty list if not present.

    Args:
        resource: Kubernetes resource dictionary.

    Returns:
        List of condition dictionaries (may be empty).

    Example:
        >>> node = {"status": {"conditions": [{"type": "Ready"}]}}
        >>> get_status_conditions(node)
        [{'type': 'Ready'}]
        >>> get_status_conditions({})
        []
    """
    status = resource.get("status", {})
    if not isinstance(status, dict):
        return []
    conditions = status.get("conditions", [])
    return cast("list[dict[str, Any]]", conditions if isinstance(conditions, list) else [])


def is_resource_ready(resource: dict[str, Any]) -> bool:
    """Check if a Kubernetes resource is ready.

    Checks the "Ready" condition in the resource's status.

    Args:
        resource: Kubernetes resource dictionary.

    Returns:
        True if the resource has a Ready=True condition.

    Example:
        >>> node = {"status": {"conditions": [{"type": "Ready", "status": "True"}]}}
        >>> is_resource_ready(node)
        True
    """
    conditions = get_status_conditions(resource)
    return is_condition_true(conditions, "Ready")


def get_condition_message(
    conditions: list[dict[str, Any]],
    condition_type: str,
) -> str | None:
    """Get the message from a specific condition.

    Args:
        conditions: List of raw condition dictionaries.
        condition_type: The condition type to find.

    Returns:
        The condition's message, or None if not found.

    Example:
        >>> conditions = [{"type": "Ready", "message": "Node is healthy"}]
        >>> get_condition_message(conditions, "Ready")
        'Node is healthy'
    """
    cond = find_condition_by_type(conditions, condition_type)
    return cond.get("message") if cond else None


def parse_mosk_condition_ready(condition: dict[str, Any]) -> bool:
    """Parse MOSK cluster condition readiness.

    MOSK conditions use both 'ready' (bool) and 'status' (string) fields.
    This function handles all formats consistently:
    - ready: True, False, "true", "false"
    - status: "True", "False", True, False

    Args:
        condition: Raw condition dictionary from Cluster CR providerStatus.

    Returns:
        True if condition is ready/True, False otherwise.

    Example:
        >>> parse_mosk_condition_ready({"ready": True})
        True
        >>> parse_mosk_condition_ready({"status": "True"})
        True
        >>> parse_mosk_condition_ready({"ready": "true"})
        True
        >>> parse_mosk_condition_ready({})
        False
    """
    ready = condition.get("ready")
    status = condition.get("status")

    # Prefer 'ready' field (MOSK cluster conditions use this)
    if ready is not None:
        return ready is True or ready == "true"

    # Fall back to 'status' field (standard K8s conditions use this)
    if status is not None:
        return status == "True" or status is True

    return False


def parse_health_ratio(health_str: str) -> tuple[int, int]:
    """Parse health ratio string like '23/23' into (ready, total).

    This is commonly used for parsing OSDPLStatus and LCM progress strings.

    Args:
        health_str: Health ratio string (e.g., '23/23', '18/20').

    Returns:
        Tuple of (ready_count, total_count). Returns (0, 0) if parsing fails.

    Example:
        >>> parse_health_ratio("23/23")
        (23, 23)
        >>> parse_health_ratio("18/20")
        (18, 20)
        >>> parse_health_ratio("invalid")
        (0, 0)
    """
    try:
        if "/" in health_str:
            parts = health_str.split("/")
            return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        pass
    return 0, 0
