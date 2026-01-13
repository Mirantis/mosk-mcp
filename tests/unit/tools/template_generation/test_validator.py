"""Tests for template validation tool.

Tests cover:
- YAML parsing validation
- Required Kubernetes fields validation
- Metadata validation (name, labels, annotations)
- Resource-specific schema validation
- Cluster conflict detection
- Strict mode
"""

import pytest

from mosk_mcp.tools.template_generation.validator import (
    TemplateValidator,
    ValidateTemplateInput,
    get_template_validator,
    validate_template,
)


class TestValidateTemplateInput:
    """Tests for ValidateTemplateInput validation."""

    def test_valid_minimal_input(self) -> None:
        """Test minimal valid input."""
        input_data = ValidateTemplateInput(
            template_yaml="apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: test"
        )
        assert input_data.template_yaml != ""
        assert input_data.strict_mode is False

    def test_empty_template_rejected(self) -> None:
        """Test empty template is rejected."""
        with pytest.raises(ValueError):
            ValidateTemplateInput(template_yaml="")


class TestTemplateValidator:
    """Tests for TemplateValidator."""

    @pytest.fixture
    def validator(self) -> TemplateValidator:
        """Create a TemplateValidator instance."""
        return TemplateValidator()

    # =========================================================================
    # YAML Parsing Tests
    # =========================================================================

    def test_invalid_yaml_syntax(self, validator: TemplateValidator) -> None:
        """Test invalid YAML syntax is detected."""
        input_data = ValidateTemplateInput(template_yaml="invalid: yaml: syntax: [")
        output = validator.validate(input_data)

        assert output.valid is False
        assert any(i.severity == "error" for i in output.issues)
        assert any("YAML" in i.message for i in output.issues)

    def test_non_dict_yaml(self, validator: TemplateValidator) -> None:
        """Test non-dictionary YAML is rejected."""
        input_data = ValidateTemplateInput(template_yaml="- just a list")
        output = validator.validate(input_data)

        assert output.valid is False
        assert any("mapping" in i.message.lower() for i in output.issues)

    def test_valid_yaml_passes(self, validator: TemplateValidator) -> None:
        """Test valid YAML passes parsing."""
        template = """
apiVersion: kaas.mirantis.com/v1alpha1
kind: Machine
metadata:
  name: compute-01
  labels:
    openstack-compute-node: enabled
spec:
  providerSpec:
    value:
      bareMetalHostProfile: compute-standard
"""
        input_data = ValidateTemplateInput(template_yaml=template)
        output = validator.validate(input_data)

        assert output.resource_kind == "Machine"
        assert output.resource_name == "compute-01"

    # =========================================================================
    # Required Kubernetes Fields Tests
    # =========================================================================

    def test_missing_apiversion(self, validator: TemplateValidator) -> None:
        """Test missing apiVersion is detected."""
        template = """
kind: Machine
metadata:
  name: test
"""
        input_data = ValidateTemplateInput(template_yaml=template)
        output = validator.validate(input_data)

        assert output.valid is False
        assert any(i.path == "/apiVersion" and i.severity == "error" for i in output.issues)

    def test_missing_kind(self, validator: TemplateValidator) -> None:
        """Test missing kind is detected."""
        template = """
apiVersion: v1
metadata:
  name: test
"""
        input_data = ValidateTemplateInput(template_yaml=template)
        output = validator.validate(input_data)

        assert output.valid is False
        assert any(i.path == "/kind" and i.severity == "error" for i in output.issues)

    def test_missing_metadata(self, validator: TemplateValidator) -> None:
        """Test missing metadata is detected."""
        template = """
apiVersion: v1
kind: ConfigMap
"""
        input_data = ValidateTemplateInput(template_yaml=template)
        output = validator.validate(input_data)

        assert output.valid is False
        assert any(i.path == "/metadata" and i.severity == "error" for i in output.issues)

    def test_missing_spec_warning(self, validator: TemplateValidator) -> None:
        """Test missing spec generates warning."""
        template = """
apiVersion: v1
kind: ConfigMap
metadata:
  name: test
"""
        input_data = ValidateTemplateInput(template_yaml=template)
        output = validator.validate(input_data)

        # Missing spec is a warning, not error
        assert any(i.path == "/spec" and i.severity == "warning" for i in output.issues)

    def test_unknown_kind_warning(self, validator: TemplateValidator) -> None:
        """Test unknown resource kind generates warning."""
        template = """
apiVersion: v1
kind: UnknownResource
metadata:
  name: test
spec:
  foo: bar
"""
        input_data = ValidateTemplateInput(template_yaml=template)
        output = validator.validate(input_data)

        assert any(
            i.path == "/kind" and i.severity == "warning" and "Unknown" in i.message
            for i in output.issues
        )

    # =========================================================================
    # Metadata Validation Tests
    # =========================================================================

    def test_missing_metadata_name(self, validator: TemplateValidator) -> None:
        """Test missing metadata.name is detected."""
        template = """
apiVersion: v1
kind: ConfigMap
metadata:
  namespace: default
"""
        input_data = ValidateTemplateInput(template_yaml=template)
        output = validator.validate(input_data)

        assert output.valid is False
        assert any(i.path == "/metadata/name" and i.severity == "error" for i in output.issues)

    def test_name_too_long(self, validator: TemplateValidator) -> None:
        """Test name exceeding 63 chars is detected."""
        template = f"""
apiVersion: v1
kind: ConfigMap
metadata:
  name: {"a" * 64}
"""
        input_data = ValidateTemplateInput(template_yaml=template)
        output = validator.validate(input_data)

        assert output.valid is False
        assert any("63" in i.message and "character" in i.message for i in output.issues)

    def test_invalid_dns_label_name(self, validator: TemplateValidator) -> None:
        """Test invalid DNS label name is detected."""
        template = """
apiVersion: v1
kind: ConfigMap
metadata:
  name: Invalid_Name
"""
        input_data = ValidateTemplateInput(template_yaml=template)
        output = validator.validate(input_data)

        assert output.valid is False
        assert any(
            "DNS label" in i.message or "not a valid" in i.message.lower() for i in output.issues
        )

    def test_valid_dns_label_name(self, validator: TemplateValidator) -> None:
        """Test valid DNS label name passes."""
        template = """
apiVersion: kaas.mirantis.com/v1alpha1
kind: Machine
metadata:
  name: compute-01
  labels:
    openstack-compute-node: enabled
spec:
  providerSpec:
    value:
      bareMetalHostProfile: compute-standard
"""
        input_data = ValidateTemplateInput(template_yaml=template)
        output = validator.validate(input_data)

        # Should not have name validation errors
        assert not any(i.path == "/metadata/name" and i.severity == "error" for i in output.issues)

    def test_label_value_too_long(self, validator: TemplateValidator) -> None:
        """Test label value exceeding 63 chars is detected."""
        template = f"""
apiVersion: v1
kind: ConfigMap
metadata:
  name: test
  labels:
    long-label: {"a" * 64}
"""
        input_data = ValidateTemplateInput(template_yaml=template)
        output = validator.validate(input_data)

        assert any("63" in i.message and "label" in i.message.lower() for i in output.issues)

    def test_non_string_label_value(self, validator: TemplateValidator) -> None:
        """Test non-string label value is detected."""
        template = """
apiVersion: v1
kind: ConfigMap
metadata:
  name: test
  labels:
    numeric-label: 123
"""
        input_data = ValidateTemplateInput(template_yaml=template)
        output = validator.validate(input_data)

        assert any(
            "string" in i.message.lower() and "label" in i.path.lower() for i in output.issues
        )

    # =========================================================================
    # Resource-Specific Validation Tests
    # =========================================================================

    def test_bmhi_missing_boot_mac(self, validator: TemplateValidator) -> None:
        """Test BMHi missing bootMACAddress is detected."""
        template = """
apiVersion: kaas.mirantis.com/v1alpha1
kind: BareMetalHostInventory
metadata:
  name: server-01
spec:
  bmc:
    address: 192.168.1.100
    credentialsName: server-01-bmc
"""
        input_data = ValidateTemplateInput(template_yaml=template)
        output = validator.validate(input_data)

        assert any(
            "bootMACAddress" in i.path or "bootMACAddress" in i.message for i in output.issues
        )

    def test_bmhi_invalid_mac_format(self, validator: TemplateValidator) -> None:
        """Test BMHi invalid MAC address format is detected."""
        template = """
apiVersion: kaas.mirantis.com/v1alpha1
kind: BareMetalHostInventory
metadata:
  name: server-01
spec:
  bootMACAddress: invalid-mac
  bmc:
    address: 192.168.1.100
    credentialsName: server-01-bmc
"""
        input_data = ValidateTemplateInput(template_yaml=template)
        output = validator.validate(input_data)

        assert any("MAC" in i.message and "invalid" in i.message.lower() for i in output.issues)

    def test_bmhi_missing_bmc_address(self, validator: TemplateValidator) -> None:
        """Test BMHi missing BMC address is detected."""
        template = """
apiVersion: kaas.mirantis.com/v1alpha1
kind: BareMetalHostInventory
metadata:
  name: server-01
spec:
  bootMACAddress: aa:bb:cc:dd:ee:ff
  bmc:
    credentialsName: server-01-bmc
"""
        input_data = ValidateTemplateInput(template_yaml=template)
        output = validator.validate(input_data)

        assert any(
            "address" in i.message.lower() and "bmc" in i.path.lower() for i in output.issues
        )

    def test_machine_missing_role_label_warning(self, validator: TemplateValidator) -> None:
        """Test Machine without role label generates warning."""
        template = """
apiVersion: cluster.k8s.io/v1alpha1
kind: Machine
metadata:
  name: node-01
spec:
  providerSpec:
    value:
      bareMetalHostProfile: standard
"""
        input_data = ValidateTemplateInput(template_yaml=template)
        output = validator.validate(input_data)

        assert any("role" in i.message.lower() and i.severity == "warning" for i in output.issues)

    def test_machine_missing_bmhp_reference(self, validator: TemplateValidator) -> None:
        """Test Machine missing bareMetalHostProfile is detected."""
        template = """
apiVersion: cluster.k8s.io/v1alpha1
kind: Machine
metadata:
  name: compute-01
  labels:
    openstack-compute-node: enabled
spec:
  providerSpec:
    value:
      someOtherField: value
"""
        input_data = ValidateTemplateInput(template_yaml=template)
        output = validator.validate(input_data)

        assert any(
            "bareMetalHostProfile" in i.message or "bareMetalHostProfile" in i.path
            for i in output.issues
        )

    # =========================================================================
    # Cluster Conflict Tests
    # =========================================================================

    def test_conflict_detection(self, validator: TemplateValidator) -> None:
        """Test cluster conflict detection."""
        template = """
apiVersion: kaas.mirantis.com/v1alpha1
kind: Machine
metadata:
  name: compute-01
  labels:
    openstack-compute-node: enabled
spec:
  providerSpec:
    value:
      bareMetalHostProfile: compute-standard
"""
        input_data = ValidateTemplateInput(
            template_yaml=template,
            check_cluster_conflicts=True,
            existing_resources=["Machine/compute-01", "Machine/compute-02"],
        )
        output = validator.validate(input_data)

        assert output.valid is False
        assert any("already exists" in i.message and i.severity == "error" for i in output.issues)

    def test_no_conflict_different_name(self, validator: TemplateValidator) -> None:
        """Test no conflict with different resource name."""
        template = """
apiVersion: kaas.mirantis.com/v1alpha1
kind: Machine
metadata:
  name: compute-03
  labels:
    openstack-compute-node: enabled
spec:
  providerSpec:
    value:
      bareMetalHostProfile: compute-standard
"""
        input_data = ValidateTemplateInput(
            template_yaml=template,
            check_cluster_conflicts=True,
            existing_resources=["Machine/compute-01", "Machine/compute-02"],
        )
        output = validator.validate(input_data)

        # Should not have conflict errors
        assert not any(
            "already exists" in i.message and i.severity == "error" for i in output.issues
        )

    # =========================================================================
    # Strict Mode Tests
    # =========================================================================

    def test_strict_mode_warnings_become_errors(self, validator: TemplateValidator) -> None:
        """Test strict mode treats warnings as errors."""
        template = """
apiVersion: v1
kind: UnknownResource
metadata:
  name: test
spec:
  foo: bar
"""
        # Non-strict: should pass (unknown kind is just a warning)
        input_non_strict = ValidateTemplateInput(template_yaml=template, strict_mode=False)
        output_non_strict = validator.validate(input_non_strict)
        assert output_non_strict.valid is True

        # Strict: should fail
        input_strict = ValidateTemplateInput(template_yaml=template, strict_mode=True)
        output_strict = validator.validate(input_strict)
        assert output_strict.valid is False

    # =========================================================================
    # Summary Tests
    # =========================================================================

    def test_summary_valid_no_issues(self, validator: TemplateValidator) -> None:
        """Test summary for valid template with no issues."""
        template = """
apiVersion: kaas.mirantis.com/v1alpha1
kind: Machine
metadata:
  name: compute-01
  labels:
    openstack-compute-node: enabled
spec:
  providerSpec:
    value:
      bareMetalHostProfile: compute-standard
"""
        input_data = ValidateTemplateInput(template_yaml=template)
        output = validator.validate(input_data)

        # Even without errors, there may be warnings
        if output.valid:
            assert "PASSED" in output.summary

    def test_summary_with_errors(self, validator: TemplateValidator) -> None:
        """Test summary for template with errors."""
        template = """
kind: Machine
metadata:
  name: test
"""
        input_data = ValidateTemplateInput(template_yaml=template)
        output = validator.validate(input_data)

        assert output.valid is False
        assert "FAILED" in output.summary


class TestGetTemplateValidator:
    """Tests for singleton validator."""

    def test_singleton_returns_same_instance(self) -> None:
        """Test that get_template_validator returns the same instance."""
        val1 = get_template_validator()
        val2 = get_template_validator()
        assert val1 is val2


class TestValidateTemplateAsync:
    """Tests for async validate_template function."""

    @pytest.mark.asyncio
    async def test_validate_template_async(self) -> None:
        """Test async validate_template function."""
        template = """
apiVersion: kaas.mirantis.com/v1alpha1
kind: Machine
metadata:
  name: compute-01
  labels:
    openstack-compute-node: enabled
spec:
  providerSpec:
    value:
      bareMetalHostProfile: compute-standard
"""
        output = await validate_template(template)

        assert output.resource_kind == "Machine"
        assert output.resource_name == "compute-01"

    @pytest.mark.asyncio
    async def test_validate_template_with_conflicts(self) -> None:
        """Test async validate_template with conflict detection."""
        template = """
apiVersion: kaas.mirantis.com/v1alpha1
kind: Machine
metadata:
  name: existing-node
  labels:
    openstack-compute-node: enabled
spec:
  providerSpec:
    value:
      bareMetalHostProfile: compute-standard
"""
        output = await validate_template(
            template,
            check_cluster_conflicts=True,
            existing_resources=["Machine/existing-node"],
        )

        assert output.valid is False


class TestL2TemplateValidation:
    """Tests for L2Template-specific validation."""

    @pytest.fixture
    def validator(self) -> TemplateValidator:
        """Create a TemplateValidator instance."""
        return TemplateValidator()

    def test_l2template_undefined_interface_warning(self, validator: TemplateValidator) -> None:
        """Test L2Template with undefined interface reference generates warning."""
        template = """
apiVersion: ipam.mirantis.com/v1alpha1
kind: L2Template
metadata:
  name: compute-network
spec:
  interfaces:
    - name: eth0
  bonds:
    - name: bond0
      interfaces:
        - eth0
        - eth1
"""
        input_data = ValidateTemplateInput(template_yaml=template)
        output = validator.validate(input_data)

        # eth1 is not defined, should warn
        assert any("eth1" in i.message and i.severity == "warning" for i in output.issues)


class TestIpamHostValidation:
    """Tests for IpamHost-specific validation."""

    @pytest.fixture
    def validator(self) -> TemplateValidator:
        """Create a TemplateValidator instance."""
        return TemplateValidator()

    def test_ipamhost_duplicate_network(self, validator: TemplateValidator) -> None:
        """Test IpamHost with duplicate network assignment is detected."""
        template = """
apiVersion: ipam.mirantis.com/v1alpha1
kind: IpamHost
metadata:
  name: compute-01
spec:
  l2Template: compute-template
  networkAssignments:
    - network: management
      address: 10.0.0.10
    - network: management
      address: 10.0.0.11
"""
        input_data = ValidateTemplateInput(template_yaml=template)
        output = validator.validate(input_data)

        assert any("Duplicate" in i.message and "management" in i.message for i in output.issues)

    def test_ipamhost_invalid_ip(self, validator: TemplateValidator) -> None:
        """Test IpamHost with invalid IP address is detected."""
        template = """
apiVersion: ipam.mirantis.com/v1alpha1
kind: IpamHost
metadata:
  name: compute-01
spec:
  l2Template: compute-template
  networkAssignments:
    - network: management
      address: not-an-ip
"""
        input_data = ValidateTemplateInput(template_yaml=template)
        output = validator.validate(input_data)

        assert any("IP" in i.message or "address" in i.message.lower() for i in output.issues)
