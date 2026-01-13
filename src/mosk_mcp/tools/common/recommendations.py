"""Shared recommendation generation utilities.

This module provides reusable recommendation builders to eliminate duplication
across health check and diagnostic tools.

Usage:
    from mosk_mcp.tools.common.recommendations import RecommendationBuilder

    recommendations = (RecommendationBuilder()
        .add_if(not api_healthy, "API server is unhealthy - check kube-apiserver pods")
        .add_if(osds_down > 0, f"{osds_down} OSD(s) down - check ceph-osd pods")
        .add_if(capacity_pct > 85, f"Capacity at {capacity_pct:.1f}% - plan expansion")
        .add_for_each(unhealthy_nodes, lambda n: f"Node {n} unhealthy - investigate")
        .build())
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, TypeVar


if TYPE_CHECKING:
    from collections.abc import Callable


class RecommendationPriority(str, Enum):
    """Priority levels for recommendations."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@dataclass
class Recommendation:
    """A single recommendation with priority and context.

    Attributes:
        message: The recommendation text.
        priority: Importance level.
        category: Optional category for grouping (e.g., "storage", "network").
        context: Optional additional context.
    """

    message: str
    priority: RecommendationPriority = RecommendationPriority.MEDIUM
    category: str | None = None
    context: dict[str, Any] = field(default_factory=dict)


T = TypeVar("T")


class RecommendationBuilder:
    """Fluent builder for generating recommendations.

    Provides a clean API for conditionally adding recommendations
    with automatic deduplication and limiting.

    Example:
        recs = (RecommendationBuilder(max_recommendations=10)
            .add_if(ceph_health != "HEALTH_OK", "Ceph cluster unhealthy")
            .add_if(osds_down > 0, f"{osds_down} OSDs down", priority=Priority.HIGH)
            .add_if(capacity > 85, "Storage capacity warning")
            .build())
    """

    def __init__(
        self,
        max_recommendations: int = 10,
        deduplicate: bool = True,
    ) -> None:
        """Initialize recommendation builder.

        Args:
            max_recommendations: Maximum recommendations to return.
            deduplicate: Whether to remove duplicate messages.
        """
        self._recommendations: list[Recommendation] = []
        self._max = max_recommendations
        self._deduplicate = deduplicate
        self._seen_messages: set[str] = set()

    def add(
        self,
        message: str,
        priority: RecommendationPriority = RecommendationPriority.MEDIUM,
        category: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> RecommendationBuilder:
        """Add a recommendation unconditionally.

        Args:
            message: The recommendation text.
            priority: Importance level.
            category: Optional category for grouping.
            context: Optional additional context.

        Returns:
            Self for method chaining.
        """
        # Skip duplicates if deduplication enabled
        if self._deduplicate and message in self._seen_messages:
            return self

        self._recommendations.append(
            Recommendation(
                message=message,
                priority=priority,
                category=category,
                context=context or {},
            )
        )
        self._seen_messages.add(message)
        return self

    def add_if(
        self,
        condition: bool,
        message: str,
        priority: RecommendationPriority = RecommendationPriority.MEDIUM,
        category: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> RecommendationBuilder:
        """Add a recommendation if condition is true.

        Args:
            condition: Whether to add this recommendation.
            message: The recommendation text.
            priority: Importance level.
            category: Optional category for grouping.
            context: Optional additional context.

        Returns:
            Self for method chaining.
        """
        if condition:
            self.add(message, priority, category, context)
        return self

    def add_for_each(
        self,
        items: list[T],
        message_fn: Callable[[T], str],
        priority: RecommendationPriority = RecommendationPriority.MEDIUM,
        category: str | None = None,
        max_items: int | None = None,
    ) -> RecommendationBuilder:
        """Add recommendations for each item in a list.

        Args:
            items: List of items to generate recommendations for.
            message_fn: Function to generate message from item.
            priority: Importance level for all generated recommendations.
            category: Optional category for grouping.
            max_items: Maximum items to process (None = no limit).

        Returns:
            Self for method chaining.
        """
        for i, item in enumerate(items):
            if max_items is not None and i >= max_items:
                break
            self.add(message_fn(item), priority, category)
        return self

    def add_threshold(
        self,
        value: float,
        thresholds: list[tuple[float, str, RecommendationPriority]],
        category: str | None = None,
    ) -> RecommendationBuilder:
        """Add recommendation based on threshold levels.

        Args:
            value: The value to check.
            thresholds: List of (threshold, message, priority) tuples.
                       First threshold where value >= threshold wins.
                       Check from highest to lowest threshold.
            category: Optional category for grouping.

        Returns:
            Self for method chaining.
        """
        # Sort thresholds descending so higher thresholds are checked first
        sorted_thresholds = sorted(thresholds, key=lambda x: x[0], reverse=True)

        for threshold, message, priority in sorted_thresholds:
            if value >= threshold:
                self.add(message, priority, category)
                break

        return self

    def build(self, sort_by_priority: bool = True) -> list[str]:
        """Build and return the list of recommendation messages.

        Args:
            sort_by_priority: Whether to sort by priority (highest first).

        Returns:
            List of recommendation message strings (limited to max).
        """
        recs = self._recommendations

        if sort_by_priority:
            # Sort by priority (CRITICAL first, INFO last)
            priority_order = {
                RecommendationPriority.CRITICAL: 0,
                RecommendationPriority.HIGH: 1,
                RecommendationPriority.MEDIUM: 2,
                RecommendationPriority.LOW: 3,
                RecommendationPriority.INFO: 4,
            }
            recs = sorted(recs, key=lambda r: priority_order.get(r.priority, 2))

        return [r.message for r in recs[: self._max]]

    def build_detailed(self, sort_by_priority: bool = True) -> list[Recommendation]:
        """Build and return the full Recommendation objects.

        Args:
            sort_by_priority: Whether to sort by priority (highest first).

        Returns:
            List of Recommendation objects (limited to max).
        """
        recs = self._recommendations

        if sort_by_priority:
            priority_order = {
                RecommendationPriority.CRITICAL: 0,
                RecommendationPriority.HIGH: 1,
                RecommendationPriority.MEDIUM: 2,
                RecommendationPriority.LOW: 3,
                RecommendationPriority.INFO: 4,
            }
            recs = sorted(recs, key=lambda r: priority_order.get(r.priority, 2))

        return recs[: self._max]

    def is_empty(self) -> bool:
        """Check if no recommendations have been added."""
        return len(self._recommendations) == 0

    def __len__(self) -> int:
        """Return number of recommendations added."""
        return len(self._recommendations)


# Convenience alias for common priority
Priority = RecommendationPriority
