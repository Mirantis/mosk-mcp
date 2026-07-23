"""Unit tests for kubectl_get tool."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.tools.kubectl.kubectl_get import kubectl_get
from mosk_mcp.tools.kubectl.models import KubectlGetInput


def _make_kr8s_object(raw: dict) -> MagicMock:
    obj = MagicMock()
    obj.raw = raw
    return obj


async def _async_gen(items: list[MagicMock]):
    for item in items:
        yield item


@pytest.fixture
def mock_adapter() -> MagicMock:
    """Create a mock KubernetesAdapter with API client."""
    adapter = MagicMock()
    adapter.api = MagicMock(name="kr8s_api")
    adapter.api.lookup_kind = AsyncMock(return_value=("Pod", "pods", True))
    return adapter


class TestKubectlGetStandardResources:
    """Tests for kubectl_get with standard Kubernetes resources."""

    @pytest.mark.asyncio
    @patch("mosk_mcp.tools.kubectl.kubectl_get.kr8s.asyncio.get")
    async def test_list_pods_all_namespaces(
        self,
        mock_get: MagicMock,
        mock_adapter: MagicMock,
    ) -> None:
        mock_get.return_value = _async_gen(
            [
                _make_kr8s_object(
                    {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "pod-1"}}
                ),
                _make_kr8s_object(
                    {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "pod-2"}}
                ),
            ]
        )

        input_data = KubectlGetInput(cluster="mos", resource_type="pods")
        result = await kubectl_get(mock_adapter, input_data)

        mock_adapter.api.lookup_kind.assert_awaited_once_with("pods")
        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args.kwargs
        assert call_kwargs["api"] is mock_adapter.api
        assert result.count == 2
        assert result.kind == "Pod"
        assert result.data["kind"] == "PodList"
        assert len(result.data["items"]) == 2

    @pytest.mark.asyncio
    @patch("mosk_mcp.tools.kubectl.kubectl_get.kr8s.asyncio.get")
    async def test_get_single_pod(
        self,
        mock_get: MagicMock,
        mock_adapter: MagicMock,
    ) -> None:
        mock_get.return_value = _async_gen(
            [
                _make_kr8s_object(
                    {
                        "apiVersion": "v1",
                        "kind": "Pod",
                        "metadata": {"name": "my-pod", "namespace": "openstack"},
                    }
                ),
            ]
        )

        input_data = KubectlGetInput(
            cluster="mos",
            resource_type="pods",
            namespace="openstack",
            name="my-pod",
        )
        result = await kubectl_get(mock_adapter, input_data)

        mock_get.assert_called_once_with(
            "pods",
            "my-pod",
            api=mock_adapter.api,
            namespace="openstack",
        )
        assert result.count == 1
        assert result.data["metadata"]["name"] == "my-pod"

    @pytest.mark.asyncio
    @patch("mosk_mcp.tools.kubectl.kubectl_get.kr8s.asyncio.get")
    async def test_list_with_label_selector(
        self,
        mock_get: MagicMock,
        mock_adapter: MagicMock,
    ) -> None:
        mock_get.return_value = _async_gen([])

        input_data = KubectlGetInput(
            cluster="mos",
            resource_type="pods",
            namespace="openstack",
            label_selector="app=nova-api",
        )
        await kubectl_get(mock_adapter, input_data)

        call_kwargs = mock_get.call_args.kwargs
        assert call_kwargs["label_selector"] == "app=nova-api"
        assert call_kwargs["namespace"] == "openstack"


class TestKubectlGetCrdResources:
    """Tests for kubectl_get with custom resources via kr8s discovery."""

    @pytest.mark.asyncio
    @patch("mosk_mcp.tools.kubectl.kubectl_get.kr8s.asyncio.get")
    async def test_list_machines(
        self,
        mock_get: MagicMock,
        mock_adapter: MagicMock,
    ) -> None:
        mock_adapter.api.lookup_kind.return_value = ("Machine", "machines", True)
        mock_get.return_value = _async_gen(
            [
                _make_kr8s_object(
                    {
                        "apiVersion": "cluster.k8s.io/v1alpha1",
                        "kind": "Machine",
                        "metadata": {"name": "node-1"},
                    }
                ),
            ]
        )

        input_data = KubectlGetInput(
            cluster="kaas-mgmt",
            resource_type="machines",
            namespace="default",
        )
        result = await kubectl_get(mock_adapter, input_data)

        mock_get.assert_called_once_with(
            "machines",
            api=mock_adapter.api,
            namespace="default",
        )
        assert result.count == 1
        assert result.api_version == "cluster.k8s.io/v1alpha1"


class TestKubectlGetJsonpath:
    """Tests for kubectl_get jsonpath filtering."""

    @pytest.mark.asyncio
    @patch("mosk_mcp.tools.kubectl.kubectl_get.kr8s.asyncio.get")
    async def test_jsonpath_on_list(
        self,
        mock_get: MagicMock,
        mock_adapter: MagicMock,
    ) -> None:
        mock_get.return_value = _async_gen(
            [
                _make_kr8s_object({"metadata": {"name": "pod-a"}}),
                _make_kr8s_object({"metadata": {"name": "pod-b"}}),
            ]
        )

        input_data = KubectlGetInput(
            cluster="mos",
            resource_type="pods",
            jsonpath="{.items[*].metadata.name}",
        )
        result = await kubectl_get(mock_adapter, input_data)

        assert result.data == ["pod-a", "pod-b"]
        assert result.count == 2

    @pytest.mark.asyncio
    @patch("mosk_mcp.tools.kubectl.kubectl_get.kr8s.asyncio.get")
    async def test_jsonpath_on_single_resource(
        self,
        mock_get: MagicMock,
        mock_adapter: MagicMock,
    ) -> None:
        mock_get.return_value = _async_gen(
            [
                _make_kr8s_object(
                    {
                        "metadata": {"name": "my-pod"},
                        "status": {"phase": "Running"},
                    }
                ),
            ]
        )

        input_data = KubectlGetInput(
            cluster="mos",
            resource_type="pods",
            name="my-pod",
            jsonpath="{.status.phase}",
        )
        result = await kubectl_get(mock_adapter, input_data)

        assert result.data == "Running"
        assert result.count == 1
