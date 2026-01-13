"""List all configured MCC clusters.

This tool shows all available cluster profiles with their status
and safety indicators. Use this to see what clusters are available
before switching.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mosk_mcp.cluster.manager import get_cluster_manager


if TYPE_CHECKING:
    from mosk_mcp.cluster.models import ListClustersOutput


logger = logging.getLogger(__name__)


async def list_clusters() -> ListClustersOutput:
    """List all configured MCC clusters.

    Shows available cluster profiles with their URLs, environments,
    and safety indicators. The active cluster is marked.

    Returns:
        ListClustersOutput with cluster information

    Example output:
        active_cluster: "default"
        clusters:
          - id: "default" [ACTIVE]
            name: "Dev Cluster"
            url: "https://mcc.example.com"
            environment: "development"

          - id: "prod" [PROD]
            name: "Production"
            url: "https://mcc-prod.example.com"
            environment: "production"
    """
    manager = get_cluster_manager()

    logger.info("list_clusters_called")

    result = await manager.list_clusters()

    logger.info(
        "list_clusters_completed",
        extra={
            "cluster_count": result.total_count,
            "active": result.active_cluster,
        },
    )

    return result
