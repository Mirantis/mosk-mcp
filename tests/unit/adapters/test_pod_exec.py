"""Unit tests for mosk_mcp.adapters.utils.pod_exec module.

Tests pod execution utility including K8s name validation,
error handling, and kr8s-based pod exec functionality.
"""

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosk_mcp.adapters.utils.pod_exec import (
    PodExecError,
    PodExecResult,
    execute_in_pod,
)
from mosk_mcp.core.exceptions import ToolExecutionError, ValidationError
from mosk_mcp.core.validation import validate_kubernetes_name


# =============================================================================
# K8s Name Validation Tests
# =============================================================================


class TestValidateK8sName:
    """Tests for validate_kubernetes_name function (from core.validation)."""

    def test_valid_simple_name(self) -> None:
        """Test valid simple names."""
        validate_kubernetes_name("my-pod", "pod_name")
        validate_kubernetes_name("namespace", "namespace")
        validate_kubernetes_name("a", "name")  # Single char
        validate_kubernetes_name("ceph-tools-0", "pod_name")

    def test_valid_names_with_dots(self) -> None:
        """Test valid names with dots."""
        validate_kubernetes_name("my.pod.name", "pod_name")
        validate_kubernetes_name("pod.example.com", "pod_name")

    def test_valid_names_with_numbers(self) -> None:
        """Test valid names with numbers."""
        validate_kubernetes_name("pod123", "pod_name")
        validate_kubernetes_name("123pod", "pod_name")
        validate_kubernetes_name("pod-123-test", "pod_name")

    def test_empty_name_raises(self) -> None:
        """Test that empty name raises ValidationError."""
        with pytest.raises(ValidationError, match="cannot be empty"):
            validate_kubernetes_name("", "pod_name")

    def test_too_long_name_raises(self) -> None:
        """Test that name exceeding 253 chars raises ValidationError."""
        long_name = "a" * 254
        with pytest.raises(ValidationError, match="must be at most"):
            validate_kubernetes_name(long_name, "pod_name")

    def test_uppercase_name_invalid(self) -> None:
        """Test that uppercase names are invalid."""
        with pytest.raises(ValidationError, match="DNS-1123"):
            validate_kubernetes_name("MyPod", "pod_name")

    def test_underscore_invalid(self) -> None:
        """Test that underscores are invalid."""
        with pytest.raises(ValidationError, match="DNS-1123"):
            validate_kubernetes_name("my_pod", "pod_name")

    def test_special_chars_invalid(self) -> None:
        """Test that special characters are rejected."""
        invalid_names = [
            "pod;rm -rf /",  # Command injection attempt
            "pod$(whoami)",  # Command substitution
            "pod`id`",  # Backtick command
            "pod|cat /etc/passwd",  # Pipe
            "pod > /tmp/file",  # Redirect
            "pod & sleep 10",  # Background
            'pod"test',  # Quote
            "pod'test",  # Single quote
            "pod\ntest",  # Newline
            "pod\ttest",  # Tab
        ]
        for name in invalid_names:
            with pytest.raises(ValidationError, match="DNS-1123"):
                validate_kubernetes_name(name, "pod_name")

    def test_leading_hyphen_invalid(self) -> None:
        """Test that names starting with hyphen are invalid."""
        with pytest.raises(ValidationError, match="DNS-1123"):
            validate_kubernetes_name("-mypod", "pod_name")

    def test_trailing_hyphen_invalid(self) -> None:
        """Test that names ending with hyphen are invalid."""
        with pytest.raises(ValidationError, match="DNS-1123"):
            validate_kubernetes_name("mypod-", "pod_name")

    def test_leading_dot_invalid(self) -> None:
        """Test that names starting with dot are invalid."""
        with pytest.raises(ValidationError, match="DNS-1123"):
            validate_kubernetes_name(".mypod", "pod_name")


# =============================================================================
# PodExecResult Tests
# =============================================================================


class TestPodExecResult:
    """Tests for PodExecResult dataclass."""

    def test_successful_result(self) -> None:
        """Test successful execution result."""
        result = PodExecResult(
            stdout="output data",
            stderr="",
            return_code=0,
            success=True,
        )

        assert result.stdout == "output data"
        assert result.success is True
        assert result.output == "output data"

    def test_failed_result(self) -> None:
        """Test failed execution result."""
        result = PodExecResult(
            stdout="",
            stderr="error message",
            return_code=1,
            success=False,
        )

        assert result.success is False
        assert result.output == "error message"  # Falls back to stderr

    def test_output_prefers_stdout(self) -> None:
        """Test that output property prefers stdout over stderr."""
        result = PodExecResult(
            stdout="stdout data",
            stderr="stderr data",
            return_code=0,
            success=True,
        )

        assert result.output == "stdout data"


class TestPodExecError:
    """Tests for PodExecError exception."""

    def test_error_with_details(self) -> None:
        """Test PodExecError with all details."""
        error = PodExecError(
            "Command failed",
            stdout="partial output",
            stderr="error: connection refused",
            return_code=1,
        )

        assert str(error) == "Command failed"
        assert error.stdout == "partial output"
        assert error.stderr == "error: connection refused"
        assert error.return_code == 1


# =============================================================================
# execute_in_pod Tests (kr8s-based implementation)
# =============================================================================


@dataclass
class MockExecResult:
    """Mock kr8s exec result."""

    stdout: bytes
    stderr: bytes
    returncode: int


class TestExecuteInPod:
    """Tests for execute_in_pod function using kr8s native exec."""

    def _create_mock_adapter(self) -> MagicMock:
        """Create a mock KubernetesAdapter with kr8s API."""
        adapter = MagicMock()
        adapter._api = MagicMock()  # Mock kr8s API
        return adapter

    def _create_mock_pod(
        self,
        stdout: bytes = b"output data",
        stderr: bytes = b"",
        returncode: int = 0,
    ) -> MagicMock:
        """Create a mock kr8s Pod with exec capability."""
        mock_pod = MagicMock()
        mock_exec_result = MockExecResult(
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
        )
        mock_pod.exec = AsyncMock(return_value=mock_exec_result)
        return mock_pod

    @pytest.mark.asyncio
    async def test_invalid_namespace_raises(self) -> None:
        """Test that invalid namespace raises ToolExecutionError."""
        adapter = self._create_mock_adapter()

        with pytest.raises(ToolExecutionError, match="Invalid pod execution parameters"):
            await execute_in_pod(
                kubernetes_adapter=adapter,
                pod_name="valid-pod",
                namespace="INVALID;rm -rf /",
                command=["echo", "test"],
            )

    @pytest.mark.asyncio
    async def test_invalid_pod_name_raises(self) -> None:
        """Test that invalid pod name raises ToolExecutionError."""
        adapter = self._create_mock_adapter()

        with pytest.raises(ToolExecutionError, match="Invalid pod execution parameters"):
            await execute_in_pod(
                kubernetes_adapter=adapter,
                pod_name="pod$(id)",
                namespace="default",
                command=["echo", "test"],
            )

    @pytest.mark.asyncio
    async def test_shell_metachar_in_command_rejected(self) -> None:
        """Test that commands with shell metacharacters are rejected."""
        adapter = self._create_mock_adapter()

        dangerous_commands = [
            ["echo", "test; rm -rf /"],
            ["cat", "file | grep pass"],
            ["ls", "$(whoami)"],
            ["cmd", "`id`"],
        ]

        for cmd in dangerous_commands:
            with pytest.raises(ToolExecutionError, match="unsafe shell metacharacter"):
                await execute_in_pod(
                    kubernetes_adapter=adapter,
                    pod_name="my-pod",
                    namespace="default",
                    command=cmd,
                )

    @pytest.mark.asyncio
    async def test_successful_execution(self) -> None:
        """Test successful command execution via kr8s."""
        adapter = self._create_mock_adapter()
        mock_pod = self._create_mock_pod(
            stdout=b"output data",
            stderr=b"",
            returncode=0,
        )

        with patch("kr8s.asyncio.objects.Pod") as MockPod:
            MockPod.get = AsyncMock(return_value=mock_pod)

            result = await execute_in_pod(
                kubernetes_adapter=adapter,
                pod_name="ceph-tools-0",
                namespace="rook-ceph",
                command=["ceph", "status", "-f", "json"],
            )

            assert result.success is True
            assert result.stdout == "output data"
            assert result.return_code == 0

            # Verify Pod.get was called correctly
            MockPod.get.assert_called_once_with(
                "ceph-tools-0",
                namespace="rook-ceph",
                api=adapter._api,
            )

            # Verify exec was called with the command
            mock_pod.exec.assert_called_once()
            call_args = mock_pod.exec.call_args
            assert call_args[0][0] == ["ceph", "status", "-f", "json"]

    @pytest.mark.asyncio
    async def test_command_failure_raises_by_default(self) -> None:
        """Test that command failure raises ToolExecutionError by default."""
        adapter = self._create_mock_adapter()
        mock_pod = self._create_mock_pod(
            stdout=b"",
            stderr=b"command not found",
            returncode=1,
        )

        with patch("kr8s.asyncio.objects.Pod") as MockPod:
            MockPod.get = AsyncMock(return_value=mock_pod)

            with pytest.raises(ToolExecutionError, match="Pod exec failed"):
                await execute_in_pod(
                    kubernetes_adapter=adapter,
                    pod_name="ceph-tools-0",
                    namespace="rook-ceph",
                    command=["nonexistent-command"],
                )

    @pytest.mark.asyncio
    async def test_command_failure_returns_result_when_disabled(self) -> None:
        """Test that command failure returns result when raise_on_error=False."""
        adapter = self._create_mock_adapter()
        mock_pod = self._create_mock_pod(
            stdout=b"",
            stderr=b"command not found",
            returncode=1,
        )

        with patch("kr8s.asyncio.objects.Pod") as MockPod:
            MockPod.get = AsyncMock(return_value=mock_pod)

            result = await execute_in_pod(
                kubernetes_adapter=adapter,
                pod_name="ceph-tools-0",
                namespace="rook-ceph",
                command=["nonexistent-command"],
                raise_on_error=False,
            )

            assert result.success is False
            assert result.return_code == 1
            assert "command not found" in result.stderr

    @pytest.mark.asyncio
    async def test_timeout_handling(self) -> None:
        """Test that timeout is properly handled."""
        adapter = self._create_mock_adapter()
        mock_pod = MagicMock()
        mock_pod.exec = AsyncMock(side_effect=TimeoutError("Command timed out"))

        with patch("kr8s.asyncio.objects.Pod") as MockPod:
            MockPod.get = AsyncMock(return_value=mock_pod)

            with pytest.raises(ToolExecutionError, match="timed out"):
                await execute_in_pod(
                    kubernetes_adapter=adapter,
                    pod_name="ceph-tools-0",
                    namespace="rook-ceph",
                    command=["sleep", "100"],
                    timeout=1,
                )

    @pytest.mark.asyncio
    async def test_pod_not_found_raises(self) -> None:
        """Test that missing pod raises ToolExecutionError."""
        import kr8s

        adapter = self._create_mock_adapter()

        with patch("kr8s.asyncio.objects.Pod") as MockPod:
            MockPod.get = AsyncMock(side_effect=kr8s.NotFoundError("Pod not found"))

            with pytest.raises(ToolExecutionError, match="Pod not found"):
                await execute_in_pod(
                    kubernetes_adapter=adapter,
                    pod_name="nonexistent-pod",
                    namespace="default",
                    command=["echo", "test"],
                )

    @pytest.mark.asyncio
    async def test_service_name_in_error(self) -> None:
        """Test that service_name appears in error details."""
        import kr8s

        adapter = self._create_mock_adapter()

        with patch("kr8s.asyncio.objects.Pod") as MockPod:
            MockPod.get = AsyncMock(side_effect=kr8s.NotFoundError("Pod not found"))

            with pytest.raises(ToolExecutionError) as exc_info:
                await execute_in_pod(
                    kubernetes_adapter=adapter,
                    pod_name="my-pod",
                    namespace="default",
                    command=["echo"],
                    service_name="ceph_operations",
                )

            assert exc_info.value.tool_name == "ceph_operations"

    @pytest.mark.asyncio
    async def test_command_with_arguments(self) -> None:
        """Test command with multiple arguments."""
        adapter = self._create_mock_adapter()
        mock_pod = self._create_mock_pod(
            stdout=b'{"status":"ok"}',
            stderr=b"",
            returncode=0,
        )

        with patch("kr8s.asyncio.objects.Pod") as MockPod:
            MockPod.get = AsyncMock(return_value=mock_pod)

            await execute_in_pod(
                kubernetes_adapter=adapter,
                pod_name="ceph-tools-0",
                namespace="rook-ceph",
                command=["ceph", "osd", "tree", "-f", "json", "--id", "5"],
            )

            # Verify exec was called with full command
            mock_pod.exec.assert_called_once()
            call_args = mock_pod.exec.call_args
            assert call_args[0][0] == ["ceph", "osd", "tree", "-f", "json", "--id", "5"]

    @pytest.mark.asyncio
    async def test_exec_error_handling(self) -> None:
        """Test handling of kr8s ExecError."""
        import kr8s

        adapter = self._create_mock_adapter()
        mock_pod = MagicMock()

        # Create a mock ExecError
        exec_error = kr8s.ExecError("Command failed")
        exec_error.stdout = b"partial output"
        exec_error.returncode = 127
        mock_pod.exec = AsyncMock(side_effect=exec_error)

        with patch("kr8s.asyncio.objects.Pod") as MockPod:
            MockPod.get = AsyncMock(return_value=mock_pod)

            with pytest.raises(ToolExecutionError, match="Pod exec failed"):
                await execute_in_pod(
                    kubernetes_adapter=adapter,
                    pod_name="my-pod",
                    namespace="default",
                    command=["bad-command"],
                )

    @pytest.mark.asyncio
    async def test_exec_error_returns_result_when_not_raising(self) -> None:
        """Test ExecError returns result when raise_on_error=False."""
        import kr8s

        adapter = self._create_mock_adapter()
        mock_pod = MagicMock()

        exec_error = kr8s.ExecError("Command failed")
        exec_error.stdout = b"partial output"
        exec_error.returncode = 127
        mock_pod.exec = AsyncMock(side_effect=exec_error)

        with patch("kr8s.asyncio.objects.Pod") as MockPod:
            MockPod.get = AsyncMock(return_value=mock_pod)

            result = await execute_in_pod(
                kubernetes_adapter=adapter,
                pod_name="my-pod",
                namespace="default",
                command=["bad-command"],
                raise_on_error=False,
            )

            assert result.success is False
            assert result.return_code == 127
            assert result.stdout == "partial output"

    @pytest.mark.asyncio
    async def test_handles_string_stdout(self) -> None:
        """Test that string stdout is handled correctly."""
        adapter = self._create_mock_adapter()
        mock_pod = MagicMock()

        # Some exec implementations might return string instead of bytes
        @dataclass
        class StringExecResult:
            stdout: str
            stderr: str
            returncode: int

        mock_pod.exec = AsyncMock(
            return_value=StringExecResult(
                stdout="string output",
                stderr="",
                returncode=0,
            )
        )

        with patch("kr8s.asyncio.objects.Pod") as MockPod:
            MockPod.get = AsyncMock(return_value=mock_pod)

            result = await execute_in_pod(
                kubernetes_adapter=adapter,
                pod_name="my-pod",
                namespace="default",
                command=["echo", "test"],
            )

            assert result.success is True
            assert result.stdout == "string output"

    @pytest.mark.asyncio
    async def test_timeout_passed_to_exec(self) -> None:
        """Test that timeout parameter is passed to pod.exec."""
        adapter = self._create_mock_adapter()
        mock_pod = self._create_mock_pod()

        with patch("kr8s.asyncio.objects.Pod") as MockPod:
            MockPod.get = AsyncMock(return_value=mock_pod)

            await execute_in_pod(
                kubernetes_adapter=adapter,
                pod_name="my-pod",
                namespace="default",
                command=["echo", "test"],
                timeout=60,
            )

            # Verify timeout was passed
            mock_pod.exec.assert_called_once()
            call_kwargs = mock_pod.exec.call_args[1]
            assert call_kwargs.get("timeout") == 60
