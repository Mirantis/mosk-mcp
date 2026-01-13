"""Keycloak client utilities for MOSK MCP Server.

This module provides Keycloak SSO utilities for Device Flow authentication:
- JWT token decoding and claims extraction
- MCC endpoint discovery from config.js
- StackLight endpoint discovery from K8s services
- Kubeconfig generation for authenticated sessions
- Token exchange for MOSK cluster access (RFC 8693)

Authentication is handled by the Device Flow mechanism in device_flow.py.
This module provides supporting utilities for endpoint discovery and
kubeconfig generation.

Architecture Notes:
    The MCC Keycloak is configured so that the 'kaas' client's id_token
    can access ALL services:
    - Kubernetes API (configured for OIDC with 'kaas' audience)
    - Prometheus/Alertmanager (via IAM Proxy with oidc_extra_audiences="kaas")

    This "unified session" architecture means we only need to authenticate once
    and use the same token everywhere.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx

from mosk_mcp.core.exceptions import AuthenticationError as MoskAuthError
from mosk_mcp.observability.logging import get_logger


logger = get_logger(__name__)


# =============================================================================
# JWT Utilities
# =============================================================================


def decode_jwt_payload(token: str) -> dict[str, Any] | None:
    """Decode JWT payload without signature verification.

    This decodes the JWT for informational purposes (username, roles, etc.).
    Actual token validation is done by the APIs that receive the token.

    Args:
        token: JWT token string in format "header.payload.signature".

    Returns:
        Decoded payload claims as dictionary, or None if decoding fails.
        Note: Returns None (not empty dict) for malformed tokens, so callers
        can distinguish between "token has no claims" vs "token is invalid".

    Example:
        >>> claims = decode_jwt_payload(id_token)
        >>> if claims is not None:
        ...     username = claims.get("preferred_username", "unknown")
        ...     iam_roles = claims.get("iam_roles", [])
    """
    import base64

    if not token or not isinstance(token, str):
        logger.warning(
            "jwt_decode_invalid_input",
            token_type=type(token).__name__,
            has_value=bool(token),
        )
        return None

    parts = token.split(".")
    if len(parts) != 3:
        logger.warning(
            "jwt_decode_invalid_format",
            parts_count=len(parts),
            expected=3,
        )
        return None

    try:
        payload = parts[1]
        # Add padding if needed for base64 decoding
        # Base64 requires length to be multiple of 4
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        return cast("dict[str, Any]", json.loads(base64.urlsafe_b64decode(payload)))
    except (ValueError, json.JSONDecodeError) as e:
        # Specific exceptions for base64/JSON parsing errors
        # These are expected for malformed tokens
        logger.warning(
            "jwt_decode_parse_error",
            error_type=type(e).__name__,
            error=str(e),
            token_length=len(token) if token else 0,
        )
        return None
    except Exception as e:
        # Unexpected errors (memory issues, etc.) - log with full context
        # These should be investigated as they may indicate serious issues
        logger.error(
            "jwt_decode_unexpected_error",
            error_type=type(e).__name__,
            error=str(e),
            token_length=len(token) if token else 0,
            hint="This is an unexpected error type. Consider investigating.",
        )
        # Re-raise for memory errors and other critical issues
        # These should not be silently swallowed
        if isinstance(e, (MemoryError, SystemError)):
            raise
        return None


def get_jwt_claim(token: str, claim: str, default: Any = None) -> Any:
    """Extract a specific claim from a JWT token.

    Args:
        token: JWT token string.
        claim: Name of the claim to extract.
        default: Default value if claim is not found.

    Returns:
        Claim value, or default if not found or decoding fails.

    Example:
        >>> username = get_jwt_claim(token, "preferred_username", "unknown")
        >>> iam_roles = get_jwt_claim(token, "iam_roles", [])
    """
    claims = decode_jwt_payload(token)
    if claims is None:
        return default
    return claims.get(claim, default)


def get_iam_roles(token: str) -> list[str]:
    """Extract IAM roles from a JWT token.

    Args:
        token: JWT token string.

    Returns:
        List of IAM role strings, or empty list if not found.

    Example:
        >>> roles = get_iam_roles(id_token)
        >>> if "m:kaas:admin" in roles:
        ...     print("User is admin")
    """
    return cast("list[str]", get_jwt_claim(token, "iam_roles", []))


# =============================================================================
# Data Models
# =============================================================================


@dataclass(frozen=True)
class TokenResponse:
    """OIDC token response from Keycloak.

    This is a frozen (immutable) dataclass to prevent accidental modification
    of token data after authentication.

    Attributes:
        access_token: OAuth2 access token (used by some services).
        id_token: OIDC ID token (used for K8s API and IAM Proxy).
        refresh_token: Token for refreshing the session.
        token_type: Token type (usually "Bearer").
        expires_in: Access token lifetime in seconds.
        refresh_expires_in: Refresh token lifetime in seconds.
        scope: Granted OAuth scopes.
        issued_at: When the token was issued (for computing expiry).
    """

    access_token: str
    id_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int = 1800  # 30 minutes default fallback
    refresh_expires_in: int = 3600  # 1 hour default fallback
    scope: str = ""
    # Store when token was issued to compute expiry dynamically
    issued_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def access_token_expiry(self) -> datetime:
        """Compute access token expiry timestamp.

        Returns expiry based on issued_at + expires_in.
        """
        return self.issued_at + timedelta(seconds=self.expires_in)

    @property
    def id_token_claims(self) -> dict[str, Any]:
        """Decode and return claims from id_token.

        Returns empty dict if token cannot be decoded.
        """
        claims = decode_jwt_payload(self.id_token)
        return claims if claims is not None else {}

    @property
    def is_expired(self) -> bool:
        """Check if access token is expired (with 30s buffer)."""
        return datetime.now(UTC) >= (self.access_token_expiry - timedelta(seconds=30))

    def is_expired_with_buffer(self, buffer_seconds: int = 60) -> bool:
        """Check if access token is expired or will expire within buffer time.

        Args:
            buffer_seconds: Time buffer before actual expiry (default 60s).

        Returns:
            True if token is expired or will expire within buffer time.
        """
        return datetime.now(UTC) >= (self.access_token_expiry - timedelta(seconds=buffer_seconds))

    @property
    def username(self) -> str:
        """Get username from token claims."""
        return cast("str", self.id_token_claims.get("preferred_username", "unknown"))

    @property
    def iam_roles(self) -> list[str]:
        """Get IAM roles from token claims.

        These roles determine K8s RBAC permissions:
        - m:kaas@global-admin: Full admin access
        - m:kaas:{namespace}@operator: Namespace operator
        - m:sl:{namespace}:{cluster}@stacklight-admin: StackLight access
        """
        return cast("list[str]", self.id_token_claims.get("iam_roles", []))

    @property
    def subject(self) -> str:
        """Get subject (user ID) from token claims."""
        return cast("str", self.id_token_claims.get("sub", ""))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "username": self.username,
            "subject": self.subject,
            "expires_in": self.expires_in,
            "iam_roles": self.iam_roles,
            "scope": self.scope,
            "is_expired": self.is_expired,
        }


@dataclass
class MCCEndpoints:
    """Discovered MCC endpoint URLs.

    These endpoints are discovered from the MCC UI config.js file,
    which contains all the URLs needed for authentication and API access.

    Attributes:
        keycloak_url: Keycloak base URL (e.g., "https://keycloak.example.com").
        keycloak_realm: Keycloak realm name (default: "iam").
        keycloak_client_id: OIDC client ID (default: "kaas").
        k8s_api_url: Kubernetes API server URL.
        prometheus_url: Prometheus IAM Proxy URL.
        alertmanager_url: Alertmanager IAM Proxy URL.
        grafana_url: Grafana IAM Proxy URL.
        opensearch_url: OpenSearch/Kibana IAM Proxy URL.
    """

    keycloak_url: str
    keycloak_realm: str = "iam"
    keycloak_client_id: str = "kaas"
    k8s_api_url: str = ""
    prometheus_url: str = ""
    alertmanager_url: str = ""
    grafana_url: str = ""
    opensearch_url: str = ""

    @property
    def token_endpoint(self) -> str:
        """Get Keycloak token endpoint URL."""
        return (
            f"{self.keycloak_url}/auth/realms/{self.keycloak_realm}/protocol/openid-connect/token"
        )

    @property
    def userinfo_endpoint(self) -> str:
        """Get Keycloak userinfo endpoint URL."""
        return f"{self.keycloak_url}/auth/realms/{self.keycloak_realm}/protocol/openid-connect/userinfo"

    @property
    def issuer_url(self) -> str:
        """Get OIDC issuer URL."""
        return f"{self.keycloak_url}/auth/realms/{self.keycloak_realm}"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "keycloak_url": self.keycloak_url,
            "keycloak_realm": self.keycloak_realm,
            "keycloak_client_id": self.keycloak_client_id,
            "k8s_api_url": self.k8s_api_url,
            "prometheus_url": self.prometheus_url,
            "alertmanager_url": self.alertmanager_url,
            "grafana_url": self.grafana_url,
            "opensearch_url": self.opensearch_url,
        }


# =============================================================================
# MCC Endpoint Discovery
# =============================================================================


async def discover_mcc_endpoints(
    mcc_ui_url: str,
    *,
    timeout: float = 30.0,
    verify_ssl: bool = True,
) -> MCCEndpoints:
    """Discover MCC endpoints from the UI config.js.

    The MCC UI serves a config.js file containing endpoint URLs for:
    - Keycloak (authentication)
    - Kubernetes API server
    - Other services (derived from config)

    Args:
        mcc_ui_url: MCC UI URL (e.g., "https://mcc.example.com" or "mcc.example.com").
        timeout: HTTP request timeout in seconds.
        verify_ssl: Whether to verify SSL certificates.

    Returns:
        MCCEndpoints with discovered URLs.

    Raises:
        MoskAuthError: If discovery fails.
    """
    # Normalize URL
    if not mcc_ui_url.startswith("http"):
        mcc_ui_url = f"https://{mcc_ui_url}"
    mcc_ui_url = mcc_ui_url.rstrip("/")

    logger.info("discovering_mcc_endpoints", mcc_url=mcc_ui_url)

    try:
        async with httpx.AsyncClient(
            verify=verify_ssl,
            timeout=httpx.Timeout(timeout),
        ) as client:
            # Fetch config.js from MCC UI
            response = await client.get(f"{mcc_ui_url}/config.js")

            if response.status_code != 200:
                raise MoskAuthError(
                    f"Failed to fetch MCC UI config: HTTP {response.status_code}",
                    details={"url": f"{mcc_ui_url}/config.js"},
                )

            # Parse config.js (it's JavaScript, but we can extract the JSON)
            config_text = response.text

            # Extract JSON from "window.CONFIG = {...};"
            start = config_text.find("{")
            end = config_text.rfind("}") + 1

            if start == -1 or end == 0:
                raise MoskAuthError(
                    "Could not parse config.js: no JSON object found",
                    details={"content_preview": config_text[:200]},
                )

            config = json.loads(config_text[start:end])

            # Extract endpoints
            keycloak_config = config.get("keycloak", {})
            k8s_config = config.get("kubernetes", {})

            # Keycloak URL: extract base from url or idp-issuer-url
            keycloak_url = keycloak_config.get("url", "")
            if "/auth/realms" in keycloak_url:
                keycloak_url = keycloak_url.rsplit("/auth/realms", 1)[0]

            if not keycloak_url:
                # Fallback: derive from idp-issuer-url
                issuer = keycloak_config.get("idp-issuer-url", "")
                if issuer and "/auth/realms" in issuer:
                    keycloak_url = issuer.rsplit("/auth/realms", 1)[0]

            # K8s API URL
            k8s_api_url = k8s_config.get("server", "")

            # Keycloak realm and client
            realm = keycloak_config.get("realm", "iam")
            client_id = keycloak_config.get("clientId", "kaas")

            endpoints = MCCEndpoints(
                keycloak_url=keycloak_url,
                keycloak_realm=realm,
                keycloak_client_id=client_id,
                k8s_api_url=k8s_api_url,
            )

            logger.info(
                "mcc_endpoints_discovered",
                keycloak_url=endpoints.keycloak_url,
                k8s_api_url=endpoints.k8s_api_url,
            )

            return endpoints

    except httpx.RequestError as e:
        raise MoskAuthError(
            f"Failed to connect to MCC UI: {e}",
            details={"url": mcc_ui_url},
        ) from e
    except json.JSONDecodeError as e:
        raise MoskAuthError(
            f"Failed to parse MCC config.js: {e}",
            details={"url": f"{mcc_ui_url}/config.js"},
        ) from e


async def discover_stacklight_endpoints(
    k8s_api_url: str,
    id_token: str,
    *,
    namespace: str = "stacklight",
    timeout: float = 30.0,
    verify_ssl: bool = True,
) -> dict[str, str]:
    """Discover StackLight IAM Proxy endpoints from K8s services.

    StackLight services are exposed via LoadBalancer services with
    external IPs. This function queries the K8s API to find them.

    Args:
        k8s_api_url: Kubernetes API server URL.
        id_token: Valid id_token for K8s API authentication.
        namespace: StackLight namespace (default: "stacklight").
        timeout: HTTP request timeout in seconds.
        verify_ssl: Whether to verify SSL certificates.

    Returns:
        Dict with prometheus_url, alertmanager_url, grafana_url, opensearch_url.
        Empty strings for services not found.
    """
    endpoints: dict[str, str] = {
        "prometheus_url": "",
        "alertmanager_url": "",
        "grafana_url": "",
        "opensearch_url": "",
    }

    logger.debug("discovering_stacklight_endpoints", namespace=namespace)

    try:
        async with httpx.AsyncClient(
            verify=verify_ssl,
            timeout=httpx.Timeout(timeout),
        ) as client:
            # List services in stacklight namespace
            response = await client.get(
                f"{k8s_api_url}/api/v1/namespaces/{namespace}/services",
                headers={"Authorization": f"Bearer {id_token}"},
            )

            if response.status_code == 403:
                logger.debug("stacklight_discovery_rbac_denied")
                return endpoints

            if response.status_code != 200:
                logger.debug(
                    "stacklight_discovery_failed",
                    status_code=response.status_code,
                )
                return endpoints

            services = response.json().get("items", [])

            for svc in services:
                name = svc["metadata"]["name"]
                spec = svc.get("spec", {})

                # Look for LoadBalancer services with external IPs
                if spec.get("type") != "LoadBalancer":
                    continue

                status = svc.get("status", {}).get("loadBalancer", {})
                ingress = status.get("ingress", [])
                if not ingress:
                    continue

                external_ip = ingress[0].get("ip")
                if not external_ip:
                    continue

                port = spec.get("ports", [{}])[0].get("port", 443)
                url = f"https://{external_ip}" if port == 443 else f"https://{external_ip}:{port}"

                # Map service names to endpoint keys
                name_lower = name.lower()
                if "prometheus" in name_lower and "iam" in name_lower:
                    endpoints["prometheus_url"] = url
                elif "alertmanager" in name_lower and "iam" in name_lower:
                    endpoints["alertmanager_url"] = url
                elif "grafana" in name_lower and "iam" in name_lower:
                    endpoints["grafana_url"] = url
                elif ("kibana" in name_lower or "opensearch" in name_lower) and "iam" in name_lower:
                    endpoints["opensearch_url"] = url

            logger.info(
                "stacklight_endpoints_discovered",
                prometheus=bool(endpoints["prometheus_url"]),
                alertmanager=bool(endpoints["alertmanager_url"]),
            )

    except Exception as e:
        logger.debug("stacklight_discovery_error", error=str(e))

    return endpoints


# =============================================================================
# Kubeconfig Generation
# =============================================================================


def generate_token_kubeconfig(
    endpoints: MCCEndpoints,
    tokens: TokenResponse,
    cluster_name: str = "mcc",
    *,
    ca_data: str | None = None,
) -> str:
    """Generate a kubeconfig file with a static Bearer token.

    This is a simpler variant that uses the id_token directly as a Bearer
    token, without the OIDC provider configuration. This is useful when:
    - The kubectl oidc-login plugin is not available
    - You need a simple, short-lived kubeconfig
    - Token refresh will be handled externally

    Note: This kubeconfig will stop working when the token expires (typically
    5-30 minutes). For longer sessions, use Device Flow with token refresh.

    Args:
        endpoints: MCC endpoint configuration with K8s API URL.
        tokens: Token response from Keycloak authentication.
        cluster_name: Name for the cluster context (default: "mcc").
        ca_data: Base64-encoded CA certificate data.

    Returns:
        YAML string of the kubeconfig file.
    """
    import yaml

    user_name = f"{cluster_name}-{tokens.username}"
    context_name = f"{cluster_name}-{tokens.username}"

    # Build cluster config
    cluster_config: dict[str, Any] = {
        "server": endpoints.k8s_api_url,
    }
    if ca_data:
        cluster_config["certificate-authority-data"] = ca_data
    else:
        cluster_config["insecure-skip-tls-verify"] = True

    # Simple token-based user config
    user_config: dict[str, Any] = {
        "token": tokens.id_token,
    }

    kubeconfig = {
        "apiVersion": "v1",
        "kind": "Config",
        "current-context": context_name,
        "clusters": [
            {
                "name": cluster_name,
                "cluster": cluster_config,
            }
        ],
        "contexts": [
            {
                "name": context_name,
                "context": {
                    "cluster": cluster_name,
                    "user": user_name,
                },
            }
        ],
        "users": [
            {
                "name": user_name,
                "user": user_config,
            }
        ],
        "preferences": {},
    }

    return yaml.dump(kubeconfig, default_flow_style=False, sort_keys=False)


@dataclass
class ClusterOIDCInfo:
    """OIDC configuration from Cluster CR status.providerStatus.oidc.

    This contains the information needed to generate user kubeconfigs
    for child clusters (MOSK clusters managed by MCC).

    Attributes:
        client_id: OIDC client ID for the cluster (usually "k8s" for child clusters).
        issuer_url: Keycloak issuer URL (e.g., "https://keycloak.example.com/auth/realms/iam").
        certificate: Base64-encoded IDP CA certificate.
        groups_claim: Claim used for group/role mapping (usually "iam_roles").
        api_server_certificate: Base64-encoded K8s API server CA certificate.
        load_balancer_host: K8s API server host/IP.
    """

    client_id: str
    issuer_url: str
    certificate: str
    groups_claim: str
    api_server_certificate: str
    load_balancer_host: str

    @property
    def k8s_api_url(self) -> str:
        """Get the Kubernetes API server URL."""
        return f"https://{self.load_balancer_host}:443"


async def get_cluster_oidc_info(
    mcc_k8s_api_url: str,
    mcc_id_token: str,
    cluster_name: str,
    namespace: str,
    *,
    timeout: float = 30.0,
    verify_ssl: bool = True,
) -> ClusterOIDCInfo | None:
    """Get OIDC configuration from a Cluster CR on MCC.

    This retrieves the OIDC configuration from cluster.status.providerStatus.oidc
    which is required to generate user-specific kubeconfigs.

    Reference: https://docs.mirantis.com/mosk/25.2/ops/getting-access/generate-kubecofig-cli.html

    Args:
        mcc_k8s_api_url: MCC Kubernetes API URL.
        mcc_id_token: id_token for accessing MCC K8s API.
        cluster_name: Name of the cluster (e.g., "mos").
        namespace: Namespace where cluster is deployed (e.g., "lab").
        timeout: HTTP request timeout.
        verify_ssl: Whether to verify SSL certificates.

    Returns:
        ClusterOIDCInfo with OIDC configuration, or None if not accessible.
    """
    logger.debug(
        "getting_cluster_oidc_info",
        cluster=cluster_name,
        namespace=namespace,
    )

    try:
        async with httpx.AsyncClient(
            verify=verify_ssl,
            timeout=httpx.Timeout(timeout),
        ) as client:
            # Get the Cluster CR - using cluster.k8s.io/v1alpha1 API
            response = await client.get(
                f"{mcc_k8s_api_url}/apis/cluster.k8s.io/v1alpha1/namespaces/{namespace}/clusters/{cluster_name}",
                headers={"Authorization": f"Bearer {mcc_id_token}"},
            )

            if response.status_code == 403:
                logger.warning(
                    "cluster_cr_access_denied",
                    cluster=cluster_name,
                    namespace=namespace,
                )
                return None

            if response.status_code == 404:
                logger.warning(
                    "cluster_cr_not_found",
                    cluster=cluster_name,
                    namespace=namespace,
                )
                return None

            if response.status_code != 200:
                logger.warning(
                    "cluster_cr_fetch_error",
                    cluster=cluster_name,
                    status_code=response.status_code,
                )
                return None

            cluster_cr = response.json()
            provider_status = cluster_cr.get("status", {}).get("providerStatus", {})
            oidc = provider_status.get("oidc", {})

            # Validate required fields
            client_id = oidc.get("clientId", "")
            issuer_url = oidc.get("issuerUrl", "")
            certificate = oidc.get("certificate", "")
            groups_claim = oidc.get("groupsClaim", "iam_roles")
            api_server_cert = provider_status.get("apiServerCertificate", "")
            lb_host = provider_status.get("loadBalancerHost", "")

            if not all([client_id, issuer_url, lb_host]):
                logger.warning(
                    "cluster_oidc_incomplete",
                    cluster=cluster_name,
                    has_client_id=bool(client_id),
                    has_issuer_url=bool(issuer_url),
                    has_lb_host=bool(lb_host),
                )
                return None

            oidc_info = ClusterOIDCInfo(
                client_id=client_id,
                issuer_url=issuer_url,
                certificate=certificate,
                groups_claim=groups_claim,
                api_server_certificate=api_server_cert,
                load_balancer_host=lb_host,
            )

            logger.info(
                "cluster_oidc_info_retrieved",
                cluster=cluster_name,
                client_id=client_id,
                issuer_url=issuer_url,
                lb_host=lb_host,
            )

            return oidc_info

    except httpx.ConnectError as e:
        # Network connectivity issue - should be retried or escalated
        logger.error(
            "cluster_oidc_info_network_error",
            cluster=cluster_name,
            error=str(e),
            hint="Check network connectivity to MCC API server",
        )
        return None
    except httpx.TimeoutException as e:
        # Request timeout - should be retried
        logger.error(
            "cluster_oidc_info_timeout",
            cluster=cluster_name,
            error=str(e),
            hint="Consider increasing timeout or checking API server load",
        )
        return None
    except httpx.HTTPStatusError as e:
        # HTTP error (4xx, 5xx) - may indicate auth or server issues
        logger.error(
            "cluster_oidc_info_http_error",
            cluster=cluster_name,
            status_code=e.response.status_code,
            error=str(e),
            hint="401/403 may indicate expired token, 5xx indicates server issues",
        )
        return None
    except Exception as e:
        # Unexpected error - log with context for debugging
        logger.error(
            "cluster_oidc_info_error",
            cluster=cluster_name,
            error_type=type(e).__name__,
            error=str(e),
            hint="Unexpected error type - consider investigating",
        )
        # Re-raise for critical system errors
        if isinstance(e, (MemoryError, SystemError)):
            raise
        return None


def generate_cluster_kubeconfig(
    oidc_info: ClusterOIDCInfo,
    tokens: TokenResponse,
    cluster_name: str,
) -> str:
    """Generate a user kubeconfig for a MOSK cluster.

    This follows the official MOSK documentation:
    https://docs.mirantis.com/mosk/25.2/ops/getting-access/generate-kubecofig-cli.html

    The generated kubeconfig includes:
    - Cluster CA certificate from cluster.status.providerStatus.apiServerCertificate
    - Cluster API URL from cluster.status.providerStatus.loadBalancerHost
    - OIDC auth-provider with:
      - client-id from cluster.status.providerStatus.oidc.clientId
      - idp-issuer-url from cluster.status.providerStatus.oidc.issuerUrl
      - idp-certificate-authority-data from cluster.status.providerStatus.oidc.certificate
      - id-token and refresh-token from Keycloak token response

    Args:
        oidc_info: OIDC configuration from the Cluster CR.
        tokens: Token response from Device Flow or token exchange.
        cluster_name: Name of the cluster for the kubeconfig context.

    Returns:
        YAML string of the kubeconfig file.
    """
    import yaml

    user_name = tokens.username
    context_name = f"{user_name}@{cluster_name}"

    # Build cluster config with CA certificate if available
    cluster_config: dict[str, Any] = {
        "server": oidc_info.k8s_api_url,
    }
    if oidc_info.api_server_certificate:
        cluster_config["certificate-authority-data"] = oidc_info.api_server_certificate
    else:
        # Fall back to insecure if no CA cert available
        cluster_config["insecure-skip-tls-verify"] = True

    kubeconfig = {
        "apiVersion": "v1",
        "kind": "Config",
        "current-context": context_name,
        "clusters": [
            {
                "name": cluster_name,
                "cluster": cluster_config,
            }
        ],
        "contexts": [
            {
                "name": context_name,
                "context": {
                    "cluster": cluster_name,
                    "user": user_name,
                },
            }
        ],
        "users": [
            {
                "name": user_name,
                "user": {
                    # Use direct token instead of auth-provider.
                    # The auth-provider mechanism was deprecated in Kubernetes 1.22
                    # and removed in 1.27. kr8s and other modern clients don't support it.
                    # Use id_token directly for authentication.
                    "token": tokens.id_token,
                },
            }
        ],
        "preferences": {},
    }

    return yaml.dump(kubeconfig, default_flow_style=False, sort_keys=False)


# =============================================================================
# Token Exchange (RFC 8693)
# =============================================================================


async def exchange_token_for_audience(
    issuer_url: str,
    subject_token: str,
    target_audience: str,
    *,
    subject_token_type: str = "urn:ietf:params:oauth:token-type:access_token",
    requested_token_type: str = "urn:ietf:params:oauth:token-type:access_token",
    timeout: float = 30.0,
    verify_ssl: bool = True,
) -> TokenResponse:
    """Exchange a token for one with a different audience (RFC 8693).

    Token Exchange allows exchanging an MCC token (client_id=kaas) for
    a MOSK cluster token (client_id=k8s) without requiring the user to
    re-authenticate.

    This is essential for Device Flow authentication where we don't have
    the user's password to request a new token with a different client_id.

    Reference: https://www.keycloak.org/securing-apps/token-exchange

    Args:
        issuer_url: OIDC issuer URL (e.g., "https://keycloak.example.com/auth/realms/iam").
        subject_token: The token to exchange (typically id_token or access_token).
        target_audience: The target client_id for the new token (e.g., "k8s").
        subject_token_type: Type of subject_token (default: access_token).
        requested_token_type: Type of token to receive (default: access_token).
        timeout: HTTP request timeout.
        verify_ssl: Whether to verify SSL certificates.

    Returns:
        TokenResponse with the exchanged token.

    Raises:
        MoskAuthError: If token exchange fails.
    """
    token_endpoint = f"{issuer_url}/protocol/openid-connect/token"

    logger.info(
        "token_exchange_initiating",
        issuer_url=issuer_url,
        target_audience=target_audience,
    )

    try:
        async with httpx.AsyncClient(
            verify=verify_ssl,
            timeout=httpx.Timeout(timeout),
        ) as client:
            response = await client.post(
                token_endpoint,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                    "subject_token": subject_token,
                    "subject_token_type": subject_token_type,
                    "requested_token_type": requested_token_type,
                    "audience": target_audience,
                },
            )

            if response.status_code != 200:
                error_data = {}
                if response.headers.get("content-type", "").startswith("application/json"):
                    error_data = response.json()

                error_msg = error_data.get("error_description", response.text)
                error_code = error_data.get("error", "token_exchange_failed")

                logger.warning(
                    "token_exchange_failed",
                    status_code=response.status_code,
                    error=error_msg,
                    error_code=error_code,
                )

                # Provide helpful error messages for common issues
                if error_code == "access_denied":
                    raise MoskAuthError(
                        f"Token exchange denied. Target client '{target_audience}' may not allow "
                        "token exchange from the source client. "
                        "Check Keycloak token-exchange permissions.",
                        details={
                            "error_code": error_code,
                            "target_audience": target_audience,
                            "hint": "Enable token-exchange in Keycloak for target client",
                        },
                    )

                if error_code == "invalid_target":
                    raise MoskAuthError(
                        f"Invalid target audience '{target_audience}'. "
                        "The client may not exist or may not be accessible.",
                        details={
                            "error_code": error_code,
                            "target_audience": target_audience,
                        },
                    )

                raise MoskAuthError(
                    f"Token exchange failed: {error_msg}",
                    details={
                        "error_code": error_code,
                        "status_code": response.status_code,
                        "target_audience": target_audience,
                    },
                )

            data = response.json()

            # Token exchange may return different token structure
            tokens = TokenResponse(
                access_token=data.get("access_token", ""),
                id_token=data.get("id_token", data.get("access_token", "")),
                refresh_token=data.get("refresh_token", ""),
                token_type=data.get("token_type", "Bearer"),
                expires_in=data.get("expires_in", 1800),
                refresh_expires_in=data.get("refresh_expires_in", 3600),
                scope=data.get("scope", ""),
            )

            logger.info(
                "token_exchange_success",
                target_audience=target_audience,
                username=tokens.username,
                expires_in=tokens.expires_in,
            )

            return tokens

    except httpx.RequestError as e:
        logger.error("token_exchange_request_error", error=str(e))
        raise MoskAuthError(
            f"Failed to connect for token exchange: {e}",
            details={"endpoint": token_endpoint},
        ) from e


# =============================================================================
# Module exports
# =============================================================================


__all__ = [
    # Data models
    "ClusterOIDCInfo",
    "MCCEndpoints",
    "TokenResponse",
    # JWT utilities
    "decode_jwt_payload",
    # Endpoint discovery
    "discover_mcc_endpoints",
    "discover_stacklight_endpoints",
    # Token exchange (for Device Flow MOSK auth)
    "exchange_token_for_audience",
    # Kubeconfig generation
    "generate_cluster_kubeconfig",
    "generate_token_kubeconfig",
    # Cluster OIDC info
    "get_cluster_oidc_info",
    "get_iam_roles",
    "get_jwt_claim",
]
