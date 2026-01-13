"""Lock or unlock a cluster to prevent accidental switches.

SECURITY FEATURES:
1. Locked clusters cannot be switched away from (without force)
2. Useful for production work sessions
3. Prevents accidental context switches during critical operations
4. Audit logged for compliance
"""

from __future__ import annotations

import logging

from mosk_mcp.cluster.manager import get_cluster_manager
from mosk_mcp.cluster.models import LockClusterOutput


logger = logging.getLogger(__name__)


async def lock_cluster(
    cluster_id: str | None = None,
    lock: bool = True,
) -> LockClusterOutput:
    """Lock or unlock a cluster to prevent accidental switches.

    SECURITY:
    - Locking a cluster prevents switching away from it
    - Useful when performing critical operations on production
    - Switching from a locked cluster requires force=True
    - All lock/unlock operations are audit logged

    Args:
        cluster_id: Cluster to lock/unlock (defaults to active cluster)
        lock: True to lock, False to unlock

    Returns:
        LockClusterOutput with new lock status

    Raises:
        ValueError: If cluster_id not found

    Examples:
        # Lock current cluster before critical work
        lock_cluster()

        # Lock specific production cluster
        lock_cluster(cluster_id="prod", lock=True)

        # Unlock when done
        lock_cluster(cluster_id="prod", lock=False)

    Use Cases:
        1. Lock production before deployment:
           - Prevents accidental switch to dev during rollout
           - Ensures all commands target production

        2. Lock during incident response:
           - Maintains focus on affected cluster
           - Prevents confusion during high-stress situations

        3. Lock for audit compliance:
           - Shows intentional cluster selection
           - Provides audit trail of lock/unlock events
    """
    manager = get_cluster_manager()

    logger.info(
        "lock_cluster_requested",
        extra={
            "cluster_id": cluster_id or "(active)",
            "lock_action": "lock" if lock else "unlock",
        },
    )

    affected_cluster, is_locked = await manager.lock_cluster(cluster_id, lock)

    action = "locked" if is_locked else "unlocked"
    message = f"Cluster '{affected_cluster}' is now {action}"

    result = LockClusterOutput(
        success=True,
        cluster_id=affected_cluster,
        is_locked=is_locked,
        message=message,
    )

    # Audit log with appropriate level
    log_level = logging.WARNING if is_locked else logging.INFO
    logger.log(
        log_level,
        f"cluster_{action}",
        extra={
            "cluster_id": affected_cluster,
            "is_locked": is_locked,
        },
    )

    return result
