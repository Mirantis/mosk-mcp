"""Adapters module for MOSK MCP Server.

This module contains adapters for external services:
- kubernetes: kr8s-based async Kubernetes client wrapper
- crd: Pydantic models for MOSK Kubernetes CRDs
- stacklight: StackLight subpackage for logs, metrics, and alerts
- ceph: Ceph toolbox commands
- openstack: OpenStack API client

Adapters provide a clean abstraction layer between tools and
external services, making testing and mocking easier.

Usage:
    from mosk_mcp.adapters import KubernetesAdapter, kubernetes_client
    from mosk_mcp.adapters.crd import Machine, OpenStackDeployment
    from mosk_mcp.adapters.stacklight import StackLightAdapter, StackLightManager

    async with kubernetes_client(settings) as k8s:
        machines = await k8s.list_machines()
"""

from __future__ import annotations

from mosk_mcp.adapters.kubernetes import (
    KubernetesAdapter,
    kubernetes_client,
)


__all__ = [
    "KubernetesAdapter",
    "kubernetes_client",
]
