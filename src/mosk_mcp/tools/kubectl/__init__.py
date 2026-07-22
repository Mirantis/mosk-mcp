"""Kubectl-compatible MCP tools for Kubernetes resource operations.

This package provides tools that mimic kubectl commands, built on top of
the existing KubernetesAdapter infrastructure.

Tools:
    kubectl_get: Retrieve Kubernetes resources (equivalent to kubectl get)
"""

from __future__ import annotations

from mosk_mcp.tools.kubectl.cluster_resolver import (
    MANAGEMENT_CLUSTER_NAME,
    resolve_adapter_for_cluster_name,
)
from mosk_mcp.tools.kubectl.kubectl_get import (
    TOOL_DESCRIPTION as KUBECTL_GET_DESCRIPTION,
)
from mosk_mcp.tools.kubectl.kubectl_get import (
    TOOL_NAME as KUBECTL_GET_NAME,
)
from mosk_mcp.tools.kubectl.kubectl_get import (
    TOOL_SAFETY_LEVEL as KUBECTL_GET_SAFETY_LEVEL,
)
from mosk_mcp.tools.kubectl.kubectl_get import kubectl_get
from mosk_mcp.tools.kubectl.models import KubectlGetInput, KubectlGetOutput


__all__ = [
    "KUBECTL_GET_DESCRIPTION",
    "KUBECTL_GET_NAME",
    "KUBECTL_GET_SAFETY_LEVEL",
    "KubectlGetInput",
    "KubectlGetOutput",
    "MANAGEMENT_CLUSTER_NAME",
    "kubectl_get",
    "resolve_adapter_for_cluster_name",
]
