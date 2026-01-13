"""Input validation utilities for MOSK MCP Server.

This module provides validation functions for:
- Kubernetes resource names (DNS-1123 format)
- Namespace names
- Label selectors
- CRQ numbers
- Other input validation
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator, Field

# Import ValidationError from exceptions to avoid duplication
# The canonical ValidationError is in core.exceptions with full MoskMCPError support
from mosk_mcp.core.exceptions import ValidationError


# Re-export for backward compatibility
__all__ = ["ValidationError"]


# =============================================================================
# Kubernetes Resource Name Validation
# =============================================================================

# DNS-1123 subdomain format (used by Kubernetes)
# - lowercase alphanumeric
# - may contain '-' but not at start or end
# - max 253 characters
DNS_1123_SUBDOMAIN_PATTERN = re.compile(
    r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$"
)
DNS_1123_MAX_LENGTH = 253

# DNS-1123 label format (single label, no dots)
# - lowercase alphanumeric
# - may contain '-' but not at start or end
# - max 63 characters
DNS_1123_LABEL_PATTERN = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
DNS_1123_LABEL_MAX_LENGTH = 63

# Kubernetes label key format
# - optional prefix (DNS subdomain) followed by '/'
# - name part follows label rules
LABEL_KEY_PATTERN = re.compile(
    r"^([a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*/)?"
    r"[a-zA-Z0-9]([-_.a-zA-Z0-9]*[a-zA-Z0-9])?$"
)
LABEL_KEY_MAX_LENGTH = 253

# Kubernetes label value: alphanumeric with -_.  allowed, max 63 chars, may be empty
LABEL_VALUE_PATTERN = re.compile(r"^([a-zA-Z0-9]([-_.a-zA-Z0-9]*[a-zA-Z0-9])?)?$")
LABEL_VALUE_MAX_LENGTH = 63


def validate_kubernetes_name(
    name: str,
    field_name: str = "name",
    max_length: int = DNS_1123_MAX_LENGTH,
) -> str:
    """Validate a Kubernetes resource name.

    Validates that the name follows DNS-1123 subdomain format:
    - lowercase alphanumeric characters
    - may contain '-' but not at start or end
    - max 253 characters by default

    Args:
        name: The name to validate.
        field_name: Field name for error messages.
        max_length: Maximum allowed length.

    Returns:
        The validated name (unchanged).

    Raises:
        ValidationError: If the name is invalid.
    """
    if not name:
        raise ValidationError(
            f"{field_name} cannot be empty",
            field=field_name,
            value=name,
        )

    if len(name) > max_length:
        raise ValidationError(
            f"{field_name} must be at most {max_length} characters, got {len(name)}",
            field=field_name,
            value=name,
        )

    if not DNS_1123_SUBDOMAIN_PATTERN.match(name):
        raise ValidationError(
            f"{field_name} must be a valid DNS-1123 subdomain: lowercase alphanumeric, "
            f"may contain '-' but not at start or end. Got: '{name}'",
            field=field_name,
            value=name,
        )

    return name


def validate_kubernetes_label(
    name: str,
    field_name: str = "label",
) -> str:
    """Validate a Kubernetes label name (single DNS label).

    Validates that the name follows DNS-1123 label format:
    - lowercase alphanumeric characters
    - may contain '-' but not at start or end
    - max 63 characters

    Args:
        name: The label to validate.
        field_name: Field name for error messages.

    Returns:
        The validated label (unchanged).

    Raises:
        ValidationError: If the label is invalid.
    """
    if not name:
        raise ValidationError(
            f"{field_name} cannot be empty",
            field=field_name,
            value=name,
        )

    if len(name) > DNS_1123_LABEL_MAX_LENGTH:
        raise ValidationError(
            f"{field_name} must be at most {DNS_1123_LABEL_MAX_LENGTH} characters, got {len(name)}",
            field=field_name,
            value=name,
        )

    if not DNS_1123_LABEL_PATTERN.match(name):
        raise ValidationError(
            f"{field_name} must be a valid DNS-1123 label: lowercase alphanumeric, "
            f"may contain '-' but not at start or end. Got: '{name}'",
            field=field_name,
            value=name,
        )

    return name


def validate_namespace(
    namespace: str,
    field_name: str = "namespace",
) -> str:
    """Validate a Kubernetes namespace name.

    Namespaces follow the same rules as DNS labels.

    Args:
        namespace: The namespace to validate.
        field_name: Field name for error messages.

    Returns:
        The validated namespace (unchanged).

    Raises:
        ValidationError: If the namespace is invalid.
    """
    return validate_kubernetes_label(namespace, field_name)


def validate_label_selector(
    selector: str,
    field_name: str = "label_selector",
) -> str:
    """Validate a Kubernetes label selector.

    Validates basic label selector syntax. This is a simplified check
    that validates common patterns but may not cover all edge cases.

    Args:
        selector: The selector string (e.g., "app=nginx,env!=prod").
        field_name: Field name for error messages.

    Returns:
        The validated selector (unchanged).

    Raises:
        ValidationError: If the selector is obviously invalid.
    """
    if not selector:
        return selector  # Empty selector is valid

    # Basic validation - check for obvious issues
    # More complex validation would require a full parser
    if len(selector) > 1000:
        raise ValidationError(
            f"{field_name} is too long (max 1000 characters)",
            field=field_name,
            value=selector,
        )

    # Check for balanced parentheses in set-based selectors
    if selector.count("(") != selector.count(")"):
        raise ValidationError(
            f"{field_name} has unbalanced parentheses",
            field=field_name,
            value=selector,
        )

    return selector


# =============================================================================
# Pydantic Annotated Types
# =============================================================================


def _validate_k8s_name(v: str) -> str:
    """Pydantic validator for Kubernetes names."""
    return validate_kubernetes_name(v)


def _validate_k8s_namespace(v: str) -> str:
    """Pydantic validator for Kubernetes namespaces."""
    return validate_namespace(v)


def _validate_k8s_label(v: str) -> str:
    """Pydantic validator for Kubernetes labels."""
    return validate_kubernetes_label(v)


# Annotated types for use in Pydantic models
KubernetesName = Annotated[
    str,
    Field(min_length=1, max_length=253, description="Kubernetes resource name"),
    AfterValidator(_validate_k8s_name),
]

KubernetesNamespace = Annotated[
    str,
    Field(min_length=1, max_length=63, description="Kubernetes namespace"),
    AfterValidator(_validate_k8s_namespace),
]

KubernetesLabel = Annotated[
    str,
    Field(min_length=1, max_length=63, description="Kubernetes label name"),
    AfterValidator(_validate_k8s_label),
]


# =============================================================================
# CRQ Validation
# =============================================================================

# Default CRQ pattern (ServiceNow format)
DEFAULT_CRQ_PATTERN = re.compile(r"^CRQ\d{9}$")


def validate_crq_format(
    crq: str,
    pattern: re.Pattern[str] = DEFAULT_CRQ_PATTERN,
    field_name: str = "crq_number",
) -> str:
    """Validate a Change Request (CRQ) number format.

    Args:
        crq: The CRQ number to validate.
        pattern: Regex pattern for validation.
        field_name: Field name for error messages.

    Returns:
        The validated CRQ (unchanged).

    Raises:
        ValidationError: If the CRQ format is invalid.
    """
    if not crq:
        raise ValidationError(
            f"{field_name} cannot be empty",
            field=field_name,
            value=crq,
        )

    if not pattern.match(crq):
        raise ValidationError(
            f"{field_name} must match pattern {pattern.pattern}. Got: '{crq}'",
            field=field_name,
            value=crq,
        )

    return crq


# =============================================================================
# General Input Validation
# =============================================================================


def validate_positive_int(
    value: int,
    field_name: str = "value",
    max_value: int | None = None,
) -> int:
    """Validate a positive integer.

    Args:
        value: The value to validate.
        field_name: Field name for error messages.
        max_value: Optional maximum value.

    Returns:
        The validated value (unchanged).

    Raises:
        ValidationError: If the value is invalid.
    """
    if value <= 0:
        raise ValidationError(
            f"{field_name} must be positive, got {value}",
            field=field_name,
            value=str(value),
        )

    if max_value is not None and value > max_value:
        raise ValidationError(
            f"{field_name} must be at most {max_value}, got {value}",
            field=field_name,
            value=str(value),
        )

    return value


def sanitize_log_message(message: str, max_length: int = 1000) -> str:
    """Sanitize a message for safe logging.

    Removes or replaces potentially sensitive patterns and truncates
    long messages.

    Args:
        message: The message to sanitize.
        max_length: Maximum allowed length.

    Returns:
        Sanitized message.
    """
    if not message:
        return ""

    # Remove potential secrets (basic patterns)
    # This is a simple check - production should use more sophisticated detection
    sensitive_patterns = [
        (
            re.compile(r"(password|passwd|secret|token|api_key|apikey)[\s:=]+\S+", re.I),
            r"\1=[REDACTED]",
        ),
        (re.compile(r"Bearer\s+\S+", re.I), "Bearer [REDACTED]"),
        (re.compile(r"Basic\s+\S+", re.I), "Basic [REDACTED]"),
    ]

    result = message
    for pattern, replacement in sensitive_patterns:
        result = pattern.sub(replacement, result)

    # Truncate if too long
    if len(result) > max_length:
        result = result[: max_length - 3] + "..."

    return result
