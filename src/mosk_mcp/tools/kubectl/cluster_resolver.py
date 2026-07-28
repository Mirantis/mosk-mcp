"""Resolve Kubernetes Cluster CR names to API adapters.

Maps cluster names (e.g. ``mos``, ``kaas-mgmt``) to the correct
KubernetesAdapter for the management or workload API endpoint.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mosk_mcp.core.exceptions import ValidationError
from mosk_mcp.core.validation import validate_kubernetes_name


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter
    from mosk_mcp.core.server_context import SSOServerContext


MANAGEMENT_CLUSTER_NAME = "kaas-mgmt"


async def resolve_adapter_for_cluster_name(
    cluster_name: str,
    context: SSOServerContext,
) -> KubernetesAdapter:
    """Resolve a Cluster CR name to the appropriate KubernetesAdapter.

    Args:
        cluster_name: Kubernetes Cluster CR name (e.g. ``mos``, ``kaas-mgmt``).
        context: Authenticated server context with session state.

    Returns:
        KubernetesAdapter for the target cluster API endpoint.

    Raises:
        ValidationError: If the cluster name is unknown or MOSK is not configured.
    """
    validate_kubernetes_name(cluster_name, field_name="cluster")

    session = context.session
    mosk_cluster_name = session.mosk_cluster_name

    if cluster_name == mosk_cluster_name:
        return await context.get_mosk_adapter()

    if cluster_name == MANAGEMENT_CLUSTER_NAME:
        return await context.get_mcc_adapter()

    workload_hint = (
        f" or workload cluster '{mosk_cluster_name}'"
        if mosk_cluster_name
        else ""
    )
    raise ValidationError(
        f"Unknown cluster '{cluster_name}'. "
        f"Expected management cluster '{MANAGEMENT_CLUSTER_NAME}'{workload_hint}.",
        field="cluster",
        value=cluster_name,
    )
