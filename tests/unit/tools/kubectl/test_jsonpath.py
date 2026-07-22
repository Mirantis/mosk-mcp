"""Unit tests for kubectl jsonpath evaluation."""

from mosk_mcp.tools.kubectl.jsonpath import apply_jsonpath, normalize_jsonpath


class TestNormalizeJsonpath:
    """Tests for normalize_jsonpath."""

    def test_strips_braces(self) -> None:
        assert normalize_jsonpath("{.metadata.name}") == ".metadata.name"

    def test_no_braces_unchanged(self) -> None:
        assert normalize_jsonpath(".metadata.name") == ".metadata.name"


class TestApplyJsonpath:
    """Tests for apply_jsonpath."""

    def test_single_field(self) -> None:
        data = {"metadata": {"name": "my-pod", "namespace": "default"}}
        result = apply_jsonpath(data, "{.metadata.name}")
        assert result == "my-pod"

    def test_nested_field(self) -> None:
        data = {"status": {"phase": "Running"}}
        result = apply_jsonpath(data, ".status.phase")
        assert result == "Running"

    def test_array_wildcard(self) -> None:
        data = {
            "items": [
                {"metadata": {"name": "pod-a"}},
                {"metadata": {"name": "pod-b"}},
            ]
        }
        result = apply_jsonpath(data, "{.items[*].metadata.name}")
        assert result == ["pod-a", "pod-b"]

    def test_empty_expression_returns_data(self) -> None:
        data = {"key": "value"}
        assert apply_jsonpath(data, "") == data

    def test_missing_field_returns_none(self) -> None:
        data = {"metadata": {"name": "test"}}
        result = apply_jsonpath(data, ".status.phase")
        assert result is None
