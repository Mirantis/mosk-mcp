"""Shared scoring utilities for health calculations.

This module provides reusable scoring components to eliminate duplication
across health check tools. Instead of each tool having its own scoring
functions, they can use these shared utilities.

Usage:
    from mosk_mcp.tools.common.scoring import ScoreCalculator, ScoreComponent

    calculator = ScoreCalculator()
    calculator.add_component("health_status", weight=25, score=25)  # Full points
    calculator.add_component("osd_health", weight=30, score=osd_ratio * 30)
    calculator.add_component("pg_health", weight=25, score=pg_score)
    calculator.add_component("capacity", weight=20, score=capacity_score)
    final_score = calculator.calculate()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScoreComponent:
    """A single scoring component with weight and value.

    Attributes:
        name: Component identifier for debugging/logging.
        weight: Maximum points this component can contribute (0-100).
        score: Actual score achieved (0 to weight).
        details: Optional details about the scoring decision.
    """

    name: str
    weight: int
    score: float
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def normalized_score(self) -> float:
        """Score clamped to [0, weight] range."""
        return min(self.weight, max(0, self.score))


class ScoreCalculator:
    """Fluent builder for calculating health scores.

    Aggregates multiple scoring components and calculates a final score.
    All scores are clamped to valid ranges automatically.

    Example:
        score = (ScoreCalculator()
            .add_component("api", 30, 30 if api_healthy else 0)
            .add_component("nodes", 40, (ready_nodes / total_nodes) * 40)
            .add_component("pods", 30, pod_health_score)
            .calculate())
    """

    def __init__(self) -> None:
        """Initialize empty score calculator."""
        self._components: list[ScoreComponent] = []

    def add_component(
        self,
        name: str,
        weight: int,
        score: float,
        details: dict[str, Any] | None = None,
    ) -> ScoreCalculator:
        """Add a scoring component.

        Args:
            name: Component identifier.
            weight: Maximum points for this component.
            score: Actual score achieved (will be clamped to [0, weight]).
            details: Optional details about the scoring.

        Returns:
            Self for method chaining.
        """
        self._components.append(
            ScoreComponent(
                name=name,
                weight=weight,
                score=score,
                details=details or {},
            )
        )
        return self

    def add_ratio_component(
        self,
        name: str,
        weight: int,
        numerator: int | float,
        denominator: int | float,
        details: dict[str, Any] | None = None,
    ) -> ScoreCalculator:
        """Add a component based on a ratio (e.g., ready_nodes/total_nodes).

        Handles division by zero gracefully.

        Args:
            name: Component identifier.
            weight: Maximum points for this component.
            numerator: The numerator of the ratio.
            denominator: The denominator of the ratio.
            details: Optional details about the scoring.

        Returns:
            Self for method chaining.
        """
        if denominator > 0:
            ratio = numerator / denominator
            score = ratio * weight
        else:
            score = weight  # No items = full score (nothing to fail)

        return self.add_component(
            name=name,
            weight=weight,
            score=score,
            details={"numerator": numerator, "denominator": denominator, **(details or {})},
        )

    def add_threshold_component(
        self,
        name: str,
        weight: int,
        value: float,
        thresholds: list[tuple[float, float]],
        details: dict[str, Any] | None = None,
    ) -> ScoreCalculator:
        """Add a component based on threshold levels.

        Args:
            name: Component identifier.
            weight: Maximum points for this component.
            value: The value to check against thresholds.
            thresholds: List of (threshold, score_fraction) tuples, checked in order.
                       First matching threshold wins. Score is fraction * weight.
                       Example: [(70, 1.0), (85, 0.75), (95, 0.4), (100, 0.0)]
            details: Optional details about the scoring.

        Returns:
            Self for method chaining.
        """
        score = 0.0
        for threshold, fraction in thresholds:
            if value < threshold:
                score = fraction * weight
                break

        return self.add_component(
            name=name,
            weight=weight,
            score=score,
            details={"value": value, "thresholds": thresholds, **(details or {})},
        )

    def calculate(self) -> int:
        """Calculate final score from all components.

        Returns:
            Integer score clamped to [0, 100].
        """
        total = sum(c.normalized_score for c in self._components)
        return min(100, max(0, int(total)))

    def get_breakdown(self) -> dict[str, Any]:
        """Get detailed breakdown of all components.

        Returns:
            Dictionary with component details and final score.
        """
        return {
            "components": [
                {
                    "name": c.name,
                    "weight": c.weight,
                    "score": c.score,
                    "normalized": c.normalized_score,
                    "details": c.details,
                }
                for c in self._components
            ],
            "total_weight": sum(c.weight for c in self._components),
            "total_score": self.calculate(),
        }

    @property
    def components(self) -> list[ScoreComponent]:
        """Get list of all components."""
        return self._components.copy()


def calculate_ratio_score(
    numerator: int | float,
    denominator: int | float,
    max_score: int,
) -> int:
    """Calculate score based on a ratio.

    Utility function for simple ratio-based scoring.

    Args:
        numerator: The numerator value.
        denominator: The denominator value.
        max_score: Maximum score when ratio is 1.0.

    Returns:
        Integer score between 0 and max_score.
    """
    if denominator <= 0:
        return max_score  # No items = full score
    ratio = min(1.0, max(0.0, numerator / denominator))
    return int(ratio * max_score)


def calculate_threshold_score(
    value: float,
    thresholds: list[tuple[float, int]],
    default_score: int = 0,
) -> int:
    """Calculate score based on threshold levels.

    Utility function for threshold-based scoring.

    Args:
        value: The value to check.
        thresholds: List of (threshold, score) tuples. Returns score for
                   first threshold where value < threshold.
        default_score: Score if no threshold matches.

    Returns:
        Integer score based on thresholds.
    """
    for threshold, score in thresholds:
        if value < threshold:
            return score
    return default_score
