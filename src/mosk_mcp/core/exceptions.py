"""Exception hierarchy for MOSK MCP Server.

This module defines all custom exceptions used throughout the application.
All exceptions inherit from MoskMCPError for easy catching of application-specific errors.
"""

from __future__ import annotations

import os
import re
from typing import Any


# Production mode - controlled by environment variable
_PRODUCTION_MODE = os.environ.get("MCP_ENVIRONMENT", "development").lower() == "production"

# Patterns to redact from error messages in production
_SENSITIVE_PATTERNS = [
    # File paths (kubeconfig, etc.)
    (r"/[a-zA-Z0-9._/-]+kubeconfig[a-zA-Z0-9._/-]*", "[REDACTED_PATH]"),
    (r"/Users/[a-zA-Z0-9._/-]+", "[REDACTED_PATH]"),
    (r"/home/[a-zA-Z0-9._/-]+", "[REDACTED_PATH]"),
    (r"/tmp/[a-zA-Z0-9._/-]+", "[REDACTED_TEMP_PATH]"),
    # URLs with potential tokens
    (r"https?://[^\s]+token=[^\s&]+", "[REDACTED_URL]"),
    (r"https?://[^\s]+access_token=[^\s&]+", "[REDACTED_URL]"),
    # Bearer tokens
    (r"Bearer\s+[a-zA-Z0-9._-]+", "Bearer [REDACTED]"),
    # IP addresses (keep format but obscure)
    (r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "[REDACTED_IP]"),
]


def _sanitize_message(message: str) -> str:
    """Sanitize a message by redacting sensitive information.

    Args:
        message: The raw message.

    Returns:
        Sanitized message with sensitive data redacted.
    """
    sanitized = message
    for pattern, replacement in _SENSITIVE_PATTERNS:
        sanitized = re.sub(pattern, replacement, sanitized)
    return sanitized


def _sanitize_details(details: dict[str, Any]) -> dict[str, Any]:
    """Sanitize error details by redacting sensitive keys and values.

    Args:
        details: The raw error details.

    Returns:
        Sanitized details with sensitive data redacted.
    """
    # Keys that should be completely redacted
    sensitive_keys = {
        "kubeconfig_path",
        "config_path",
        "file_path",
        "endpoint",
        "url",
        "token",
        "access_token",
        "refresh_token",
        "password",
        "secret",
        "api_key",
    }

    sanitized: dict[str, Any] = {}
    for key, value in details.items():
        if key.lower() in sensitive_keys:
            sanitized[key] = "[REDACTED]"
        elif isinstance(value, str):
            sanitized[key] = _sanitize_message(value)
        elif isinstance(value, dict):
            sanitized[key] = _sanitize_details(value)
        else:
            sanitized[key] = value

    return sanitized


def sanitize_for_production(message: str) -> str:
    """Sanitize error message for production environments.

    Only sanitizes when MCP_ENVIRONMENT=production. Use _sanitize_message
    for unconditional sanitization.

    Args:
        message: The raw error message.

    Returns:
        Sanitized error message safe for production.
    """
    if not _PRODUCTION_MODE:
        return message
    return _sanitize_message(message)


def sanitize_details_for_production(details: dict[str, Any]) -> dict[str, Any]:
    """Sanitize error details for production environments.

    Only sanitizes when MCP_ENVIRONMENT=production. Use _sanitize_details
    for unconditional sanitization.

    Args:
        details: The raw error details.

    Returns:
        Sanitized details safe for production.
    """
    if not _PRODUCTION_MODE:
        return details
    return _sanitize_details(details)


class MoskMCPError(Exception):
    """Base exception for all MOSK MCP Server errors.

    All application-specific exceptions inherit from this class,
    allowing calling code to catch all MOSK MCP errors with a single
    except clause while still allowing fine-grained error handling.

    Attributes:
        message: Human-readable error message.
        details: Optional additional context about the error.
        error_code: Optional error code for programmatic handling.
    """

    def __init__(
        self,
        message: str,
        details: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> None:
        """Initialize the exception.

        Args:
            message: Human-readable error message.
            details: Optional dictionary with additional error context.
            error_code: Optional error code for programmatic handling.
        """
        super().__init__(message)
        self.message = message
        self.details = details or {}
        self.error_code = error_code or self._default_error_code()

    def _default_error_code(self) -> str:
        """Generate default error code from class name."""
        return self.__class__.__name__.upper()

    def to_dict(self, production_safe: bool = False) -> dict[str, Any]:
        """Convert exception to dictionary for JSON serialization.

        Args:
            production_safe: If True, sanitize sensitive information.
                Defaults to False; auto-enabled when MCP_ENVIRONMENT=production.

        Returns:
            Dictionary representation of the error.
        """
        should_sanitize = production_safe or _PRODUCTION_MODE

        if should_sanitize:
            message = _sanitize_message(self.message)
            details = _sanitize_details(self.details)
        else:
            message = self.message
            details = self.details

        return {
            "error": self.error_code,
            "message": message,
            "details": details,
        }

    def to_safe_dict(self) -> dict[str, Any]:
        """Convert exception to production-safe dictionary.

        Always sanitizes sensitive information regardless of environment.

        Returns:
            Sanitized dictionary representation of the error.
        """
        return self.to_dict(production_safe=True)

    def __str__(self) -> str:
        """Return string representation of the error."""
        if self.details:
            return f"{self.message} (details: {self.details})"
        return self.message

    def __repr__(self) -> str:
        """Return detailed string representation."""
        return (
            f"{self.__class__.__name__}("
            f"message={self.message!r}, "
            f"details={self.details!r}, "
            f"error_code={self.error_code!r})"
        )


class AuthenticationError(MoskMCPError):
    """Raised when authentication fails.

    This exception is raised when:
    - API key is missing or invalid
    - Token has expired
    - Credentials are malformed

    Attributes:
        message: Human-readable error message.
        auth_method: The authentication method that failed.
    """

    def __init__(
        self,
        message: str = "Authentication failed",
        auth_method: str | None = None,
        details: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> None:
        """Initialize authentication error.

        Args:
            message: Human-readable error message.
            auth_method: The authentication method that failed (e.g., 'api_key', 'oidc').
            details: Optional additional context.
            error_code: Optional specific error code (defaults to AUTHENTICATION_ERROR).
        """
        details = details or {}
        if auth_method:
            details["auth_method"] = auth_method
        super().__init__(message, details, error_code=error_code or "AUTHENTICATION_ERROR")
        self.auth_method = auth_method


class AuthorizationError(MoskMCPError):
    """Raised when user lacks required permissions.

    This exception is raised when an authenticated user attempts
    to perform an action they are not authorized for.

    Attributes:
        message: Human-readable error message.
        required_permission: The permission that was required.
        user: The user who was denied access.
        resource: The resource that was being accessed.
    """

    def __init__(
        self,
        message: str = "Access denied",
        required_permission: str | None = None,
        user: str | None = None,
        resource: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize authorization error.

        Args:
            message: Human-readable error message.
            required_permission: The permission that was required.
            user: The user who was denied access.
            resource: The resource that was being accessed.
            details: Optional additional context.
        """
        details = details or {}
        if required_permission:
            details["required_permission"] = required_permission
        if user:
            details["user"] = user
        if resource:
            details["resource"] = resource
        super().__init__(message, details, error_code="AUTHORIZATION_ERROR")
        self.required_permission = required_permission
        self.user = user
        self.resource = resource


class UnsupportedVersionError(MoskMCPError):
    """Raised when MOSK cluster version is not supported.

    This exception is raised when the connected MOSK cluster version
    does not meet the minimum requirements (25.1+). The MCP tools are
    designed for MOSK 25.1+ and will not work correctly with older versions.

    Attributes:
        message: Human-readable error message.
        detected_version: The version that was detected.
        required_version: The minimum required version.
    """

    def __init__(
        self,
        message: str = "MOSK version not supported",
        detected_version: str | None = None,
        required_version: str = "25.1",
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize unsupported version error.

        Args:
            message: Human-readable error message.
            detected_version: The version that was detected.
            required_version: The minimum required version.
            details: Optional additional context.
        """
        details = details or {}
        if detected_version:
            details["detected_version"] = detected_version
        details["required_version"] = required_version
        super().__init__(message, details, error_code="UNSUPPORTED_VERSION")
        self.detected_version = detected_version
        self.required_version = required_version


class ValidationError(MoskMCPError):
    """Raised when input validation fails.

    This exception is raised when:
    - Required parameters are missing
    - Parameter values are invalid
    - Input format is incorrect

    Attributes:
        message: Human-readable error message.
        field: The field that failed validation.
        value: The invalid value (sanitized).
        constraint: The constraint that was violated.
    """

    def __init__(
        self,
        message: str = "Validation failed",
        field: str | None = None,
        value: Any = None,
        constraint: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize validation error.

        Args:
            message: Human-readable error message.
            field: The field that failed validation.
            value: The invalid value (will be sanitized in output).
            constraint: Description of the constraint that was violated.
            details: Optional additional context.
        """
        details = details or {}
        if field:
            details["field"] = field
        if value is not None:
            # Sanitize value to avoid exposing sensitive data
            details["value"] = self._sanitize_value(value)
        if constraint:
            details["constraint"] = constraint
        super().__init__(message, details, error_code="VALIDATION_ERROR")
        self.field = field
        self.value = value
        self.constraint = constraint

    @staticmethod
    def _sanitize_value(value: Any) -> str:
        """Sanitize value for safe logging.

        Args:
            value: The value to sanitize.

        Returns:
            Sanitized string representation of the value.
        """
        str_value = str(value)
        # Truncate long values
        if len(str_value) > 100:
            return str_value[:100] + "..."
        return str_value


class KubernetesError(MoskMCPError):
    """Raised when Kubernetes operations fail.

    This exception is raised when:
    - API calls to Kubernetes fail
    - Resources cannot be found
    - Permission denied by Kubernetes RBAC
    - Connection to cluster fails

    Attributes:
        message: Human-readable error message.
        operation: The operation that failed.
        resource_kind: The kind of resource involved.
        resource_name: The name of the resource.
        namespace: The namespace of the resource.
        status_code: HTTP status code from the API.
    """

    def __init__(
        self,
        message: str = "Kubernetes operation failed",
        operation: str | None = None,
        resource_kind: str | None = None,
        resource_name: str | None = None,
        namespace: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize Kubernetes error.

        Args:
            message: Human-readable error message.
            operation: The operation that failed (e.g., 'get', 'create', 'delete').
            resource_kind: The kind of resource (e.g., 'Machine', 'Pod').
            resource_name: The name of the resource.
            namespace: The namespace of the resource.
            status_code: HTTP status code from the Kubernetes API.
            details: Optional additional context.
        """
        details = details or {}
        if operation:
            details["operation"] = operation
        if resource_kind:
            details["resource_kind"] = resource_kind
        if resource_name:
            details["resource_name"] = resource_name
        if namespace:
            details["namespace"] = namespace
        if status_code:
            details["status_code"] = status_code
        super().__init__(message, details, error_code="KUBERNETES_ERROR")
        self.operation = operation
        self.resource_kind = resource_kind
        self.resource_name = resource_name
        self.namespace = namespace
        self.status_code = status_code


class ToolExecutionError(MoskMCPError):
    """Raised when tool execution fails.

    This exception is raised when:
    - A tool encounters an error during execution
    - Tool preconditions are not met
    - Tool timeout occurs

    Attributes:
        message: Human-readable error message.
        tool_name: The name of the tool that failed.
        phase: The phase of execution where failure occurred.
    """

    def __init__(
        self,
        message: str = "Tool execution failed",
        tool_name: str | None = None,
        phase: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize tool execution error.

        Args:
            message: Human-readable error message.
            tool_name: The name of the tool that failed.
            phase: The phase of execution (e.g., 'validation', 'execution', 'cleanup').
            details: Optional additional context.
        """
        details = details or {}
        if tool_name:
            details["tool_name"] = tool_name
        if phase:
            details["phase"] = phase
        super().__init__(message, details, error_code="TOOL_EXECUTION_ERROR")
        self.tool_name = tool_name
        self.phase = phase


class ConfigurationError(MoskMCPError):
    """Raised when configuration is invalid or missing.

    This exception is raised when:
    - Required configuration is missing
    - Configuration values are invalid
    - Configuration files cannot be read

    Attributes:
        message: Human-readable error message.
        config_key: The configuration key that is invalid.
    """

    def __init__(
        self,
        message: str = "Configuration error",
        config_key: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize configuration error.

        Args:
            message: Human-readable error message.
            config_key: The configuration key that is invalid or missing.
            details: Optional additional context.
        """
        details = details or {}
        if config_key:
            details["config_key"] = config_key
        super().__init__(message, details, error_code="CONFIGURATION_ERROR")
        self.config_key = config_key


class MoskConnectionError(MoskMCPError):
    """Raised when connection to external service fails.

    This exception is raised when:
    - Cannot connect to Kubernetes cluster
    - Cannot connect to external APIs
    - Network timeout occurs

    Note:
        This class is named MoskConnectionError to avoid shadowing Python's
        built-in ConnectionError exception.

    Attributes:
        message: Human-readable error message.
        service: The service that could not be reached.
        endpoint: The endpoint that was being connected to.
    """

    def __init__(
        self,
        message: str = "Connection failed",
        service: str | None = None,
        endpoint: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize connection error.

        Args:
            message: Human-readable error message.
            service: The service that could not be reached.
            endpoint: The endpoint that was being connected to.
            details: Optional additional context.
        """
        details = details or {}
        if service:
            details["service"] = service
        if endpoint:
            details["endpoint"] = endpoint
        super().__init__(message, details, error_code="CONNECTION_ERROR")
        self.service = service
        self.endpoint = endpoint


class ResourceNotFoundError(MoskMCPError):
    """Raised when a requested resource is not found.

    This exception is raised when:
    - Kubernetes resource does not exist
    - Configuration file not found
    - Referenced entity missing

    Attributes:
        message: Human-readable error message.
        resource_type: The type of resource.
        resource_id: The identifier of the resource.
    """

    def __init__(
        self,
        message: str = "Resource not found",
        resource_type: str | None = None,
        resource_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize resource not found error.

        Args:
            message: Human-readable error message.
            resource_type: The type of resource (e.g., 'Machine', 'Pod').
            resource_id: The identifier of the resource.
            details: Optional additional context.
        """
        details = details or {}
        if resource_type:
            details["resource_type"] = resource_type
        if resource_id:
            details["resource_id"] = resource_id
        super().__init__(message, details, error_code="RESOURCE_NOT_FOUND")
        self.resource_type = resource_type
        self.resource_id = resource_id


class RateLimitError(MoskMCPError):
    """Raised when rate limit is exceeded.

    This exception is raised when:
    - Too many requests in a time window
    - API quota exceeded

    Attributes:
        message: Human-readable error message.
        retry_after: Seconds to wait before retrying.
    """

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        retry_after: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize rate limit error.

        Args:
            message: Human-readable error message.
            retry_after: Seconds to wait before retrying.
            details: Optional additional context.
        """
        details = details or {}
        if retry_after:
            details["retry_after"] = retry_after
        super().__init__(message, details, error_code="RATE_LIMIT_ERROR")
        self.retry_after = retry_after
