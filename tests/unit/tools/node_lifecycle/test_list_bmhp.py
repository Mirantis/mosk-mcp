"""Unit tests for list_bmhp tool."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from mosk_mcp.tools.node_lifecycle.list_bmhp import (
    BMHPSummary,
    ListBMHPInput,
    ListBMHPOutput,
    _extract_bmhp_summary,
    list_bmhp,
)


# Sample BMHP data for testing (based on real MOSK cluster data)
SAMPLE_BMHP_DEFAULT = {
    "apiVersion": "metal3.io/v1alpha1",
    "kind": "BareMetalHostProfile",
    "metadata": {
        "name": "default",
        "namespace": "default",
        "creationTimestamp": "2024-01-15T10:00:00Z",
        "labels": {
            "kaas.mirantis.com/default": "true",
            "kaas.mirantis.com/provider": "baremetal",
        },
    },
    "spec": {
        "devices": [
            {
                "device": {
                    "wipe": True,
                    "workBy": "by-path",
                },
                "partitions": [
                    {"name": "bios_grub", "partflags": ["bios_grub"], "size": "4 MiB"},
                    {"name": "uefi", "partflags": ["esp"], "size": "200 MiB"},
                    {"name": "config-2", "size": "64 MiB"},
                    {"name": "lvm_root_part", "size": "0"},
                ],
            }
        ],
        "fileSystems": [
            {"filesystem": "vfat", "mount": "/boot/efi", "partition": "uefi"},
        ],
        "grubConfig": {
            "defaultGrubOptions": ["console=tty0", "console=ttyS1,115200n8"],
        },
        "kernelParameters": {
            "sysctl": {
                "kernel.core_uses_pid": "1",
                "net.ipv4.tcp_syncookies": "1",
            }
        },
        "logicalVolumes": [
            {"logicalVolume": "root", "size": "50 GiB", "volumeGroup": "lvm_root"},
        ],
        "preDeployScript": "#!/bin/bash\necho 'Pre-deploy'",
        "postDeployScript": "#!/bin/bash\necho 'Post-deploy'",
        "softRaidDevices": [],
        "volumeGroups": [
            {"name": "lvm_root", "devices": ["lvm_root_part"]},
        ],
    },
}


SAMPLE_BMHP_COMPUTE = {
    "apiVersion": "metal3.io/v1alpha1",
    "kind": "BareMetalHostProfile",
    "metadata": {
        "name": "compute-profile",
        "namespace": "default",
        "creationTimestamp": "2024-01-15T11:00:00Z",
        "labels": {
            "role": "compute",
            "kaas.mirantis.com/provider": "baremetal",
        },
    },
    "spec": {
        "devices": [
            {
                "device": {
                    "wipe": True,
                    "workBy": "by-path",
                },
            }
        ],
        "hardwareProfile": {
            "rootDeviceHints": {
                "minSizeGigabytes": 100,
                "deviceType": "SSD",
            },
        },
        "kernelParameters": ["hugepages=1024", "intel_iommu=on"],
    },
}


SAMPLE_BMHP_STORAGE = {
    "apiVersion": "metal3.io/v1alpha1",
    "kind": "BareMetalHostProfile",
    "metadata": {
        "name": "storage-profile",
        "namespace": "default",
        "creationTimestamp": "2024-01-15T12:00:00Z",
        "labels": {
            "role": "storage",
        },
    },
    "spec": {
        "devices": [],
        "kernelParameters": [],
    },
}


class TestExtractBMHPSummary:
    """Tests for _extract_bmhp_summary function."""

    def test_extract_default_profile(self):
        """Test extracting summary from default profile."""
        summary = _extract_bmhp_summary(SAMPLE_BMHP_DEFAULT)

        assert summary.name == "default"
        assert summary.namespace == "default"
        assert summary.is_default is True
        assert summary.has_pre_deploy_script is True
        assert summary.has_post_deploy_script is True
        assert isinstance(summary.kernel_parameters, dict)
        assert "sysctl" in summary.kernel_parameters
        assert summary.labels["kaas.mirantis.com/default"] == "true"

    def test_extract_compute_profile(self):
        """Test extracting summary from compute profile."""
        summary = _extract_bmhp_summary(SAMPLE_BMHP_COMPUTE)

        assert summary.name == "compute-profile"
        assert summary.is_default is False
        assert summary.has_root_device_hints is True
        assert summary.has_pre_deploy_script is False
        assert summary.has_post_deploy_script is False
        assert isinstance(summary.kernel_parameters, list)
        assert "hugepages=1024" in summary.kernel_parameters

    def test_extract_storage_profile(self):
        """Test extracting summary from storage profile."""
        summary = _extract_bmhp_summary(SAMPLE_BMHP_STORAGE)

        assert summary.name == "storage-profile"
        assert summary.is_default is False
        assert summary.has_root_device_hints is False
        assert summary.has_pre_deploy_script is False
        assert summary.has_post_deploy_script is False
        assert summary.labels.get("role") == "storage"

    def test_extract_minimal_profile(self):
        """Test extracting summary from minimal profile data."""
        minimal_profile = {
            "metadata": {"name": "minimal-profile"},
            "spec": {},
        }
        summary = _extract_bmhp_summary(minimal_profile)

        assert summary.name == "minimal-profile"
        assert summary.namespace == "default"
        assert summary.is_default is False
        assert summary.has_root_device_hints is False


class TestListBMHP:
    """Tests for list_bmhp function."""

    @pytest.fixture
    def mock_k8s_adapter(self):
        """Create a mock Kubernetes adapter."""
        adapter = MagicMock()
        adapter.list_custom_resources = AsyncMock(
            return_value=[
                SAMPLE_BMHP_DEFAULT,
                SAMPLE_BMHP_COMPUTE,
                SAMPLE_BMHP_STORAGE,
            ]
        )
        return adapter

    @pytest.mark.asyncio
    async def test_list_bmhp_default(self, mock_k8s_adapter):
        """Test listing BMHPs with default parameters."""
        input_data = ListBMHPInput()

        result = await list_bmhp(mock_k8s_adapter, input_data)

        assert isinstance(result, ListBMHPOutput)
        assert result.total_count == 3
        assert result.namespace == "default"
        assert len(result.profiles) == 3

    @pytest.mark.asyncio
    async def test_list_bmhp_finds_default_profile(self, mock_k8s_adapter):
        """Test that default profile is identified."""
        input_data = ListBMHPInput()

        result = await list_bmhp(mock_k8s_adapter, input_data)

        assert result.default_profile == "default"

    @pytest.mark.asyncio
    async def test_list_bmhp_with_limit(self, mock_k8s_adapter):
        """Test listing BMHPs with limit."""
        input_data = ListBMHPInput(limit=2)

        result = await list_bmhp(mock_k8s_adapter, input_data)

        assert len(result.profiles) <= 2

    @pytest.mark.asyncio
    async def test_list_bmhp_all_namespaces(self, mock_k8s_adapter):
        """Test listing BMHPs in all namespaces."""
        input_data = ListBMHPInput(namespace="*")

        await list_bmhp(mock_k8s_adapter, input_data)

        mock_k8s_adapter.list_custom_resources.assert_called_once()
        call_args = mock_k8s_adapter.list_custom_resources.call_args
        assert call_args.kwargs["namespace"] is None

    @pytest.mark.asyncio
    async def test_list_bmhp_with_label_selector(self, mock_k8s_adapter):
        """Test listing BMHPs with label selector."""
        input_data = ListBMHPInput(label_selector="role=compute")

        await list_bmhp(mock_k8s_adapter, input_data)

        mock_k8s_adapter.list_custom_resources.assert_called_once()
        call_args = mock_k8s_adapter.list_custom_resources.call_args
        assert call_args.kwargs["label_selector"] == "role=compute"

    @pytest.mark.asyncio
    async def test_list_bmhp_correct_api_group(self, mock_k8s_adapter):
        """Test that list_bmhp uses correct API group."""
        input_data = ListBMHPInput()

        await list_bmhp(mock_k8s_adapter, input_data)

        mock_k8s_adapter.list_custom_resources.assert_called_once()
        call_args = mock_k8s_adapter.list_custom_resources.call_args
        assert call_args.kwargs["group"] == "metal3.io"
        assert call_args.kwargs["version"] == "v1alpha1"
        assert call_args.kwargs["plural"] == "baremetalhostprofiles"

    @pytest.mark.asyncio
    async def test_list_bmhp_empty_result(self):
        """Test listing BMHPs when none exist."""
        mock_adapter = MagicMock()
        mock_adapter.list_custom_resources = AsyncMock(return_value=[])

        input_data = ListBMHPInput()

        result = await list_bmhp(mock_adapter, input_data)

        assert result.total_count == 0
        assert result.profiles == []
        assert result.default_profile is None

    @pytest.mark.asyncio
    async def test_list_bmhp_no_default_profile(self):
        """Test listing BMHPs when no default profile exists."""
        mock_adapter = MagicMock()
        mock_adapter.list_custom_resources = AsyncMock(
            return_value=[SAMPLE_BMHP_COMPUTE, SAMPLE_BMHP_STORAGE]
        )

        input_data = ListBMHPInput()

        result = await list_bmhp(mock_adapter, input_data)

        assert result.default_profile is None

    @pytest.mark.asyncio
    async def test_list_bmhp_error_handling(self):
        """Test error handling when Kubernetes API fails."""
        mock_adapter = MagicMock()
        mock_adapter.list_custom_resources = AsyncMock(
            side_effect=Exception("API connection failed")
        )

        input_data = ListBMHPInput()

        with pytest.raises(Exception) as exc_info:
            await list_bmhp(mock_adapter, input_data)

        assert "BareMetalHostProfiles" in str(exc_info.value) or "API" in str(exc_info.value)


class TestListBMHPInput:
    """Tests for ListBMHPInput validation."""

    def test_default_values(self):
        """Test default input values."""
        input_data = ListBMHPInput()

        assert input_data.namespace == "default"
        assert input_data.label_selector is None
        assert input_data.limit == 50

    def test_custom_namespace(self):
        """Test custom namespace input."""
        input_data = ListBMHPInput(namespace="lab")

        assert input_data.namespace == "lab"

    def test_limit_validation(self):
        """Test limit validation."""
        # Valid limit
        input_data = ListBMHPInput(limit=25)
        assert input_data.limit == 25

        # Invalid limit (too low)
        with pytest.raises(ValueError):
            ListBMHPInput(limit=0)

        # Invalid limit (too high)
        with pytest.raises(ValueError):
            ListBMHPInput(limit=201)


class TestBMHPSummary:
    """Tests for BMHPSummary model."""

    def test_required_fields(self):
        """Test that required fields are validated."""
        summary = BMHPSummary(
            name="test-profile",
            namespace="default",
        )

        assert summary.name == "test-profile"
        assert summary.namespace == "default"

    def test_optional_fields_defaults(self):
        """Test optional fields have correct defaults."""
        summary = BMHPSummary(
            name="test-profile",
            namespace="default",
        )

        assert summary.is_default is False
        assert summary.has_root_device_hints is False
        assert summary.kernel_parameters == []
        assert summary.has_pre_deploy_script is False
        assert summary.has_post_deploy_script is False
        assert summary.labels == {}
        assert summary.age_seconds is None

    def test_kernel_parameters_as_list(self):
        """Test kernel parameters as list."""
        summary = BMHPSummary(
            name="test-profile",
            namespace="default",
            kernel_parameters=["hugepages=1024", "intel_iommu=on"],
        )

        assert isinstance(summary.kernel_parameters, list)
        assert len(summary.kernel_parameters) == 2

    def test_kernel_parameters_as_dict(self):
        """Test kernel parameters as dict (sysctl format)."""
        summary = BMHPSummary(
            name="test-profile",
            namespace="default",
            kernel_parameters={"sysctl": {"net.ipv4.ip_forward": "1"}},
        )

        assert isinstance(summary.kernel_parameters, dict)
        assert "sysctl" in summary.kernel_parameters
