"""Privacy and data protection module for MOSK MCP Server.

This module provides data redaction and PII protection to prevent
sensitive information from being exposed to LLM providers.

Key Features:
- IP address redaction (IPv4, IPv6)
- MAC address redaction
- Hostname/FQDN redaction
- UUID/instance ID redaction
- Username redaction
- Custom pattern redaction
- Optional Microsoft Presidio integration for advanced PII detection

Configuration:
- MCP_PRIVACY_ENABLED=true - Enable privacy protection (default: false)
- MCP_PRIVACY_LEVEL=standard - Redaction level: none, minimal, standard, aggressive
- MCP_PRIVACY_PRESIDIO=false - Use Microsoft Presidio for advanced detection
- MCP_PRIVACY_PRESERVE_STRUCTURE=true - Keep data structure visible

Usage:
    from mosk_mcp.privacy import get_redactor, redact_response

    # Simple redaction
    safe_data = redact_response(sensitive_data)

    # With custom config
    redactor = get_redactor(level="aggressive")
    safe_data = redactor.redact(sensitive_data)
"""

from mosk_mcp.privacy.middleware import PrivacyMiddleware, create_privacy_middleware
from mosk_mcp.privacy.redactor import (
    DataRedactor,
    PrivacyLevel,
    RedactionConfig,
    get_redactor,
    is_privacy_enabled,
    redact_response,
)


__all__ = [
    "DataRedactor",
    "PrivacyLevel",
    "PrivacyMiddleware",
    "RedactionConfig",
    "create_privacy_middleware",
    "get_redactor",
    "is_privacy_enabled",
    "redact_response",
]
