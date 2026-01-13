"""Unit tests for list_available_releases tool."""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.tools.operations_visibility.list_available_releases import (
    ListAvailableReleasesInput,
    _compare_versions,
    _extract_major_version,
    _parse_component_versions,
    list_available_releases,
)


class TestParseComponentVersions:
    """Tests for _parse_component_versions helper."""

    def test_parse_full_description(self):
        """Test parsing a full component description."""
        description = """kubernetes: v1.30.13
containerd: 1.7.27m3
mcr: 25.0.12m1
coredns: 1.11.5
etcd: 3.5.18
calico: 3.28.5
openstack_operator: 0.15.27
tungstenfabric_operator: 0.8.2"""

        result = _parse_component_versions(description)

        assert result.kubernetes == "v1.30.13"
        assert result.containerd == "1.7.27m3"
        assert result.mcr == "25.0.12m1"
        assert result.coredns == "1.11.5"
        assert result.etcd == "3.5.18"
        assert result.calico == "3.28.5"
        assert result.openstack_operator == "0.15.27"
        assert result.tungstenfabric_operator == "0.8.2"

    def test_parse_partial_description(self):
        """Test parsing description with only some components."""
        description = """kubernetes: v1.29.10
containerd: 1.7.20"""

        result = _parse_component_versions(description)

        assert result.kubernetes == "v1.29.10"
        assert result.containerd == "1.7.20"
        # Defaults for missing components
        assert result.mcr == "unknown"
        assert result.etcd == "unknown"

    def test_parse_empty_description(self):
        """Test parsing empty description."""
        result = _parse_component_versions("")

        assert result.kubernetes == "unknown"
        assert result.containerd == "unknown"

    def test_parse_description_with_extra_whitespace(self):
        """Test parsing description with extra whitespace."""
        description = """  kubernetes:   v1.30.0
    containerd:   1.7.25   """

        result = _parse_component_versions(description)

        assert result.kubernetes == "v1.30.0"
        assert result.containerd == "1.7.25"

    def test_parse_description_with_hyphens(self):
        """Test parsing component names with hyphens."""
        description = """openstack-operator: 0.16.0
tungstenfabric-operator: 0.9.0"""

        result = _parse_component_versions(description)

        assert result.openstack_operator == "0.16.0"
        assert result.tungstenfabric_operator == "0.9.0"

    def test_parse_description_ignores_invalid_lines(self):
        """Test that lines without colons are ignored."""
        description = """kubernetes: v1.30.0
This is a comment line without colon
containerd: 1.7.25"""

        result = _parse_component_versions(description)

        assert result.kubernetes == "v1.30.0"
        assert result.containerd == "1.7.25"


class TestExtractMajorVersion:
    """Tests for _extract_major_version helper."""

    def test_extract_standard_version(self):
        """Test extracting major version from standard string."""
        result = _extract_major_version("21.0.0+25.2")
        assert result == "21.0"

    def test_extract_version_without_build_metadata(self):
        """Test extracting version without build metadata."""
        result = _extract_major_version("21.0.1")
        assert result == "21.0"

    def test_extract_single_digit_version(self):
        """Test extracting from single digit version."""
        result = _extract_major_version("21")
        assert result == "21"

    def test_extract_empty_string(self):
        """Test extracting from empty string."""
        result = _extract_major_version("")
        assert result == ""

    def test_extract_version_with_complex_build(self):
        """Test extracting version with complex build metadata."""
        result = _extract_major_version("17.4.6+24.1.2-rc1")
        assert result == "17.4"


class TestCompareVersions:
    """Tests for _compare_versions helper."""

    def test_compare_equal_versions(self):
        """Test comparing equal versions."""
        result = _compare_versions("mosk-21-0-0-25-2", "mosk-21-0-0-25-2")
        assert result == 0

    def test_compare_less_than(self):
        """Test comparing when first is less than second."""
        result = _compare_versions("mosk-21-0-0-25-2", "mosk-21-0-1-25-2")
        assert result == -1

    def test_compare_greater_than(self):
        """Test comparing when first is greater than second."""
        result = _compare_versions("mosk-21-0-1-25-2", "mosk-21-0-0-25-2")
        assert result == 1

    def test_compare_different_major_versions(self):
        """Test comparing different major versions."""
        result = _compare_versions("mosk-21-0-0-25-2", "mosk-17-4-6-24-1")
        assert result == 1  # 21 > 17

    def test_compare_without_mosk_prefix(self):
        """Test comparing versions without mosk- prefix."""
        result = _compare_versions("21-0-0-25-2", "21-0-1-25-2")
        assert result == -1

    def test_compare_with_version_suffix(self):
        """Test comparing versions with suffixes like m1."""
        result = _compare_versions("mosk-21-0-0-25-2", "mosk-21-0-0-25-2m1")
        # 25-2 vs 25-2m1 - the m1 adds another number
        assert result == -1  # mosk-21-0-0-25-2 < mosk-21-0-0-25-2m1


class TestListAvailableReleasesInput:
    """Tests for ListAvailableReleasesInput model."""

    def test_default_values(self):
        """Test default values."""
        input_data = ListAvailableReleasesInput()

        assert input_data.cluster_name is None
        assert input_data.cluster_namespace == "default"
        assert input_data.include_all_versions is True
        assert input_data.include_component_details is True

    def test_custom_values(self):
        """Test custom values."""
        input_data = ListAvailableReleasesInput(
            cluster_name="mos",
            cluster_namespace="lab",
            include_all_versions=False,
            include_component_details=False,
        )

        assert input_data.cluster_name == "mos"
        assert input_data.cluster_namespace == "lab"
        assert input_data.include_all_versions is False
        assert input_data.include_component_details is False


class TestListAvailableReleasesFunction:
    """Tests for list_available_releases function."""

    @pytest.fixture
    def mock_mcc_adapter(self):
        """Create mock MCC adapter."""
        adapter = AsyncMock()
        adapter.connect = AsyncMock()
        return adapter

    @pytest.fixture
    def mock_releases(self):
        """Create mock ClusterRelease resources."""
        return [
            {
                "metadata": {"name": "mosk-21-0-2-25-2-2"},
                "spec": {
                    "version": "21.0.2+25.2.2",
                    "description": "kubernetes: v1.30.13\ncontainerd: 1.7.27m3",
                    "allowedOpenstackReleases": [
                        {"id": "caracal", "description": "OpenStack Caracal"},
                        {"id": "epoxy", "description": "OpenStack Epoxy"},
                    ],
                },
            },
            {
                "metadata": {"name": "mosk-21-0-1-25-2-1"},
                "spec": {
                    "version": "21.0.1+25.2.1",
                    "description": "kubernetes: v1.30.10",
                    "allowedOpenstackReleases": [
                        {"id": "caracal", "description": "OpenStack Caracal"},
                    ],
                },
            },
            {
                "metadata": {"name": "mosk-21-0-0-25-2"},
                "spec": {
                    "version": "21.0.0+25.2",
                    "description": "kubernetes: v1.30.8",
                    "allowedOpenstackReleases": [
                        {"id": "caracal", "description": "OpenStack Caracal"},
                    ],
                },
            },
            # Non-MOSK release should be filtered out
            {
                "metadata": {"name": "kaas-2.28.0"},
                "spec": {"version": "2.28.0"},
            },
        ]

    @pytest.fixture
    def mock_cluster(self):
        """Create mock Cluster resource."""
        return {
            "metadata": {"name": "mos", "namespace": "lab"},
            "spec": {
                "providerSpec": {
                    "value": {"release": "mosk-21-0-0-25-2"},
                }
            },
            "status": {
                "providerStatus": {"release": "mosk-21-0-0-25-2"},
            },
        }

    @pytest.mark.asyncio
    async def test_list_releases_without_cluster(self, mock_mcc_adapter, mock_releases):
        """Test listing releases without specifying a cluster."""
        mock_mcc_adapter.list_cluster_releases = AsyncMock(return_value=mock_releases)

        result = await list_available_releases(
            mock_mcc_adapter,
            ListAvailableReleasesInput(),
        )

        assert result.total_count == 3  # Only MOSK releases
        assert result.current_release is None
        assert result.newest_release == "mosk-21-0-2-25-2-2"
        assert len(result.releases) == 3
        # Verify sorted by name (newest first)
        assert result.releases[0].name == "mosk-21-0-2-25-2-2"
        assert result.releases[1].name == "mosk-21-0-1-25-2-1"
        assert result.releases[2].name == "mosk-21-0-0-25-2"

    @pytest.mark.asyncio
    async def test_list_releases_with_cluster(self, mock_mcc_adapter, mock_releases, mock_cluster):
        """Test listing releases with current cluster specified."""
        mock_mcc_adapter.get_cluster = AsyncMock(return_value=mock_cluster)
        mock_mcc_adapter.list_cluster_releases = AsyncMock(return_value=mock_releases)
        mock_mcc_adapter.list_custom_resources = AsyncMock(return_value=[])

        result = await list_available_releases(
            mock_mcc_adapter,
            ListAvailableReleasesInput(cluster_name="mos", cluster_namespace="lab"),
        )

        assert result.current_release == "mosk-21-0-0-25-2"
        assert result.current_version == "21.0.0+25.2"
        # Current release should be marked
        current = next(r for r in result.releases if r.is_current)
        assert current.name == "mosk-21-0-0-25-2"
        # Newer releases should have upgrade_available=True
        assert result.releases[0].upgrade_available is True  # mosk-21-0-2-25-2-2
        assert result.releases[1].upgrade_available is True  # mosk-21-0-1-25-2-1

    @pytest.mark.asyncio
    async def test_list_releases_filter_upgrades_only(
        self, mock_mcc_adapter, mock_releases, mock_cluster
    ):
        """Test filtering to only show available upgrades."""
        mock_mcc_adapter.get_cluster = AsyncMock(return_value=mock_cluster)
        mock_mcc_adapter.list_cluster_releases = AsyncMock(return_value=mock_releases)
        mock_mcc_adapter.list_custom_resources = AsyncMock(return_value=[])

        result = await list_available_releases(
            mock_mcc_adapter,
            ListAvailableReleasesInput(
                cluster_name="mos",
                cluster_namespace="lab",
                include_all_versions=False,
            ),
        )

        # Should include current + upgrade available releases
        assert all(r.upgrade_available or r.is_current for r in result.releases)

    @pytest.mark.asyncio
    async def test_list_releases_without_component_details(self, mock_mcc_adapter, mock_releases):
        """Test listing releases without component details."""
        mock_mcc_adapter.list_cluster_releases = AsyncMock(return_value=mock_releases)

        result = await list_available_releases(
            mock_mcc_adapter,
            ListAvailableReleasesInput(include_component_details=False),
        )

        # Components should have default values
        for release in result.releases:
            assert release.components.kubernetes == "unknown"

    @pytest.mark.asyncio
    async def test_list_releases_with_component_details(self, mock_mcc_adapter, mock_releases):
        """Test listing releases with component details parsed."""
        mock_mcc_adapter.list_cluster_releases = AsyncMock(return_value=mock_releases)

        result = await list_available_releases(
            mock_mcc_adapter,
            ListAvailableReleasesInput(include_component_details=True),
        )

        # First release should have parsed components
        newest = result.releases[0]
        assert newest.components.kubernetes == "v1.30.13"
        assert newest.components.containerd == "1.7.27m3"

    @pytest.mark.asyncio
    async def test_list_releases_openstack_releases_parsed(self, mock_mcc_adapter, mock_releases):
        """Test that OpenStack releases are parsed correctly."""
        mock_mcc_adapter.list_cluster_releases = AsyncMock(return_value=mock_releases)

        result = await list_available_releases(
            mock_mcc_adapter,
            ListAvailableReleasesInput(),
        )

        newest = result.releases[0]
        assert len(newest.openstack_releases) == 2
        assert newest.openstack_releases[0].id == "caracal"
        assert newest.openstack_releases[1].id == "epoxy"

    @pytest.mark.asyncio
    async def test_list_releases_with_update_plans(
        self, mock_mcc_adapter, mock_releases, mock_cluster
    ):
        """Test listing releases with ClusterUpdatePlan resources."""
        mock_mcc_adapter.get_cluster = AsyncMock(return_value=mock_cluster)
        mock_mcc_adapter.list_cluster_releases = AsyncMock(return_value=mock_releases)
        mock_mcc_adapter.list_custom_resources = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "mosk-21-0-1"},
                    "spec": {"source": "mosk-21-0-0-25-2"},
                }
            ]
        )

        result = await list_available_releases(
            mock_mcc_adapter,
            ListAvailableReleasesInput(cluster_name="mos", cluster_namespace="lab"),
        )

        # Should have upgrade paths
        assert len(result.upgrade_paths) > 0
        assert result.upgrade_paths[0].from_release == "mosk-21-0-0-25-2"
        assert result.upgrade_paths[0].update_plan_exists is True

    @pytest.mark.asyncio
    async def test_list_releases_recommendations_when_behind(
        self, mock_mcc_adapter, mock_releases, mock_cluster
    ):
        """Test recommendations when cluster is behind."""
        mock_mcc_adapter.get_cluster = AsyncMock(return_value=mock_cluster)
        mock_mcc_adapter.list_cluster_releases = AsyncMock(return_value=mock_releases)
        mock_mcc_adapter.list_custom_resources = AsyncMock(return_value=[])

        result = await list_available_releases(
            mock_mcc_adapter,
            ListAvailableReleasesInput(cluster_name="mos", cluster_namespace="lab"),
        )

        # Should have recommendations about being behind
        assert any("behind" in r for r in result.recommendations)
        assert any("recommended upgrade" in r.lower() for r in result.recommendations)

    @pytest.mark.asyncio
    async def test_list_releases_recommendations_when_current(self, mock_mcc_adapter, mock_cluster):
        """Test recommendations when cluster is on latest."""
        # Set cluster to newest release
        mock_cluster["status"]["providerStatus"]["release"] = "mosk-21-0-2-25-2-2"
        mock_mcc_adapter.get_cluster = AsyncMock(return_value=mock_cluster)
        mock_mcc_adapter.list_cluster_releases = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "mosk-21-0-2-25-2-2"},
                    "spec": {"version": "21.0.2+25.2.2"},
                }
            ]
        )
        mock_mcc_adapter.list_custom_resources = AsyncMock(return_value=[])

        result = await list_available_releases(
            mock_mcc_adapter,
            ListAvailableReleasesInput(cluster_name="mos", cluster_namespace="lab"),
        )

        assert any("latest" in r.lower() for r in result.recommendations)

    @pytest.mark.asyncio
    async def test_list_releases_empty_result(self, mock_mcc_adapter):
        """Test when no releases are found."""
        mock_mcc_adapter.list_cluster_releases = AsyncMock(return_value=[])

        result = await list_available_releases(
            mock_mcc_adapter,
            ListAvailableReleasesInput(),
        )

        assert result.total_count == 0
        assert result.releases == []
        assert result.newest_release is None

    @pytest.mark.asyncio
    async def test_list_releases_api_error(self, mock_mcc_adapter):
        """Test API error handling."""
        mock_mcc_adapter.list_cluster_releases = AsyncMock(
            side_effect=Exception("Connection failed")
        )

        with pytest.raises(ToolExecutionError) as exc_info:
            await list_available_releases(
                mock_mcc_adapter,
                ListAvailableReleasesInput(),
            )

        assert "Failed to list available releases" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_list_releases_update_plans_error_handled(
        self, mock_mcc_adapter, mock_releases, mock_cluster
    ):
        """Test that update plan retrieval errors are handled gracefully."""
        mock_mcc_adapter.get_cluster = AsyncMock(return_value=mock_cluster)
        mock_mcc_adapter.list_cluster_releases = AsyncMock(return_value=mock_releases)
        mock_mcc_adapter.list_custom_resources = AsyncMock(
            side_effect=Exception("Update plans not found")
        )

        # Should not raise, just skip update plans
        result = await list_available_releases(
            mock_mcc_adapter,
            ListAvailableReleasesInput(cluster_name="mos", cluster_namespace="lab"),
        )

        # Should still return releases
        assert result.total_count == 3
        # Upgrade paths should be empty due to error
        assert result.upgrade_paths == []

    @pytest.mark.asyncio
    async def test_list_releases_cluster_not_found(self, mock_mcc_adapter, mock_releases):
        """Test when specified cluster is not found."""
        mock_mcc_adapter.get_cluster = AsyncMock(return_value=None)
        mock_mcc_adapter.list_cluster_releases = AsyncMock(return_value=mock_releases)

        result = await list_available_releases(
            mock_mcc_adapter,
            ListAvailableReleasesInput(cluster_name="nonexistent"),
        )

        # Should still list releases, just no current release info
        assert result.total_count == 3
        assert result.current_release is None

    @pytest.mark.asyncio
    async def test_timestamp_set(self, mock_mcc_adapter):
        """Test timestamp is set in result."""
        mock_mcc_adapter.list_cluster_releases = AsyncMock(return_value=[])

        result = await list_available_releases(
            mock_mcc_adapter,
            ListAvailableReleasesInput(),
        )

        assert result.timestamp is not None
        # Verify valid ISO format
        datetime.fromisoformat(result.timestamp.replace("Z", "+00:00"))

    @pytest.mark.asyncio
    async def test_release_major_version_extracted(self, mock_mcc_adapter):
        """Test that major version is extracted correctly."""
        mock_mcc_adapter.list_cluster_releases = AsyncMock(
            return_value=[
                {
                    "metadata": {"name": "mosk-21-0-2-25-2-2"},
                    "spec": {"version": "21.0.2+25.2.2"},
                }
            ]
        )

        result = await list_available_releases(
            mock_mcc_adapter,
            ListAvailableReleasesInput(),
        )

        assert result.releases[0].major_version == "21.0"
