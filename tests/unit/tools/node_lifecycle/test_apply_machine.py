"""Unit tests for apply_machine tool."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from mosk_mcp.auth.crq import CRQValidator, set_crq_validator
from mosk_mcp.core.exceptions import ResourceNotFoundError, ValidationError
from mosk_mcp.tools.node_lifecycle.apply_machine import (
    ApplyMachineInput,
    ApplyMachineOutput,
    _check_machine_exists,
    _parse_machine_yaml,
    _validate_machine_structure,
    _validate_prerequisites,
    apply_machine,
)


@pytest.fixture(autouse=True)
def reset_crq_validator():
    """Reset CRQ validator singleton for format-only testing."""
    # Set a validator that allows format-only validation for tests
    set_crq_validator(CRQValidator(allow_format_only=True))
    yield
    # Reset after each test
    set_crq_validator(CRQValidator(allow_format_only=True))


SAMPLE_MACHINE_YAML = """
apiVersion: kaas.mirantis.com/v1alpha1
kind: Machine
metadata:
  name: compute-01
  namespace: default
  labels:
    openstack-compute-node: enabled
    kaas.mirantis.com/provider: baremetal
spec:
  providerSpec:
    value:
      bareMetalHostProfile: compute-profile
      hostRepositories: []
"""


SAMPLE_MACHINE_DICT = {
    "apiVersion": "kaas.mirantis.com/v1alpha1",
    "kind": "Machine",
    "metadata": {
        "name": "compute-01",
        "namespace": "default",
        "labels": {
            "openstack-compute-node": "enabled",
        },
    },
    "spec": {
        "providerSpec": {
            "value": {
                "bareMetalHostProfile": "compute-profile",
            },
        },
    },
}


class TestParseMachineYAML:
    """Tests for _parse_machine_yaml function."""

    def test_parse_valid_yaml(self):
        """Test parsing valid YAML."""
        result = _parse_machine_yaml(SAMPLE_MACHINE_YAML)

        assert isinstance(result, dict)
        assert result["kind"] == "Machine"
        assert result["metadata"]["name"] == "compute-01"

    def test_parse_invalid_yaml(self):
        """Test parsing invalid YAML raises error."""
        invalid_yaml = "invalid: yaml: content: [["

        with pytest.raises(ValidationError) as exc_info:
            _parse_machine_yaml(invalid_yaml)

        assert "Invalid YAML" in str(exc_info.value)

    def test_parse_non_dict_yaml(self):
        """Test parsing YAML that's not a dict raises error."""
        list_yaml = "- item1\n- item2"

        with pytest.raises(ValidationError) as exc_info:
            _parse_machine_yaml(list_yaml)

        assert "must be a dictionary" in str(exc_info.value)


class TestValidateMachineStructure:
    """Tests for _validate_machine_structure function."""

    def test_valid_structure(self):
        """Test validating correct structure."""
        warnings = _validate_machine_structure(SAMPLE_MACHINE_DICT)

        assert len(warnings) == 0

    def test_missing_api_version(self):
        """Test warning for missing apiVersion."""
        machine = {"kind": "Machine", "metadata": {"name": "test"}}

        warnings = _validate_machine_structure(machine)

        assert any("apiVersion" in w for w in warnings)

    def test_missing_kind(self):
        """Test warning for missing kind."""
        machine = {"apiVersion": "v1", "metadata": {"name": "test"}}

        warnings = _validate_machine_structure(machine)

        assert any("kind" in w for w in warnings)

    def test_wrong_kind(self):
        """Test warning for wrong kind."""
        machine = {
            "apiVersion": "v1",
            "kind": "Deployment",
            "metadata": {"name": "test"},
        }

        warnings = _validate_machine_structure(machine)

        assert any("Kind should be 'Machine'" in w for w in warnings)

    def test_missing_name(self):
        """Test warning for missing name."""
        machine = {
            "apiVersion": "v1",
            "kind": "Machine",
            "metadata": {},
        }

        warnings = _validate_machine_structure(machine)

        assert any("metadata.name" in w for w in warnings)

    def test_missing_profile(self):
        """Test warning for missing profile."""
        machine = {
            "apiVersion": "v1",
            "kind": "Machine",
            "metadata": {"name": "test"},
            "spec": {"providerSpec": {"value": {}}},
        }

        warnings = _validate_machine_structure(machine)

        assert any("bareMetalHostProfile" in w for w in warnings)


class TestValidatePrerequisites:
    """Tests for _validate_prerequisites function."""

    @pytest.fixture
    def mock_k8s_adapter(self):
        """Create a mock Kubernetes adapter."""
        adapter = MagicMock()
        adapter.get_custom_resource = AsyncMock()
        return adapter

    @pytest.mark.asyncio
    async def test_all_prerequisites_exist(self, mock_k8s_adapter):
        """Test when all prerequisites exist."""
        valid, issues = await _validate_prerequisites(
            mock_k8s_adapter,
            SAMPLE_MACHINE_DICT,
            "default",
        )

        assert valid is True
        assert len(issues) == 0

    @pytest.mark.asyncio
    async def test_missing_bmhp(self, mock_k8s_adapter):
        """Test when BareMetalHostProfile is missing."""
        mock_k8s_adapter.get_custom_resource = AsyncMock(
            side_effect=[
                Exception("Not found"),  # BMHp
                {},  # BMHi
            ]
        )

        valid, issues = await _validate_prerequisites(
            mock_k8s_adapter,
            SAMPLE_MACHINE_DICT,
            "default",
        )

        assert valid is False
        assert any("BareMetalHostProfile" in i for i in issues)

    @pytest.mark.asyncio
    async def test_missing_bmhi(self, mock_k8s_adapter):
        """Test when BareMetalHostInventory is missing."""
        mock_k8s_adapter.get_custom_resource = AsyncMock(
            side_effect=[
                {},  # BMHp
                Exception("Not found"),  # BMHi
            ]
        )

        valid, issues = await _validate_prerequisites(
            mock_k8s_adapter,
            SAMPLE_MACHINE_DICT,
            "default",
        )

        assert valid is False
        assert any("BareMetalHostInventory" in i for i in issues)

    # Note: test_missing_ipam_host was removed because IpamHost is auto-created
    # and the validation for it was removed from _validate_prerequisites


class TestCheckMachineExists:
    """Tests for _check_machine_exists function."""

    @pytest.fixture
    def mock_k8s_adapter(self):
        """Create a mock Kubernetes adapter."""
        adapter = MagicMock()
        adapter.get_machine = AsyncMock()
        return adapter

    @pytest.mark.asyncio
    async def test_machine_exists(self, mock_k8s_adapter):
        """Test when machine exists."""
        mock_k8s_adapter.get_machine = AsyncMock(return_value=SAMPLE_MACHINE_DICT)

        result = await _check_machine_exists(
            mock_k8s_adapter,
            "compute-01",
            "default",
        )

        assert result.exists is True
        assert result.query_succeeded is True
        assert result.error is None

    @pytest.mark.asyncio
    async def test_machine_not_exists(self, mock_k8s_adapter):
        """Test when machine doesn't exist (ResourceNotFoundError)."""
        mock_k8s_adapter.get_machine = AsyncMock(
            side_effect=ResourceNotFoundError("Machine not found")
        )

        result = await _check_machine_exists(
            mock_k8s_adapter,
            "nonexistent",
            "default",
        )

        assert result.exists is False
        assert result.query_succeeded is True
        assert result.error is None

    @pytest.mark.asyncio
    async def test_machine_check_query_failed(self, mock_k8s_adapter):
        """Test when machine existence check fails due to API error."""
        mock_k8s_adapter.get_machine = AsyncMock(side_effect=Exception("Connection refused"))

        result = await _check_machine_exists(
            mock_k8s_adapter,
            "some-machine",
            "default",
        )

        assert result.exists is False
        assert result.query_succeeded is False
        assert result.error is not None
        assert "Connection refused" in result.error


class TestApplyMachine:
    """Tests for apply_machine function."""

    @pytest.fixture
    def mock_k8s_adapter(self):
        """Create a mock Kubernetes adapter."""
        adapter = MagicMock()
        adapter.get_custom_resource = AsyncMock()
        # Use ResourceNotFoundError to simulate "machine doesn't exist"
        adapter.get_machine = AsyncMock(side_effect=ResourceNotFoundError("Machine not found"))
        adapter.create_custom_resource = AsyncMock()
        adapter.patch_custom_resource = AsyncMock()
        adapter.apply_custom_resource = AsyncMock()
        return adapter

    @pytest.mark.asyncio
    async def test_apply_machine_dry_run_with_yaml(self, mock_k8s_adapter, admin_context):
        """Test applying machine in dry run mode with YAML."""
        input_data = ApplyMachineInput(
            machine_yaml=SAMPLE_MACHINE_YAML,
            dry_run=True,
        )

        result = await apply_machine(mock_k8s_adapter, input_data, context=admin_context)

        assert isinstance(result, ApplyMachineOutput)
        assert result.name == "compute-01"
        assert result.dry_run is True
        assert result.applied is False
        assert "would be created" in result.message

        # Should not have applied
        mock_k8s_adapter.create_custom_resource.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_machine_dry_run_with_dict(self, mock_k8s_adapter, admin_context):
        """Test applying machine in dry run mode with dict."""
        input_data = ApplyMachineInput(
            machine_dict=SAMPLE_MACHINE_DICT,
            dry_run=True,
        )

        result = await apply_machine(mock_k8s_adapter, input_data, context=admin_context)

        assert result.name == "compute-01"
        assert result.dry_run is True

    @pytest.mark.asyncio
    async def test_apply_machine_requires_crq(self, mock_k8s_adapter, admin_context):
        """Test that CRQ is required for non-dry-run."""
        input_data = ApplyMachineInput(
            machine_yaml=SAMPLE_MACHINE_YAML,
            dry_run=False,
            crq_number=None,  # No CRQ
        )

        with pytest.raises(ValidationError) as exc_info:
            await apply_machine(mock_k8s_adapter, input_data, context=admin_context)

        assert "CRQ number is required" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_apply_machine_with_crq(self, mock_k8s_adapter, admin_context):
        """Test applying machine with valid CRQ."""
        input_data = ApplyMachineInput(
            machine_yaml=SAMPLE_MACHINE_YAML,
            crq_number="CRQ123456789",
            dry_run=False,
        )

        result = await apply_machine(mock_k8s_adapter, input_data, context=admin_context)

        assert result.applied is True
        assert result.created is True
        assert result.crq_validated is True

        # Should have created
        mock_k8s_adapter.create_custom_resource.assert_called_once()

    @pytest.mark.asyncio
    async def test_apply_machine_rejects_existing(self, mock_k8s_adapter, admin_context):
        """Test that applying to existing machine raises error (updates not supported)."""
        mock_k8s_adapter.get_machine = AsyncMock(return_value=SAMPLE_MACHINE_DICT)

        input_data = ApplyMachineInput(
            machine_yaml=SAMPLE_MACHINE_YAML,
            crq_number="CRQ123456789",
            dry_run=False,
        )

        with pytest.raises(ValidationError) as exc_info:
            await apply_machine(mock_k8s_adapter, input_data, context=admin_context)

        assert "already exists" in str(exc_info.value)
        assert "not supported" in str(exc_info.value)

        # Should not have called create or patch
        mock_k8s_adapter.create_custom_resource.assert_not_called()
        mock_k8s_adapter.patch_custom_resource.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_machine_prerequisite_issues(self, mock_k8s_adapter, admin_context):
        """Test that prerequisite issues are reported."""
        mock_k8s_adapter.get_custom_resource = AsyncMock(side_effect=Exception("Not found"))

        input_data = ApplyMachineInput(
            machine_yaml=SAMPLE_MACHINE_YAML,
            dry_run=True,
            validate_prerequisites=True,
        )

        result = await apply_machine(mock_k8s_adapter, input_data, context=admin_context)

        assert result.prerequisites_valid is False
        assert len(result.prerequisite_issues) > 0

    @pytest.mark.asyncio
    async def test_apply_machine_blocks_without_prerequisites(
        self, mock_k8s_adapter, admin_context
    ):
        """Test that apply is blocked without prerequisites."""
        mock_k8s_adapter.get_custom_resource = AsyncMock(side_effect=Exception("Not found"))

        input_data = ApplyMachineInput(
            machine_yaml=SAMPLE_MACHINE_YAML,
            crq_number="CRQ123456789",
            dry_run=False,
            validate_prerequisites=True,
        )

        with pytest.raises(ValidationError) as exc_info:
            await apply_machine(mock_k8s_adapter, input_data, context=admin_context)

        assert "prerequisites not met" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_apply_machine_no_input(self, mock_k8s_adapter, admin_context):
        """Test that either YAML or dict is required."""
        input_data = ApplyMachineInput(
            machine_yaml=None,
            machine_dict=None,
            dry_run=True,
        )

        with pytest.raises(ValidationError) as exc_info:
            await apply_machine(mock_k8s_adapter, input_data, context=admin_context)

        assert "must be provided" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_apply_machine_missing_name(self, mock_k8s_adapter, admin_context):
        """Test that name is required."""
        machine_without_name = {
            "apiVersion": "v1",
            "kind": "Machine",
            "metadata": {},
            "spec": {},
        }

        input_data = ApplyMachineInput(
            machine_dict=machine_without_name,
            dry_run=True,
        )

        with pytest.raises(ValidationError) as exc_info:
            await apply_machine(mock_k8s_adapter, input_data, context=admin_context)

        assert "name is required" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_apply_machine_namespace_override(self, mock_k8s_adapter, admin_context):
        """Test namespace override."""
        input_data = ApplyMachineInput(
            machine_yaml=SAMPLE_MACHINE_YAML,
            namespace="production",
            dry_run=True,
        )

        result = await apply_machine(mock_k8s_adapter, input_data, context=admin_context)

        assert result.namespace == "production"

    @pytest.mark.asyncio
    async def test_apply_machine_next_steps(self, mock_k8s_adapter, admin_context):
        """Test next steps are generated."""
        input_data = ApplyMachineInput(
            machine_yaml=SAMPLE_MACHINE_YAML,
            dry_run=True,
        )

        result = await apply_machine(mock_k8s_adapter, input_data, context=admin_context)

        assert len(result.next_steps) > 0


class TestApplyMachineInput:
    """Tests for ApplyMachineInput validation."""

    def test_default_values(self):
        """Test default values."""
        input_data = ApplyMachineInput(machine_yaml=SAMPLE_MACHINE_YAML)

        assert input_data.crq_number is None
        assert input_data.namespace is None
        assert input_data.dry_run is True
        assert input_data.validate_prerequisites is True
        assert input_data.server_side_apply is False

    def test_crq_pattern_validation(self):
        """Test CRQ pattern validation."""
        # Valid pattern
        input_data = ApplyMachineInput(
            machine_yaml=SAMPLE_MACHINE_YAML,
            crq_number="CRQ123456789",
        )
        assert input_data.crq_number == "CRQ123456789"

        # Invalid pattern
        with pytest.raises(ValueError):
            ApplyMachineInput(
                machine_yaml=SAMPLE_MACHINE_YAML,
                crq_number="INVALID",
            )

        with pytest.raises(ValueError):
            ApplyMachineInput(
                machine_yaml=SAMPLE_MACHINE_YAML,
                crq_number="CRQ12345",  # Too short
            )

    def test_either_yaml_or_dict(self):
        """Test that either YAML or dict can be provided."""
        # With YAML
        input_data = ApplyMachineInput(machine_yaml=SAMPLE_MACHINE_YAML)
        assert input_data.machine_yaml is not None
        assert input_data.machine_dict is None

        # With dict
        input_data = ApplyMachineInput(machine_dict=SAMPLE_MACHINE_DICT)
        assert input_data.machine_yaml is None
        assert input_data.machine_dict is not None


class TestApplyMachineOutput:
    """Tests for ApplyMachineOutput model."""

    def test_required_fields(self):
        """Test required fields."""
        output = ApplyMachineOutput(
            name="compute-01",
            namespace="default",
            applied=False,
            dry_run=True,
            crq_validated=True,
            message="Test message",
        )

        assert output.name == "compute-01"
        assert output.namespace == "default"
        assert output.applied is False

    def test_optional_fields_defaults(self):
        """Test optional fields have defaults."""
        output = ApplyMachineOutput(
            name="compute-01",
            namespace="default",
            applied=False,
            dry_run=True,
            crq_validated=True,
            message="Test message",
        )

        assert output.created is False
        assert output.prerequisites_valid is True
        assert output.prerequisite_issues == []
        assert output.machine_spec == {}
        assert output.warnings == []
        assert output.next_steps == []
