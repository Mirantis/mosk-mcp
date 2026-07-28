"""Unit tests for kubectl cluster name resolution."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from mosk_mcp.core.exceptions import ValidationError
from mosk_mcp.tools.kubectl.cluster_resolver import (
    MANAGEMENT_CLUSTER_NAME,
    resolve_adapter_for_cluster_name,
)


@pytest.fixture
def mock_context() -> MagicMock:
    """Create a mock SSOServerContext with session."""
    context = MagicMock()
    context.session.mosk_cluster_name = "mos"
    context.get_mosk_adapter = AsyncMock(return_value=MagicMock(name="mosk_adapter"))
    context.get_mcc_adapter = AsyncMock(return_value=MagicMock(name="mcc_adapter"))
    return context


class TestResolveAdapterForClusterName:
    """Tests for resolve_adapter_for_cluster_name."""

    @pytest.mark.asyncio
    async def test_workload_cluster_resolves_to_mosk(
        self, mock_context: MagicMock
    ) -> None:
        adapter = await resolve_adapter_for_cluster_name("mos", mock_context)

        assert adapter is mock_context.get_mosk_adapter.return_value
        mock_context.get_mosk_adapter.assert_awaited_once()
        mock_context.get_mcc_adapter.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_management_cluster_resolves_to_mcc(
        self, mock_context: MagicMock
    ) -> None:
        adapter = await resolve_adapter_for_cluster_name(
            MANAGEMENT_CLUSTER_NAME, mock_context
        )

        assert adapter is mock_context.get_mcc_adapter.return_value
        mock_context.get_mcc_adapter.assert_awaited_once()
        mock_context.get_mosk_adapter.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_cluster_raises(self, mock_context: MagicMock) -> None:
        with pytest.raises(ValidationError, match="Unknown cluster 'other'"):
            await resolve_adapter_for_cluster_name("other", mock_context)

    @pytest.mark.asyncio
    async def test_empty_cluster_name_raises(self, mock_context: MagicMock) -> None:
        with pytest.raises(ValidationError, match="cluster cannot be empty"):
            await resolve_adapter_for_cluster_name("", mock_context)

    @pytest.mark.asyncio
    async def test_unknown_with_no_mosk_configured(self) -> None:
        context = MagicMock()
        context.session.mosk_cluster_name = None
        context.get_mosk_adapter = AsyncMock()
        context.get_mcc_adapter = AsyncMock()

        with pytest.raises(ValidationError, match=MANAGEMENT_CLUSTER_NAME):
            await resolve_adapter_for_cluster_name("unknown", context)
