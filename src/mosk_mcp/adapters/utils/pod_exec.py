"""Pod execution utilities for MOSK MCP adapters.

This module provides a shared implementation for executing commands in
Kubernetes pods using kr8s native exec. Used by CephAdapter, OpenStackAdapter,
and other adapters that need to run commands inside pods.

Example:
    from mosk_mcp.adapters.utils import execute_in_pod

    async with KubernetesAdapter() as k8s:
        result = await execute_in_pod(
            kubernetes_adapter=k8s,
            pod_name="ceph-tools-pod",
            namespace="rook-ceph",
            command=["ceph", "status", "-f", "json"],
            timeout=30,
        )
        if result.success:
            data = json.loads(result.stdout)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mosk_mcp.core.exceptions import ToolExecutionError, ValidationError
from mosk_mcp.core.validation import validate_kubernetes_name
from mosk_mcp.observability.logging import get_logger


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter

logger = get_logger(__name__)


# Shell metacharacters that could be used for command injection
# These characters have special meaning in shells and should not appear in commands
# Includes: command separators (;&|), substitution (`$()), braces ({}), redirection (<>),
# newlines (\n\r), glob patterns (*?[]), history expansion (!), home expansion (~)
_SHELL_METACHAR_PATTERN = re.compile(r"[;&|`$(){}\\<>\n\r*?\[\]!~]")


class PodExecError(Exception):
    """Error during pod command execution."""

    def __init__(
        self,
        message: str,
        stdout: str = "",
        stderr: str = "",
        return_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr
        self.return_code = return_code


@dataclass
class PodExecResult:
    """Result from executing a command in a pod."""

    stdout: str
    stderr: str
    return_code: int
    success: bool

    @property
    def output(self) -> str:
        """Get stdout, falling back to stderr if stdout is empty."""
        return self.stdout or self.stderr


async def execute_in_pod(
    kubernetes_adapter: KubernetesAdapter,
    pod_name: str,
    namespace: str,
    command: list[str],
    timeout: int = 30,
    *,
    raise_on_error: bool = True,
    service_name: str = "pod_exec",
) -> PodExecResult:
    """Execute a command in a Kubernetes pod using kr8s native exec.

    This function uses kr8s's native pod exec functionality, which
    communicates directly with the Kubernetes API server without
    requiring kubectl to be installed.

    Args:
        kubernetes_adapter: Connected KubernetesAdapter instance.
        pod_name: Name of the pod to execute in.
        namespace: Namespace of the pod.
        command: Command and arguments to execute.
        timeout: Timeout in seconds (default: 30).
        raise_on_error: If True, raise ToolExecutionError on non-zero exit.
        service_name: Service name for error messages (default: "pod_exec").

    Returns:
        PodExecResult with stdout, stderr, return code, and success flag.

    Raises:
        ToolExecutionError: If exec fails, timeout, or command fails
            (when raise_on_error=True).
        ValidationError: If namespace or pod_name contain invalid characters.
    """
    # Validate inputs to prevent command injection.
    try:
        validate_kubernetes_name(namespace, field_name="namespace")
        validate_kubernetes_name(pod_name, field_name="pod_name")
    except ValidationError as e:
        raise ToolExecutionError(
            f"Invalid pod execution parameters: {e}",
            tool_name=service_name,
            details={"namespace": namespace, "pod_name": pod_name},
        ) from e

    # Check for shell metacharacters that could enable command injection
    for cmd_part in command:
        if _SHELL_METACHAR_PATTERN.search(cmd_part):
            raise ToolExecutionError(
                f"Command contains potentially unsafe shell metacharacter in: {cmd_part[:50]}",
                tool_name=service_name,
                details={"rejected_chars": ";&|`$(){}\\<>*?[]!~"},
            )

    logger.debug(
        "executing_pod_exec",
        pod=pod_name,
        namespace=namespace,
        command=command,
        service=service_name,
    )

    try:
        # Import kr8s here to avoid circular imports
        import kr8s
        from kr8s.asyncio.objects import Pod

        # Get the kr8s API from the kubernetes adapter
        api = kubernetes_adapter._api

        # Get the pod object using kr8s
        try:
            pod = await Pod.get(pod_name, namespace=namespace, api=api)
        except kr8s.NotFoundError:
            raise ToolExecutionError(
                f"Pod not found: {pod_name}",
                tool_name=service_name,
                details={"pod": pod_name, "namespace": namespace},
            ) from None
        except Exception as e:
            raise ToolExecutionError(
                f"Failed to get pod: {e}",
                tool_name=service_name,
                details={"pod": pod_name, "namespace": namespace, "error": str(e)},
            ) from e

        # Execute command in the pod using kr8s native exec
        try:
            # kr8s exec returns an ExecResult with stdout, stderr, returncode
            exec_result = await pod.exec(
                command,
                timeout=timeout,
            )

            # Handle the result - kr8s returns bytes for stdout/stderr
            stdout_str = ""
            stderr_str = ""
            return_code = 0

            if hasattr(exec_result, "stdout") and exec_result.stdout:
                stdout_str = (
                    exec_result.stdout.decode("utf-8")
                    if isinstance(exec_result.stdout, bytes)
                    else str(exec_result.stdout)
                ).strip()

            if hasattr(exec_result, "stderr") and exec_result.stderr:
                stderr_str = (
                    exec_result.stderr.decode("utf-8")
                    if isinstance(exec_result.stderr, bytes)
                    else str(exec_result.stderr)
                ).strip()

            if hasattr(exec_result, "returncode"):
                return_code = exec_result.returncode or 0

            success = return_code == 0

        except kr8s.ExecError as e:
            # kr8s raises ExecError when the command fails
            logger.warning(
                "pod_exec_command_failed",
                pod=pod_name,
                namespace=namespace,
                error=str(e),
            )
            # Try to extract stdout/stderr from the error
            stdout_str = getattr(e, "stdout", "") or ""
            if isinstance(stdout_str, bytes):
                stdout_str = stdout_str.decode("utf-8").strip()
            stderr_str = str(e)
            return_code = getattr(e, "returncode", 1) or 1
            success = False

        except TimeoutError as e:
            raise ToolExecutionError(
                f"Command timed out after {timeout}s",
                tool_name=service_name,
                details={"command": command, "timeout": timeout, "pod": pod_name},
            ) from e

        # Log result
        if success:
            logger.debug(
                "pod_exec_success",
                pod=pod_name,
                namespace=namespace,
                stdout_len=len(stdout_str),
            )
        else:
            logger.warning(
                "pod_exec_failed",
                pod=pod_name,
                namespace=namespace,
                return_code=return_code,
                stderr=stderr_str[:500] if stderr_str else None,
            )

        result = PodExecResult(
            stdout=stdout_str,
            stderr=stderr_str,
            return_code=return_code,
            success=success,
        )

        # Raise error if requested and command failed
        if raise_on_error and not success:
            error_msg = stderr_str or f"Command failed with exit code {return_code}"
            raise ToolExecutionError(
                f"Pod exec failed: {error_msg}",
                tool_name=service_name,
                details={
                    "command": command,
                    "pod": pod_name,
                    "namespace": namespace,
                    "return_code": return_code,
                    "stderr": stderr_str[:500] if stderr_str else None,
                },
            )

        return result

    except ToolExecutionError:
        raise
    except Exception as e:
        logger.error(
            "pod_exec_error",
            pod=pod_name,
            namespace=namespace,
            error=str(e),
            error_type=type(e).__name__,
        )
        raise ToolExecutionError(
            f"Failed to execute command in pod: {e}",
            tool_name=service_name,
            details={"command": command, "pod": pod_name, "error": str(e)},
        ) from e
