"""Data redaction engine for MOSK MCP Server.

This module provides comprehensive data redaction to protect sensitive
information before it's sent to LLM providers like Claude or OpenAI.

Security Considerations:
- All detected PII is replaced with consistent placeholders
- Placeholders maintain referential integrity (same IP -> same placeholder)
- No sensitive data is logged or cached
- Redaction happens in-memory only
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from re import Pattern
from typing import Any, ClassVar

from mosk_mcp.observability.logging import get_logger


logger = get_logger(__name__)


class PrivacyLevel(str, Enum):
    """Privacy protection levels.

    Attributes:
        NONE: No redaction (not recommended for production)
        MINIMAL: Only redact secrets/credentials
        STANDARD: Redact IPs, MACs, hostnames, secrets (recommended)
        AGGRESSIVE: Redact all identifiable information including UUIDs
    """

    NONE = "none"
    MINIMAL = "minimal"
    STANDARD = "standard"
    AGGRESSIVE = "aggressive"


@dataclass
class RedactionConfig:
    """Configuration for data redaction.

    Attributes:
        level: Privacy protection level
        redact_ipv4: Redact IPv4 addresses
        redact_ipv6: Redact IPv6 addresses
        redact_mac: Redact MAC addresses
        redact_hostname: Redact hostnames/FQDNs
        redact_uuid: Redact UUIDs (instance IDs, volume IDs)
        redact_username: Redact usernames in paths/logs
        redact_email: Redact email addresses
        redact_secrets: Redact passwords, tokens, API keys
        preserve_structure: Keep data structure visible (show types)
        custom_patterns: Additional regex patterns to redact
        allowlist_ips: IPs to never redact (e.g., localhost)
        allowlist_hostnames: Hostnames to never redact
    """

    level: PrivacyLevel = PrivacyLevel.STANDARD
    redact_ipv4: bool = True
    redact_ipv6: bool = True
    redact_mac: bool = True
    redact_hostname: bool = True
    redact_uuid: bool = False  # Off by default, enable for AGGRESSIVE
    redact_username: bool = True
    redact_email: bool = True
    redact_secrets: bool = True
    preserve_structure: bool = True
    custom_patterns: list[tuple[str, str]] = field(default_factory=list)
    allowlist_ips: set[str] = field(
        default_factory=lambda: {"127.0.0.1", "localhost", "::1", "0.0.0.0"}
    )
    allowlist_hostnames: set[str] = field(
        default_factory=lambda: {"localhost", "localhost.localdomain"}
    )

    @classmethod
    def from_level(cls, level: PrivacyLevel) -> RedactionConfig:
        """Create config from privacy level.

        Args:
            level: Privacy level to use.

        Returns:
            RedactionConfig instance.
        """
        if level == PrivacyLevel.NONE:
            return cls(
                level=level,
                redact_ipv4=False,
                redact_ipv6=False,
                redact_mac=False,
                redact_hostname=False,
                redact_uuid=False,
                redact_username=False,
                redact_email=False,
                redact_secrets=False,
            )
        elif level == PrivacyLevel.MINIMAL:
            return cls(
                level=level,
                redact_ipv4=False,
                redact_ipv6=False,
                redact_mac=False,
                redact_hostname=False,
                redact_uuid=False,
                redact_username=False,
                redact_email=False,
                redact_secrets=True,
            )
        elif level == PrivacyLevel.AGGRESSIVE:
            return cls(
                level=level,
                redact_ipv4=True,
                redact_ipv6=True,
                redact_mac=True,
                redact_hostname=True,
                redact_uuid=True,
                redact_username=True,
                redact_email=True,
                redact_secrets=True,
            )
        else:  # STANDARD (default)
            return cls(level=level)

    @classmethod
    def from_env(cls) -> RedactionConfig:
        """Create config from environment variables.

        Environment Variables:
            MCP_PRIVACY_LEVEL: none, minimal, standard, aggressive
            MCP_PRIVACY_REDACT_UUID: true/false
            MCP_PRIVACY_PRESERVE_STRUCTURE: true/false

        Returns:
            RedactionConfig instance.
        """
        level_str = os.environ.get("MCP_PRIVACY_LEVEL", "standard").lower()
        try:
            level = PrivacyLevel(level_str)
        except ValueError:
            logger.warning(
                "privacy_invalid_level",
                level=level_str,
                fallback="standard",
            )
            level = PrivacyLevel.STANDARD

        config = cls.from_level(level)

        # Allow individual overrides
        if os.environ.get("MCP_PRIVACY_REDACT_UUID", "").lower() == "true":
            config.redact_uuid = True
        if os.environ.get("MCP_PRIVACY_PRESERVE_STRUCTURE", "").lower() == "false":
            config.preserve_structure = False

        return config


class DataRedactor:
    """Redacts sensitive data from tool responses.

    This class provides comprehensive PII redaction using pattern matching.
    It maintains referential integrity by using consistent placeholders
    for the same sensitive values within a single redaction session.

    Example:
        redactor = DataRedactor()
        safe_data = redactor.redact({"ip": "192.168.1.100", "host": "server01"})
        # Returns: {"ip": "[IP-1]", "host": "[HOST-1]"}
    """

    # Compiled regex patterns for efficiency
    PATTERNS: ClassVar[dict[str, Pattern[str]]] = {
        "ipv4": re.compile(
            r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}"
            r"(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
        ),
        "ipv6": re.compile(
            r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b|"
            r"\b(?:[0-9a-fA-F]{1,4}:){1,7}:\b|"
            r"\b(?:[0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}\b|"
            r"\b::(?:[0-9a-fA-F]{1,4}:){0,5}[0-9a-fA-F]{1,4}\b|"
            r"\b[0-9a-fA-F]{1,4}::(?:[0-9a-fA-F]{1,4}:){0,5}[0-9a-fA-F]{1,4}\b"
        ),
        "mac": re.compile(r"\b(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}\b"),
        "uuid": re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
        "email": re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b"),
        # FQDN pattern (hostname.domain.tld)
        # Must have at least one dot and not be inside brackets
        "hostname": re.compile(
            r"(?<!\[)"  # Not preceded by [
            r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.){1,}"
            r"(?:[a-zA-Z]{2,})\b"
            r"(?!\])"  # Not followed by ]
        ),
        # Simple hostname (node-01, compute-3, etc.)
        # Negative lookbehind to avoid matching inside placeholders like [HOST-1]
        "simple_hostname": re.compile(
            r"(?<!\[)"  # Not preceded by [
            r"\b(?:node|compute|control|storage|gateway|master|worker|server)"
            r"[-_]?[0-9]+\b",
            re.IGNORECASE,
        ),
        # Username patterns in paths (two patterns for fixed-width lookbehind)
        "username_home": re.compile(r"(?<=/home/)[a-zA-Z0-9_.-]+(?=/|$)"),
        "username_users": re.compile(r"(?<=/Users/)[a-zA-Z0-9_.-]+(?=/|$)"),
        # Secrets and credentials
        "bearer_token": re.compile(r"Bearer\s+[A-Za-z0-9\-_.~+/]+=*", re.IGNORECASE),
        "basic_auth": re.compile(r"Basic\s+[A-Za-z0-9+/]+=*", re.IGNORECASE),
        "password_field": re.compile(
            r"(password|passwd|secret|token|api_key|apikey|auth_token|"
            r"access_token|private_key|privatekey|credential)"
            r"[\s:=]+['\"]?[^\s'\"]+['\"]?",
            re.IGNORECASE,
        ),
        # SSH keys
        "ssh_key": re.compile(r"ssh-(?:rsa|dss|ed25519|ecdsa)\s+[A-Za-z0-9+/]+[=]{0,2}"),
        # Base64 encoded data (potential secrets)
        "base64_secret": re.compile(
            r"\b(?:eyJ[A-Za-z0-9_-]*\.){2}[A-Za-z0-9_-]*\b"  # JWT
        ),
    }

    # Keys that contain sensitive data
    SENSITIVE_KEYS: ClassVar[set[str]] = {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "auth",
        "credential",
        "credentials",
        "private_key",
        "privatekey",
        "access_token",
        "refresh_token",
        "bearer",
        "authorization",
        "ssh_key",
        "ssh_private_key",
        "client_secret",
        "encryption_key",
    }

    def __init__(self, config: RedactionConfig | None = None) -> None:
        """Initialize the data redactor.

        Args:
            config: Redaction configuration. Uses environment config if None.
        """
        self.config = config or RedactionConfig.from_env()
        self._placeholder_cache: dict[str, str] = {}
        self._counters: dict[str, int] = {
            "ip": 0,
            "mac": 0,
            "uuid": 0,
            "host": 0,
            "email": 0,
            "user": 0,
            "secret": 0,
        }
        self._stats = {
            "total_redactions": 0,
            "by_type": {},
        }

    def _get_placeholder(self, value: str, ptype: str) -> str:
        """Get or create a consistent placeholder for a value.

        Args:
            value: The sensitive value to replace.
            ptype: The type of placeholder (ip, mac, uuid, etc.)

        Returns:
            Consistent placeholder string.
        """
        # Create cache key from value hash (don't store actual value)
        cache_key = f"{ptype}:{hashlib.sha256(value.encode()).hexdigest()[:8]}"

        if cache_key not in self._placeholder_cache:
            self._counters[ptype] = self._counters.get(ptype, 0) + 1
            placeholder = f"[{ptype.upper()}-{self._counters[ptype]}]"
            self._placeholder_cache[cache_key] = placeholder
            self._stats["total_redactions"] += 1
            self._stats["by_type"][ptype] = self._stats["by_type"].get(ptype, 0) + 1

        return self._placeholder_cache[cache_key]

    def _redact_text(self, text: str) -> str:
        """Redact sensitive patterns from text.

        Args:
            text: Text to redact.

        Returns:
            Redacted text.
        """
        if not text or not isinstance(text, str):
            return text

        result = text

        # Redact secrets first (they may contain other patterns)
        if self.config.redact_secrets:
            result = self.PATTERNS["bearer_token"].sub(
                lambda m: f"Bearer {self._get_placeholder(m.group(0), 'secret')}",
                result,
            )
            result = self.PATTERNS["basic_auth"].sub(
                lambda m: f"Basic {self._get_placeholder(m.group(0), 'secret')}",
                result,
            )
            result = self.PATTERNS["password_field"].sub(
                lambda m: f"{m.group(1)}={self._get_placeholder(m.group(0), 'secret')}",
                result,
            )
            result = self.PATTERNS["ssh_key"].sub(
                lambda m: self._get_placeholder(m.group(0), "secret"),
                result,
            )
            result = self.PATTERNS["base64_secret"].sub(
                lambda m: self._get_placeholder(m.group(0), "secret"),
                result,
            )

        # Redact emails
        if self.config.redact_email:
            result = self.PATTERNS["email"].sub(
                lambda m: self._get_placeholder(m.group(0), "email"),
                result,
            )

        # Redact IPv4
        if self.config.redact_ipv4:

            def replace_ipv4(match: re.Match) -> str:
                ip = match.group(0)
                if ip in self.config.allowlist_ips:
                    return ip
                return self._get_placeholder(ip, "ip")

            result = self.PATTERNS["ipv4"].sub(replace_ipv4, result)

        # Redact IPv6
        if self.config.redact_ipv6:

            def replace_ipv6(match: re.Match) -> str:
                ip = match.group(0)
                if ip in self.config.allowlist_ips:
                    return ip
                return self._get_placeholder(ip, "ip")

            result = self.PATTERNS["ipv6"].sub(replace_ipv6, result)

        # Redact MAC addresses
        if self.config.redact_mac:
            result = self.PATTERNS["mac"].sub(
                lambda m: self._get_placeholder(m.group(0), "mac"),
                result,
            )

        # Redact UUIDs
        if self.config.redact_uuid:
            result = self.PATTERNS["uuid"].sub(
                lambda m: self._get_placeholder(m.group(0), "uuid"),
                result,
            )

        # Redact hostnames
        if self.config.redact_hostname:

            def replace_hostname(match: re.Match) -> str:
                hostname = match.group(0)
                if hostname.lower() in self.config.allowlist_hostnames:
                    return hostname
                # Don't redact common TLDs without subdomain
                if hostname.count(".") == 1 and hostname.split(".")[1] in {
                    "com",
                    "org",
                    "net",
                    "io",
                    "dev",
                }:
                    return hostname
                return self._get_placeholder(hostname, "host")

            result = self.PATTERNS["hostname"].sub(replace_hostname, result)
            result = self.PATTERNS["simple_hostname"].sub(
                lambda m: self._get_placeholder(m.group(0), "host"),
                result,
            )

        # Redact usernames in paths
        if self.config.redact_username:
            result = self.PATTERNS["username_home"].sub(
                lambda m: self._get_placeholder(m.group(0), "user"),
                result,
            )
            result = self.PATTERNS["username_users"].sub(
                lambda m: self._get_placeholder(m.group(0), "user"),
                result,
            )

        # Apply custom patterns
        for pattern_str, replacement in self.config.custom_patterns:
            try:
                pattern = re.compile(pattern_str)
                result = pattern.sub(replacement, result)
            except re.error as e:
                logger.warning(
                    "privacy_invalid_custom_pattern",
                    pattern=pattern_str,
                    error=str(e),
                )

        return result

    def _redact_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        """Redact sensitive data from a dictionary.

        Args:
            data: Dictionary to redact.

        Returns:
            Redacted dictionary.
        """
        result: dict[str, Any] = {}

        for key, value in data.items():
            key_lower = key.lower()

            # Check if key itself indicates sensitive data
            if any(s in key_lower for s in self.SENSITIVE_KEYS):
                if self.config.redact_secrets:
                    result[key] = self._get_placeholder(str(value), "secret")
                else:
                    result[key] = value
            else:
                result[key] = self.redact(value)

        return result

    def _redact_list(self, data: list[Any]) -> list[Any]:
        """Redact sensitive data from a list.

        Args:
            data: List to redact.

        Returns:
            Redacted list.
        """
        return [self.redact(item) for item in data]

    def redact(self, data: Any) -> Any:
        """Redact sensitive data from any data structure.

        This method recursively processes dictionaries, lists, and strings
        to redact all sensitive information based on the configuration.

        Args:
            data: Data to redact (dict, list, str, or other).

        Returns:
            Redacted data with same structure.

        Example:
            redactor = DataRedactor()
            safe = redactor.redact({
                "server_ip": "192.168.1.100",
                "password": "secret123",
                "nodes": ["node-01", "node-02"]
            })
            # Returns: {
            #     "server_ip": "[IP-1]",
            #     "password": "[SECRET-1]",
            #     "nodes": ["[HOST-1]", "[HOST-2]"]
            # }
        """
        if self.config.level == PrivacyLevel.NONE:
            return data

        if isinstance(data, dict):
            return self._redact_dict(data)
        elif isinstance(data, list):
            return self._redact_list(data)
        elif isinstance(data, str):
            return self._redact_text(data)
        else:
            # For other types (int, float, bool, None), return as-is
            return data

    def redact_json(self, json_str: str) -> str:
        """Redact sensitive data from a JSON string.

        Args:
            json_str: JSON string to redact.

        Returns:
            Redacted JSON string.
        """
        try:
            data = json.loads(json_str)
            redacted = self.redact(data)
            return json.dumps(redacted)
        except json.JSONDecodeError:
            # If not valid JSON, treat as plain text
            return self._redact_text(json_str)

    def get_stats(self) -> dict[str, Any]:
        """Get redaction statistics.

        Returns:
            Dictionary with redaction counts by type.
        """
        return self._stats.copy()

    def reset(self) -> None:
        """Reset placeholder cache and counters.

        Call this between sessions to ensure different placeholder
        mappings for different tool invocations.
        """
        self._placeholder_cache.clear()
        self._counters = {
            "ip": 0,
            "mac": 0,
            "uuid": 0,
            "host": 0,
            "email": 0,
            "user": 0,
            "secret": 0,
        }


# Module-level singleton
_redactor: DataRedactor | None = None


def get_redactor(
    level: str | PrivacyLevel | None = None,
    config: RedactionConfig | None = None,
) -> DataRedactor:
    """Get the data redactor singleton.

    Args:
        level: Privacy level override.
        config: Full config override.

    Returns:
        DataRedactor instance.
    """
    global _redactor

    if config is not None:
        return DataRedactor(config)

    if level is not None:
        if isinstance(level, str):
            level = PrivacyLevel(level)
        return DataRedactor(RedactionConfig.from_level(level))

    if _redactor is None:
        _redactor = DataRedactor()

    return _redactor


def redact_response(data: Any, level: str | PrivacyLevel | None = None) -> Any:
    """Convenience function to redact data.

    Args:
        data: Data to redact.
        level: Optional privacy level override.

    Returns:
        Redacted data.
    """
    redactor = get_redactor(level=level)
    return redactor.redact(data)


def is_privacy_enabled() -> bool:
    """Check if privacy protection is enabled.

    Returns:
        True if privacy is enabled (default: False).
    """
    enabled = os.environ.get("MCP_PRIVACY_ENABLED", "false").lower()
    return enabled in ("true", "1", "yes")
