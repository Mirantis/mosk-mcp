"""Tools module for MOSK MCP Server.

This module contains all MCP tools organized by category:
- template_generation: Generate Kubernetes CRs for MOSK
- node_lifecycle: Manage machine lifecycle
- ceph_operations: Ceph storage operations
- visibility: Cluster status and monitoring
- health: Health checks and alerts
- troubleshooting: Diagnostics and log analysis

Tools are registered with the FastMCP server in server.py.
"""

from __future__ import annotations

from enum import Enum

from mosk_mcp.tools.template_generation import (
    OutputFormat,
    generate_bmhi,
    generate_bmhp,
    generate_l2template,
    generate_machine,
    generate_osdpl_patch,
    validate_template,
)


class SafetyLevel(str, Enum):
    """Safety classification for tools.

    Tools are categorized by their potential impact on the cluster:

    READ_ONLY: Tools that only read data and have no side effects.
        Examples: list_machines, get_openstack_deployment_status, query_logs

    NON_DESTRUCTIVE: Tools that may modify state but can be safely
        retried or undone without data loss.
        Examples: set_maintenance_mode, update_labels

    PRIVILEGED: Tools that can cause significant changes or potential
        data loss. These require elevated permissions and often CRQ validation.
        Examples: delete_machine, force_osd_removal, scale_down_services
    """

    READ_ONLY = "read_only"
    NON_DESTRUCTIVE = "non_destructive"
    PRIVILEGED = "privileged"


__all__ = [
    # Output format
    "OutputFormat",
    # Safety levels
    "SafetyLevel",
    # Template generation tools
    "generate_bmhi",
    "generate_bmhp",
    "generate_l2template",
    "generate_machine",
    "generate_osdpl_patch",
    "validate_template",
]
