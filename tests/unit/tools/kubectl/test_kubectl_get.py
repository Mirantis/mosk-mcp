"""Unit tests for kubectl_get tool."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.core.exceptions import ToolExecutionError
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


class TestKubectlGetNamespaceRequired:
    """Tests for namespace requirements with named gets."""

    @pytest.mark.asyncio
    @patch("mosk_mcp.tools.kubectl.kubectl_get.kr8s.asyncio.get")
    async def test_named_namespaced_resource_requires_namespace(
        self,
        mock_get: MagicMock,
        mock_adapter: MagicMock,
    ) -> None:
        input_data = KubectlGetInput(
            cluster="mos",
            resource_type="pods",
            name="my-pod",
        )
        with pytest.raises(ToolExecutionError, match="namespace is required"):
            await kubectl_get(mock_adapter, input_data)

        mock_get.assert_not_called()


class TestKubectlGetJqFilter:
    """Tests for kubectl_get jq filtering."""

    @pytest.mark.asyncio
    @patch("mosk_mcp.tools.kubectl.kubectl_get.kr8s.asyncio.get")
    async def test_jq_filter_on_list(
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
            jq_filter=".items[].metadata.name",
        )
        result = await kubectl_get(mock_adapter, input_data)

        assert result.data == ["pod-a", "pod-b"]
        assert result.count == 2

    @pytest.mark.asyncio
    @patch("mosk_mcp.tools.kubectl.kubectl_get.kr8s.asyncio.get")
    async def test_jq_filter_on_single_resource(
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
            namespace="openstack",
            name="my-pod",
            jq_filter=".status.phase",
        )
        result = await kubectl_get(mock_adapter, input_data)

        assert result.data == "Running"
        assert result.count == 1

    @pytest.mark.asyncio
    @patch("mosk_mcp.tools.kubectl.kubectl_get.kr8s.asyncio.get")
    async def test_invalid_jq_filter_fails_before_fetch(
        self,
        mock_get: MagicMock,
        mock_adapter: MagicMock,
    ) -> None:
        input_data = KubectlGetInput(
            cluster="mos",
            resource_type="pods",
            jq_filter=".[",
        )
        with pytest.raises(ToolExecutionError, match="Invalid jq filter"):
            await kubectl_get(mock_adapter, input_data)

        mock_get.assert_not_called()
        mock_adapter.api.lookup_kind.assert_not_called()


class TestKubectlGetSecretSafetyTier:
    """Secret payload values are redacted outside the development safety tier."""

    @pytest.mark.asyncio
    @patch("mosk_mcp.tools.kubectl.kubectl_get.kr8s.asyncio.get")
    @patch("mosk_mcp.tools.kubectl.kubectl_get._resolve_safety_tier", new_callable=AsyncMock)
    async def test_secret_values_visible_on_development(
        self,
        mock_tier: AsyncMock,
        mock_get: MagicMock,
        mock_adapter: MagicMock,
    ) -> None:
        mock_tier.return_value = "development"
        mock_adapter.api.lookup_kind.return_value = ("Secret", "secrets", True)
        mock_get.return_value = _async_gen(
            [
                _make_kr8s_object(
                    {
                        "apiVersion": "v1",
                        "kind": "Secret",
                        "metadata": {"name": "my-secret", "namespace": "default"},
                        "data": {"password": "c2VjcmV0"},
                        "stringData": {"token": "plain-token"},
                    }
                ),
            ]
        )

        input_data = KubectlGetInput(
            cluster="mos",
            resource_type="secrets",
            namespace="default",
            name="my-secret",
        )
        result = await kubectl_get(mock_adapter, input_data)

        mock_tier.assert_awaited_once_with("mos")
        assert result.data["data"]["password"] == "c2VjcmV0"
        assert result.data["stringData"]["token"] == "plain-token"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tier", ["staging", "production"])
    @patch("mosk_mcp.tools.kubectl.kubectl_get.kr8s.asyncio.get")
    @patch("mosk_mcp.tools.kubectl.kubectl_get._resolve_safety_tier", new_callable=AsyncMock)
    async def test_secret_values_redacted_outside_development(
        self,
        mock_tier: AsyncMock,
        mock_get: MagicMock,
        mock_adapter: MagicMock,
        tier: str,
    ) -> None:
        from mosk_mcp.tools.kubectl.kubectl_get import SECRET_VALUE_REDACTED

        mock_tier.return_value = tier
        mock_adapter.api.lookup_kind.return_value = ("Secret", "secrets", True)
        mock_get.return_value = _async_gen(
            [
                _make_kr8s_object(
                    {
                        "apiVersion": "v1",
                        "kind": "Secret",
                        "metadata": {"name": "my-secret", "namespace": "default"},
                        "data": {"password": "c2VjcmV0"},
                        "stringData": {"token": "plain-token"},
                        "binaryData": {"blob": "YmluYXJ5"},
                    }
                ),
            ]
        )

        input_data = KubectlGetInput(
            cluster="mos",
            resource_type="secrets",
            namespace="default",
            name="my-secret",
        )
        result = await kubectl_get(mock_adapter, input_data)

        mock_get.assert_called_once()
        mock_tier.assert_awaited_once_with("mos")
        assert result.data["metadata"]["name"] == "my-secret"
        assert result.data["data"] == {"password": SECRET_VALUE_REDACTED}
        assert result.data["stringData"] == {"token": SECRET_VALUE_REDACTED}
        assert result.data["binaryData"] == {"blob": SECRET_VALUE_REDACTED}

    @pytest.mark.asyncio
    @patch("mosk_mcp.tools.kubectl.kubectl_get.kr8s.asyncio.get")
    @patch("mosk_mcp.tools.kubectl.kubectl_get._resolve_safety_tier", new_callable=AsyncMock)
    async def test_secret_list_values_redacted(
        self,
        mock_tier: AsyncMock,
        mock_get: MagicMock,
        mock_adapter: MagicMock,
    ) -> None:
        from mosk_mcp.tools.kubectl.kubectl_get import SECRET_VALUE_REDACTED

        mock_tier.return_value = "production"
        mock_adapter.api.lookup_kind.return_value = ("Secret", "secrets", True)
        mock_get.return_value = _async_gen(
            [
                _make_kr8s_object(
                    {
                        "apiVersion": "v1",
                        "kind": "Secret",
                        "metadata": {"name": "secret-a"},
                        "data": {"key": "dmFsdWUtYQ=="},
                    }
                ),
                _make_kr8s_object(
                    {
                        "apiVersion": "v1",
                        "kind": "Secret",
                        "metadata": {"name": "secret-b"},
                        "data": {"key": "dmFsdWUtYg=="},
                    }
                ),
            ]
        )

        input_data = KubectlGetInput(cluster="mos", resource_type="secrets")
        result = await kubectl_get(mock_adapter, input_data)

        assert result.count == 2
        assert result.data["items"][0]["data"] == {"key": SECRET_VALUE_REDACTED}
        assert result.data["items"][1]["data"] == {"key": SECRET_VALUE_REDACTED}

    @pytest.mark.asyncio
    @patch("mosk_mcp.tools.kubectl.kubectl_get.kr8s.asyncio.get")
    @patch("mosk_mcp.tools.kubectl.kubectl_get._resolve_safety_tier", new_callable=AsyncMock)
    async def test_redaction_happens_before_jq_filter(
        self,
        mock_tier: AsyncMock,
        mock_get: MagicMock,
        mock_adapter: MagicMock,
    ) -> None:
        from mosk_mcp.tools.kubectl.kubectl_get import SECRET_VALUE_REDACTED

        mock_tier.return_value = "staging"
        mock_adapter.api.lookup_kind.return_value = ("Secret", "secrets", True)
        mock_get.return_value = _async_gen(
            [
                _make_kr8s_object(
                    {
                        "apiVersion": "v1",
                        "kind": "Secret",
                        "metadata": {"name": "my-secret", "namespace": "default"},
                        "data": {"password": "c2VjcmV0"},
                    }
                ),
            ]
        )

        input_data = KubectlGetInput(
            cluster="mos",
            resource_type="secrets",
            namespace="default",
            name="my-secret",
            jq_filter=".data.password",
        )
        result = await kubectl_get(mock_adapter, input_data)

        assert result.data == SECRET_VALUE_REDACTED

    @pytest.mark.asyncio
    @patch("mosk_mcp.tools.kubectl.kubectl_get.kr8s.asyncio.get")
    @patch("mosk_mcp.tools.kubectl.kubectl_get._resolve_safety_tier", new_callable=AsyncMock)
    async def test_non_secret_skips_tier_check(
        self,
        mock_tier: AsyncMock,
        mock_get: MagicMock,
        mock_adapter: MagicMock,
    ) -> None:
        mock_get.return_value = _async_gen(
            [
                _make_kr8s_object(
                    {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "pod-1"}}
                ),
            ]
        )

        input_data = KubectlGetInput(cluster="mos", resource_type="pods")
        result = await kubectl_get(mock_adapter, input_data)

        mock_tier.assert_not_awaited()
        assert result.count == 1


class TestResolveSafetyTier:
    """Tests for safety tier resolution used by Secret redaction."""

    @pytest.mark.asyncio
    async def test_uses_requested_cluster_environment(self) -> None:
        from mosk_mcp.cluster.config import ClusterEnvironment
        from mosk_mcp.tools.kubectl.kubectl_get import _resolve_safety_tier

        lab = MagicMock()
        lab.environment = ClusterEnvironment.DEVELOPMENT
        prod = MagicMock()
        prod.environment = ClusterEnvironment.PRODUCTION
        config = MagicMock()
        config.clusters = {"lab": lab, "production-us": prod}
        manager = MagicMock()
        manager.get_config = AsyncMock(return_value=config)

        with patch(
            "mosk_mcp.cluster.manager.get_cluster_manager",
            return_value=manager,
        ):
            assert await _resolve_safety_tier("production-us") == "production"
            assert await _resolve_safety_tier("lab") == "development"

    @pytest.mark.asyncio
    async def test_falls_back_to_settings_when_cluster_unknown(self) -> None:
        from mosk_mcp.core.config import Environment
        from mosk_mcp.tools.kubectl.kubectl_get import _resolve_safety_tier

        settings = MagicMock()
        settings.environment = Environment.PRODUCTION
        config = MagicMock()
        config.clusters = {}
        manager = MagicMock()
        manager.get_config = AsyncMock(return_value=config)

        with (
            patch(
                "mosk_mcp.cluster.manager.get_cluster_manager",
                return_value=manager,
            ),
            patch(
                "mosk_mcp.core.config.get_settings",
                return_value=settings,
            ),
        ):
            assert await _resolve_safety_tier("mos") == "production"
