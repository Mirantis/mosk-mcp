"""Unit tests for jq filter evaluation."""

import pytest

from mosk_mcp.core.exceptions import ValidationError
from mosk_mcp.tools.kubectl.jq_filter import apply_jq_program, compile_jq_filter


class TestCompileJqFilter:
    """Tests for compile_jq_filter."""

    def test_compiles_valid_expression(self) -> None:
        program = compile_jq_filter(".metadata.name")
        assert program is not None

    def test_empty_expression_returns_none(self) -> None:
        assert compile_jq_filter("") is None
        assert compile_jq_filter("   ") is None

    def test_invalid_expression_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError, match="Invalid jq filter"):
            compile_jq_filter(".[")


class TestApplyJqProgram:
    """Tests for apply_jq_program."""

    def test_single_field(self) -> None:
        data = {"metadata": {"name": "my-pod", "namespace": "default"}}
        program = compile_jq_filter(".metadata.name")
        assert apply_jq_program(data, program) == "my-pod"

    def test_nested_field(self) -> None:
        data = {"status": {"phase": "Running"}}
        program = compile_jq_filter(".status.phase")
        assert apply_jq_program(data, program) == "Running"

    def test_array_iteration(self) -> None:
        data = {
            "items": [
                {"metadata": {"name": "pod-a"}},
                {"metadata": {"name": "pod-b"}},
            ]
        }
        program = compile_jq_filter(".items[].metadata.name")
        assert apply_jq_program(data, program) == ["pod-a", "pod-b"]

    def test_missing_field_returns_null(self) -> None:
        data = {"metadata": {"name": "test"}}
        program = compile_jq_filter(".status.phase")
        assert apply_jq_program(data, program) is None
