"""Kubectl-compatible tools registration for MOSK MCP Server.

This module registers tools that mimic kubectl commands:
- kubectl_get: Get Kubernetes resources
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import Field

from mosk_mcp.observability.logging import get_logger
from mosk_mcp.registration.utils import with_logging_context
from mosk_mcp.tools.kubectl import (
    KubectlGetInput,
    kubectl_get,
)
from mosk_mcp.tools.kubectl.cluster_resolver import resolve_adapter_for_cluster_name


if TYPE_CHECKING:
    from collections.abc import Callable

    from fastmcp import FastMCP

    from mosk_mcp.core.config import Settings
    from mosk_mcp.core.server_context import SSOServerContext


logger = get_logger(__name__)


def register_kubectl_tools(
    mcp: FastMCP, settings: Settings, context_getter: Callable[[], SSOServerContext | None]
) -> None:
    """Register kubectl-compatible tools with the MCP server.

    These tools provide generic Kubernetes resource access, mimicking
    native kubectl commands. All tools are READ_ONLY.

    CLUSTER ROUTING:
    - cluster name matches session workload cluster (e.g. 'mos') -> workload API
    - cluster name is management cluster ('kaas-mgmt') -> management API

    Args:
        mcp: FastMCP server instance.
        settings: Application settings.
        context_getter: Function that returns the current global SSOServerContext.
    """

    @mcp.tool(
        name="kubectl_get",
        description=(
            "Get Kubernetes resources, mimicking kubectl get. "
            "The cluster parameter is the Kubernetes Cluster CR name "
            "(e.g. workload 'mos' or management 'kaas-mgmt'), not a role. "
            "Supports resource types in TYPE[.VERSION][.GROUP] format "
            "(e.g. 'pods', 'deployments.apps', 'machines.cluster.k8s.io'), "
            "optional namespace scoping, label selectors, and jsonpath field filtering. "
            "Short resource names rely on kr8s API discovery (requires api-resources RBAC)."
        ),
    )
    async def _kubectl_get(
        cluster: str = Field(
            ...,
            description=(
                "Kubernetes Cluster CR name (e.g. workload 'mos' or management 'kaas-mgmt')"
            ),
        ),
        resource_type: str = Field(
            ...,
            description="Resource type: TYPE[.VERSION][.GROUP] (e.g. 'pods', 'machines')",
        ),
        namespace: str | None = Field(
            default=None,
            description="Namespace to query. Omit to search all namespaces.",
        ),
        name: str | None = Field(
            default=None,
            description="Optional resource name. Omit to list resources.",
        ),
        label_selector: str | None = Field(
            default=None,
            description="Label selector (e.g. 'app=nova-api').",
        ),
        jsonpath: str | None = Field(
            default=None,
            description="Optional jsonpath to filter fields (e.g. '{.items[*].metadata.name}')",
        ),
    ) -> dict[str, Any]:
        """Get Kubernetes resources."""
        async with with_logging_context("kubectl_get"):
            context = context_getter()
            if not context:
                raise RuntimeError("Server context not initialized")
            adapter = await resolve_adapter_for_cluster_name(cluster, context)
            input_data = KubectlGetInput(
                cluster=cluster,
                resource_type=resource_type,
                namespace=namespace,
                name=name,
                label_selector=label_selector,
                jsonpath=jsonpath,
            )
            result = await kubectl_get(adapter, input_data)
            return result.model_dump()
