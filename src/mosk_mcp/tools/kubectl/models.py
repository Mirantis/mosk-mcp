"""Pydantic models for kubectl-compatible MCP tools."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class KubectlGetInput(BaseModel):
    """Input parameters for kubectl_get."""

    cluster: str = Field(
        ...,
        description=(
            "Kubernetes Cluster CR name to execute against "
            "(e.g. workload cluster 'mos' or management cluster 'kaas-mgmt')"
        ),
    )
    resource_type: str = Field(
        ...,
        description=(
            "Resource type in kubectl format TYPE[.VERSION][.GROUP], "
            "e.g. 'pods', 'deployments.apps', 'machines.cluster.k8s.io'"
        ),
    )
    namespace: str | None = Field(
        default=None,
        description="Namespace to query. Omit to search all namespaces.",
    )
    name: str | None = Field(
        default=None,
        description="Optional resource name. Omit to list matching resources.",
    )
    label_selector: str | None = Field(
        default=None,
        description="Kubernetes label selector (e.g. 'app=nova-api').",
    )
    jq_filter: str | None = Field(
        default=None,
        description=(
            "Optional jq filter expression to select/transform output fields. "
            "Syntax must follow jq (not kubectl jsonpath), e.g. "
            "'.items[].metadata.name' or '.status.phase'. Response is always JSON."
        ),
    )


class KubectlGetOutput(BaseModel):
    """Output from kubectl_get."""

    cluster: str = Field(..., description="Cluster CR name the command was executed against")
    resource_type: str = Field(..., description="Requested resource type")
    namespace: str | None = Field(
        default=None,
        description="Namespace scope (None means all namespaces)",
    )
    name: str | None = Field(default=None, description="Resource name if specified")
    kind: str | None = Field(default=None, description="Resolved Kubernetes kind")
    api_version: str | None = Field(default=None, description="Resolved API version")
    count: int = Field(default=0, description="Number of resources returned")
    data: Any = Field(..., description="Resource data or jq-filtered result")
