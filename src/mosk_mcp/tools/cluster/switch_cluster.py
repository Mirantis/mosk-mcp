"""Switch to a different MCC cluster.

SECURITY FEATURES:
1. Production clusters require explicit confirmation
2. Locked clusters cannot be switched from (unless forced)
3. All previous session state is cleared on switch
4. Audit logging for all switches
"""

from __future__ import annotations

import logging

from mosk_mcp.cluster.config import ClusterEnvironment
from mosk_mcp.cluster.manager import (
    ClusterLockedError,
    ClusterSecurityError,
    get_cluster_manager,
)
from mosk_mcp.cluster.models import SwitchClusterInput, SwitchClusterOutput


logger = logging.getLogger(__name__)


async def switch_cluster(
    cluster_id: str,
    confirm_production: bool = False,
    force: bool = False,
) -> SwitchClusterOutput:
    """Switch to a different MCC cluster.

    SECURITY: Switching clusters:
    - Clears ALL previous authentication state
    - Requires re-authentication to the new cluster
    - Production clusters require confirm_production=True
    - Locked clusters require force=True (dangerous)

    Args:
        cluster_id: ID of the cluster to switch to (from list_clusters)
        confirm_production: Set to True to confirm switching to production cluster
        force: Force switch even if current cluster is locked (DANGEROUS)

    Returns:
        SwitchClusterOutput with switch status

    Raises:
        ValueError: If cluster_id not found
        ClusterLockedError: If current cluster is locked and force=False
        ClusterSecurityError: For other security violations

    Examples:
        # Switch to staging
        switch_cluster(cluster_id="staging")

        # Switch to production (requires confirmation)
        switch_cluster(cluster_id="prod", confirm_production=True)

        # Force switch from locked cluster (dangerous)
        switch_cluster(cluster_id="dev", force=True)
    """
    manager = get_cluster_manager()

    logger.info(
        "switch_cluster_requested",
        extra={
            "target_cluster": cluster_id,
            "confirm_production": confirm_production,
            "force": force,
        },
    )

    try:
        input_model = SwitchClusterInput(
            cluster_id=cluster_id,
            confirm_production=confirm_production,
            force=force,
        )

        result = await manager.switch_cluster(input_model)

        # Log audit event
        if result.success:
            log_level = (
                logging.WARNING
                if result.new_cluster_environment == ClusterEnvironment.PRODUCTION
                else logging.INFO
            )
            logger.log(
                log_level,
                "cluster_switched_successfully",
                extra={
                    "previous_cluster": result.previous_cluster,
                    "new_cluster": result.new_cluster,
                    "environment": result.new_cluster_environment.value,
                    "session_cleared": result.session_cleared,
                },
            )
        else:
            logger.info(
                "cluster_switch_requires_confirmation",
                extra={
                    "target_cluster": cluster_id,
                    "reason": "production_confirmation_required",
                },
            )

        return result

    except ClusterLockedError as e:
        logger.warning(
            "cluster_switch_blocked_locked",
            extra={
                "target_cluster": cluster_id,
                "error": str(e),
            },
        )
        raise

    except ClusterSecurityError as e:
        logger.error(
            "cluster_switch_security_error",
            extra={
                "target_cluster": cluster_id,
                "error": str(e),
            },
        )
        raise

    except ValueError as e:
        logger.warning(
            "cluster_switch_invalid",
            extra={
                "target_cluster": cluster_id,
                "error": str(e),
            },
        )
        raise
