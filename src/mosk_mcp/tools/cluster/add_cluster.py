"""Add a new MCC cluster configuration.

SECURITY FEATURES:
1. URL validation (must be HTTPS for production)
2. Cluster ID format validation
3. Duplicate detection
4. Optional connectivity test
5. Fingerprint capture on first successful auth
"""

from __future__ import annotations

import logging

from mosk_mcp.cluster.config import ClusterEnvironment
from mosk_mcp.cluster.manager import get_cluster_manager
from mosk_mcp.cluster.models import AddClusterInput, AddClusterOutput


logger = logging.getLogger(__name__)


async def add_cluster(
    cluster_id: str,
    url: str,
    name: str | None = None,
    environment: str = "development",
    ssl_verify: bool = True,
    description: str | None = None,
    set_active: bool = False,
) -> AddClusterOutput:
    """Add a new MCC cluster configuration.

    SECURITY:
    - Production clusters MUST use HTTPS
    - SSL verification is enabled by default
    - Cluster fingerprint is captured on first authentication

    Args:
        cluster_id: Unique identifier (e.g., 'prod', 'staging', 'dev')
                   Must start with letter, contain only alphanumeric, - or _
        url: MCC cluster URL (must be https:// for production)
        name: Human-readable display name (defaults to cluster_id)
        environment: One of 'development', 'staging', 'production'
        ssl_verify: Verify SSL certificates (default: True, required for production)
        description: Optional description of this cluster
        set_active: Make this the active cluster after adding

    Returns:
        AddClusterOutput with status and next steps

    Raises:
        ValueError: If cluster_id format is invalid or cluster already exists
        ValidationError: If production cluster uses HTTP or disables SSL

    Examples:
        # Add development cluster
        add_cluster(
            cluster_id="dev",
            url="https://mcc.example.com",
            environment="development",
            ssl_verify=False  # OK for dev only
        )

        # Add production cluster (strict requirements)
        add_cluster(
            cluster_id="prod",
            url="https://mcc-prod.example.com",
            name="Production MCC",
            environment="production"
            # ssl_verify defaults to True (required for prod)
        )
    """
    manager = get_cluster_manager()

    # Convert string environment to enum
    try:
        env_enum = ClusterEnvironment(environment.lower())
    except ValueError:
        valid_envs = [e.value for e in ClusterEnvironment]
        raise ValueError(
            f"Invalid environment '{environment}'. Must be one of: {valid_envs}"
        ) from None

    logger.info(
        "add_cluster_requested",
        extra={
            "cluster_id": cluster_id,
            "url": url,
            "environment": environment,
            "ssl_verify": ssl_verify,
            "set_active": set_active,
        },
    )

    input_model = AddClusterInput(
        cluster_id=cluster_id,
        url=url,
        name=name,
        environment=env_enum,
        ssl_verify=ssl_verify,
        description=description,
        set_active=set_active,
    )

    result = await manager.add_cluster(input_model)

    if result.success:
        log_level = logging.WARNING if env_enum == ClusterEnvironment.PRODUCTION else logging.INFO
        logger.log(
            log_level,
            "cluster_added_successfully",
            extra={
                "cluster_id": result.cluster_id,
                "url": result.cluster_url,
                "is_active": result.is_active,
                "validation_warnings": result.validation_warnings,
            },
        )
    else:
        logger.warning(
            "cluster_add_failed",
            extra={
                "cluster_id": cluster_id,
                "validation_warnings": result.validation_warnings,
            },
        )

    return result
