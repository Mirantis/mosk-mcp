"""Unit tests for apply_cluster_release_patch tool."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.core.exceptions import (
    ResourceNotFoundError,
    ToolExecutionError,
    ValidationError,
)
from mosk_mcp.tools.operations_visibility.apply_cluster_release_patch import (
    ApplyClusterReleasePatchInput,
    ApplyClusterReleasePatchOutput,
    _get_available_mosk_releases,
    _validate_target_release,
    apply_cluster_release_patch,
)


class TestApplyClusterReleasePatchInput:
    """Tests for ApplyClusterReleasePatchInput model."""

    def test_required_fields(self):
        """Test required fields."""
        with pytest.raises(Exception):  # Pydantic validation error
            ApplyClusterReleasePatchInput()

    def test_valid_input(self):
        """Test valid input with all required fields."""
        input_data = ApplyClusterReleasePatchInput(
            cluster_name="mos",
            namespace="lab",
            target_release="mosk-21-0-2-25-2-2",
            crq_number="CRQ123456789",
        )

        assert input_data.cluster_name == "mos"
        assert input_data.namespace == "lab"
        assert input_data.target_release == "mosk-21-0-2-25-2-2"
        assert input_data.crq_number == "CRQ123456789"
        assert input_data.dry_run is False  # default

    def test_dry_run_flag(self):
        """Test dry_run flag."""
        input_data = ApplyClusterReleasePatchInput(
            cluster_name="mos",
            namespace="lab",
            target_release="mosk-21-0-2-25-2-2",
            crq_number="CRQ123456789",
            dry_run=True,
        )

        assert input_data.dry_run is True

    def test_crq_length_validation_too_short(self):
        """Test CRQ length validation - too short."""
        with pytest.raises(ValueError):
            ApplyClusterReleasePatchInput(
                cluster_name="mos",
                namespace="lab",
                target_release="mosk-21-0-2-25-2-2",
                crq_number="CRQ12345",
            )

    def test_crq_length_validation_too_long(self):
        """Test CRQ length validation - too long."""
        with pytest.raises(ValueError):
            ApplyClusterReleasePatchInput(
                cluster_name="mos",
                namespace="lab",
                target_release="mosk-21-0-2-25-2-2",
                crq_number="CRQ1234567890",
            )


class TestApplyClusterReleasePatchOutput:
    """Tests for ApplyClusterReleasePatchOutput model."""

    def test_output_creation(self):
        """Test output model creation."""
        output = ApplyClusterReleasePatchOutput(
            success=True,
            cluster_name="mos",
            namespace="lab",
            message="Successfully applied patch",
            crq_number="CRQ123456789",
            dry_run=False,
        )

        assert output.success is True
        assert output.cluster_name == "mos"
        assert output.namespace == "lab"
        assert output.applied_at is None
        assert output.before_release is None
        assert output.after_release is None
        assert output.available_releases == []
        assert output.warnings == []
        assert output.error_message is None

    def test_output_with_all_fields(self):
        """Test output model with all fields populated."""
        output = ApplyClusterReleasePatchOutput(
            success=True,
            cluster_name="mos",
            namespace="lab",
            message="Successfully applied patch",
            applied_at="2025-01-01T00:00:00Z",
            crq_number="CRQ123456789",
            dry_run=False,
            before_release="mosk-17-4-0-25-1",
            after_release="mosk-21-0-2-25-2-2",
            available_releases=["mosk-17-4-0-25-1", "mosk-21-0-2-25-2-2"],
            warnings=["Platform upgrade initiated"],
            error_message=None,
        )

        assert output.before_release == "mosk-17-4-0-25-1"
        assert output.after_release == "mosk-21-0-2-25-2-2"
        assert len(output.available_releases) == 2
        assert len(output.warnings) == 1


class TestGetAvailableMoskReleases:
    """Tests for _get_available_mosk_releases helper."""

    @pytest.mark.asyncio
    async def test_returns_mosk_releases_only(self):
        """Test that only MOSK releases are returned."""
        mock_adapter = AsyncMock()
        mock_adapter.list_cluster_releases = AsyncMock(
            return_value=[
                {"metadata": {"name": "mosk-17-4-0-25-1"}},
                {"metadata": {"name": "mosk-21-0-2-25-2-2"}},
                {"metadata": {"name": "kaas-21-0-0"}},
                {"metadata": {"name": "mosk-24-1-0-25-3"}},
            ]
        )

        result = await _get_available_mosk_releases(mock_adapter)

        assert len(result) == 3
        assert "mosk-17-4-0-25-1" in result
        assert "mosk-21-0-2-25-2-2" in result
        assert "mosk-24-1-0-25-3" in result
        assert "kaas-21-0-0" not in result

    @pytest.mark.asyncio
    async def test_returns_sorted_releases(self):
        """Test that releases are sorted."""
        mock_adapter = AsyncMock()
        mock_adapter.list_cluster_releases = AsyncMock(
            return_value=[
                {"metadata": {"name": "mosk-24-1-0-25-3"}},
                {"metadata": {"name": "mosk-17-4-0-25-1"}},
                {"metadata": {"name": "mosk-21-0-2-25-2-2"}},
            ]
        )

        result = await _get_available_mosk_releases(mock_adapter)

        assert result == sorted(result)

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        """Test that empty list is returned on exception."""
        mock_adapter = AsyncMock()
        mock_adapter.list_cluster_releases = AsyncMock(side_effect=Exception("API error"))

        result = await _get_available_mosk_releases(mock_adapter)

        assert result == []

    @pytest.mark.asyncio
    async def test_handles_missing_metadata(self):
        """Test handling of releases with missing metadata."""
        mock_adapter = AsyncMock()
        mock_adapter.list_cluster_releases = AsyncMock(
            return_value=[
                {"metadata": {"name": "mosk-17-4-0-25-1"}},
                {"metadata": {}},  # Missing name
                {},  # Missing metadata
            ]
        )

        result = await _get_available_mosk_releases(mock_adapter)

        assert len(result) == 1
        assert result[0] == "mosk-17-4-0-25-1"


class TestValidateTargetRelease:
    """Tests for _validate_target_release helper."""

    @pytest.mark.asyncio
    async def test_invalid_format_non_mosk_prefix(self):
        """Test invalid release format (not starting with mosk-)."""
        mock_adapter = AsyncMock()

        is_valid, error = await _validate_target_release(mock_adapter, "kaas-21-0-0")

        assert is_valid is False
        assert "must start with 'mosk-'" in error

    @pytest.mark.asyncio
    async def test_valid_release_exists(self):
        """Test valid release that exists."""
        mock_adapter = AsyncMock()
        mock_adapter.get_cluster_release = AsyncMock(
            return_value={"metadata": {"name": "mosk-21-0-2-25-2-2"}}
        )

        is_valid, error = await _validate_target_release(mock_adapter, "mosk-21-0-2-25-2-2")

        assert is_valid is True
        assert error is None

    @pytest.mark.asyncio
    async def test_valid_format_but_not_found_returns_none(self):
        """Test valid format but release returns None."""
        mock_adapter = AsyncMock()
        mock_adapter.get_cluster_release = AsyncMock(return_value=None)
        mock_adapter.list_cluster_releases = AsyncMock(
            return_value=[{"metadata": {"name": "mosk-17-4-0-25-1"}}]
        )

        is_valid, error = await _validate_target_release(mock_adapter, "mosk-99-0-0-25-1")

        assert is_valid is False
        assert "not found" in error
        assert "Available MOSK releases" in error

    @pytest.mark.asyncio
    async def test_valid_format_but_resource_not_found_exception(self):
        """Test valid format but release raises ResourceNotFoundError."""
        mock_adapter = AsyncMock()
        mock_adapter.get_cluster_release = AsyncMock(
            side_effect=ResourceNotFoundError(
                message="Release not found",
                resource_type="ClusterRelease",
                resource_id="mosk-99-0-0-25-1",
            )
        )
        mock_adapter.list_cluster_releases = AsyncMock(
            return_value=[{"metadata": {"name": "mosk-17-4-0-25-1"}}]
        )

        is_valid, error = await _validate_target_release(mock_adapter, "mosk-99-0-0-25-1")

        assert is_valid is False
        assert "not found" in error

    @pytest.mark.asyncio
    async def test_general_exception_fails_closed(self):
        """Test that general exceptions fail closed (validation fails)."""
        mock_adapter = AsyncMock()
        mock_adapter.get_cluster_release = AsyncMock(side_effect=Exception("Network error"))

        is_valid, error = await _validate_target_release(mock_adapter, "mosk-21-0-2-25-2-2")

        assert is_valid is False
        assert "Failed to validate release" in error
        assert "Cannot proceed with upgrade" in error


class TestApplyClusterReleasePatchFunction:
    """Tests for apply_cluster_release_patch function."""

    @pytest.fixture
    def mock_mcc_adapter(self):
        """Create mock MCC adapter."""
        adapter = AsyncMock()
        return adapter

    @pytest.fixture
    def valid_input(self):
        """Create valid input."""
        return ApplyClusterReleasePatchInput(
            cluster_name="mos",
            namespace="lab",
            target_release="mosk-21-0-2-25-2-2",
            crq_number="CRQ123456789",
        )

    @pytest.fixture
    def mock_crq_validator(self):
        """Create mock CRQ validator."""
        validator = MagicMock()
        result = MagicMock()
        result.is_valid = True
        result.message = "CRQ is valid"
        validator.validate = MagicMock(return_value=result)
        return validator

    @pytest.fixture
    def mock_cluster(self):
        """Create mock cluster object."""
        return {
            "metadata": {"name": "mos", "namespace": "lab"},
            "spec": {
                "providerSpec": {
                    "value": {
                        "release": "mosk-17-4-0-25-1",
                    }
                }
            },
        }

    @pytest.mark.asyncio
    async def test_invalid_crq_rejected(self, mock_mcc_adapter, valid_input):
        """Test that invalid CRQ is rejected."""
        mock_validator = MagicMock()
        mock_result = MagicMock()
        mock_result.is_valid = False
        mock_result.message = "CRQ expired"
        mock_validator.validate = MagicMock(return_value=mock_result)

        with patch(
            "mosk_mcp.tools.operations_visibility.apply_cluster_release_patch.get_crq_validator",
            return_value=mock_validator,
        ):
            with pytest.raises(ValidationError) as exc_info:
                await apply_cluster_release_patch(mock_mcc_adapter, valid_input)

        assert "Invalid CRQ" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_invalid_target_release_rejected(self, mock_mcc_adapter, mock_crq_validator):
        """Test that invalid target release is rejected."""
        input_data = ApplyClusterReleasePatchInput(
            cluster_name="mos",
            namespace="lab",
            target_release="invalid-release",  # Doesn't start with mosk-
            crq_number="CRQ123456789",
        )

        mock_mcc_adapter.list_cluster_releases = AsyncMock(return_value=[])

        with patch(
            "mosk_mcp.tools.operations_visibility.apply_cluster_release_patch.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            with pytest.raises(ValidationError) as exc_info:
                await apply_cluster_release_patch(mock_mcc_adapter, input_data)

        assert "must start with 'mosk-'" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_release_not_found_rejected(self, mock_mcc_adapter, mock_crq_validator):
        """Test when target release doesn't exist."""
        input_data = ApplyClusterReleasePatchInput(
            cluster_name="mos",
            namespace="lab",
            target_release="mosk-99-0-0-25-1",
            crq_number="CRQ123456789",
        )

        mock_mcc_adapter.list_cluster_releases = AsyncMock(
            return_value=[{"metadata": {"name": "mosk-17-4-0-25-1"}}]
        )
        mock_mcc_adapter.get_cluster_release = AsyncMock(return_value=None)

        with patch(
            "mosk_mcp.tools.operations_visibility.apply_cluster_release_patch.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            with pytest.raises(ValidationError) as exc_info:
                await apply_cluster_release_patch(mock_mcc_adapter, input_data)

        assert "not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_cluster_not_found(
        self, mock_mcc_adapter, valid_input, mock_crq_validator, mock_cluster
    ):
        """Test when Cluster is not found."""
        mock_mcc_adapter.list_cluster_releases = AsyncMock(
            return_value=[{"metadata": {"name": "mosk-21-0-2-25-2-2"}}]
        )
        mock_mcc_adapter.get_cluster_release = AsyncMock(
            return_value={"metadata": {"name": "mosk-21-0-2-25-2-2"}}
        )
        mock_mcc_adapter.get_cluster = AsyncMock(return_value=None)

        with patch(
            "mosk_mcp.tools.operations_visibility.apply_cluster_release_patch.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            with pytest.raises(ResourceNotFoundError) as exc_info:
                await apply_cluster_release_patch(mock_mcc_adapter, valid_input)

        assert "not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_already_at_target_release(self, mock_mcc_adapter, mock_crq_validator):
        """Test when cluster is already at target release."""
        input_data = ApplyClusterReleasePatchInput(
            cluster_name="mos",
            namespace="lab",
            target_release="mosk-21-0-2-25-2-2",
            crq_number="CRQ123456789",
        )

        mock_mcc_adapter.list_cluster_releases = AsyncMock(
            return_value=[{"metadata": {"name": "mosk-21-0-2-25-2-2"}}]
        )
        mock_mcc_adapter.get_cluster_release = AsyncMock(
            return_value={"metadata": {"name": "mosk-21-0-2-25-2-2"}}
        )
        mock_mcc_adapter.get_cluster = AsyncMock(
            return_value={
                "metadata": {"name": "mos", "namespace": "lab"},
                "spec": {
                    "providerSpec": {
                        "value": {"release": "mosk-21-0-2-25-2-2"}  # Already at target
                    }
                },
            }
        )

        with patch(
            "mosk_mcp.tools.operations_visibility.apply_cluster_release_patch.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            result = await apply_cluster_release_patch(mock_mcc_adapter, input_data)

        assert result.success is True
        assert "already at release" in result.message
        assert result.before_release == "mosk-21-0-2-25-2-2"
        assert result.after_release == "mosk-21-0-2-25-2-2"
        assert result.applied_at is None  # No change made
        assert len(result.warnings) == 0

    @pytest.mark.asyncio
    async def test_dry_run_success(self, mock_mcc_adapter, mock_crq_validator, mock_cluster):
        """Test dry-run returns success without applying."""
        input_data = ApplyClusterReleasePatchInput(
            cluster_name="mos",
            namespace="lab",
            target_release="mosk-21-0-2-25-2-2",
            crq_number="CRQ123456789",
            dry_run=True,
        )

        mock_mcc_adapter.list_cluster_releases = AsyncMock(
            return_value=[{"metadata": {"name": "mosk-21-0-2-25-2-2"}}]
        )
        mock_mcc_adapter.get_cluster_release = AsyncMock(
            return_value={"metadata": {"name": "mosk-21-0-2-25-2-2"}}
        )
        mock_mcc_adapter.get_cluster = AsyncMock(return_value=mock_cluster)

        with patch(
            "mosk_mcp.tools.operations_visibility.apply_cluster_release_patch.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            result = await apply_cluster_release_patch(mock_mcc_adapter, input_data)

        assert result.success is True
        assert result.dry_run is True
        assert result.applied_at is None
        assert result.before_release == "mosk-17-4-0-25-1"
        assert result.after_release is None  # Not applied yet
        assert "Dry-run successful" in result.message
        assert len(result.warnings) > 0  # Should have upgrade warning

        # Verify patch was NOT applied
        mock_mcc_adapter.patch_cluster_release.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_patch_success(
        self, mock_mcc_adapter, valid_input, mock_crq_validator, mock_cluster
    ):
        """Test successful patch application."""
        mock_mcc_adapter.list_cluster_releases = AsyncMock(
            return_value=[{"metadata": {"name": "mosk-21-0-2-25-2-2"}}]
        )
        mock_mcc_adapter.get_cluster_release = AsyncMock(
            return_value={"metadata": {"name": "mosk-21-0-2-25-2-2"}}
        )
        mock_mcc_adapter.get_cluster = AsyncMock(return_value=mock_cluster)
        mock_mcc_adapter.patch_cluster_release = AsyncMock(
            return_value={
                "metadata": {"name": "mos", "namespace": "lab"},
                "spec": {"providerSpec": {"value": {"release": "mosk-21-0-2-25-2-2"}}},
            }
        )

        with patch(
            "mosk_mcp.tools.operations_visibility.apply_cluster_release_patch.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            result = await apply_cluster_release_patch(mock_mcc_adapter, valid_input)

        assert result.success is True
        assert result.dry_run is False
        assert result.applied_at is not None
        assert result.before_release == "mosk-17-4-0-25-1"
        assert result.after_release == "mosk-21-0-2-25-2-2"
        assert "Successfully" in result.message
        assert "upgrade initiated" in result.message

        # Verify adapter was called correctly
        mock_mcc_adapter.patch_cluster_release.assert_called_once_with(
            name="mos",
            target_release="mosk-21-0-2-25-2-2",
            namespace="lab",
        )

    @pytest.mark.asyncio
    async def test_result_includes_warnings(
        self, mock_mcc_adapter, valid_input, mock_crq_validator, mock_cluster
    ):
        """Test that result includes upgrade warnings."""
        mock_mcc_adapter.list_cluster_releases = AsyncMock(
            return_value=[{"metadata": {"name": "mosk-21-0-2-25-2-2"}}]
        )
        mock_mcc_adapter.get_cluster_release = AsyncMock(
            return_value={"metadata": {"name": "mosk-21-0-2-25-2-2"}}
        )
        mock_mcc_adapter.get_cluster = AsyncMock(return_value=mock_cluster)
        mock_mcc_adapter.patch_cluster_release = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"providerSpec": {"value": {"release": "mosk-21-0-2-25-2-2"}}},
            }
        )

        with patch(
            "mosk_mcp.tools.operations_visibility.apply_cluster_release_patch.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            result = await apply_cluster_release_patch(mock_mcc_adapter, valid_input)

        assert len(result.warnings) > 0
        # Should have critical upgrade warning
        assert any("CRITICAL" in w for w in result.warnings)
        assert any("platform upgrade" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_result_includes_available_releases(
        self, mock_mcc_adapter, valid_input, mock_crq_validator, mock_cluster
    ):
        """Test that result includes available releases."""
        mock_mcc_adapter.list_cluster_releases = AsyncMock(
            return_value=[
                {"metadata": {"name": "mosk-17-4-0-25-1"}},
                {"metadata": {"name": "mosk-21-0-2-25-2-2"}},
            ]
        )
        mock_mcc_adapter.get_cluster_release = AsyncMock(
            return_value={"metadata": {"name": "mosk-21-0-2-25-2-2"}}
        )
        mock_mcc_adapter.get_cluster = AsyncMock(return_value=mock_cluster)
        mock_mcc_adapter.patch_cluster_release = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"providerSpec": {"value": {"release": "mosk-21-0-2-25-2-2"}}},
            }
        )

        with patch(
            "mosk_mcp.tools.operations_visibility.apply_cluster_release_patch.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            result = await apply_cluster_release_patch(mock_mcc_adapter, valid_input)

        assert len(result.available_releases) == 2
        assert "mosk-17-4-0-25-1" in result.available_releases
        assert "mosk-21-0-2-25-2-2" in result.available_releases

    @pytest.mark.asyncio
    async def test_adapter_error_handled(
        self, mock_mcc_adapter, valid_input, mock_crq_validator, mock_cluster
    ):
        """Test that adapter errors are handled."""
        mock_mcc_adapter.list_cluster_releases = AsyncMock(
            return_value=[{"metadata": {"name": "mosk-21-0-2-25-2-2"}}]
        )
        mock_mcc_adapter.get_cluster_release = AsyncMock(
            return_value={"metadata": {"name": "mosk-21-0-2-25-2-2"}}
        )
        mock_mcc_adapter.get_cluster = AsyncMock(return_value=mock_cluster)
        mock_mcc_adapter.patch_cluster_release = AsyncMock(
            side_effect=Exception("API server unavailable")
        )

        with patch(
            "mosk_mcp.tools.operations_visibility.apply_cluster_release_patch.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            with pytest.raises(ToolExecutionError) as exc_info:
                await apply_cluster_release_patch(mock_mcc_adapter, valid_input)

        assert "Failed to apply cluster release patch" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_timestamp_format(
        self, mock_mcc_adapter, valid_input, mock_crq_validator, mock_cluster
    ):
        """Test that applied_at timestamp is valid ISO format."""
        mock_mcc_adapter.list_cluster_releases = AsyncMock(
            return_value=[{"metadata": {"name": "mosk-21-0-2-25-2-2"}}]
        )
        mock_mcc_adapter.get_cluster_release = AsyncMock(
            return_value={"metadata": {"name": "mosk-21-0-2-25-2-2"}}
        )
        mock_mcc_adapter.get_cluster = AsyncMock(return_value=mock_cluster)
        mock_mcc_adapter.patch_cluster_release = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"providerSpec": {"value": {"release": "mosk-21-0-2-25-2-2"}}},
            }
        )

        with patch(
            "mosk_mcp.tools.operations_visibility.apply_cluster_release_patch.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            result = await apply_cluster_release_patch(mock_mcc_adapter, valid_input)

        assert result.applied_at is not None
        # Verify valid ISO format
        datetime.fromisoformat(result.applied_at.replace("Z", "+00:00"))

    @pytest.mark.asyncio
    async def test_handles_missing_provider_spec(
        self, mock_mcc_adapter, valid_input, mock_crq_validator
    ):
        """Test handling of cluster with missing providerSpec."""
        # Cluster with missing providerSpec structure
        mock_cluster = {
            "metadata": {"name": "mos", "namespace": "lab"},
            "spec": {},  # Missing providerSpec
        }

        mock_mcc_adapter.list_cluster_releases = AsyncMock(
            return_value=[{"metadata": {"name": "mosk-21-0-2-25-2-2"}}]
        )
        mock_mcc_adapter.get_cluster_release = AsyncMock(
            return_value={"metadata": {"name": "mosk-21-0-2-25-2-2"}}
        )
        mock_mcc_adapter.get_cluster = AsyncMock(return_value=mock_cluster)
        mock_mcc_adapter.patch_cluster_release = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"providerSpec": {"value": {"release": "mosk-21-0-2-25-2-2"}}},
            }
        )

        with patch(
            "mosk_mcp.tools.operations_visibility.apply_cluster_release_patch.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            result = await apply_cluster_release_patch(mock_mcc_adapter, valid_input)

        assert result.success is True
        assert result.before_release == "unknown"  # Falls back to unknown
        assert result.after_release == "mosk-21-0-2-25-2-2"
