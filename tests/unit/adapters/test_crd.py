"""Tests for CRD (Custom Resource Definition) adapter models.

This module tests the Pydantic models and parsing logic for MOSK Kubernetes CRDs,
including Machine, BareMetalHost, L2Template, and other infrastructure resources.
"""

import pytest

from mosk_mcp.adapters.crd.base import (
    KubernetesMetadata,
    OwnerReference,
)
from mosk_mcp.adapters.crd.machine import (
    BareMetalHostProfileRef,
    L2TemplateSelector,
    Machine,
    MachinePhase,
    MachineProviderSpec,
    MachineSpec,
    MachineStatus,
)


class TestOwnerReference:
    """Tests for OwnerReference model."""

    def test_create_minimal(self):
        """Test creating OwnerReference with minimal fields."""
        ref = OwnerReference(
            api_version="cluster.k8s.io/v1alpha1",
            kind="Machine",
            name="test-machine",
            uid="12345",
        )
        assert ref.api_version == "cluster.k8s.io/v1alpha1"
        assert ref.kind == "Machine"
        assert ref.name == "test-machine"
        assert ref.uid == "12345"
        assert ref.controller is None
        assert ref.block_owner_deletion is None

    def test_create_full(self):
        """Test creating OwnerReference with all fields."""
        ref = OwnerReference(
            api_version="cluster.k8s.io/v1alpha1",
            kind="Machine",
            name="test-machine",
            uid="12345",
            controller=True,
            block_owner_deletion=True,
        )
        assert ref.controller is True
        assert ref.block_owner_deletion is True

    def test_to_kubernetes(self):
        """Test converting OwnerReference to Kubernetes API format."""
        ref = OwnerReference(
            api_version="v1",
            kind="Pod",
            name="my-pod",
            uid="abc123",
            controller=True,
        )
        result = ref.to_kubernetes()
        assert result == {
            "apiVersion": "v1",
            "kind": "Pod",
            "name": "my-pod",
            "uid": "abc123",
            "controller": True,
        }

    def test_to_kubernetes_minimal(self):
        """Test converting minimal OwnerReference to Kubernetes format."""
        ref = OwnerReference(
            api_version="v1",
            kind="Pod",
            name="my-pod",
            uid="abc123",
        )
        result = ref.to_kubernetes()
        assert "controller" not in result
        assert "blockOwnerDeletion" not in result


class TestKubernetesMetadata:
    """Tests for KubernetesMetadata model."""

    def test_create_minimal(self):
        """Test creating metadata with minimal fields."""
        meta = KubernetesMetadata(name="test-resource")
        assert meta.name == "test-resource"
        assert meta.namespace is None
        assert meta.labels == {}
        assert meta.annotations == {}

    def test_create_full(self):
        """Test creating metadata with all fields."""
        meta = KubernetesMetadata(
            name="test-resource",
            namespace="default",
            labels={"app": "test"},
            annotations={"note": "example"},
            uid="uid-12345",
            resource_version="100",
            generation=3,
        )
        assert meta.namespace == "default"
        assert meta.labels == {"app": "test"}
        assert meta.uid == "uid-12345"

    def test_from_kubernetes_format(self):
        """Test parsing from Kubernetes API format with camelCase."""
        data = {
            "name": "my-machine",
            "namespace": "lab",
            "resourceVersion": "12345",
            "creationTimestamp": "2024-01-15T10:30:00Z",
        }
        meta = KubernetesMetadata.model_validate(data)
        assert meta.name == "my-machine"
        assert meta.resource_version == "12345"
        assert meta.creation_timestamp is not None

    def test_to_kubernetes(self):
        """Test converting metadata to Kubernetes format."""
        meta = KubernetesMetadata(
            name="my-resource",
            namespace="prod",
            labels={"env": "production"},
        )
        result = meta.to_kubernetes()
        assert result["name"] == "my-resource"
        assert result["namespace"] == "prod"
        assert result["labels"] == {"env": "production"}


class TestMachinePhase:
    """Tests for MachinePhase enum."""

    def test_all_phases_defined(self):
        """Test all expected phases are defined."""
        expected = {
            "Pending",
            "Provisioning",
            "Provisioned",
            "Running",
            "Deleting",
            "Deleted",
            "Failed",
            "Unknown",
        }
        actual = {phase.value for phase in MachinePhase}
        assert expected == actual

    def test_phase_from_string(self):
        """Test creating phase from string value."""
        assert MachinePhase("Running") == MachinePhase.RUNNING
        assert MachinePhase("Failed") == MachinePhase.FAILED


class TestBareMetalHostProfileRef:
    """Tests for BareMetalHostProfileRef model."""

    def test_create_with_name_only(self):
        """Test creating ref with name only."""
        ref = BareMetalHostProfileRef(name="worker-profile")
        assert ref.name == "worker-profile"
        assert ref.namespace is None

    def test_create_with_namespace(self):
        """Test creating ref with namespace."""
        ref = BareMetalHostProfileRef(name="worker-profile", namespace="lab")
        assert ref.name == "worker-profile"
        assert ref.namespace == "lab"

    def test_from_value_string(self):
        """Test creating from string value."""
        ref = BareMetalHostProfileRef.from_value("my-profile")
        assert ref.name == "my-profile"
        assert ref.namespace is None

    def test_from_value_dict(self):
        """Test creating from dict value."""
        ref = BareMetalHostProfileRef.from_value({"name": "my-profile", "namespace": "ns"})
        assert ref.name == "my-profile"
        assert ref.namespace == "ns"

    def test_from_value_existing_ref(self):
        """Test creating from existing BareMetalHostProfileRef."""
        original = BareMetalHostProfileRef(name="test", namespace="lab")
        ref = BareMetalHostProfileRef.from_value(original)
        assert ref is original

    def test_from_value_none(self):
        """Test creating from None returns empty."""
        ref = BareMetalHostProfileRef.from_value(None)
        assert ref.name == ""

    def test_from_value_invalid_dict(self):
        """Test creating from dict without name raises error."""
        with pytest.raises(ValueError, match="must have a 'name' field"):
            BareMetalHostProfileRef.from_value({"namespace": "ns"})

    def test_from_value_invalid_type(self):
        """Test creating from invalid type raises error."""
        with pytest.raises(ValueError, match="Invalid BareMetalHostProfile reference type"):
            BareMetalHostProfileRef.from_value(123)

    def test_to_kubernetes(self):
        """Test converting to Kubernetes format."""
        ref = BareMetalHostProfileRef(name="profile", namespace="lab")
        result = ref.to_kubernetes()
        assert result == {"name": "profile", "namespace": "lab"}

    def test_to_kubernetes_no_namespace(self):
        """Test converting to Kubernetes format without namespace."""
        ref = BareMetalHostProfileRef(name="profile")
        result = ref.to_kubernetes()
        assert result == {"name": "profile"}

    def test_to_string(self):
        """Test string conversion."""
        ref = BareMetalHostProfileRef(name="profile", namespace="lab")
        assert ref.to_string() == "lab/profile"

    def test_to_string_no_namespace(self):
        """Test string conversion without namespace."""
        ref = BareMetalHostProfileRef(name="profile")
        assert ref.to_string() == "profile"

    def test_str_method(self):
        """Test __str__ method."""
        ref = BareMetalHostProfileRef(name="profile", namespace="ns")
        assert str(ref) == "ns/profile"


class TestL2TemplateSelector:
    """Tests for L2TemplateSelector model."""

    def test_create_with_label(self):
        """Test creating selector with label."""
        selector = L2TemplateSelector(label="compute-template")
        assert selector.label == "compute-template"

    def test_from_kubernetes(self):
        """Test parsing from Kubernetes format."""
        data = {"label": "storage-template"}
        selector = L2TemplateSelector.model_validate(data)
        assert selector.label == "storage-template"

    def test_to_kubernetes(self):
        """Test converting to Kubernetes format."""
        selector = L2TemplateSelector(label="my-template")
        result = selector.to_kubernetes()
        assert result == {"label": "my-template"}


class TestMachineProviderSpec:
    """Tests for MachineProviderSpec model."""

    def test_create_minimal(self):
        """Test creating minimal provider spec with required fields."""
        spec = MachineProviderSpec(bare_metal_host_profile="worker")
        assert spec.bare_metal_host_profile.name == "worker"
        assert spec.host_selector is None
        assert spec.node_labels == []

    def test_create_full(self):
        """Test creating full provider spec."""
        from mosk_mcp.adapters.crd.machine import HostSelector

        spec = MachineProviderSpec(
            host_selector=HostSelector(match_labels={"hostId": "compute-01"}),
            bare_metal_host_profile=BareMetalHostProfileRef(name="worker"),
            l2_template_selector=L2TemplateSelector(label="compute-template"),
        )
        assert spec.host_selector.match_labels == {"hostId": "compute-01"}
        assert spec.bare_metal_host_profile.name == "worker"

    def test_from_kubernetes_format(self):
        """Test parsing from Kubernetes API format."""
        data = {
            "hostSelector": {"matchLabels": {"hostId": "node-1"}},
            "bareMetalHostProfile": {"name": "profile-1", "namespace": "lab"},
            "l2TemplateSelector": {"label": "storage-template"},
        }
        spec = MachineProviderSpec.model_validate(data)
        assert spec.host_selector.match_labels == {"hostId": "node-1"}
        assert spec.bare_metal_host_profile.name == "profile-1"
        assert spec.l2_template_selector.label == "storage-template"

    def test_bmhp_from_string_format(self):
        """Test parsing BareMetalHostProfile from legacy string format."""
        data = {"bareMetalHostProfile": "worker-profile"}
        spec = MachineProviderSpec.model_validate(data)
        assert spec.bare_metal_host_profile.name == "worker-profile"

    def test_profile_name_property(self):
        """Test profile_name property."""
        spec = MachineProviderSpec(bare_metal_host_profile="my-profile")
        assert spec.profile_name == "my-profile"

    def test_profile_namespace_property(self):
        """Test profile_namespace property."""
        spec = MachineProviderSpec(
            bare_metal_host_profile=BareMetalHostProfileRef(name="profile", namespace="lab")
        )
        assert spec.profile_namespace == "lab"

    def test_to_kubernetes(self):
        """Test converting to Kubernetes format."""
        spec = MachineProviderSpec(bare_metal_host_profile="worker")
        result = spec.to_kubernetes()
        assert result["apiVersion"] == "baremetal.k8s.io/v1alpha1"
        assert result["kind"] == "BareMetalMachineProviderSpec"
        assert result["bareMetalHostProfile"] == {"name": "worker"}


class TestMachineSpec:
    """Tests for MachineSpec model."""

    def test_create_with_provider_spec(self):
        """Test creating spec with provider spec."""
        provider = MachineProviderSpec(bare_metal_host_profile=BareMetalHostProfileRef(name="test"))
        spec = MachineSpec(provider_spec=provider)
        assert spec.provider_spec.bare_metal_host_profile.name == "test"

    def test_from_kubernetes_format(self):
        """Test parsing from Kubernetes format."""
        data = {
            "providerSpec": {
                "bareMetalHostProfile": "worker",
            }
        }
        spec = MachineSpec.model_validate(data)
        assert spec.provider_spec.bare_metal_host_profile.name == "worker"


class TestMachineStatus:
    """Tests for MachineStatus model."""

    def test_create_empty(self):
        """Test creating empty status."""
        status = MachineStatus()
        assert status.phase is None
        assert status.conditions == []
        assert status.addresses == []

    def test_create_with_phase(self):
        """Test creating status with phase."""
        status = MachineStatus(phase=MachinePhase.RUNNING)
        assert status.phase == MachinePhase.RUNNING

    def test_internal_ip_property(self):
        """Test internal_ip property."""
        from mosk_mcp.adapters.crd.machine import MachineAddress

        status = MachineStatus(
            addresses=[
                MachineAddress(type="InternalIP", address="10.0.0.1"),
                MachineAddress(type="ExternalIP", address="192.168.1.1"),
            ]
        )
        assert status.internal_ip == "10.0.0.1"

    def test_hostname_property(self):
        """Test hostname property."""
        from mosk_mcp.adapters.crd.machine import MachineAddress

        status = MachineStatus(
            addresses=[
                MachineAddress(type="Hostname", address="compute-01"),
            ]
        )
        assert status.hostname == "compute-01"

    def test_from_kubernetes_format(self):
        """Test parsing from Kubernetes API format."""
        data = {
            "phase": "Running",
            "addresses": [
                {"type": "InternalIP", "address": "10.0.0.1"},
            ],
        }
        status = MachineStatus.model_validate(data)
        assert status.phase == MachinePhase.RUNNING
        assert status.internal_ip == "10.0.0.1"


class TestMachine:
    """Tests for Machine model."""

    def test_create_with_spec(self):
        """Test creating Machine with required spec."""
        machine = Machine(
            metadata=KubernetesMetadata(name="compute-01"),
            spec=MachineSpec(provider_spec=MachineProviderSpec(bare_metal_host_profile="worker")),
        )
        assert machine.metadata.name == "compute-01"
        assert machine.kind == "Machine"
        assert machine.api_version == "cluster.k8s.io/v1alpha1"

    def test_create_full(self):
        """Test creating full Machine."""
        machine = Machine(
            metadata=KubernetesMetadata(
                name="compute-01",
                namespace="lab",
                labels={"role": "compute"},
            ),
            spec=MachineSpec(
                provider_spec=MachineProviderSpec(
                    bare_metal_host_profile=BareMetalHostProfileRef(name="worker")
                )
            ),
            status=MachineStatus(phase=MachinePhase.RUNNING),
        )
        assert machine.metadata.labels["role"] == "compute"
        assert machine.spec.provider_spec.bare_metal_host_profile.name == "worker"
        assert machine.status.phase == MachinePhase.RUNNING

    def test_from_kubernetes_dict(self):
        """Test parsing from Kubernetes API response."""

        data = {
            "apiVersion": "cluster.k8s.io/v1alpha1",
            "kind": "Machine",
            "metadata": {
                "name": "storage-01",
                "namespace": "lab",
                "labels": {
                    "cluster.sigs.k8s.io/cluster-name": "mos",
                    "kaas.mirantis.com/region": "region-one",
                },
            },
            "spec": {
                "providerSpec": {
                    "hostSelector": {"matchLabels": {"hostId": "storage-01"}},
                    "bareMetalHostProfile": {"name": "storage-profile"},
                },
            },
            "status": {
                "phase": "Provisioning",
            },
        }
        machine = Machine.model_validate(data)
        assert machine.metadata.name == "storage-01"
        assert machine.metadata.labels["cluster.sigs.k8s.io/cluster-name"] == "mos"
        assert machine.spec.provider_spec.host_selector.match_labels["hostId"] == "storage-01"
        assert machine.status.phase == MachinePhase.PROVISIONING

    def test_to_kubernetes(self):
        """Test converting to Kubernetes API format."""
        machine = Machine(
            metadata=KubernetesMetadata(
                name="compute-02",
                namespace="lab",
            ),
            spec=MachineSpec(
                provider_spec=MachineProviderSpec(
                    bare_metal_host_profile=BareMetalHostProfileRef(name="worker")
                )
            ),
        )
        result = machine.to_kubernetes()
        assert result["apiVersion"] == "cluster.k8s.io/v1alpha1"
        assert result["kind"] == "Machine"
        assert result["metadata"]["name"] == "compute-02"

    def test_role_from_labels(self):
        """Test role extraction from labels."""
        machine = Machine(
            metadata=KubernetesMetadata(
                name="compute-01",
                labels={"kaas.mirantis.com/machine-role": "compute"},
            ),
            spec=MachineSpec(provider_spec=MachineProviderSpec(bare_metal_host_profile="worker")),
        )
        # Role should be extractable from labels
        role = machine.metadata.labels.get("kaas.mirantis.com/machine-role")
        assert role == "compute"

    def test_class_constants(self):
        """Test class constants."""
        assert Machine.API_VERSION == "cluster.k8s.io/v1alpha1"
        assert Machine.KIND == "Machine"
        assert Machine.PLURAL == "machines"
        assert Machine.GROUP == "cluster.k8s.io"
