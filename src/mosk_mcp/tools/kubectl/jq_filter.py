"""jq-based JSON filtering for kubectl tool output.

Uses the external ``jq`` library (libjq bindings). Filter expressions must
follow jq syntax, e.g.::

    .metadata.name
    .items[].metadata.name
    .status.phase
"""

from __future__ import annotations

from typing import Any

import jq

from mosk_mcp.core.exceptions import ValidationError


def compile_jq_filter(expression: str) -> Any | None:
    """Compile and validate a jq filter expression.

    Args:
        expression: jq filter expression (e.g. ``.items[].metadata.name``).

    Returns:
        A compiled jq program, or ``None`` if the expression is empty/whitespace.

    Raises:
        ValidationError: If the jq expression is syntactically invalid.
    """
    stripped = expression.strip()
    if not stripped:
        return None

    try:
        return jq.compile(stripped)
    except ValueError as e:
        raise ValidationError(
            f"Invalid jq filter: {e}",
            field="jq_filter",
            value=stripped,
            constraint="must be a valid jq expression",
        ) from e


def apply_jq_program(data: Any, program: Any) -> Any:
    """Evaluate a compiled jq program against JSON data.

    Args:
        data: JSON-serializable data (dict, list, or scalar).
        program: Compiled jq program from :func:`compile_jq_filter`.

    Returns:
        Extracted value. A single jq output is returned as-is; multiple
        outputs are returned as a list.

    Raises:
        ValidationError: If jq evaluation fails.
    """
    try:
        results = program.input_value(data).all()
    except ValueError as e:
        raise ValidationError(
            f"jq filter evaluation failed: {e}",
            field="jq_filter",
            constraint="must be a valid jq expression for the returned data",
        ) from e

    if len(results) == 1:
        return results[0]
    return results
