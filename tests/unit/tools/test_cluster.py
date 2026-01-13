"""Unit tests for cluster management tools.

Tests for list_clusters, current_cluster, add_cluster, switch_cluster, and lock_cluster.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.cluster.config import ClusterEnvironment
from mosk_mcp.cluster.manager import (
    ClusterLockedError,
    ClusterSecurityError,
)
from mosk_mcp.cluster.models import (
    AddClusterOutput,
    ClusterInfo,
    ClusterSwitchConfirmation,
    CurrentClusterOutput,
    ListClustersOutput,
    SwitchClusterOutput,
)
from mosk_mcp.tools.cluster.add_cluster import add_cluster
from mosk_mcp.tools.cluster.current_cluster import current_cluster
from mosk_mcp.tools.cluster.list_clusters import list_clusters
from mosk_mcp.tools.cluster.lock_cluster import lock_cluster
from mosk_mcp.tools.cluster.switch_cluster import switch_cluster


@pytest.fixture
def mock_cluster_manager() -> MagicMock:
    """Create a mock cluster manager."""
    manager = MagicMock()
    manager.list_clusters = AsyncMock()
    manager.get_current_cluster = AsyncMock()
    manager.add_cluster = AsyncMock()
    manager.switch_cluster = AsyncMock()
    manager.lock_cluster = AsyncMock()
    return manager


@pytest.fixture
def sample_cluster_info() -> ClusterInfo:
    """Create a sample cluster info object."""
    return ClusterInfo(
        id="dev",
        name="Development Cluster",
        url="https://dev.example.com",
        environment=ClusterEnvironment.DEVELOPMENT,
        ssl_verify=True,
        is_active=True,
        is_authenticated=True,
        is_locked=False,
        has_fingerprint=True,
        description="Dev environment",
        last_used_at=datetime.now(UTC),
    )


@pytest.fixture
def production_cluster_info() -> ClusterInfo:
    """Create a production cluster info object."""
    return ClusterInfo(
        id="prod",
        name="Production Cluster",
        url="https://prod.example.com",
        environment=ClusterEnvironment.PRODUCTION,
        ssl_verify=True,
        is_active=False,
        is_authenticated=False,
        is_locked=True,
        has_fingerprint=True,
        description="Production environment",
        last_used_at=None,
    )


class TestListClusters:
    """Tests for list_clusters tool."""

    @pytest.mark.asyncio
    async def test_list_clusters_success(
        self,
        mock_cluster_manager: MagicMock,
        sample_cluster_info: ClusterInfo,
    ) -> None:
        """Test successful cluster listing."""
        expected_output = ListClustersOutput(
            active_cluster="dev",
            clusters=[sample_cluster_info],
            total_count=1,
            active_is_production=False,
            warning=None,
        )
        mock_cluster_manager.list_clusters.return_value = expected_output

        with patch(
            "mosk_mcp.tools.cluster.list_clusters.get_cluster_manager",
            return_value=mock_cluster_manager,
        ):
            result = await list_clusters()

        assert result.active_cluster == "dev"
        assert result.total_count == 1
        assert len(result.clusters) == 1
        assert result.clusters[0].id == "dev"

    @pytest.mark.asyncio
    async def test_list_clusters_empty(
        self,
        mock_cluster_manager: MagicMock,
    ) -> None:
        """Test listing with no clusters configured."""
        expected_output = ListClustersOutput(
            active_cluster=None,
            clusters=[],
            total_count=0,
            active_is_production=False,
            warning=None,
        )
        mock_cluster_manager.list_clusters.return_value = expected_output

        with patch(
            "mosk_mcp.tools.cluster.list_clusters.get_cluster_manager",
            return_value=mock_cluster_manager,
        ):
            result = await list_clusters()

        assert result.active_cluster is None
        assert result.total_count == 0
        assert len(result.clusters) == 0

    @pytest.mark.asyncio
    async def test_list_clusters_multiple(
        self,
        mock_cluster_manager: MagicMock,
        sample_cluster_info: ClusterInfo,
        production_cluster_info: ClusterInfo,
    ) -> None:
        """Test listing with multiple clusters."""
        expected_output = ListClustersOutput(
            active_cluster="dev",
            clusters=[sample_cluster_info, production_cluster_info],
            total_count=2,
            active_is_production=False,
            warning=None,
        )
        mock_cluster_manager.list_clusters.return_value = expected_output

        with patch(
            "mosk_mcp.tools.cluster.list_clusters.get_cluster_manager",
            return_value=mock_cluster_manager,
        ):
            result = await list_clusters()

        assert result.total_count == 2
        assert len(result.clusters) == 2

    @pytest.mark.asyncio
    async def test_list_clusters_production_warning(
        self,
        mock_cluster_manager: MagicMock,
        production_cluster_info: ClusterInfo,
    ) -> None:
        """Test listing with active production cluster shows warning."""
        production_cluster_info.is_active = True
        expected_output = ListClustersOutput(
            active_cluster="prod",
            clusters=[production_cluster_info],
            total_count=1,
            active_is_production=True,
            warning="Active cluster is production",
        )
        mock_cluster_manager.list_clusters.return_value = expected_output

        with patch(
            "mosk_mcp.tools.cluster.list_clusters.get_cluster_manager",
            return_value=mock_cluster_manager,
        ):
            result = await list_clusters()

        assert result.active_is_production is True
        assert result.warning is not None


class TestCurrentCluster:
    """Tests for current_cluster tool."""

    @pytest.mark.asyncio
    async def test_current_cluster_with_active(
        self,
        mock_cluster_manager: MagicMock,
        sample_cluster_info: ClusterInfo,
    ) -> None:
        """Test getting current cluster when one is active."""
        expected_output = CurrentClusterOutput(
            has_active_cluster=True,
            cluster=sample_cluster_info,
            is_authenticated=True,
            auth_expires_at=datetime.now(UTC),
            username="admin@example.com",
            fingerprint_verified=True,
            warnings=[],
            next_action=None,
        )
        mock_cluster_manager.get_current_cluster.return_value = expected_output

        with patch(
            "mosk_mcp.tools.cluster.current_cluster.get_cluster_manager",
            return_value=mock_cluster_manager,
        ):
            result = await current_cluster()

        assert result.has_active_cluster is True
        assert result.cluster is not None
        assert result.cluster.id == "dev"
        assert result.is_authenticated is True

    @pytest.mark.asyncio
    async def test_current_cluster_none_active(
        self,
        mock_cluster_manager: MagicMock,
    ) -> None:
        """Test getting current cluster when none is active."""
        expected_output = CurrentClusterOutput(
            has_active_cluster=False,
            cluster=None,
            is_authenticated=False,
            auth_expires_at=None,
            username=None,
            fingerprint_verified=False,
            warnings=[],
            next_action="Use list_clusters to see available clusters",
        )
        mock_cluster_manager.get_current_cluster.return_value = expected_output

        with patch(
            "mosk_mcp.tools.cluster.current_cluster.get_cluster_manager",
            return_value=mock_cluster_manager,
        ):
            result = await current_cluster()

        assert result.has_active_cluster is False
        assert result.cluster is None
        assert result.next_action is not None

    @pytest.mark.asyncio
    async def test_current_cluster_production_warnings(
        self,
        mock_cluster_manager: MagicMock,
        production_cluster_info: ClusterInfo,
    ) -> None:
        """Test production cluster shows warnings."""
        production_cluster_info.is_active = True
        expected_output = CurrentClusterOutput(
            has_active_cluster=True,
            cluster=production_cluster_info,
            is_authenticated=True,
            auth_expires_at=datetime.now(UTC),
            username="admin@example.com",
            fingerprint_verified=True,
            warnings=["Production cluster - exercise caution"],
            next_action=None,
        )
        mock_cluster_manager.get_current_cluster.return_value = expected_output

        with patch(
            "mosk_mcp.tools.cluster.current_cluster.get_cluster_manager",
            return_value=mock_cluster_manager,
        ):
            result = await current_cluster()

        assert len(result.warnings) > 0
        assert "Production" in result.warnings[0]


class TestAddCluster:
    """Tests for add_cluster tool."""

    @pytest.mark.asyncio
    async def test_add_cluster_development(
        self,
        mock_cluster_manager: MagicMock,
    ) -> None:
        """Test adding a development cluster."""
        expected_output = AddClusterOutput(
            success=True,
            cluster_id="dev",
            cluster_url="https://dev.example.com",
            is_active=False,
            url_reachable=True,
            validation_warnings=[],
            message="Cluster 'dev' added successfully",
            next_action="Use switch_cluster to make it active",
        )
        mock_cluster_manager.add_cluster.return_value = expected_output

        with patch(
            "mosk_mcp.tools.cluster.add_cluster.get_cluster_manager",
            return_value=mock_cluster_manager,
        ):
            result = await add_cluster(
                cluster_id="dev",
                url="https://dev.example.com",
                environment="development",
            )

        assert result.success is True
        assert result.cluster_id == "dev"
        mock_cluster_manager.add_cluster.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_cluster_production(
        self,
        mock_cluster_manager: MagicMock,
    ) -> None:
        """Test adding a production cluster."""
        expected_output = AddClusterOutput(
            success=True,
            cluster_id="prod",
            cluster_url="https://prod.example.com",
            is_active=False,
            url_reachable=True,
            validation_warnings=["Production cluster added - exercise caution"],
            message="Cluster 'prod' added successfully",
            next_action="Use switch_cluster with confirm_production=True to activate",
        )
        mock_cluster_manager.add_cluster.return_value = expected_output

        with patch(
            "mosk_mcp.tools.cluster.add_cluster.get_cluster_manager",
            return_value=mock_cluster_manager,
        ):
            result = await add_cluster(
                cluster_id="prod",
                url="https://prod.example.com",
                environment="production",
            )

        assert result.success is True
        assert result.cluster_id == "prod"

    @pytest.mark.asyncio
    async def test_add_cluster_invalid_environment(
        self,
        mock_cluster_manager: MagicMock,
    ) -> None:
        """Test adding cluster with invalid environment."""
        with patch(
            "mosk_mcp.tools.cluster.add_cluster.get_cluster_manager",
            return_value=mock_cluster_manager,
        ):
            with pytest.raises(ValueError, match="Invalid environment"):
                await add_cluster(
                    cluster_id="test",
                    url="https://test.example.com",
                    environment="invalid",
                )

    @pytest.mark.asyncio
    async def test_add_cluster_set_active(
        self,
        mock_cluster_manager: MagicMock,
    ) -> None:
        """Test adding cluster and setting it active."""
        expected_output = AddClusterOutput(
            success=True,
            cluster_id="dev",
            cluster_url="https://dev.example.com",
            is_active=True,
            url_reachable=True,
            validation_warnings=[],
            message="Cluster 'dev' added and activated",
            next_action="Use login_secure to authenticate",
        )
        mock_cluster_manager.add_cluster.return_value = expected_output

        with patch(
            "mosk_mcp.tools.cluster.add_cluster.get_cluster_manager",
            return_value=mock_cluster_manager,
        ):
            result = await add_cluster(
                cluster_id="dev",
                url="https://dev.example.com",
                environment="development",
                set_active=True,
            )

        assert result.is_active is True

    @pytest.mark.asyncio
    async def test_add_cluster_failed(
        self,
        mock_cluster_manager: MagicMock,
    ) -> None:
        """Test add cluster failure."""
        expected_output = AddClusterOutput(
            success=False,
            cluster_id="dup",
            cluster_url="https://dup.example.com",
            is_active=False,
            url_reachable=False,
            validation_warnings=["Cluster ID already exists"],
            message="Failed to add cluster",
            next_action="Choose a different cluster_id",
        )
        mock_cluster_manager.add_cluster.return_value = expected_output

        with patch(
            "mosk_mcp.tools.cluster.add_cluster.get_cluster_manager",
            return_value=mock_cluster_manager,
        ):
            result = await add_cluster(
                cluster_id="dup",
                url="https://dup.example.com",
            )

        assert result.success is False


class TestSwitchCluster:
    """Tests for switch_cluster tool."""

    @pytest.mark.asyncio
    async def test_switch_cluster_success(
        self,
        mock_cluster_manager: MagicMock,
    ) -> None:
        """Test successful cluster switch."""
        expected_output = SwitchClusterOutput(
            success=True,
            previous_cluster="dev",
            new_cluster="staging",
            new_cluster_url="https://staging.example.com",
            new_cluster_environment=ClusterEnvironment.STAGING,
            requires_login=True,
            session_cleared=True,
            message="Switched to staging",
            warnings=[],
            confirmation_required=None,
        )
        mock_cluster_manager.switch_cluster.return_value = expected_output

        with patch(
            "mosk_mcp.tools.cluster.switch_cluster.get_cluster_manager",
            return_value=mock_cluster_manager,
        ):
            result = await switch_cluster(cluster_id="staging")

        assert result.success is True
        assert result.new_cluster == "staging"
        assert result.session_cleared is True

    @pytest.mark.asyncio
    async def test_switch_cluster_to_production_requires_confirmation(
        self,
        mock_cluster_manager: MagicMock,
    ) -> None:
        """Test switching to production without confirmation."""
        confirmation = ClusterSwitchConfirmation(
            requires_confirmation=True,
            target_cluster="prod",
            target_environment=ClusterEnvironment.PRODUCTION,
            warning_message="You are switching to production!",
            confirmation_phrase="confirm_production=True",
        )
        expected_output = SwitchClusterOutput(
            success=False,
            previous_cluster="dev",
            new_cluster="prod",
            new_cluster_url="https://prod.example.com",
            new_cluster_environment=ClusterEnvironment.PRODUCTION,
            requires_login=True,
            session_cleared=False,
            message="Confirmation required",
            warnings=["Production cluster requires confirmation"],
            confirmation_required=confirmation,
        )
        mock_cluster_manager.switch_cluster.return_value = expected_output

        with patch(
            "mosk_mcp.tools.cluster.switch_cluster.get_cluster_manager",
            return_value=mock_cluster_manager,
        ):
            result = await switch_cluster(cluster_id="prod")

        assert result.success is False
        assert result.confirmation_required is not None

    @pytest.mark.asyncio
    async def test_switch_cluster_to_production_with_confirmation(
        self,
        mock_cluster_manager: MagicMock,
    ) -> None:
        """Test switching to production with confirmation."""
        expected_output = SwitchClusterOutput(
            success=True,
            previous_cluster="dev",
            new_cluster="prod",
            new_cluster_url="https://prod.example.com",
            new_cluster_environment=ClusterEnvironment.PRODUCTION,
            requires_login=True,
            session_cleared=True,
            message="Switched to production",
            warnings=["You are now targeting production"],
            confirmation_required=None,
        )
        mock_cluster_manager.switch_cluster.return_value = expected_output

        with patch(
            "mosk_mcp.tools.cluster.switch_cluster.get_cluster_manager",
            return_value=mock_cluster_manager,
        ):
            result = await switch_cluster(
                cluster_id="prod",
                confirm_production=True,
            )

        assert result.success is True
        assert result.new_cluster_environment == ClusterEnvironment.PRODUCTION

    @pytest.mark.asyncio
    async def test_switch_cluster_locked_error(
        self,
        mock_cluster_manager: MagicMock,
    ) -> None:
        """Test switching from locked cluster raises error."""
        mock_cluster_manager.switch_cluster.side_effect = ClusterLockedError(
            "Current cluster is locked"
        )

        with patch(
            "mosk_mcp.tools.cluster.switch_cluster.get_cluster_manager",
            return_value=mock_cluster_manager,
        ):
            with pytest.raises(ClusterLockedError):
                await switch_cluster(cluster_id="dev")

    @pytest.mark.asyncio
    async def test_switch_cluster_force_from_locked(
        self,
        mock_cluster_manager: MagicMock,
    ) -> None:
        """Test force switch from locked cluster."""
        expected_output = SwitchClusterOutput(
            success=True,
            previous_cluster="prod",
            new_cluster="dev",
            new_cluster_url="https://dev.example.com",
            new_cluster_environment=ClusterEnvironment.DEVELOPMENT,
            requires_login=True,
            session_cleared=True,
            message="Force switched from locked cluster",
            warnings=["Forced switch from locked cluster"],
            confirmation_required=None,
        )
        mock_cluster_manager.switch_cluster.return_value = expected_output

        with patch(
            "mosk_mcp.tools.cluster.switch_cluster.get_cluster_manager",
            return_value=mock_cluster_manager,
        ):
            result = await switch_cluster(
                cluster_id="dev",
                force=True,
            )

        assert result.success is True

    @pytest.mark.asyncio
    async def test_switch_cluster_security_error(
        self,
        mock_cluster_manager: MagicMock,
    ) -> None:
        """Test security error during switch."""
        mock_cluster_manager.switch_cluster.side_effect = ClusterSecurityError("Security violation")

        with patch(
            "mosk_mcp.tools.cluster.switch_cluster.get_cluster_manager",
            return_value=mock_cluster_manager,
        ):
            with pytest.raises(ClusterSecurityError):
                await switch_cluster(cluster_id="unknown")

    @pytest.mark.asyncio
    async def test_switch_cluster_not_found(
        self,
        mock_cluster_manager: MagicMock,
    ) -> None:
        """Test switching to non-existent cluster."""
        mock_cluster_manager.switch_cluster.side_effect = ValueError("Cluster 'unknown' not found")

        with patch(
            "mosk_mcp.tools.cluster.switch_cluster.get_cluster_manager",
            return_value=mock_cluster_manager,
        ):
            with pytest.raises(ValueError, match="not found"):
                await switch_cluster(cluster_id="unknown")


class TestLockCluster:
    """Tests for lock_cluster tool."""

    @pytest.mark.asyncio
    async def test_lock_cluster_default(
        self,
        mock_cluster_manager: MagicMock,
    ) -> None:
        """Test locking active cluster."""
        mock_cluster_manager.lock_cluster.return_value = ("dev", True)

        with patch(
            "mosk_mcp.tools.cluster.lock_cluster.get_cluster_manager",
            return_value=mock_cluster_manager,
        ):
            result = await lock_cluster()

        assert result.success is True
        assert result.cluster_id == "dev"
        assert result.is_locked is True
        assert "locked" in result.message

    @pytest.mark.asyncio
    async def test_lock_specific_cluster(
        self,
        mock_cluster_manager: MagicMock,
    ) -> None:
        """Test locking specific cluster."""
        mock_cluster_manager.lock_cluster.return_value = ("prod", True)

        with patch(
            "mosk_mcp.tools.cluster.lock_cluster.get_cluster_manager",
            return_value=mock_cluster_manager,
        ):
            result = await lock_cluster(cluster_id="prod", lock=True)

        assert result.cluster_id == "prod"
        assert result.is_locked is True

    @pytest.mark.asyncio
    async def test_unlock_cluster(
        self,
        mock_cluster_manager: MagicMock,
    ) -> None:
        """Test unlocking cluster."""
        mock_cluster_manager.lock_cluster.return_value = ("prod", False)

        with patch(
            "mosk_mcp.tools.cluster.lock_cluster.get_cluster_manager",
            return_value=mock_cluster_manager,
        ):
            result = await lock_cluster(cluster_id="prod", lock=False)

        assert result.is_locked is False
        assert "unlocked" in result.message


class TestClusterInfoModel:
    """Tests for ClusterInfo model."""

    def test_safety_indicator_production(
        self,
        production_cluster_info: ClusterInfo,
    ) -> None:
        """Test safety indicator for production cluster."""
        indicator = production_cluster_info.safety_indicator
        assert "[PROD]" in indicator
        assert "[LOCKED]" in indicator

    def test_safety_indicator_development(
        self,
        sample_cluster_info: ClusterInfo,
    ) -> None:
        """Test safety indicator for development cluster."""
        indicator = sample_cluster_info.safety_indicator
        assert "[ACTIVE]" in indicator
        assert "[PROD]" not in indicator

    def test_safety_indicator_no_ssl(self) -> None:
        """Test safety indicator when SSL is disabled."""
        cluster = ClusterInfo(
            id="test",
            name="Test",
            url="https://test.example.com",
            environment=ClusterEnvironment.DEVELOPMENT,
            ssl_verify=False,
            is_active=False,
            is_authenticated=False,
            is_locked=False,
            has_fingerprint=False,
        )
        indicator = cluster.safety_indicator
        assert "[NO-SSL]" in indicator

    def test_safety_indicator_empty(self) -> None:
        """Test empty safety indicator."""
        cluster = ClusterInfo(
            id="test",
            name="Test",
            url="https://test.example.com",
            environment=ClusterEnvironment.DEVELOPMENT,
            ssl_verify=True,
            is_active=False,
            is_authenticated=False,
            is_locked=False,
            has_fingerprint=False,
        )
        indicator = cluster.safety_indicator
        assert indicator == ""
