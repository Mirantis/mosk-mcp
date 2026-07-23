"""kubectl get tool — retrieve Kubernetes resources.

Mimics ``kubectl get`` using kr8s API discovery and the connected
adapter's API client.

Safety Level: READ_ONLY

Secret payload values (``data`` / ``stringData``) are redacted unless the
requested cluster's safety tier is ``development`` (``environment`` in
``clusters.yaml`` for that cluster id, or ``MCP_ENVIRONMENT`` when that
cluster is not configured).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import kr8s

from mosk_mcp.auth.rbac import ToolSafetyLevel
from mosk_mcp.cluster.config import ClusterEnvironment
from mosk_mcp.core.exceptions import KubernetesError, ResourceNotFoundError, ValidationError
from mosk_mcp.core.validation import (
    validate_kubernetes_name,
    validate_label_selector,
    validate_namespace,
)
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.common.errors import tool_handler
from mosk_mcp.tools.kubectl.jq_filter import apply_jq_program, compile_jq_filter
from mosk_mcp.tools.kubectl.models import KubectlGetInput, KubectlGetOutput


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)

TOOL_NAME = "kubectl_get"
TOOL_SAFETY_LEVEL = ToolSafetyLevel.READ_ONLY
SECRET_KIND = "Secret"
SECRET_VALUE_REDACTED = "<redacted>"
TOOL_DESCRIPTION = (
    "Get Kubernetes resources, mimicking kubectl get. "
    "Supports resource types in TYPE[.VERSION][.GROUP] format, "
    "namespace scoping, label selectors, and optional jq filtering. "
    "The jq_filter parameter uses jq syntax (not kubectl jsonpath), "
    "e.g. '.items[].metadata.name'. "
    "The cluster parameter is the Kubernetes Cluster CR name "
    "(e.g. workload cluster 'mos' or management cluster 'kaas-mgmt'). "
    "Secret values are redacted unless the requested cluster's "
    "safety tier is 'development'."
)


async def _resolve_safety_tier(cluster: str) -> str:
    """Resolve the safety tier for the cluster named in the request.

    Looks up ``cluster`` in ``clusters.yaml`` and returns its
    ``environment``. Falls back to the process ``MCP_ENVIRONMENT`` when
    that cluster id is not configured.
    """
    try:
        from mosk_mcp.cluster.manager import get_cluster_manager

        config = await get_cluster_manager().get_config()
        cluster_config = config.clusters.get(cluster)
        if cluster_config is not None:
            return cluster_config.environment.value
    except Exception as e:
        logger.debug(
            "safety_tier_cluster_config_unavailable",
            cluster=cluster,
            error=str(e),
            error_type=type(e).__name__,
        )

    from mosk_mcp.core.config import get_settings

    return get_settings().environment.value


def _redact_secret_payload(secret: dict[str, Any]) -> None:
    """Replace Secret ``data`` / ``stringData`` values in place."""
    for field in ("data", "stringData"):
        values = secret.get(field)
        if isinstance(values, dict):
            secret[field] = {key: SECRET_VALUE_REDACTED for key in values}


async def _redact_secret_contents_if_needed(
    data: Any,
    kind: str,
    cluster: str,
) -> Any:
    """Hide Secret values outside the development safety tier.

    Metadata (names, keys, labels, etc.) is preserved; only payload values
    under ``data`` and ``stringData`` are replaced. Redaction runs before
    jq filtering so filters cannot recover the original values.
    """
    if kind != SECRET_KIND:
        return data

    safety_tier = await _resolve_safety_tier(cluster)
    if safety_tier == ClusterEnvironment.DEVELOPMENT.value:
        return data

    logger.info(
        "kubectl_get_secret_contents_redacted",
        cluster=cluster,
        safety_tier=safety_tier,
    )

    if isinstance(data, dict) and isinstance(data.get("items"), list):
        for item in data["items"]:
            if isinstance(item, dict):
                _redact_secret_payload(item)
        return data

    if isinstance(data, dict):
        _redact_secret_payload(data)

    return data


def _count_resources(data: Any) -> int:
    if isinstance(data, dict) and "items" in data:
        items = data["items"]
        return len(items) if isinstance(items, list) else 0
    if isinstance(data, list):
        return len(data)
    return 1 if data is not None else 0


async def _fetch_resources(
    adapter: KubernetesAdapter,
    resource_type: str,
    *,
    name: str | None,
    namespace: str | None,
    label_selector: str | None,
) -> tuple[Any, str, str | None]:
    """Fetch resources via kr8s discovery and return data, kind, api_version."""
    kind, _plural, namespaced = await adapter.api.lookup_kind(resource_type)

    # Match kubectl: named gets require an explicit namespace for namespaced kinds.
    # ``kubectl get pod NAME -A`` is not supported; all-namespaces is for lists only.
    if namespaced and name and namespace is None:
        raise ValidationError(
            "namespace is required when name is provided for namespaced resources",
            field="namespace",
            constraint="must be provided when name is set",
        )

    if not namespaced:
        ns = None
    elif namespace is None:
        ns = kr8s.ALL
    else:
        ns = namespace

    get_kwargs: dict[str, Any] = {"api": adapter.api}
    if label_selector:
        get_kwargs["label_selector"] = label_selector
    if ns is not None:
        get_kwargs["namespace"] = ns

    try:
        resources_gen = kr8s.asyncio.get(
            resource_type,
            *(name,) if name else (),
            **get_kwargs,
        )
        items: list[dict[str, Any]] = []
        async for obj in resources_gen:
            items.append(obj.raw)

    except kr8s.NotFoundError as e:
        if name:
            resource_id = f"{ns}/{name}" if ns and ns is not kr8s.ALL else name
            raise ResourceNotFoundError(
                f"{kind} '{name}' not found",
                resource_type=kind,
                resource_id=resource_id,
            ) from e
        raise KubernetesError(
            f"Failed to list {resource_type}: {e}",
            operation="list",
            resource_kind=kind,
        ) from e
    except Exception as e:
        operation = "get" if name else "list"
        raise KubernetesError(
            f"Failed to {operation} {resource_type}: {e}",
            operation=operation,
            resource_kind=kind,
            resource_name=name,
            namespace=str(ns) if ns is not None and ns is not kr8s.ALL else None,
        ) from e

    if name:
        if not items:
            resource_id = f"{ns}/{name}" if ns and ns is not kr8s.ALL else name
            raise ResourceNotFoundError(
                f"{kind} '{name}' not found",
                resource_type=kind,
                resource_id=resource_id,
            )
        resource = items[0]
        api_version = resource.get("apiVersion")
        return resource, kind, api_version

    api_version = items[0].get("apiVersion") if items else None
    list_data: dict[str, Any] = {
        "apiVersion": api_version,
        "kind": f"{kind}List",
        "items": items,
    }
    return list_data, kind, api_version


@tool_handler(tool_name=TOOL_NAME)
async def kubectl_get(
    adapter: KubernetesAdapter,
    input_data: KubectlGetInput,
) -> KubectlGetOutput:
    """Get Kubernetes resources mimicking kubectl get.

    Args:
        adapter: KubernetesAdapter for the resolved cluster API endpoint.
        input_data: Validated input parameters.

    Returns:
        KubectlGetOutput with resource data as JSON.
    """
    if input_data.namespace is not None:
        validate_namespace(input_data.namespace)
    if input_data.label_selector:
        validate_label_selector(input_data.label_selector)
    if input_data.name:
        validate_kubernetes_name(input_data.name, field_name="name")

    resource_type = input_data.resource_type.strip()
    if not resource_type:
        raise ValidationError(
            "resource_type cannot be empty",
            field="resource_type",
            value=input_data.resource_type,
        )

    # Compile jq filter before fetching so invalid expressions fail fast.
    jq_program = None
    if input_data.jq_filter:
        jq_program = compile_jq_filter(input_data.jq_filter)

    data, kind, api_version = await _fetch_resources(
        adapter,
        resource_type,
        name=input_data.name,
        namespace=input_data.namespace,
        label_selector=input_data.label_selector,
    )

    # Redact before jq so filters cannot extract secret values.
    data = await _redact_secret_contents_if_needed(data, kind, input_data.cluster)

    if jq_program is not None:
        data = apply_jq_program(data, jq_program)

    count = _count_resources(data)

    return KubectlGetOutput(
            cluster=input_data.cluster,
            resource_type=resource_type,
            namespace=input_data.namespace,
            name=input_data.name,
            kind=kind,
            api_version=api_version,
            count=count,
            data=data,
        )
