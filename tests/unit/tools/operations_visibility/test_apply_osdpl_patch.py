"""Unit tests for apply_osdpl_patch tool."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.core.exceptions import (
    ResourceNotFoundError,
    ToolExecutionError,
    ValidationError,
)
from mosk_mcp.tools.operations_visibility.apply_osdpl_patch import (
    ALLOWED_PATH,
    VALID_OPENSTACK_VERSIONS,
    ApplyOSDPLPatchInput,
    _validate_patch_safety,
    apply_osdpl_patch,
)


class TestValidatePatchSafety:
    """Tests for _validate_patch_safety helper."""

    def test_valid_replace_openstack_version(self):
        """Test valid replace operation on openstack_version."""
        patch = [{"op": "replace", "path": "/spec/openstack_version", "value": "caracal"}]

        is_valid, errors, warnings = _validate_patch_safety(patch)

        assert is_valid is True
        assert len(errors) == 0
        assert len(warnings) > 0  # Should have upgrade warning

    def test_invalid_operation_add(self):
        """Test that 'add' operation is not allowed."""
        patch = [{"op": "add", "path": "/spec/openstack_version", "value": "caracal"}]

        is_valid, errors, _warnings = _validate_patch_safety(patch)

        assert is_valid is False
        assert any("'add' is not allowed" in e for e in errors)

    def test_invalid_operation_remove(self):
        """Test that 'remove' operation is not allowed."""
        patch = [{"op": "remove", "path": "/spec/openstack_version"}]

        is_valid, errors, _warnings = _validate_patch_safety(patch)

        assert is_valid is False
        assert any("not allowed" in e for e in errors)

    def test_invalid_path(self):
        """Test that only /spec/openstack_version path is allowed."""
        patch = [{"op": "replace", "path": "/spec/services/nova/replicas", "value": 3}]

        is_valid, errors, _warnings = _validate_patch_safety(patch)

        assert is_valid is False
        assert any("not allowed" in e for e in errors)
        assert any(ALLOWED_PATH in e for e in errors)

    def test_multiple_operations_not_allowed(self):
        """Test that multiple operations are not allowed."""
        patch = [
            {"op": "replace", "path": "/spec/openstack_version", "value": "caracal"},
            {"op": "replace", "path": "/spec/openstack_version", "value": "epoxy"},
        ]

        is_valid, errors, _warnings = _validate_patch_safety(patch)

        assert is_valid is False
        assert any("Only one patch operation" in e for e in errors)

    def test_missing_value(self):
        """Test that value is required for replace."""
        patch = [{"op": "replace", "path": "/spec/openstack_version"}]

        is_valid, errors, _warnings = _validate_patch_safety(patch)

        assert is_valid is False
        assert any("requires a 'value' field" in e for e in errors)

    def test_empty_value(self):
        """Test that value cannot be empty."""
        patch = [{"op": "replace", "path": "/spec/openstack_version", "value": ""}]

        is_valid, errors, _warnings = _validate_patch_safety(patch)

        assert is_valid is False
        assert any("non-empty string" in e for e in errors)

    def test_invalid_openstack_version(self):
        """Test that OpenStack version must be valid."""
        patch = [{"op": "replace", "path": "/spec/openstack_version", "value": "invalid-version"}]

        is_valid, errors, _warnings = _validate_patch_safety(patch)

        assert is_valid is False
        assert any("Invalid OpenStack version" in e for e in errors)

    def test_valid_versions(self):
        """Test all valid OpenStack versions are accepted."""
        for version in VALID_OPENSTACK_VERSIONS:
            patch = [{"op": "replace", "path": "/spec/openstack_version", "value": version}]

            is_valid, _errors, _warnings = _validate_patch_safety(patch)

            assert is_valid is True, f"Version '{version}' should be valid"

    def test_warning_includes_upgrade_notice(self):
        """Test that valid patch includes upgrade warning."""
        patch = [{"op": "replace", "path": "/spec/openstack_version", "value": "caracal"}]

        is_valid, _errors, warnings = _validate_patch_safety(patch)

        assert is_valid is True
        assert any("cluster upgrade" in w.lower() for w in warnings)


class TestApplyOSDPLPatchInput:
    """Tests for ApplyOSDPLPatchInput model."""

    def test_required_fields(self):
        """Test required fields."""
        with pytest.raises(Exception):  # Pydantic validation error
            ApplyOSDPLPatchInput()

    def test_valid_input(self):
        """Test valid input with all required fields."""
        input_data = ApplyOSDPLPatchInput(
            osdpl_name="mos",
            patch=[{"op": "replace", "path": "/spec/openstack_version", "value": "caracal"}],
            crq_number="CRQ123456789",
        )

        assert input_data.osdpl_name == "mos"
        assert input_data.namespace == "openstack"  # default
        assert input_data.dry_run is False  # default
        assert len(input_data.patch) == 1

    def test_custom_namespace(self):
        """Test custom namespace."""
        input_data = ApplyOSDPLPatchInput(
            osdpl_name="mos",
            namespace="custom-ns",
            patch=[{"op": "replace", "path": "/spec/openstack_version", "value": "caracal"}],
            crq_number="CRQ123456789",
        )

        assert input_data.namespace == "custom-ns"

    def test_dry_run_flag(self):
        """Test dry_run flag."""
        input_data = ApplyOSDPLPatchInput(
            osdpl_name="mos",
            patch=[{"op": "replace", "path": "/spec/openstack_version", "value": "caracal"}],
            crq_number="CRQ123456789",
            dry_run=True,
        )

        assert input_data.dry_run is True

    def test_crq_length_validation(self):
        """Test CRQ length validation."""
        # Too short
        with pytest.raises(ValueError):
            ApplyOSDPLPatchInput(
                osdpl_name="mos",
                patch=[{"op": "replace", "path": "/spec/openstack_version", "value": "caracal"}],
                crq_number="CRQ12345",
            )

        # Too long
        with pytest.raises(ValueError):
            ApplyOSDPLPatchInput(
                osdpl_name="mos",
                patch=[{"op": "replace", "path": "/spec/openstack_version", "value": "caracal"}],
                crq_number="CRQ1234567890",
            )

    def test_patch_max_length(self):
        """Test patch only allows one operation."""
        with pytest.raises(ValueError):
            ApplyOSDPLPatchInput(
                osdpl_name="mos",
                patch=[
                    {"op": "replace", "path": "/spec/openstack_version", "value": "caracal"},
                    {"op": "replace", "path": "/spec/openstack_version", "value": "epoxy"},
                ],
                crq_number="CRQ123456789",
            )


class TestApplyOSDPLPatchFunction:
    """Tests for apply_osdpl_patch function."""

    @pytest.fixture
    def mock_k8s_adapter(self):
        """Create mock Kubernetes adapter."""
        adapter = AsyncMock()
        return adapter

    @pytest.fixture
    def valid_input(self):
        """Create valid input."""
        return ApplyOSDPLPatchInput(
            osdpl_name="mos",
            patch=[{"op": "replace", "path": "/spec/openstack_version", "value": "caracal"}],
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

    @pytest.mark.asyncio
    async def test_invalid_patch_rejected(self, mock_k8s_adapter):
        """Test that invalid patch is rejected immediately."""
        input_data = ApplyOSDPLPatchInput(
            osdpl_name="mos",
            patch=[{"op": "add", "path": "/spec/services", "value": {}}],
            crq_number="CRQ123456789",
        )

        with pytest.raises(ValidationError) as exc_info:
            await apply_osdpl_patch(mock_k8s_adapter, input_data)

        assert "not allowed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_invalid_crq_rejected(self, mock_k8s_adapter, valid_input):
        """Test that invalid CRQ is rejected."""
        mock_validator = MagicMock()
        mock_result = MagicMock()
        mock_result.is_valid = False
        mock_result.message = "CRQ expired"
        mock_validator.validate = MagicMock(return_value=mock_result)

        with patch(
            "mosk_mcp.tools.operations_visibility.apply_osdpl_patch.get_crq_validator",
            return_value=mock_validator,
        ):
            with pytest.raises(ValidationError) as exc_info:
                await apply_osdpl_patch(mock_k8s_adapter, valid_input)

        assert "Invalid CRQ" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_osdpl_not_found(self, mock_k8s_adapter, valid_input, mock_crq_validator):
        """Test when OSDPL is not found."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(return_value=None)

        with patch(
            "mosk_mcp.tools.operations_visibility.apply_osdpl_patch.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            with pytest.raises(ResourceNotFoundError) as exc_info:
                await apply_osdpl_patch(mock_k8s_adapter, valid_input)

        assert "not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_dry_run_success(self, mock_k8s_adapter, mock_crq_validator):
        """Test dry-run returns success without applying."""
        input_data = ApplyOSDPLPatchInput(
            osdpl_name="mos",
            patch=[{"op": "replace", "path": "/spec/openstack_version", "value": "caracal"}],
            crq_number="CRQ123456789",
            dry_run=True,
        )

        mock_k8s_adapter.get_openstack_deployment = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"openstack_version": "antelope"},
            }
        )

        with patch(
            "mosk_mcp.tools.operations_visibility.apply_osdpl_patch.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            result = await apply_osdpl_patch(mock_k8s_adapter, input_data)

        assert result.success is True
        assert result.dry_run is True
        assert result.applied_at is None  # Not applied
        assert result.before_version == "antelope"
        assert "Dry-run successful" in result.message

        # Verify patch was NOT applied
        mock_k8s_adapter.patch_openstack_deployment.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_patch_success(self, mock_k8s_adapter, valid_input, mock_crq_validator):
        """Test successful patch application."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"openstack_version": "antelope"},
            }
        )
        mock_k8s_adapter.patch_openstack_deployment = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"openstack_version": "caracal"},
            }
        )

        with patch(
            "mosk_mcp.tools.operations_visibility.apply_osdpl_patch.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            result = await apply_osdpl_patch(mock_k8s_adapter, valid_input)

        assert result.success is True
        assert result.dry_run is False
        assert result.applied_at is not None
        assert result.before_version == "antelope"
        assert result.after_version == "caracal"
        assert "Successfully applied" in result.message
        assert "caracal" in result.message

    @pytest.mark.asyncio
    async def test_patch_includes_changes_summary(
        self, mock_k8s_adapter, valid_input, mock_crq_validator
    ):
        """Test that result includes changes summary."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"openstack_version": "antelope"},
            }
        )
        mock_k8s_adapter.patch_openstack_deployment = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"openstack_version": "caracal"},
            }
        )

        with patch(
            "mosk_mcp.tools.operations_visibility.apply_osdpl_patch.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            result = await apply_osdpl_patch(mock_k8s_adapter, valid_input)

        assert len(result.changes_applied) > 0
        assert any("replace" in c for c in result.changes_applied)
        assert any("openstack_version" in c for c in result.changes_applied)

    @pytest.mark.asyncio
    async def test_patch_includes_upgrade_warning(
        self, mock_k8s_adapter, valid_input, mock_crq_validator
    ):
        """Test that result includes upgrade warning."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"openstack_version": "antelope"},
            }
        )
        mock_k8s_adapter.patch_openstack_deployment = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"openstack_version": "caracal"},
            }
        )

        with patch(
            "mosk_mcp.tools.operations_visibility.apply_osdpl_patch.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            result = await apply_osdpl_patch(mock_k8s_adapter, valid_input)

        assert len(result.warnings) > 0
        assert any("upgrade" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_adapter_error_handled(self, mock_k8s_adapter, valid_input, mock_crq_validator):
        """Test that adapter errors are handled."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"openstack_version": "antelope"},
            }
        )
        mock_k8s_adapter.patch_openstack_deployment = AsyncMock(
            side_effect=Exception("API server unavailable")
        )

        with patch(
            "mosk_mcp.tools.operations_visibility.apply_osdpl_patch.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            with pytest.raises(ToolExecutionError) as exc_info:
                await apply_osdpl_patch(mock_k8s_adapter, valid_input)

        assert "Failed to apply OSDPL patch" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_value_error_from_adapter(
        self, mock_k8s_adapter, valid_input, mock_crq_validator
    ):
        """Test ValueError from adapter is converted to ValidationError."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"openstack_version": "antelope"},
            }
        )
        mock_k8s_adapter.patch_openstack_deployment = AsyncMock(
            side_effect=ValueError("Invalid patch format")
        )

        with patch(
            "mosk_mcp.tools.operations_visibility.apply_osdpl_patch.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            with pytest.raises(ValidationError) as exc_info:
                await apply_osdpl_patch(mock_k8s_adapter, valid_input)

        assert "Invalid patch format" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_version_no_change(self, mock_k8s_adapter, mock_crq_validator):
        """Test when version doesn't change (same version applied)."""
        input_data = ApplyOSDPLPatchInput(
            osdpl_name="mos",
            patch=[{"op": "replace", "path": "/spec/openstack_version", "value": "antelope"}],
            crq_number="CRQ123456789",
        )

        mock_k8s_adapter.get_openstack_deployment = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"openstack_version": "antelope"},
            }
        )
        mock_k8s_adapter.patch_openstack_deployment = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"openstack_version": "antelope"},
            }
        )

        with patch(
            "mosk_mcp.tools.operations_visibility.apply_osdpl_patch.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            result = await apply_osdpl_patch(mock_k8s_adapter, input_data)

        assert result.success is True
        assert result.before_version == "antelope"
        assert result.after_version == "antelope"
        # No version change warning
        assert not any("initiated" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_timestamp_set(self, mock_k8s_adapter, valid_input, mock_crq_validator):
        """Test that applied_at timestamp is set."""
        mock_k8s_adapter.get_openstack_deployment = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"openstack_version": "antelope"},
            }
        )
        mock_k8s_adapter.patch_openstack_deployment = AsyncMock(
            return_value={
                "metadata": {"name": "mos"},
                "spec": {"openstack_version": "caracal"},
            }
        )

        with patch(
            "mosk_mcp.tools.operations_visibility.apply_osdpl_patch.get_crq_validator",
            return_value=mock_crq_validator,
        ):
            result = await apply_osdpl_patch(mock_k8s_adapter, valid_input)

        assert result.applied_at is not None
        # Verify valid ISO format
        datetime.fromisoformat(result.applied_at.replace("Z", "+00:00"))
