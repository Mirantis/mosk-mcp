"""Integration test fixtures with realistic mock adapters.

This module provides pytest fixtures for integration testing MCP tools
with mocked adapters that return realistic cluster data. The fixtures
simulate actual MOSK cluster responses without requiring real cluster access.

Usage:
    @pytest.mark.integration
    async def test_cluster_health(mock_kubernetes_adapter, mock_ceph_adapter):
        result = await get_mosk_cluster_health(
            kubernetes_adapter=mock_kubernetes_adapter,
            ...
        )
        assert result.overall_status == "HEALTHY"
"""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from mosk_mcp.auth.types import Permission, Role, UserContext


# =============================================================================
# User Context Fixtures
# =============================================================================


@pytest.fixture
def admin_context() -> UserContext:
    """Create an administrator user context for testing."""
    return UserContext(
        user_id="integration-admin-001",
        username="integration-admin",
        role=Role.ADMINISTRATOR,
        permissions=frozenset(Permission),
        authenticated_at=datetime.now(UTC),
        auth_method="test",
    )


@pytest.fixture
def operator_context() -> UserContext:
    """Create an operator user context for testing."""
    return UserContext(
        user_id="integration-operator-001",
        username="integration-operator",
        role=Role.OPERATOR,
        permissions=frozenset(
            [
                Permission.READ_MACHINES,
                Permission.READ_OSDPL,
                Permission.READ_CEPH,
                Permission.READ_LOGS,
                Permission.READ_HEALTH,
                Permission.WRITE_MACHINES,
                Permission.WRITE_OSDPL,
                Permission.EXECUTE_MAINTENANCE,
                Permission.EXECUTE_CEPH_OPS,
            ]
        ),
        authenticated_at=datetime.now(UTC),
        auth_method="test",
    )


# =============================================================================
# Realistic Mock Data
# =============================================================================


def create_machine_data(
    name: str,
    role: str = "compute",
    phase: str = "Running",
    namespace: str = "lab",
) -> dict[str, Any]:
    """Create realistic Machine CR data."""
    return {
        "apiVersion": "cluster.k8s.io/v1alpha1",
        "kind": "Machine",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {
                "cluster.sigs.k8s.io/cluster-name": "mos",
                "kaas.mirantis.com/region": "region-one",
                "kaas.mirantis.com/machine-role": role,
            },
            "uid": f"machine-uid-{name}",
            "resourceVersion": "12345",
            "creationTimestamp": "2024-01-15T10:30:00Z",
        },
        "spec": {
            "providerSpec": {
                "hostSelector": {"matchLabels": {"hostId": name}},
                "bareMetalHostProfile": {"name": f"{role}-profile", "namespace": namespace},
                "l2TemplateSelector": {"label": f"{role}-template"},
            }
        },
        "status": {
            "phase": phase,
            "addresses": [
                {"type": "InternalIP", "address": f"10.0.0.{hash(name) % 254 + 1}"},
                {"type": "Hostname", "address": name},
            ],
            "conditions": [
                {
                    "type": "Ready",
                    "status": "True" if phase == "Running" else "False",
                    "lastTransitionTime": "2024-01-15T10:35:00Z",
                }
            ],
        },
    }


def create_osdpl_data(
    name: str = "mos",
    namespace: str = "openstack",
    phase: str = "Deployed",
    openstack_version: str = "antelope",
) -> dict[str, Any]:
    """Create realistic OpenStackDeployment CR data."""
    return {
        "apiVersion": "lcm.mirantis.com/v1alpha1",
        "kind": "OpenStackDeployment",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "uid": f"osdpl-uid-{name}",
        },
        "spec": {
            "openStackVersion": openstack_version,
            "region": "RegionOne",
        },
        "status": {
            "phase": phase,
            "openStackVersion": openstack_version,
            "conditions": [
                {
                    "type": "Ready",
                    "status": "True" if phase == "Deployed" else "False",
                    "lastTransitionTime": "2024-01-15T10:30:00Z",
                }
            ],
        },
    }


def create_osdplst_data(
    name: str = "mos",
    namespace: str = "openstack",
    state: str = "APPLIED",
    health: str = "18/18",
) -> dict[str, Any]:
    """Create realistic OSDPLStatus CR data."""
    services = {
        "identity": {"state": "APPLIED", "openstackVersion": "antelope"},
        "compute": {"state": "APPLIED", "openstackVersion": "antelope"},
        "networking": {"state": "APPLIED", "openstackVersion": "antelope"},
        "image": {"state": "APPLIED", "openstackVersion": "antelope"},
        "block-storage": {"state": "APPLIED", "openstackVersion": "antelope"},
        "orchestration": {"state": "APPLIED", "openstackVersion": "antelope"},
        "dashboard": {"state": "APPLIED", "openstackVersion": "antelope"},
        "load-balancing": {"state": "APPLIED", "openstackVersion": "antelope"},
    }
    return {
        "apiVersion": "lcm.mirantis.com/v1alpha1",
        "kind": "OSDPLStatus",
        "metadata": {
            "name": name,
            "namespace": namespace,
        },
        "status": {
            "osdpl": {
                "state": state,
                "health": health,
                "lcmProgress": health,
                "openstackVersion": "antelope",
            },
            "services": services,
        },
    }


def create_node_data(
    name: str,
    ready: bool = True,
    role: str = "compute",
) -> dict[str, Any]:
    """Create realistic Kubernetes Node data."""
    labels = {
        "kubernetes.io/hostname": name,
        "node-role.kubernetes.io/worker": "",
    }
    if role == "control":
        labels["node-role.kubernetes.io/control-plane"] = ""
    elif role == "storage":
        labels["node-role.kubernetes.io/storage"] = ""

    return {
        "apiVersion": "v1",
        "kind": "Node",
        "metadata": {
            "name": name,
            "labels": labels,
        },
        "status": {
            "conditions": [
                {
                    "type": "Ready",
                    "status": "True" if ready else "False",
                    "lastTransitionTime": "2024-01-15T10:30:00Z",
                },
                {"type": "MemoryPressure", "status": "False"},
                {"type": "DiskPressure", "status": "False"},
            ],
            "addresses": [
                {"type": "InternalIP", "address": f"10.0.0.{hash(name) % 254 + 1}"},
                {"type": "Hostname", "address": name},
            ],
        },
    }


def create_ceph_status() -> dict[str, Any]:
    """Create realistic Ceph cluster status data."""
    return {
        "health": {
            "status": "HEALTH_OK",
            "checks": {},
        },
        "osd_map": {
            "num_osds": 12,
            "num_up_osds": 12,
            "num_in_osds": 12,
        },
        "pg_summary": {
            "num_pgs": 256,
            "pgs_by_state": [{"state_name": "active+clean", "count": 256}],
        },
        "fsmap": {"up": 1, "in": 1},
        "pools": [
            {"name": "cinder-volumes", "id": 1},
            {"name": "nova-vms", "id": 2},
            {"name": "glance-images", "id": 3},
        ],
    }


def create_ceph_df() -> dict[str, Any]:
    """Create realistic Ceph df (disk free) data."""
    return {
        "stats": {
            "total_bytes": 10_995_116_277_760,  # ~10 TiB
            "total_used_bytes": 3_298_534_883_328,  # ~3 TiB (30%)
            "total_avail_bytes": 7_696_581_394_432,  # ~7 TiB
            "total_used_raw_bytes": 9_895_604_649_984,
            "total_used_raw_ratio": 0.90,
        },
        "pools": [
            {
                "name": "cinder-volumes",
                "id": 1,
                "stats": {
                    "stored": 1_099_511_627_776,
                    "percent_used": 0.10,
                    "max_avail": 7_696_581_394_432,
                },
            },
            {
                "name": "nova-vms",
                "id": 2,
                "stats": {
                    "stored": 549_755_813_888,
                    "percent_used": 0.05,
                    "max_avail": 7_696_581_394_432,
                },
            },
            {
                "name": "glance-images",
                "id": 3,
                "stats": {
                    "stored": 109_951_162_778,
                    "percent_used": 0.01,
                    "max_avail": 7_696_581_394_432,
                },
            },
        ],
    }


def create_osd_dump() -> dict[str, Any]:
    """Create realistic Ceph OSD dump data."""
    osds = []
    for i in range(12):
        host = f"storage-0{(i // 4) + 1}"
        osds.append(
            {
                "osd": i,
                "uuid": f"osd-uuid-{i}",
                "up": 1,
                "in": 1,
                "weight": 1.0,
                "primary_affinity": 1.0,
                "host": host,
            }
        )
    return {"osds": osds}


def create_pg_stat() -> dict[str, Any]:
    """Create realistic Ceph PG stat data."""
    return {
        "pg_stats": [
            {"pgid": f"1.{i:x}", "state": "active+clean", "up": [0, 1, 2]} for i in range(50)
        ],
        "num_pgs": 256,
        "num_bytes": 1_759_218_604_442,
        "recovering_objects_per_sec": 0,
        "recovering_bytes_per_sec": 0,
    }


# =============================================================================
# Mock Kubernetes Adapter
# =============================================================================


@pytest.fixture
def mock_kubernetes_adapter() -> MagicMock:
    """Create a mock Kubernetes adapter with realistic responses.

    Returns:
        MagicMock configured to return realistic MOSK cluster data.
    """
    adapter = MagicMock()

    # Machine operations
    machines = [
        create_machine_data("compute-01", "compute"),
        create_machine_data("compute-02", "compute"),
        create_machine_data("compute-03", "compute"),
        create_machine_data("control-01", "control"),
        create_machine_data("control-02", "control"),
        create_machine_data("control-03", "control"),
        create_machine_data("storage-01", "storage"),
        create_machine_data("storage-02", "storage"),
        create_machine_data("storage-03", "storage"),
    ]
    adapter.list_machines = AsyncMock(return_value=machines)
    adapter.get_machine = AsyncMock(
        side_effect=lambda name, **kw: next(
            (m for m in machines if m["metadata"]["name"] == name), None
        )
    )

    # Node operations
    nodes = [
        create_node_data("compute-01"),
        create_node_data("compute-02"),
        create_node_data("compute-03"),
        create_node_data("control-01", role="control"),
        create_node_data("control-02", role="control"),
        create_node_data("control-03", role="control"),
        create_node_data("storage-01", role="storage"),
        create_node_data("storage-02", role="storage"),
        create_node_data("storage-03", role="storage"),
    ]
    adapter.list_nodes = AsyncMock(return_value=nodes)

    # OSDPL operations
    osdpl = create_osdpl_data()
    adapter.list_openstack_deployments = AsyncMock(return_value=[osdpl])
    adapter.get_openstack_deployment = AsyncMock(return_value=osdpl)

    # OSDPLStatus operations
    osdplst = create_osdplst_data()
    adapter.list_openstack_deployment_status = AsyncMock(return_value=[osdplst])
    adapter.get_openstack_deployment_status = AsyncMock(return_value=osdplst)

    # Cluster operations
    cluster = {
        "metadata": {"name": "mos", "namespace": "lab"},
        "spec": {
            "providerSpec": {
                "value": {
                    "release": "mosk-21-0-0-25-2",
                }
            }
        },
        "status": {
            "phase": "Ready",
            "conditions": [{"type": "Ready", "status": "True"}],
        },
    }
    adapter.get_cluster = AsyncMock(return_value=cluster)
    adapter.list_clusters = AsyncMock(return_value=[cluster])

    # Health checks
    adapter.check_health = AsyncMock(
        return_value={
            "healthy": True,
            "version": "v1.28.5",
            "nodes_ready": 9,
            "nodes_total": 9,
        }
    )
    adapter.check_connectivity = AsyncMock(return_value=True)
    adapter.check_api_health = AsyncMock(return_value=True)

    # Pods
    adapter.list_pods = AsyncMock(
        return_value=[
            {
                "metadata": {"name": "nova-api-0", "namespace": "openstack"},
                "status": {"phase": "Running"},
            },
            {
                "metadata": {"name": "keystone-api-0", "namespace": "openstack"},
                "status": {"phase": "Running"},
            },
        ]
    )

    # Maintenance requests
    adapter.list_maintenance_requests = AsyncMock(return_value=[])

    # LCM Machines
    adapter.list_lcm_machines = AsyncMock(
        return_value=[
            {"metadata": {"name": name}, "status": {"ready": True}}
            for name in [
                "compute-01",
                "compute-02",
                "compute-03",
                "control-01",
                "control-02",
                "control-03",
                "storage-01",
                "storage-02",
                "storage-03",
            ]
        ]
    )

    # Helm bundles
    adapter.list_helm_bundles = AsyncMock(return_value=[])

    # Namespace for machines
    adapter.get_mosk_machines_namespace = AsyncMock(return_value="lab")

    # Custom resources generic method
    adapter.get_custom_resource = AsyncMock(return_value=None)
    adapter.list_custom_resources = AsyncMock(return_value=[])

    return adapter


# =============================================================================
# Mock Ceph Adapter
# =============================================================================


@pytest.fixture
def mock_ceph_adapter() -> MagicMock:
    """Create a mock Ceph adapter with realistic responses.

    Returns:
        MagicMock configured to return realistic Ceph cluster data.
    """
    adapter = MagicMock()

    # Ceph status
    adapter.get_status = AsyncMock(return_value=create_ceph_status())

    # Ceph df (capacity)
    adapter.get_df = AsyncMock(return_value=create_ceph_df())

    # OSD dump
    adapter.get_osd_dump = AsyncMock(return_value=create_osd_dump())

    # OSD tree
    adapter.get_osd_tree = AsyncMock(
        return_value={
            "nodes": [
                {"id": -1, "name": "default", "type": "root", "children": [-2, -3, -4]},
                {"id": -2, "name": "storage-01", "type": "host", "children": [0, 1, 2, 3]},
                {"id": -3, "name": "storage-02", "type": "host", "children": [4, 5, 6, 7]},
                {"id": -4, "name": "storage-03", "type": "host", "children": [8, 9, 10, 11]},
            ]
            + [
                {"id": i, "name": f"osd.{i}", "type": "osd", "status": "up", "reweight": 1.0}
                for i in range(12)
            ]
        }
    )

    # PG stat
    adapter.get_pg_stat = AsyncMock(return_value=create_pg_stat())

    # OSD metadata
    adapter.get_osd_metadata = AsyncMock(
        return_value={
            "osd_objectstore": "bluestore",
            "hostname": "storage-01",
            "devices": "/dev/sdb",
        }
    )

    # Pool stats
    adapter.get_pool_stats = AsyncMock(
        return_value=[
            {"pool_name": "cinder-volumes", "pool_id": 1},
            {"pool_name": "nova-vms", "pool_id": 2},
            {"pool_name": "glance-images", "pool_id": 3},
        ]
    )

    # Health detail
    adapter.get_health_detail = AsyncMock(
        return_value={
            "health": {"status": "HEALTH_OK", "checks": {}},
            "timechecks": {"round": 1, "epoch": 100},
        }
    )

    # Connection
    adapter.is_connected = MagicMock(return_value=True)

    return adapter


# =============================================================================
# Mock OpenStack Adapter
# =============================================================================


@pytest.fixture
def mock_openstack_adapter() -> MagicMock:
    """Create a mock OpenStack adapter with realistic responses.

    Returns:
        MagicMock configured to return realistic OpenStack data.
    """
    adapter = MagicMock()

    # Hypervisor list
    adapter.list_hypervisors = AsyncMock(
        return_value=[
            {
                "id": 1,
                "hypervisor_hostname": "compute-01",
                "state": "up",
                "status": "enabled",
                "vcpus": 64,
                "vcpus_used": 32,
                "memory_mb": 131072,
                "memory_mb_used": 65536,
            },
            {
                "id": 2,
                "hypervisor_hostname": "compute-02",
                "state": "up",
                "status": "enabled",
                "vcpus": 64,
                "vcpus_used": 48,
                "memory_mb": 131072,
                "memory_mb_used": 98304,
            },
            {
                "id": 3,
                "hypervisor_hostname": "compute-03",
                "state": "up",
                "status": "enabled",
                "vcpus": 64,
                "vcpus_used": 16,
                "memory_mb": 131072,
                "memory_mb_used": 32768,
            },
        ]
    )

    # Service list (Nova compute agents)
    adapter.list_compute_services = AsyncMock(
        return_value=[
            {"host": "compute-01", "binary": "nova-compute", "status": "enabled", "state": "up"},
            {"host": "compute-02", "binary": "nova-compute", "status": "enabled", "state": "up"},
            {"host": "compute-03", "binary": "nova-compute", "status": "enabled", "state": "up"},
        ]
    )

    # Network agents
    adapter.list_network_agents = AsyncMock(
        return_value=[
            {"host": "compute-01", "agent_type": "Open vSwitch agent", "alive": True},
            {"host": "compute-02", "agent_type": "Open vSwitch agent", "alive": True},
            {"host": "compute-03", "agent_type": "Open vSwitch agent", "alive": True},
            {"host": "control-01", "agent_type": "L3 agent", "alive": True},
            {"host": "control-02", "agent_type": "L3 agent", "alive": True},
            {"host": "control-03", "agent_type": "L3 agent", "alive": True},
        ]
    )

    # Live migrations
    adapter.list_migrations = AsyncMock(return_value=[])

    # Servers on host
    adapter.list_servers_on_host = AsyncMock(return_value=[])

    return adapter


# =============================================================================
# Mock StackLight Adapter
# =============================================================================


@pytest.fixture
def mock_stacklight_adapter() -> MagicMock:
    """Create a mock StackLight adapter with realistic responses.

    Returns:
        MagicMock configured to return realistic StackLight data.
    """
    adapter = MagicMock()

    # Alerts
    adapter.list_alerts = AsyncMock(return_value=[])
    adapter.get_alert = AsyncMock(return_value=None)

    # Logs
    adapter.query_logs = AsyncMock(
        return_value={
            "logs": [],
            "total": 0,
            "cursor": None,
        }
    )

    # Metrics
    adapter.query_instant = AsyncMock(return_value=[])
    adapter.query_range = AsyncMock(return_value=[])

    return adapter


# =============================================================================
# Combined Mock Context
# =============================================================================


@pytest.fixture
def mock_server_context(
    mock_kubernetes_adapter: MagicMock,
    mock_ceph_adapter: MagicMock,
    mock_openstack_adapter: MagicMock,
    mock_stacklight_adapter: MagicMock,
    admin_context: UserContext,
) -> MagicMock:
    """Create a mock server context with all adapters configured.

    This fixture provides a complete mock environment for integration testing
    MCP tools that require multiple adapters.

    Returns:
        MagicMock configured as a ServerContext with all adapters.
    """
    context = MagicMock()

    # Adapters
    context.mcc_adapter = mock_kubernetes_adapter
    context.mosk_adapter = mock_kubernetes_adapter  # Same for simplicity
    context.ceph_adapter = mock_ceph_adapter
    context.openstack_adapter = mock_openstack_adapter
    context.stacklight_adapter = mock_stacklight_adapter

    # User context
    context.user_context = admin_context
    context.is_authenticated = True

    # Settings mock
    context.settings = MagicMock()
    context.settings.mosk_namespace = "openstack"
    context.settings.ceph_namespace = "rook-ceph"

    return context


# =============================================================================
# Pytest Markers Configuration
# =============================================================================


def pytest_configure(config: Any) -> None:
    """Configure pytest markers for integration tests."""
    config.addinivalue_line(
        "markers",
        "integration: mark test as an integration test",
    )
