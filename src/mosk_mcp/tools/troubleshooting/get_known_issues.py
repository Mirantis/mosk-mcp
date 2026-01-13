"""Get known issues tool for intelligent troubleshooting.

This tool matches symptoms and error messages against a knowledge base
of known issues to help quickly identify problems.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from mosk_mcp.core.exceptions import ToolExecutionError
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.troubleshooting.known_issues import (
    get_known_issue_database,
)
from mosk_mcp.tools.troubleshooting.models import (
    DiagnosisCategory,
    GetKnownIssuesOutput,
    KnownIssue,
)


if TYPE_CHECKING:
    from mosk_mcp.adapters.kubernetes import KubernetesAdapter


logger = get_logger(__name__)


async def get_known_issues(
    kubernetes_adapter: KubernetesAdapter,
    symptoms: list[str] | None = None,
    error_message: str | None = None,
    service: str | None = None,
    category: DiagnosisCategory | None = None,
    include_resolved: bool = False,
    limit: int = 10,
) -> GetKnownIssuesOutput:
    """Match symptoms against known issue database.

    This tool searches the knowledge base of known MOSK issues and
    returns matches based on symptoms, error messages, service names,
    or categories. It uses pattern matching to find relevant known
    issues that can help diagnose and resolve problems faster.

    Args:
        kubernetes_adapter: Kubernetes adapter (for consistency, not used).
        symptoms: List of symptoms to match against.
        error_message: Error message to search for.
        service: Service name to filter by (e.g., 'nova', 'ceph').
        category: Issue category to filter by.
        include_resolved: Include issues fixed in newer versions.
        limit: Maximum number of issues to return (default: 10).

    Returns:
        GetKnownIssuesOutput with matching known issues.

    Raises:
        ToolExecutionError: If search fails.

    Example:
        # Search by symptoms
        result = await get_known_issues(
            k8s,
            symptoms=["RPC timeout", "nova errors"],
        )

        # Search by error message
        result = await get_known_issues(
            k8s,
            error_message="MessagingTimeout waiting for response",
            service="nova",
        )
    """
    logger.info(
        "get_known_issues_started",
        symptoms=symptoms,
        error_message=error_message[:50] if error_message else None,
        service=service,
        category=category.value if category else None,
    )

    try:
        # Get known issues database
        db = get_known_issue_database()

        # Build search criteria for output
        search_criteria = {
            "symptoms": symptoms,
            "error_message": error_message[:100] if error_message else None,
            "service": service,
            "category": category.value if category else None,
            "include_resolved": include_resolved,
        }

        # Find matching issues
        matches = db.find_matching_issues(
            error_message=error_message,
            symptoms=symptoms,
            service=service,
            category=category,
            min_score=0.0,  # Get all matches, we'll filter later
            limit=limit * 2,  # Get more to account for filtering
        )

        # Convert to output models
        issues: list[KnownIssue] = []
        for issue_pattern, score in matches:
            # Filter resolved issues if not requested
            if not include_resolved and issue_pattern.is_resolved_upstream:
                continue

            known_issue = issue_pattern.to_known_issue(match_score=score)
            issues.append(known_issue)

            if len(issues) >= limit:
                break

        # Determine best match
        best_match = issues[0] if issues else None

        result = GetKnownIssuesOutput(
            issues=issues,
            total_matches=len(matches),
            best_match=best_match,
            search_criteria=search_criteria,
            timestamp=datetime.now(UTC).isoformat(),
        )

        logger.info(
            "get_known_issues_completed",
            total_matches=result.total_matches,
            returned=len(issues),
        )

        return result

    except Exception as e:
        logger.error(
            "get_known_issues_failed",
            error=str(e),
        )
        raise ToolExecutionError(
            f"Failed to search known issues: {e}",
            tool_name="get_known_issues",
            phase="execution",
        ) from e


# Tool metadata for registration
TOOL_NAME = "get_known_issues"
TOOL_DESCRIPTION = """Match symptoms against known issue database.

Searches the knowledge base of known MOSK issues and returns matches based
on symptoms, error messages, service names, or categories.

Known issues include:
- MOSK-001: RPC timeout from RabbitMQ connection exhaustion
- MOSK-002: Ceph slow requests from OSD I/O saturation
- MOSK-003: Live migration stuck at 99%
- MOSK-004: Nova-compute down from libvirt failure
- MOSK-005: Volume attach timeout from Ceph auth failure
- And more...

This is a READ-ONLY tool that does not modify any resources."""

TOOL_TAGS = ["troubleshooting", "knowledge-base", "diagnosis", "read-only"]
