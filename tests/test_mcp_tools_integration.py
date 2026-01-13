#!/usr/bin/env python3
"""Integration tests for MOSK MCP Server tools.

These tests call the internal tool functions directly, matching how
the MCP server invokes them.

Usage:
    PYTHONPATH=src python tests/test_mcp_tools_integration.py

Prerequisites:
    - Kubeconfig files at /tmp/mcc-kubeconfig.yaml and /tmp/mosk-kubeconfig.yaml
"""

import asyncio
import os
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class RunStatus(Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


@dataclass
class RunResult:
    name: str
    status: RunStatus
    message: str
    details: dict | None = None


class MCPToolTester:
    """Test runner for MCP tools."""

    def __init__(self):
        self.results: list[RunResult] = []
        self.mosk_adapter = None
        self.mcc_adapter = None

    async def setup(self):
        """Initialize adapters."""
        from mosk_mcp.adapters.kubernetes import KubernetesAdapter

        os.environ.setdefault("MCP_MCC_KUBECONFIG_PATH", "/tmp/mcc-kubeconfig.yaml")
        os.environ.setdefault("MCP_MOSK_KUBECONFIG_PATH", "/tmp/mosk-kubeconfig.yaml")
        os.environ.setdefault("MCP_AUTH_ENABLED", "false")

        mcc_path = os.environ.get("MCP_MCC_KUBECONFIG_PATH")
        mosk_path = os.environ.get("MCP_MOSK_KUBECONFIG_PATH")

        self.mcc_adapter = KubernetesAdapter(kubeconfig_path=Path(mcc_path))
        self.mosk_adapter = KubernetesAdapter(kubeconfig_path=Path(mosk_path))

        await self.mcc_adapter.connect()
        await self.mosk_adapter.connect()

    async def teardown(self):
        """Cleanup."""
        if self.mcc_adapter:
            await self.mcc_adapter.disconnect()
        if self.mosk_adapter:
            await self.mosk_adapter.disconnect()

    def record(self, name: str, status: RunStatus, message: str, details: dict | None = None):
        """Record a test result."""
        self.results.append(RunResult(name, status, message, details))
        symbol = "✓" if status == RunStatus.PASSED else "✗" if status == RunStatus.FAILED else "○"
        print(f"  {symbol} {name}: {message}")

    # =========================================================================
    # Ceph Storage Tests (unpacked parameters)
    # =========================================================================

    async def test_get_ceph_status(self):
        """Test get_ceph_status tool."""
        from mosk_mcp.tools.ceph_operations import get_ceph_status

        try:
            result = await get_ceph_status(
                kubernetes_adapter=self.mosk_adapter,
                include_health_details=True,
                include_pg_summary=True,
            )
            if result.health:
                self.record("get_ceph_status", RunStatus.PASSED, f"Health: {result.health}")
            else:
                self.record("get_ceph_status", RunStatus.FAILED, "No health returned")
        except Exception as e:
            self.record("get_ceph_status", RunStatus.FAILED, str(e))

    async def test_list_osds(self):
        """Test list_osds tool."""
        from mosk_mcp.tools.ceph_operations import list_osds

        try:
            result = await list_osds(
                kubernetes_adapter=self.mosk_adapter,
                host_filter=None,
                status_filter=None,
                include_performance=False,
            )
            self.record("list_osds", RunStatus.PASSED, f"Found {result.total_count} OSDs")
        except Exception as e:
            self.record("list_osds", RunStatus.FAILED, str(e))

    async def test_get_osd_details(self):
        """Test get_osd_details tool."""
        from mosk_mcp.tools.ceph_operations import get_osd_details

        try:
            result = await get_osd_details(
                kubernetes_adapter=self.mosk_adapter,
                osd_id=0,
                include_pg_distribution=True,
                include_performance=True,
            )
            if result.osd:
                self.record("get_osd_details", RunStatus.PASSED, f"OSD 0 on {result.osd.host}")
            else:
                self.record("get_osd_details", RunStatus.FAILED, "No OSD data")
        except Exception as e:
            self.record("get_osd_details", RunStatus.FAILED, str(e))

    async def test_get_ceph_capacity(self):
        """Test get_ceph_capacity tool."""
        from mosk_mcp.tools.ceph_operations import get_ceph_capacity

        try:
            result = await get_ceph_capacity(
                kubernetes_adapter=self.mosk_adapter,
                include_pools=True,
                include_classes=True,
            )
            self.record("get_ceph_capacity", RunStatus.PASSED, f"{result.percent_used}% used")
        except Exception as e:
            self.record("get_ceph_capacity", RunStatus.FAILED, str(e))

    async def test_get_pg_status(self):
        """Test get_pg_status tool."""
        from mosk_mcp.tools.ceph_operations import get_pg_status

        try:
            result = await get_pg_status(
                kubernetes_adapter=self.mosk_adapter,
                include_stuck=True,
                include_recovery=True,
            )
            self.record("get_pg_status", RunStatus.PASSED, f"{result.total_pgs} PGs")
        except Exception as e:
            self.record("get_pg_status", RunStatus.FAILED, str(e))

    async def test_get_recovery_status(self):
        """Test get_recovery_status tool."""
        from mosk_mcp.tools.ceph_operations import get_recovery_status

        try:
            result = await get_recovery_status(
                kubernetes_adapter=self.mosk_adapter,
                include_osd_details=False,
                include_pg_details=False,
            )
            status = "recovering" if result.is_recovering else "stable"
            self.record("get_recovery_status", RunStatus.PASSED, f"Status: {status}")
        except Exception as e:
            self.record("get_recovery_status", RunStatus.FAILED, str(e))

    async def test_predict_capacity(self):
        """Test predict_capacity tool."""
        from mosk_mcp.tools.ceph_operations import predict_capacity

        try:
            result = await predict_capacity(
                kubernetes_adapter=self.mosk_adapter,
                days_to_forecast=30,
                growth_rate_gb_per_day=None,
                include_recommendations=True,
            )
            self.record(
                "predict_capacity",
                RunStatus.PASSED,
                f"Days to warning: {result.days_until_warning}",
            )
        except Exception as e:
            self.record("predict_capacity", RunStatus.FAILED, str(e))

    # =========================================================================
    # Machine Management Tests (input_data pattern)
    # =========================================================================

    async def test_list_machines(self):
        """Test list_machines tool."""
        from mosk_mcp.tools.node_lifecycle import ListMachinesInput, list_machines

        try:
            input_data = ListMachinesInput()
            result = await list_machines(
                k8s_adapter=self.mcc_adapter,
                input_data=input_data,
            )
            self.record("list_machines", RunStatus.PASSED, f"Found {result.total_count} machines")
        except Exception as e:
            error_msg = str(e)
            if "404" in error_msg:
                self.record("list_machines", RunStatus.SKIPPED, "Machine CRD not available")
            else:
                self.record("list_machines", RunStatus.FAILED, error_msg)

    async def test_get_node_readiness(self):
        """Test get_node_readiness tool."""
        from mosk_mcp.tools.node_lifecycle import GetNodeReadinessInput, get_node_readiness

        try:
            input_data = GetNodeReadinessInput(name="test-node")
            result = await get_node_readiness(
                k8s_adapter=self.mcc_adapter,
                input_data=input_data,
            )
            self.record("get_node_readiness", RunStatus.PASSED, f"Ready: {result.is_ready}")
        except Exception as e:
            error_msg = str(e)
            if "'GENERAL' is not a valid" in error_msg:
                self.record("get_node_readiness", RunStatus.FAILED, "Enum case bug!")
            elif "not found" in error_msg.lower() or "404" in error_msg:
                self.record(
                    "get_node_readiness", RunStatus.PASSED, "Tool works (node doesn't exist)"
                )
            else:
                self.record("get_node_readiness", RunStatus.FAILED, error_msg)

    # =========================================================================
    # OpenStack Tests (input_data pattern)
    # =========================================================================

    async def test_get_rollout_status(self):
        """Test get_rollout_status tool."""
        from mosk_mcp.tools.operations_visibility import GetRolloutStatusInput, get_rollout_status

        try:
            input_data = GetRolloutStatusInput()
            result = await get_rollout_status(
                kubernetes_adapter=self.mosk_adapter,
                input_data=input_data,
            )
            self.record(
                "get_rollout_status", RunStatus.PASSED, f"{result.total_workloads} workloads"
            )
        except Exception as e:
            self.record("get_rollout_status", RunStatus.FAILED, str(e))

    async def test_get_node_conditions(self):
        """Test get_node_conditions tool."""
        from mosk_mcp.tools.operations_visibility import GetNodeConditionsInput, get_node_conditions

        try:
            input_data = GetNodeConditionsInput()
            result = await get_node_conditions(
                kubernetes_adapter=self.mosk_adapter,
                input_data=input_data,
            )
            self.record("get_node_conditions", RunStatus.PASSED, f"{result.total_nodes} nodes")
        except Exception as e:
            self.record("get_node_conditions", RunStatus.FAILED, str(e))

    # =========================================================================
    # Cluster Health Tests (input_data pattern)
    # =========================================================================

    async def test_get_kubernetes_health(self):
        """Test get_kubernetes_health."""
        from mosk_mcp.tools.cluster_health import GetKubernetesHealthInput, get_kubernetes_health

        try:
            input_data = GetKubernetesHealthInput()
            result = await get_kubernetes_health(
                kubernetes_adapter=self.mosk_adapter,
                input_data=input_data,
            )
            self.record("get_kubernetes_health", RunStatus.PASSED, f"Health: {result.health}")
        except Exception as e:
            error_msg = str(e)
            if "has no attribute 'list_nodes'" in error_msg:
                self.record("get_kubernetes_health", RunStatus.FAILED, "Missing list_nodes!")
            else:
                self.record("get_kubernetes_health", RunStatus.FAILED, error_msg)

    async def test_get_ceph_health(self):
        """Test get_ceph_health tool."""
        from mosk_mcp.tools.cluster_health import GetCephHealthInput, get_ceph_health

        try:
            input_data = GetCephHealthInput()
            result = await get_ceph_health(
                kubernetes_adapter=self.mosk_adapter,
                input_data=input_data,
            )
            self.record("get_ceph_health", RunStatus.PASSED, f"Health: {result.health}")
        except Exception as e:
            self.record("get_ceph_health", RunStatus.FAILED, str(e))

    async def test_get_resource_utilization(self):
        """Test get_resource_utilization."""
        from mosk_mcp.tools.cluster_health import (
            GetResourceUtilizationInput,
            get_resource_utilization,
        )

        try:
            input_data = GetResourceUtilizationInput()
            await get_resource_utilization(
                kubernetes_adapter=self.mosk_adapter,
                input_data=input_data,
            )
            self.record("get_resource_utilization", RunStatus.PASSED, "Utilization retrieved")
        except Exception as e:
            error_msg = str(e)
            if "has no attribute 'list_nodes'" in error_msg:
                self.record("get_resource_utilization", RunStatus.FAILED, "Missing list_nodes!")
            else:
                self.record("get_resource_utilization", RunStatus.FAILED, error_msg)

    async def test_list_active_alerts(self):
        """Test list_active_alerts tool."""
        from mosk_mcp.tools.cluster_health import ListActiveAlertsInput, list_active_alerts

        try:
            input_data = ListActiveAlertsInput()
            result = await list_active_alerts(
                kubernetes_adapter=self.mosk_adapter,
                input_data=input_data,
            )
            self.record("list_active_alerts", RunStatus.PASSED, f"{result.total_count} alerts")
        except Exception as e:
            self.record("list_active_alerts", RunStatus.FAILED, str(e))

    # =========================================================================
    # Diagnostic Tests (unpacked parameters)
    # =========================================================================

    async def test_query_logs(self):
        """Test query_logs tool."""
        from mosk_mcp.tools.troubleshooting import query_logs

        try:
            result = await query_logs(
                kubernetes_adapter=self.mosk_adapter,
                query="nova errors",
                services=None,
                severity=None,
                hosts=None,
                time_range_minutes=60,
                keywords=None,
                project_id=None,
                request_id=None,
                limit=100,
            )
            self.record("query_logs", RunStatus.PASSED, f"{result.total_count} logs found")
        except Exception as e:
            self.record("query_logs", RunStatus.FAILED, str(e))

    async def test_get_known_issues(self):
        """Test get_known_issues tool."""
        from mosk_mcp.tools.troubleshooting import get_known_issues

        try:
            result = await get_known_issues(
                kubernetes_adapter=self.mosk_adapter,
                symptoms=None,
                error_message=None,
                service=None,
                category=None,
                include_resolved=False,
                limit=10,
            )
            self.record("get_known_issues", RunStatus.PASSED, f"{result.total_matches} issues")
        except Exception as e:
            self.record("get_known_issues", RunStatus.FAILED, str(e))

    # =========================================================================
    # Template Generation Tests (unpacked parameters, no adapter)
    # =========================================================================

    async def test_generate_bmhi(self):
        """Test generate_bmhi tool."""
        from mosk_mcp.tools.template_generation import generate_bmhi

        try:
            result = await generate_bmhi(
                hostname="test-host",
                bmc_address="ipmi://192.168.1.100",
                bmc_credentials_secret="test-secret",
                boot_mac_address="aa:bb:cc:dd:ee:ff",
            )
            if result.template and result.template.content:
                self.record("generate_bmhi", RunStatus.PASSED, "Template generated")
            else:
                self.record("generate_bmhi", RunStatus.FAILED, "No template content")
        except Exception as e:
            self.record("generate_bmhi", RunStatus.FAILED, str(e))

    async def test_generate_machine(self):
        """Test generate_machine tool."""
        from mosk_mcp.tools.template_generation import generate_machine

        try:
            result = await generate_machine(
                name="test-machine",
                role="compute",
                bmhp_ref="test-profile",
            )
            if result.template and result.template.content:
                self.record("generate_machine", RunStatus.PASSED, "Template generated")
            else:
                self.record("generate_machine", RunStatus.FAILED, "No template content")
        except Exception as e:
            self.record("generate_machine", RunStatus.FAILED, str(e))

    async def test_validate_template(self):
        """Test validate_template tool."""
        from mosk_mcp.tools.template_generation import validate_template

        try:
            result = await validate_template(
                template_yaml="""apiVersion: kaas.mirantis.com/v1alpha1
kind: Machine
metadata:
  name: test
  namespace: default
spec: {}""",
            )
            status = "valid" if result.valid else "invalid"
            self.record("validate_template", RunStatus.PASSED, f"Template {status}")
        except Exception as e:
            self.record("validate_template", RunStatus.FAILED, str(e))

    # =========================================================================
    # Run All Tests
    # =========================================================================

    async def run_all(self):
        """Run all tests."""
        print("\n" + "=" * 60)
        print("MOSK MCP Server - Integration Tests")
        print("=" * 60)

        await self.setup()

        test_groups = [
            (
                "Ceph Storage",
                [
                    self.test_get_ceph_status,
                    self.test_list_osds,
                    self.test_get_osd_details,
                    self.test_get_ceph_capacity,
                    self.test_get_pg_status,
                    self.test_get_recovery_status,
                    self.test_predict_capacity,
                ],
            ),
            (
                "Machine Management (MCC)",
                [
                    self.test_list_machines,
                    self.test_get_node_readiness,
                ],
            ),
            (
                "OpenStack Visibility",
                [
                    self.test_get_rollout_status,
                    self.test_get_node_conditions,
                ],
            ),
            (
                "Cluster Health",
                [
                    self.test_get_kubernetes_health,
                    self.test_get_ceph_health,
                    self.test_get_resource_utilization,
                    self.test_list_active_alerts,
                ],
            ),
            (
                "Diagnostics",
                [
                    self.test_query_logs,
                    self.test_get_known_issues,
                ],
            ),
            (
                "Template Generation",
                [
                    self.test_generate_bmhi,
                    self.test_generate_machine,
                    self.test_validate_template,
                ],
            ),
        ]

        for group_name, tests in test_groups:
            print(f"\n{group_name}")
            print("-" * 40)
            for test in tests:
                try:
                    await test()
                except Exception as e:
                    self.record(test.__name__, RunStatus.FAILED, f"Unexpected: {e}")

        await self.teardown()

        # Summary
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)

        passed = sum(1 for r in self.results if r.status == RunStatus.PASSED)
        failed = sum(1 for r in self.results if r.status == RunStatus.FAILED)
        skipped = sum(1 for r in self.results if r.status == RunStatus.SKIPPED)

        print(f"  Passed:  {passed}")
        print(f"  Failed:  {failed}")
        print(f"  Skipped: {skipped}")
        print(f"  Total:   {len(self.results)}")

        if failed > 0:
            print("\nFailed Tests:")
            for r in self.results:
                if r.status == RunStatus.FAILED:
                    print(f"  - {r.name}: {r.message}")

        return failed == 0


async def main():
    """Main entry point."""
    tester = MCPToolTester()
    success = await tester.run_all()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
