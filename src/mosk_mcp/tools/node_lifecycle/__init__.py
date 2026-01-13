"""Node lifecycle management tools for MOSK MCP Server.

This module provides tools for managing the complete lifecycle of MOSK cluster nodes,
including:
- Listing and inspecting machines
- Node readiness checks
- Maintenance request management (uses NodeMaintenanceRequest CRD)
- Machine CR application

IMPORTANT: All node lifecycle operations in MOSK should go through the proper
LCM/MCC workflow using CRDs:
- Live migration: Handled automatically by Rockoon when NodeMaintenanceRequest
  is created with DrainStrategy.LiveMigrate
- Node drain: Use create_maintenance_request (creates NodeMaintenanceRequest CRD)

Do NOT use direct kubectl drain or manual pod eviction - always use the
MOSK-native CRD-based workflow via create_maintenance_request.

Safety Levels:
- READ_ONLY: list_machines, get_machine_details, get_node_readiness, get_migration_status
- NON_DESTRUCTIVE: create_maintenance_request
- PRIVILEGED: apply_machine (requires CRQ validation)

Example usage:
    >>> from mosk_mcp.tools.node_lifecycle import list_machines
    >>> result = await list_machines(namespace="default")
    >>> print(f"Found {result.total_count} machines")
"""

from __future__ import annotations

# Re-export MigrationType from openstack adapter for convenience
from mosk_mcp.adapters.openstack import MigrationType
from mosk_mcp.tools.node_lifecycle.apply_machine import (
    ApplyMachineInput,
    ApplyMachineOutput,
    apply_machine,
)
from mosk_mcp.tools.node_lifecycle.create_maintenance_request import (
    CreateMaintenanceRequestInput,
    CreateMaintenanceRequestOutput,
    MaintenanceReason,
    create_maintenance_request,
)
from mosk_mcp.tools.node_lifecycle.get_ipamhost_details import (
    GetIpamHostDetailsInput,
    GetIpamHostDetailsOutput,
    NetworkBond,
    NetworkBridge,
    NetworkVLAN,
    NICMapping,
    ServiceMapping,
    get_ipamhost_details,
)
from mosk_mcp.tools.node_lifecycle.get_machine_details import (
    GetMachineDetailsInput,
    GetMachineDetailsOutput,
    MachineCondition,
    MachineEvent,
    RelatedResource,
    get_machine_details,
)
from mosk_mcp.tools.node_lifecycle.get_migration_status import (
    GetMigrationStatusInput,
    GetMigrationStatusOutput,
    MigrationInfo,
    MigrationStatus,
    get_migration_status,
)
from mosk_mcp.tools.node_lifecycle.get_node_provision_progress import (
    GetNodeProvisionProgressInput,
    GetNodeProvisionProgressOutput,
    ProvisionPhase,
    ResourceStatus,
    get_node_provision_progress,
)
from mosk_mcp.tools.node_lifecycle.get_node_readiness import (
    CheckSeverity,
    GetNodeReadinessInput,
    GetNodeReadinessOutput,
    NodeConditionStatus,
    ReadinessCheck,
    ReadinessCheckType,
    get_node_readiness,
)
from mosk_mcp.tools.node_lifecycle.list_bmh import (
    BMHOperationalStatusFilter,
    BMHStateFilter,
    BMHSummary,
    ListBMHInput,
    ListBMHOutput,
    list_bmh,
)
from mosk_mcp.tools.node_lifecycle.list_bmhp import (
    BMHPSummary,
    ListBMHPInput,
    ListBMHPOutput,
    list_bmhp,
)
from mosk_mcp.tools.node_lifecycle.list_l2templates import (
    L2TemplateSummary,
    ListL2TemplatesInput,
    ListL2TemplatesOutput,
    list_l2templates,
)
from mosk_mcp.tools.node_lifecycle.list_machines import (
    ListMachinesInput,
    ListMachinesOutput,
    MachinePhaseFilter,
    MachineRoleFilter,
    MachineSummary,
    list_machines,
)


__all__ = [
    # apply_machine
    "ApplyMachineInput",
    "ApplyMachineOutput",
    "BMHOperationalStatusFilter",
    "BMHPSummary",
    "BMHStateFilter",
    "BMHSummary",
    "CheckSeverity",
    # create_maintenance_request
    "CreateMaintenanceRequestInput",
    "CreateMaintenanceRequestOutput",
    # get_ipamhost_details
    "GetIpamHostDetailsInput",
    "GetIpamHostDetailsOutput",
    # get_machine_details
    "GetMachineDetailsInput",
    "GetMachineDetailsOutput",
    # get_migration_status
    "GetMigrationStatusInput",
    "GetMigrationStatusOutput",
    # get_node_provision_progress
    "GetNodeProvisionProgressInput",
    "GetNodeProvisionProgressOutput",
    # get_node_readiness
    "GetNodeReadinessInput",
    "GetNodeReadinessOutput",
    "L2TemplateSummary",
    # list_bmh
    "ListBMHInput",
    "ListBMHOutput",
    # list_bmhp
    "ListBMHPInput",
    "ListBMHPOutput",
    # list_l2templates
    "ListL2TemplatesInput",
    "ListL2TemplatesOutput",
    # list_machines
    "ListMachinesInput",
    "ListMachinesOutput",
    "MachineCondition",
    "MachineEvent",
    "MachinePhaseFilter",
    "MachineRoleFilter",
    "MachineSummary",
    "MaintenanceReason",
    "MigrationInfo",
    "MigrationStatus",
    "MigrationType",
    "NICMapping",
    "NetworkBond",
    "NetworkBridge",
    "NetworkVLAN",
    "NodeConditionStatus",
    "ProvisionPhase",
    "ReadinessCheck",
    "ReadinessCheckType",
    "RelatedResource",
    "ResourceStatus",
    "ServiceMapping",
    "apply_machine",
    "create_maintenance_request",
    "get_ipamhost_details",
    "get_machine_details",
    "get_migration_status",
    "get_node_provision_progress",
    "get_node_readiness",
    "list_bmh",
    "list_bmhp",
    "list_l2templates",
    "list_machines",
]
