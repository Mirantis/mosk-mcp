"""Shared constants for MOSK MCP tools.

This module centralizes constants that are used across multiple tools to avoid
duplication and ensure consistency.
"""

from __future__ import annotations


# OpenStack upgrade states (from OSDPLStatus)
OSDPLST_UPGRADING_STATES = ["APPLYING", "WAITING"]
OSDPLST_APPLYING_STATES = ["APPLYING", "WAITING"]  # Alias for clarity

# OpenStack deployment phases indicating upgrade in progress
UPGRADE_PHASES = ["Updating", "Upgrading", "Reconfiguring"]

# Core OpenStack control plane services
CONTROL_PLANE_SERVICES = [
    "keystone",
    "nova",
    "neutron",
    "glance",
    "cinder",
    "heat",
    "placement",
    "octavia",
    "barbican",
    "designate",
]

# Mapping from LCM service names (status.services) to component names (status.health)
# LCM uses descriptive names, components use project names
LCM_TO_COMPONENT_NAME = {
    "identity": "keystone",
    "compute": "nova",
    "networking": "neutron",
    "image": "glance",
    "block-storage": "cinder",
    "orchestration": "heat",
    "placement": "placement",
    "load-balancer": "octavia",
    "key-manager": "barbican",
    "dns": "designate",
    "dashboard": "horizon",
    "database": "mariadb",
    "messaging": "rabbitmq",
    "coordination": "etcd",
    "memcached": "memcached",
    "ingress": "ingress",
    "redis": "redis",
    "dynamic-resource-balancer": "drb-controller",
}

# Reverse mapping: component name to LCM service name
COMPONENT_TO_LCM_NAME = {v: k for k, v in LCM_TO_COMPONENT_NAME.items()}

# LCM service categories for MOSK deployments
LCM_SERVICE_CATEGORIES = [
    "openstack",
    "ceph",
    "kubernetes",
    "monitoring",
    "logging",
]
