"""Tool registration modules for MOSK MCP Server.

This package contains tool registration functions organized by category:
- auth: Authentication tools (login, logout, session_status)
- cluster: Cluster management tools (list, switch, add, lock)
- template_generation: CR template generation tools
- ceph_operations: Ceph storage operations tools
- messaging_operations: RabbitMQ messaging operations tools
- node_lifecycle: Node lifecycle management tools
- operations_visibility: Operations visibility tools
- cluster_health: Cluster health monitoring tools
- troubleshooting: Troubleshooting and diagnostics tools
- validation: Post-upgrade validation tools
"""

from __future__ import annotations

from mosk_mcp.registration.tools.auth import register_auth_tools
from mosk_mcp.registration.tools.ceph_operations import register_ceph_operations_tools
from mosk_mcp.registration.tools.cluster import register_cluster_tools
from mosk_mcp.registration.tools.cluster_health import register_cluster_health_tools
from mosk_mcp.registration.tools.messaging_operations import register_messaging_operations_tools
from mosk_mcp.registration.tools.node_lifecycle import register_node_lifecycle_tools
from mosk_mcp.registration.tools.operations_visibility import register_operations_visibility_tools
from mosk_mcp.registration.tools.template_generation import register_template_generation_tools
from mosk_mcp.registration.tools.troubleshooting import register_troubleshooting_tools
from mosk_mcp.registration.tools.validation import register_validation_tools


__all__ = [
    "register_auth_tools",
    "register_ceph_operations_tools",
    "register_cluster_health_tools",
    "register_cluster_tools",
    "register_messaging_operations_tools",
    "register_node_lifecycle_tools",
    "register_operations_visibility_tools",
    "register_template_generation_tools",
    "register_troubleshooting_tools",
    "register_validation_tools",
]
