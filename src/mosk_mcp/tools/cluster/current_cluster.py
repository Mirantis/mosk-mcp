"""Get the current active MCC cluster information.

This tool shows detailed information about the currently active cluster
including authentication status, safety indicators, and next actions.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mosk_mcp.cluster.manager import get_cluster_manager


if TYPE_CHECKING:
    from mosk_mcp.cluster.models import CurrentClusterOutput


logger = logging.getLogger(__name__)


async def current_cluster() -> CurrentClusterOutput:
    """Get information about the currently active MCC cluster.

    Returns detailed information about:
    - Active cluster configuration
    - Authentication status and expiry
    - Safety indicators (production, locked, SSL)
    - Fingerprint verification status
    - Suggested next actions

    Returns:
        CurrentClusterOutput with cluster details

    Example output:
        has_active_cluster: true
        cluster:
          id: "prod"
          name: "Production Cluster"
          url: "https://mcc-prod.example.com"
          environment: "production"
          is_locked: true
          safety_indicator: "[PROD] [LOCKED] [ACTIVE]"
        is_authenticated: true
        username: "admin@example.com"
        fingerprint_verified: true
        warnings:
          - "Production cluster - exercise caution"
        next_action: null
    """
    manager = get_cluster_manager()

    logger.info("current_cluster_called")

    result = await manager.get_current_cluster()

    if result.has_active_cluster:
        logger.info(
            "current_cluster_info",
            extra={
                "cluster_id": result.cluster.id if result.cluster else None,
                "environment": (result.cluster.environment.value if result.cluster else None),
                "is_authenticated": result.is_authenticated,
                "fingerprint_verified": result.fingerprint_verified,
            },
        )
    else:
        logger.info("current_cluster_none_active")

    return result
