"""Kubectl-style JSONPath evaluation for resource output filtering.

Supports common kubectl jsonpath expressions such as:
    {.metadata.name}
    {.items[*].metadata.name}
    {.status.phase}
"""

from __future__ import annotations

import re
from typing import Any


_JSONPATH_WRAPPER_RE = re.compile(r"^\{(.+)\}$", re.DOTALL)


def normalize_jsonpath(expression: str) -> str:
    """Strip kubectl-style curly braces from a jsonpath expression."""
    stripped = expression.strip()
    match = _JSONPATH_WRAPPER_RE.match(stripped)
    if match:
        return match.group(1).strip()
    return stripped


def apply_jsonpath(data: Any, expression: str) -> Any:
    """Evaluate a kubectl-style jsonpath expression against JSON data.

    Args:
        data: JSON-serializable data (dict, list, or scalar).
        expression: Jsonpath expression, with or without ``{...}`` wrapper.

    Returns:
        Extracted value(s). Array wildcards return a list of matches.
    """
    path = normalize_jsonpath(expression)
    if not path:
        return data

    if path.startswith("$"):
        path = path[1:]
    if path.startswith("."):
        path = path[1:]

    if not path:
        return data

    return _evaluate_path(data, path)


def _evaluate_path(data: Any, path: str) -> Any:
    """Recursively evaluate a dot-separated jsonpath with optional ``[*]`` wildcards."""
    segments = _split_path(path)
    return _walk(data, segments, 0)


def _split_path(path: str) -> list[str]:
    """Split a jsonpath into segments, keeping bracket notation attached."""
    segments: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(path):
        char = path[i]
        if char == "." and not current:
            i += 1
            continue
        if char == "." and current and current[-1] not in "]":
            segments.append("".join(current))
            current = []
            i += 1
            continue
        if char == "[":
            bracket_end = path.find("]", i)
            if bracket_end == -1:
                current.append(char)
                i += 1
                continue
            current.append(path[i : bracket_end + 1])
            i = bracket_end + 1
            continue
        current.append(char)
        i += 1

    if current:
        segments.append("".join(current))
    return [segment for segment in segments if segment]


def _walk(data: Any, segments: list[str], index: int) -> Any:
    if index >= len(segments):
        return data

    segment = segments[index]
    is_wildcard = segment.endswith("[*]")
    key = segment[:-3] if is_wildcard else segment

    if is_wildcard:
        if not isinstance(data, dict):
            return []
        value = data.get(key)
        if not isinstance(value, list):
            return []
        results = [_walk(item, segments, index + 1) for item in value]
        return results

    if isinstance(data, dict):
        next_data = data.get(key)
    elif isinstance(data, list) and key.isdigit():
        list_index = int(key)
        next_data = data[list_index] if 0 <= list_index < len(data) else None
    else:
        return None

    return _walk(next_data, segments, index + 1)
