"""Cluster management MCP tools.

Provides tools for managing multiple MCC clusters:
- list_clusters: Show all configured clusters
- switch_cluster: Switch to a different cluster
- current_cluster: Get active cluster info
- add_cluster: Add a new cluster
- lock_cluster: Lock/unlock cluster switching
"""

from mosk_mcp.tools.cluster.add_cluster import add_cluster
from mosk_mcp.tools.cluster.current_cluster import current_cluster
from mosk_mcp.tools.cluster.list_clusters import list_clusters
from mosk_mcp.tools.cluster.lock_cluster import lock_cluster
from mosk_mcp.tools.cluster.switch_cluster import switch_cluster


__all__ = [
    "add_cluster",
    "current_cluster",
    "list_clusters",
    "lock_cluster",
    "switch_cluster",
]
