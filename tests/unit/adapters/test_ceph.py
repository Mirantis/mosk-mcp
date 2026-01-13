"""Tests for Ceph adapter implementation.

Tests the CephAdapter class, data structures, and singleton functions.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.adapters.ceph import (
    CAPACITY_CRITICAL_THRESHOLD,
    CAPACITY_EMERGENCY_THRESHOLD,
    CAPACITY_WARNING_THRESHOLD,
    CephAdapter,
    CephClusterStatus,
    CephError,
    CephHealthStatus,
    OSDInfo,
    OSDState,
    OSDStatus,
    PGState,
    PGSummary,
    PoolInfo,
    RecoveryStatus,
    get_ceph_adapter,
    reset_ceph_adapter,
)
from mosk_mcp.core.exceptions import MoskConnectionError, ResourceNotFoundError


# =============================================================================
# Enum Tests
# =============================================================================


class TestCephHealthStatus:
    """Tests for CephHealthStatus enum."""

    def test_all_statuses_defined(self) -> None:
        """Test all health statuses are defined."""
        assert CephHealthStatus.HEALTH_OK == "HEALTH_OK"
        assert CephHealthStatus.HEALTH_WARN == "HEALTH_WARN"
        assert CephHealthStatus.HEALTH_ERR == "HEALTH_ERR"
        assert CephHealthStatus.UNKNOWN == "UNKNOWN"

    def test_status_count(self) -> None:
        """Test expected number of statuses."""
        assert len(CephHealthStatus) == 4


class TestOSDStatus:
    """Tests for OSDStatus enum."""

    def test_statuses_defined(self) -> None:
        """Test OSD statuses are defined."""
        assert OSDStatus.UP == "up"
        assert OSDStatus.DOWN == "down"
        assert OSDStatus.UNKNOWN == "unknown"


class TestOSDState:
    """Tests for OSDState enum."""

    def test_states_defined(self) -> None:
        """Test OSD states are defined."""
        assert OSDState.IN == "in"
        assert OSDState.OUT == "out"


class TestPGState:
    """Tests for PGState enum."""

    def test_common_states_defined(self) -> None:
        """Test common PG states are defined."""
        assert PGState.ACTIVE == "active"
        assert PGState.CLEAN == "clean"
        assert PGState.RECOVERING == "recovering"
        assert PGState.DEGRADED == "degraded"
        assert PGState.STALE == "stale"


# =============================================================================
# Constants Tests
# =============================================================================


class TestCapacityThresholds:
    """Tests for capacity threshold constants."""

    def test_thresholds_defined(self) -> None:
        """Test capacity thresholds are defined correctly."""
        assert CAPACITY_WARNING_THRESHOLD == 70
        assert CAPACITY_CRITICAL_THRESHOLD == 80
        assert CAPACITY_EMERGENCY_THRESHOLD == 85

    def test_thresholds_ordering(self) -> None:
        """Test thresholds are in correct order."""
        assert CAPACITY_WARNING_THRESHOLD < CAPACITY_CRITICAL_THRESHOLD
        assert CAPACITY_CRITICAL_THRESHOLD < CAPACITY_EMERGENCY_THRESHOLD


# =============================================================================
# CephError Tests
# =============================================================================


class TestCephError:
    """Tests for CephError exception."""

    def test_basic_error(self) -> None:
        """Test basic error creation."""
        error = CephError("Something failed")
        assert str(error) == "Something failed"
        assert error.ceph_command is None
        assert error.cluster_name is None

    def test_error_with_details(self) -> None:
        """Test error with command and cluster."""
        error = CephError(
            "Command failed",
            ceph_command="ceph osd tree",
            cluster_name="production",
        )
        assert error.ceph_command == "ceph osd tree"
        assert error.cluster_name == "production"
        assert error.details is not None
        assert error.details["ceph_command"] == "ceph osd tree"


# =============================================================================
# CephClusterStatus Tests
# =============================================================================


class TestCephClusterStatus:
    """Tests for CephClusterStatus dataclass."""

    def test_default_values(self) -> None:
        """Test default values."""
        status = CephClusterStatus(health=CephHealthStatus.HEALTH_OK)
        assert status.health == CephHealthStatus.HEALTH_OK
        assert status.fsid == ""
        assert status.num_osds == 0
        assert status.capacity_percent == 0.0

    def test_is_healthy(self) -> None:
        """Test is_healthy property."""
        healthy = CephClusterStatus(health=CephHealthStatus.HEALTH_OK)
        assert healthy.is_healthy is True

        unhealthy = CephClusterStatus(health=CephHealthStatus.HEALTH_WARN)
        assert unhealthy.is_healthy is False

    def test_all_osds_up(self) -> None:
        """Test all_osds_up property."""
        all_up = CephClusterStatus(
            health=CephHealthStatus.HEALTH_OK,
            num_osds=5,
            num_osds_up=5,
        )
        assert all_up.all_osds_up is True

        some_down = CephClusterStatus(
            health=CephHealthStatus.HEALTH_WARN,
            num_osds=5,
            num_osds_up=3,
        )
        assert some_down.all_osds_up is False

    def test_capacity_status_normal(self) -> None:
        """Test capacity status is normal when below warning."""
        status = CephClusterStatus(
            health=CephHealthStatus.HEALTH_OK,
            capacity_percent=50.0,
        )
        assert status.capacity_status == "normal"

    def test_capacity_status_warning(self) -> None:
        """Test capacity status is warning."""
        status = CephClusterStatus(
            health=CephHealthStatus.HEALTH_WARN,
            capacity_percent=75.0,
        )
        assert status.capacity_status == "warning"

    def test_capacity_status_critical(self) -> None:
        """Test capacity status is critical."""
        status = CephClusterStatus(
            health=CephHealthStatus.HEALTH_WARN,
            capacity_percent=82.0,
        )
        assert status.capacity_status == "critical"

    def test_capacity_status_emergency(self) -> None:
        """Test capacity status is emergency."""
        status = CephClusterStatus(
            health=CephHealthStatus.HEALTH_ERR,
            capacity_percent=90.0,
        )
        assert status.capacity_status == "emergency"

    def test_model_dump(self) -> None:
        """Test converting status to dictionary using Pydantic model_dump."""
        status = CephClusterStatus(
            health=CephHealthStatus.HEALTH_OK,
            fsid="test-fsid",
            num_osds=10,
            num_osds_up=10,
            capacity_percent=50.0,
        )
        result = status.model_dump(mode="json")

        assert result["health"] == "HEALTH_OK"
        assert result["fsid"] == "test-fsid"
        assert result["num_osds"] == 10
        assert result["is_healthy"] is True
        assert result["all_osds_up"] is True
        assert result["capacity_status"] == "normal"


# =============================================================================
# OSDInfo Tests
# =============================================================================


class TestOSDInfo:
    """Tests for OSDInfo dataclass."""

    def test_default_values(self) -> None:
        """Test default values."""
        osd = OSDInfo(osd_id=0)
        assert osd.osd_id == 0
        assert osd.status == OSDStatus.UNKNOWN
        assert osd.state == OSDState.IN
        assert osd.reweight == 1.0

    def test_is_up(self) -> None:
        """Test is_up property."""
        up_osd = OSDInfo(osd_id=0, status=OSDStatus.UP)
        assert up_osd.is_up is True

        down_osd = OSDInfo(osd_id=0, status=OSDStatus.DOWN)
        assert down_osd.is_up is False

    def test_is_in(self) -> None:
        """Test is_in property."""
        in_osd = OSDInfo(osd_id=0, state=OSDState.IN)
        assert in_osd.is_in is True

        out_osd = OSDInfo(osd_id=0, state=OSDState.OUT)
        assert out_osd.is_in is False

    def test_is_healthy(self) -> None:
        """Test is_healthy property."""
        healthy = OSDInfo(osd_id=0, status=OSDStatus.UP, state=OSDState.IN)
        assert healthy.is_healthy is True

        unhealthy_down = OSDInfo(osd_id=0, status=OSDStatus.DOWN, state=OSDState.IN)
        assert unhealthy_down.is_healthy is False

        unhealthy_out = OSDInfo(osd_id=0, status=OSDStatus.UP, state=OSDState.OUT)
        assert unhealthy_out.is_healthy is False

    def test_model_dump(self) -> None:
        """Test converting OSD info to dictionary using Pydantic model_dump."""
        osd = OSDInfo(
            osd_id=5,
            uuid="test-uuid",
            status=OSDStatus.UP,
            state=OSDState.IN,
            host="storage-01",
            device_class="ssd",
        )
        result = osd.model_dump(mode="json")

        assert result["osd_id"] == 5
        assert result["uuid"] == "test-uuid"
        assert result["status"] == "up"
        assert result["state"] == "in"
        assert result["host"] == "storage-01"
        assert result["device_class"] == "ssd"
        assert result["is_healthy"] is True


# =============================================================================
# PoolInfo Tests
# =============================================================================


class TestPoolInfo:
    """Tests for PoolInfo dataclass."""

    def test_default_values(self) -> None:
        """Test default values."""
        pool = PoolInfo(pool_id=0, pool_name="test")
        assert pool.pool_id == 0
        assert pool.pool_name == "test"
        assert pool.size == 3
        assert pool.min_size == 2

    def test_model_dump(self) -> None:
        """Test converting pool info to dictionary using Pydantic model_dump."""
        pool = PoolInfo(
            pool_id=1,
            pool_name="rbd",
            pg_num=128,
            size=3,
            application="rbd",
        )
        result = pool.model_dump()

        assert result["pool_id"] == 1
        assert result["pool_name"] == "rbd"
        assert result["pg_num"] == 128
        assert result["size"] == 3
        assert result["application"] == "rbd"


# =============================================================================
# PGSummary Tests
# =============================================================================


class TestPGSummary:
    """Tests for PGSummary dataclass."""

    def test_default_values(self) -> None:
        """Test default values."""
        summary = PGSummary()
        assert summary.total_pgs == 0
        assert summary.active_clean == 0
        assert summary.recovering is False

    def test_is_healthy(self) -> None:
        """Test is_healthy property."""
        healthy = PGSummary(total_pgs=100, active_clean=100)
        assert healthy.is_healthy is True

        unhealthy = PGSummary(total_pgs=100, active_clean=90)
        assert unhealthy.is_healthy is False

    def test_has_stuck_pgs(self) -> None:
        """Test has_stuck_pgs property."""
        no_stuck = PGSummary()
        assert no_stuck.has_stuck_pgs is False

        has_stuck = PGSummary(stuck_pgs={"stale": 5})
        assert has_stuck.has_stuck_pgs is True

    def test_model_dump(self) -> None:
        """Test converting PG summary to dictionary using Pydantic model_dump."""
        summary = PGSummary(
            total_pgs=256,
            active_clean=256,
            states={"active+clean": 256},
        )
        result = summary.model_dump()

        assert result["total_pgs"] == 256
        assert result["active_clean"] == 256
        assert result["is_healthy"] is True
        assert result["has_stuck_pgs"] is False


# =============================================================================
# RecoveryStatus Tests
# =============================================================================


class TestRecoveryStatus:
    """Tests for RecoveryStatus dataclass."""

    def test_default_values(self) -> None:
        """Test default values."""
        status = RecoveryStatus()
        assert status.is_recovering is False
        assert status.is_backfilling is False
        assert status.misplaced_ratio == 0.0

    def test_is_in_progress(self) -> None:
        """Test is_in_progress property."""
        not_in_progress = RecoveryStatus()
        assert not_in_progress.is_in_progress is False

        recovering = RecoveryStatus(is_recovering=True)
        assert recovering.is_in_progress is True

        backfilling = RecoveryStatus(is_backfilling=True)
        assert backfilling.is_in_progress is True

    def test_model_dump(self) -> None:
        """Test converting recovery status to dictionary using Pydantic model_dump."""
        status = RecoveryStatus(
            is_recovering=True,
            misplaced_objects=100,
            misplaced_total=10000,
            misplaced_ratio=1.0,
        )
        result = status.model_dump()

        assert result["is_recovering"] is True
        assert result["is_in_progress"] is True
        assert result["misplaced_objects"] == 100
        assert result["misplaced_ratio"] == 1.0


# =============================================================================
# CephAdapter Initialization Tests
# =============================================================================


class TestCephAdapterInitialization:
    """Tests for CephAdapter initialization."""

    def test_default_initialization(self) -> None:
        """Test default initialization."""
        mock_k8s = AsyncMock()
        adapter = CephAdapter(mock_k8s)

        assert adapter._toolbox_namespace == "rook-ceph"
        assert adapter._toolbox_pod_label == "app=rook-ceph-tools"
        assert adapter._cluster_name == "ceph"
        assert adapter._connected is False

    def test_custom_initialization(self) -> None:
        """Test custom initialization."""
        mock_k8s = AsyncMock()
        adapter = CephAdapter(
            mock_k8s,
            toolbox_namespace="custom-ns",
            toolbox_pod_label="app=custom",
            cluster_name="custom-cluster",
        )

        assert adapter._toolbox_namespace == "custom-ns"
        assert adapter._toolbox_pod_label == "app=custom"
        assert adapter._cluster_name == "custom-cluster"

    def test_from_settings(self) -> None:
        """Test creating adapter from settings."""
        mock_k8s = AsyncMock()
        mock_settings = MagicMock()

        adapter = CephAdapter.from_settings(mock_k8s, mock_settings)

        assert adapter._k8s == mock_k8s
        assert adapter._connected is False


# =============================================================================
# CephAdapter Connection Tests
# =============================================================================


class TestCephAdapterConnection:
    """Tests for CephAdapter connection operations."""

    @pytest.mark.asyncio
    async def test_connect_success(self) -> None:
        """Test successful connection."""
        mock_k8s = AsyncMock()
        mock_k8s.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "rook-ceph-tools-12345"},
                    "status": {"phase": "Running"},
                }
            ]
        )

        adapter = CephAdapter(mock_k8s)
        await adapter.connect()

        assert adapter._connected is True
        assert adapter._toolbox_pod_name == "rook-ceph-tools-12345"

    @pytest.mark.asyncio
    async def test_connect_no_running_pods(self) -> None:
        """Test connection fails when no running pods found."""
        mock_k8s = AsyncMock()
        mock_k8s.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "rook-ceph-tools-12345"},
                    "status": {"phase": "Pending"},
                }
            ]
        )

        adapter = CephAdapter(mock_k8s)

        with pytest.raises(MoskConnectionError) as exc_info:
            await adapter.connect()

        assert "No running Ceph toolbox pod" in str(exc_info.value)
        assert adapter._connected is False

    @pytest.mark.asyncio
    async def test_connect_idempotent(self) -> None:
        """Test connect is idempotent."""
        mock_k8s = AsyncMock()
        mock_k8s.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "rook-ceph-tools-12345"},
                    "status": {"phase": "Running"},
                }
            ]
        )

        adapter = CephAdapter(mock_k8s)
        await adapter.connect()
        await adapter.connect()  # Second call should be no-op

        # list should only be called once
        assert mock_k8s.list.call_count == 1

    @pytest.mark.asyncio
    async def test_disconnect(self) -> None:
        """Test disconnection."""
        mock_k8s = AsyncMock()
        mock_k8s.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "rook-ceph-tools-12345"},
                    "status": {"phase": "Running"},
                }
            ]
        )

        adapter = CephAdapter(mock_k8s)
        await adapter.connect()
        await adapter.disconnect()

        assert adapter._connected is False
        assert adapter._toolbox_pod_name is None

    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        """Test async context manager."""
        mock_k8s = AsyncMock()
        mock_k8s.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "rook-ceph-tools-12345"},
                    "status": {"phase": "Running"},
                }
            ]
        )

        async with CephAdapter(mock_k8s) as adapter:
            assert adapter._connected is True

        assert adapter._connected is False


class TestCephAdapterEnsureConnected:
    """Tests for _ensure_connected method."""

    def test_ensure_connected_raises_when_not_connected(self) -> None:
        """Test _ensure_connected raises when not connected."""
        mock_k8s = AsyncMock()
        adapter = CephAdapter(mock_k8s)

        with pytest.raises(MoskConnectionError) as exc_info:
            adapter._ensure_connected()

        assert "not connected" in str(exc_info.value)


# =============================================================================
# CephAdapter Command Execution Tests
# =============================================================================


class TestCephAdapterCommandExecution:
    """Tests for Ceph command execution."""

    @pytest.mark.asyncio
    async def test_execute_command_json_output(self) -> None:
        """Test executing command with JSON output."""
        mock_k8s = AsyncMock()
        mock_k8s.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "rook-ceph-tools-12345"},
                    "status": {"phase": "Running"},
                }
            ]
        )

        adapter = CephAdapter(mock_k8s)
        await adapter.connect()

        with patch("mosk_mcp.adapters.ceph.execute_in_pod") as mock_exec:
            mock_exec.return_value = MagicMock(
                success=True,
                stdout='{"status": "ok"}',
                stderr="",
                return_code=0,
            )

            result = await adapter._execute_ceph_command(["status"])

            assert result == {"status": "ok"}
            mock_exec.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_command_text_output(self) -> None:
        """Test executing command with text output."""
        mock_k8s = AsyncMock()
        mock_k8s.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "rook-ceph-tools-12345"},
                    "status": {"phase": "Running"},
                }
            ]
        )

        adapter = CephAdapter(mock_k8s)
        await adapter.connect()

        with patch("mosk_mcp.adapters.ceph.execute_in_pod") as mock_exec:
            mock_exec.return_value = MagicMock(
                success=True,
                stdout="ceph status output",
                stderr="",
                return_code=0,
            )

            result = await adapter._execute_ceph_command(["status"], json_output=False)

            assert result == "ceph status output"

    @pytest.mark.asyncio
    async def test_execute_command_failure(self) -> None:
        """Test handling command failure."""
        mock_k8s = AsyncMock()
        mock_k8s.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "rook-ceph-tools-12345"},
                    "status": {"phase": "Running"},
                }
            ]
        )

        adapter = CephAdapter(mock_k8s)
        await adapter.connect()

        with patch("mosk_mcp.adapters.ceph.execute_in_pod") as mock_exec:
            mock_exec.return_value = MagicMock(
                success=False,
                stdout="",
                stderr="command failed",
                return_code=1,
            )

            with pytest.raises(CephError) as exc_info:
                await adapter._execute_ceph_command(["invalid"])

            assert "command failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_execute_command_invalid_json(self) -> None:
        """Test handling invalid JSON output."""
        mock_k8s = AsyncMock()
        mock_k8s.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "rook-ceph-tools-12345"},
                    "status": {"phase": "Running"},
                }
            ]
        )

        adapter = CephAdapter(mock_k8s)
        await adapter.connect()

        with patch("mosk_mcp.adapters.ceph.execute_in_pod") as mock_exec:
            mock_exec.return_value = MagicMock(
                success=True,
                stdout="not valid json",
                stderr="",
                return_code=0,
            )

            with pytest.raises(CephError) as exc_info:
                await adapter._execute_ceph_command(["status"])

            assert "Failed to parse Ceph output as JSON" in str(exc_info.value)


# =============================================================================
# CephAdapter API Method Tests
# =============================================================================


class TestCephAdapterGetClusterStatus:
    """Tests for get_cluster_status method."""

    @pytest.mark.asyncio
    async def test_get_cluster_status(self) -> None:
        """Test getting cluster status."""
        mock_k8s = AsyncMock()
        mock_k8s.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "rook-ceph-tools-12345"},
                    "status": {"phase": "Running"},
                }
            ]
        )

        adapter = CephAdapter(mock_k8s)
        await adapter.connect()

        mock_status = {
            "health": {"status": "HEALTH_OK", "checks": {}},
            "fsid": "test-fsid",
            "osdmap": {
                "osdmap": {
                    "num_osds": 10,
                    "num_up_osds": 10,
                    "num_in_osds": 10,
                }
            },
            "pgmap": {
                "num_pgs": 256,
                "pgs_by_state": [{"state_name": "active+clean", "count": 256}],
                "bytes_total": 1000000000,
                "bytes_used": 500000000,
                "bytes_avail": 500000000,
            },
            "quorum_names": ["mon1", "mon2", "mon3"],
        }

        with patch.object(adapter, "_execute_ceph_command", return_value=mock_status):
            status = await adapter.get_cluster_status()

            assert status.health == CephHealthStatus.HEALTH_OK
            assert status.fsid == "test-fsid"
            assert status.num_osds == 10
            assert status.num_osds_up == 10
            assert status.num_pgs == 256
            assert status.capacity_percent == 50.0


class TestCephAdapterListOSDs:
    """Tests for list_osds method."""

    @pytest.mark.asyncio
    async def test_list_osds(self) -> None:
        """Test listing OSDs."""
        mock_k8s = AsyncMock()
        mock_k8s.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "rook-ceph-tools-12345"},
                    "status": {"phase": "Running"},
                }
            ]
        )

        adapter = CephAdapter(mock_k8s)
        await adapter.connect()

        mock_tree = {
            "nodes": [
                {"id": -1, "type": "root", "name": "default"},
                {
                    "id": -2,
                    "type": "host",
                    "name": "storage-01",
                    "children": [0, 1],
                },
                {
                    "id": 0,
                    "type": "osd",
                    "name": "osd.0",
                    "status": "up",
                    "device_class": "ssd",
                    "crush_weight": 1.0,
                    "reweight": 1.0,
                },
            ]
        }

        mock_df = {
            "nodes": [
                {
                    "id": 0,
                    "kb": 1000000,
                    "kb_used": 500000,
                    "kb_avail": 500000,
                    "utilization": 50.0,
                    "pgs": 64,
                }
            ]
        }

        mock_dump = {
            "osds": [
                {"osd": 0, "uuid": "test-uuid", "up": 1, "in": 1},
            ]
        }

        mock_perf = {
            "osd_perf_infos": [
                {
                    "id": 0,
                    "perf_stats": {
                        "commit_latency_ms": 1.5,
                        "apply_latency_ms": 2.0,
                    },
                }
            ]
        }

        async def mock_execute(cmd: list[str], **kwargs: Any) -> dict[str, Any]:
            if cmd == ["osd", "tree"]:
                return mock_tree
            elif cmd == ["osd", "df"]:
                return mock_df
            elif cmd == ["osd", "dump"]:
                return mock_dump
            elif cmd == ["osd", "perf"]:
                return mock_perf
            return {}

        with patch.object(adapter, "_execute_ceph_command", side_effect=mock_execute):
            osds = await adapter.list_osds()

            assert len(osds) == 1
            assert osds[0].osd_id == 0
            assert osds[0].host == "storage-01"
            assert osds[0].status == OSDStatus.UP
            assert osds[0].device_class == "ssd"


class TestCephAdapterGetOSDDetails:
    """Tests for get_osd_details method."""

    @pytest.mark.asyncio
    async def test_get_osd_details_found(self) -> None:
        """Test getting OSD details when found."""
        mock_k8s = AsyncMock()
        mock_k8s.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "rook-ceph-tools-12345"},
                    "status": {"phase": "Running"},
                }
            ]
        )

        adapter = CephAdapter(mock_k8s)
        await adapter.connect()

        mock_osds = [
            OSDInfo(osd_id=0, status=OSDStatus.UP),
            OSDInfo(osd_id=1, status=OSDStatus.UP),
        ]

        with patch.object(adapter, "list_osds", return_value=mock_osds):
            osd = await adapter.get_osd_details(1)

            assert osd.osd_id == 1

    @pytest.mark.asyncio
    async def test_get_osd_details_not_found(self) -> None:
        """Test getting OSD details when not found."""
        mock_k8s = AsyncMock()
        mock_k8s.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "rook-ceph-tools-12345"},
                    "status": {"phase": "Running"},
                }
            ]
        )

        adapter = CephAdapter(mock_k8s)
        await adapter.connect()

        mock_osds = [
            OSDInfo(osd_id=0, status=OSDStatus.UP),
        ]

        with patch.object(adapter, "list_osds", return_value=mock_osds):
            with pytest.raises(ResourceNotFoundError) as exc_info:
                await adapter.get_osd_details(99)

            assert "OSD 99 not found" in str(exc_info.value)


class TestCephAdapterHealthCheck:
    """Tests for check_health_for_operation method."""

    @pytest.mark.asyncio
    async def test_check_health_healthy_cluster(self) -> None:
        """Test health check on healthy cluster."""
        mock_k8s = AsyncMock()
        mock_k8s.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "rook-ceph-tools-12345"},
                    "status": {"phase": "Running"},
                }
            ]
        )

        adapter = CephAdapter(mock_k8s)
        await adapter.connect()

        mock_status = CephClusterStatus(
            health=CephHealthStatus.HEALTH_OK,
            num_osds=10,
            num_osds_up=10,
            capacity_percent=50.0,
        )

        mock_recovery = RecoveryStatus(is_recovering=False)

        with (
            patch.object(adapter, "get_cluster_status", new=AsyncMock(return_value=mock_status)),
            patch.object(adapter, "get_recovery_status", new=AsyncMock(return_value=mock_recovery)),
        ):
            is_safe, warnings = await adapter.check_health_for_operation("osd-add")

            assert is_safe is True
            assert len(warnings) == 0

    @pytest.mark.asyncio
    async def test_check_health_blocks_on_error(self) -> None:
        """Test health check blocks on HEALTH_ERR."""
        mock_k8s = AsyncMock()
        mock_k8s.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "rook-ceph-tools-12345"},
                    "status": {"phase": "Running"},
                }
            ]
        )

        adapter = CephAdapter(mock_k8s)
        await adapter.connect()

        mock_status = CephClusterStatus(
            health=CephHealthStatus.HEALTH_ERR,
            num_osds=10,
            num_osds_up=8,
            capacity_percent=50.0,
        )

        # With strict=True (default), the HEALTH_ERR returns False from the elif branch
        # We pass strict=False to hit that branch
        with patch.object(adapter, "get_cluster_status", new=AsyncMock(return_value=mock_status)):
            is_safe, warnings = await adapter.check_health_for_operation("osd-remove", strict=False)

            assert is_safe is False
            assert any("blocked" in w.lower() for w in warnings)


# =============================================================================
# Singleton Function Tests
# =============================================================================


class TestCephAdapterSingleton:
    """Tests for singleton functions."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self) -> None:
        """Reset singleton before each test."""
        reset_ceph_adapter()
        yield
        reset_ceph_adapter()

    @pytest.mark.asyncio
    async def test_get_ceph_adapter_creates_singleton(self) -> None:
        """Test get_ceph_adapter creates and returns singleton."""
        mock_k8s = AsyncMock()
        mock_k8s.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "rook-ceph-tools-12345"},
                    "status": {"phase": "Running"},
                }
            ]
        )

        adapter = await get_ceph_adapter(mock_k8s)

        assert adapter is not None
        assert adapter._connected is True

    @pytest.mark.asyncio
    async def test_get_ceph_adapter_returns_same_instance(self) -> None:
        """Test get_ceph_adapter returns same instance on subsequent calls."""
        mock_k8s = AsyncMock()
        mock_k8s.list = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "rook-ceph-tools-12345"},
                    "status": {"phase": "Running"},
                }
            ]
        )

        adapter1 = await get_ceph_adapter(mock_k8s)
        adapter2 = await get_ceph_adapter(mock_k8s)

        assert adapter1 is adapter2

    def test_reset_ceph_adapter(self) -> None:
        """Test reset_ceph_adapter clears singleton."""
        reset_ceph_adapter()

        # After reset, global should be None
        from mosk_mcp.adapters.ceph import _ceph_adapter

        assert _ceph_adapter is None
