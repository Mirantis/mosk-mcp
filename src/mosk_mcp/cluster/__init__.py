"""Cluster management module for multi-cluster MOSK MCP support.

This module provides secure multi-cluster management with:
- Named cluster profiles (like kubectl contexts)
- Cluster fingerprinting to prevent wrong-cluster operations
- Strict session isolation per cluster
- Forced re-authentication on cluster switch

SECURITY DESIGN:
1. Each cluster has a unique fingerprint (hash of Keycloak realm + K8s API)
2. Fingerprints are verified on every authenticated operation
3. Authentication tokens are NEVER shared between clusters
4. Switching clusters invalidates all previous session state
5. Production clusters require explicit confirmation for destructive ops
"""

from mosk_mcp.cluster.config import (
    ClusterConfig,
    ClusterEnvironment,
    ClustersConfig,
)
from mosk_mcp.cluster.manager import ClusterManager
from mosk_mcp.cluster.models import (
    AddClusterInput,
    AddClusterOutput,
    ClusterInfo,
    ClusterSwitchConfirmation,
    CurrentClusterOutput,
    ListClustersOutput,
    SwitchClusterInput,
    SwitchClusterOutput,
)


__all__ = [
    "AddClusterInput",
    "AddClusterOutput",
    "ClusterConfig",
    "ClusterEnvironment",
    "ClusterInfo",
    "ClusterManager",
    "ClusterSwitchConfirmation",
    "ClustersConfig",
    "CurrentClusterOutput",
    "ListClustersOutput",
    "SwitchClusterInput",
    "SwitchClusterOutput",
]
