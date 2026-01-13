"""Ceph storage operations tools for MOSK MCP Server.

This package provides MCP tools for monitoring Ceph storage clusters
in MOSK environments, including:

- Cluster health and status monitoring
- OSD listing and details
- Capacity tracking and forecasting
- Placement group status
- Recovery/rebalancing monitoring

All tools are read-only. For Ceph modifications, use kubectl directly.

Example:
    >>> from mosk_mcp.tools.ceph_operations import get_ceph_status, list_osds
    >>>
    >>> # Get cluster status
    >>> status = await get_ceph_status(k8s_adapter)
    >>> print(f"Health: {status.health}")
    >>>
    >>> # List OSDs
    >>> osds = await list_osds(k8s_adapter)
    >>> for osd in osds.osds:
    ...     print(f"OSD {osd.osd_id}: {osd.status}")
"""

from __future__ import annotations

from mosk_mcp.tools.ceph_operations.get_ceph_capacity import (
    get_ceph_capacity,
)
from mosk_mcp.tools.ceph_operations.get_ceph_status import (
    get_ceph_status,
)
from mosk_mcp.tools.ceph_operations.get_osd_details import (
    get_osd_details,
)
from mosk_mcp.tools.ceph_operations.get_pg_status import (
    get_pg_status,
)
from mosk_mcp.tools.ceph_operations.get_recovery_status import (
    get_recovery_status,
)
from mosk_mcp.tools.ceph_operations.list_osds import (
    list_osds,
)
from mosk_mcp.tools.ceph_operations.models import (
    CapacityForecast,
    CapacityStatus,
    CapacitySummary,
    CephHealthLevel,
    GetCephCapacityInput,
    GetCephCapacityOutput,
    GetCephStatusInput,
    GetCephStatusOutput,
    GetOSDDetailsInput,
    GetOSDDetailsOutput,
    GetPGStatusInput,
    GetPGStatusOutput,
    GetRecoveryStatusInput,
    GetRecoveryStatusOutput,
    HealthCheckInfo,
    ListOSDsInput,
    ListOSDsOutput,
    OSDDetails,
    OSDSummary,
    PGStateCount,
    PoolCapacity,
    PredictCapacityInput,
    PredictCapacityOutput,
    RecoveryProgress,
)
from mosk_mcp.tools.ceph_operations.predict_capacity import (
    predict_capacity,
)


__all__ = [
    "CapacityForecast",
    "CapacityStatus",
    "CapacitySummary",
    "CephHealthLevel",
    "GetCephCapacityInput",
    "GetCephCapacityOutput",
    "GetCephStatusInput",
    "GetCephStatusOutput",
    "GetOSDDetailsInput",
    "GetOSDDetailsOutput",
    "GetPGStatusInput",
    "GetPGStatusOutput",
    "GetRecoveryStatusInput",
    "GetRecoveryStatusOutput",
    "HealthCheckInfo",
    "ListOSDsInput",
    "ListOSDsOutput",
    "OSDDetails",
    "OSDSummary",
    "PGStateCount",
    "PoolCapacity",
    "PredictCapacityInput",
    "PredictCapacityOutput",
    "RecoveryProgress",
    "get_ceph_capacity",
    "get_ceph_status",
    "get_osd_details",
    "get_pg_status",
    "get_recovery_status",
    "list_osds",
    "predict_capacity",
]
