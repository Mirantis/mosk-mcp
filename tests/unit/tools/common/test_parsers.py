"""Tests for common parsing utilities.

Tests cover:
- Single condition parsing
- Multiple conditions parsing
- Condition finding utilities
- Status condition extraction
- Resource readiness checking
- Label selector parsing
- Nested dict navigation
"""

from mosk_mcp.tools.common.parsers import (
    find_condition_by_type,
    get_condition_message,
    get_status_conditions,
    is_condition_true,
    is_resource_ready,
    parse_k8s_condition,
    parse_k8s_conditions,
    parse_label_selector,
    safe_get_nested,
    utc_timestamp,
)
from mosk_mcp.tools.operations_visibility.models import ConditionStatus


class TestParseK8sCondition:
    """Tests for parse_k8s_condition."""

    def test_parse_full_condition(self) -> None:
        """Test parsing a complete condition."""
        raw = {
            "type": "Ready",
            "status": "True",
            "reason": "NodeReady",
            "message": "kubelet is ready",
            "lastTransitionTime": "2024-01-15T10:00:00Z",
        }
        cond = parse_k8s_condition(raw)

        assert cond.type == "Ready"
        assert cond.status == ConditionStatus.TRUE
        assert cond.reason == "NodeReady"
        assert cond.message == "kubelet is ready"
        assert cond.last_transition_time == "2024-01-15T10:00:00Z"

    def test_parse_minimal_condition(self) -> None:
        """Test parsing a condition with minimal fields."""
        raw = {"type": "MemoryPressure", "status": "False"}
        cond = parse_k8s_condition(raw)

        assert cond.type == "MemoryPressure"
        assert cond.status == ConditionStatus.FALSE
        assert cond.reason is None
        assert cond.message is None

    def test_parse_unknown_status(self) -> None:
        """Test parsing handles unknown status values."""
        raw = {"type": "Custom", "status": "Maybe"}
        cond = parse_k8s_condition(raw)

        assert cond.status == ConditionStatus.UNKNOWN

    def test_parse_missing_status(self) -> None:
        """Test parsing handles missing status."""
        raw = {"type": "Custom"}
        cond = parse_k8s_condition(raw)

        assert cond.status == ConditionStatus.UNKNOWN

    def test_parse_last_update_time_aliases(self) -> None:
        """Test parsing handles lastUpdateTime and lastHeartbeatTime."""
        raw = {"type": "Ready", "status": "True", "lastUpdateTime": "2024-01-15T10:00:00Z"}
        cond = parse_k8s_condition(raw)
        assert cond.last_update_time == "2024-01-15T10:00:00Z"

        raw2 = {"type": "Ready", "status": "True", "lastHeartbeatTime": "2024-01-15T11:00:00Z"}
        cond2 = parse_k8s_condition(raw2)
        assert cond2.last_update_time == "2024-01-15T11:00:00Z"


class TestParseK8sConditions:
    """Tests for parse_k8s_conditions."""

    def test_parse_multiple_conditions(self) -> None:
        """Test parsing multiple conditions."""
        raw = [
            {"type": "Ready", "status": "True"},
            {"type": "DiskPressure", "status": "False"},
            {"type": "MemoryPressure", "status": "False"},
        ]
        conditions = parse_k8s_conditions(raw)

        assert len(conditions) == 3
        assert conditions[0].type == "Ready"
        assert conditions[1].type == "DiskPressure"
        assert conditions[2].type == "MemoryPressure"

    def test_parse_empty_list(self) -> None:
        """Test parsing empty list."""
        conditions = parse_k8s_conditions([])
        assert conditions == []


class TestFindConditionByType:
    """Tests for find_condition_by_type."""

    def test_find_existing_condition(self) -> None:
        """Test finding an existing condition."""
        conditions = [
            {"type": "Ready", "status": "True", "message": "Node is ready"},
            {"type": "DiskPressure", "status": "False"},
        ]
        result = find_condition_by_type(conditions, "Ready")

        assert result is not None
        assert result["status"] == "True"
        assert result["message"] == "Node is ready"

    def test_find_nonexistent_condition(self) -> None:
        """Test finding a nonexistent condition returns None."""
        conditions = [{"type": "Ready", "status": "True"}]
        result = find_condition_by_type(conditions, "DiskPressure")

        assert result is None

    def test_find_in_empty_list(self) -> None:
        """Test finding in empty list returns None."""
        result = find_condition_by_type([], "Ready")
        assert result is None


class TestIsConditionTrue:
    """Tests for is_condition_true."""

    def test_condition_is_true(self) -> None:
        """Test condition that is True."""
        conditions = [
            {"type": "Ready", "status": "True"},
            {"type": "DiskPressure", "status": "False"},
        ]
        assert is_condition_true(conditions, "Ready") is True

    def test_condition_is_false(self) -> None:
        """Test condition that is False."""
        conditions = [
            {"type": "Ready", "status": "True"},
            {"type": "DiskPressure", "status": "False"},
        ]
        assert is_condition_true(conditions, "DiskPressure") is False

    def test_condition_not_found(self) -> None:
        """Test condition that doesn't exist."""
        conditions = [{"type": "Ready", "status": "True"}]
        assert is_condition_true(conditions, "DiskPressure") is False

    def test_empty_conditions(self) -> None:
        """Test with empty conditions list."""
        assert is_condition_true([], "Ready") is False


class TestGetStatusConditions:
    """Tests for get_status_conditions."""

    def test_extract_conditions(self) -> None:
        """Test extracting conditions from resource."""
        resource = {
            "metadata": {"name": "node-1"},
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "True"},
                    {"type": "DiskPressure", "status": "False"},
                ]
            },
        }
        conditions = get_status_conditions(resource)

        assert len(conditions) == 2
        assert conditions[0]["type"] == "Ready"

    def test_no_status(self) -> None:
        """Test resource with no status returns empty list."""
        resource = {"metadata": {"name": "node-1"}}
        conditions = get_status_conditions(resource)

        assert conditions == []

    def test_no_conditions(self) -> None:
        """Test status with no conditions returns empty list."""
        resource = {"status": {"phase": "Running"}}
        conditions = get_status_conditions(resource)

        assert conditions == []

    def test_empty_resource(self) -> None:
        """Test empty resource returns empty list."""
        conditions = get_status_conditions({})
        assert conditions == []


class TestIsResourceReady:
    """Tests for is_resource_ready."""

    def test_ready_resource(self) -> None:
        """Test resource that is ready."""
        resource = {
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "True"},
                ]
            }
        }
        assert is_resource_ready(resource) is True

    def test_not_ready_resource(self) -> None:
        """Test resource that is not ready."""
        resource = {
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "False"},
                ]
            }
        }
        assert is_resource_ready(resource) is False

    def test_no_ready_condition(self) -> None:
        """Test resource without Ready condition."""
        resource = {
            "status": {
                "conditions": [
                    {"type": "DiskPressure", "status": "False"},
                ]
            }
        }
        assert is_resource_ready(resource) is False

    def test_no_conditions(self) -> None:
        """Test resource with no conditions."""
        resource = {"status": {}}
        assert is_resource_ready(resource) is False


class TestGetConditionMessage:
    """Tests for get_condition_message."""

    def test_get_message(self) -> None:
        """Test getting message from condition."""
        conditions = [
            {"type": "Ready", "status": "True", "message": "Node is healthy"},
        ]
        message = get_condition_message(conditions, "Ready")

        assert message == "Node is healthy"

    def test_no_message(self) -> None:
        """Test condition without message."""
        conditions = [{"type": "Ready", "status": "True"}]
        message = get_condition_message(conditions, "Ready")

        assert message is None

    def test_condition_not_found(self) -> None:
        """Test nonexistent condition."""
        conditions = [{"type": "Ready", "status": "True"}]
        message = get_condition_message(conditions, "DiskPressure")

        assert message is None


class TestParseLabelSelector:
    """Tests for parse_label_selector."""

    def test_single_label(self) -> None:
        """Test single label selector."""
        result = parse_label_selector({"app": "nginx"})
        assert result == "app=nginx"

    def test_multiple_labels(self) -> None:
        """Test multiple labels are sorted."""
        result = parse_label_selector({"env": "prod", "app": "nginx"})
        assert result == "app=nginx,env=prod"

    def test_empty_labels(self) -> None:
        """Test empty labels."""
        result = parse_label_selector({})
        assert result == ""


class TestSafeGetNested:
    """Tests for safe_get_nested."""

    def test_single_level(self) -> None:
        """Test single level access."""
        data = {"key": "value"}
        assert safe_get_nested(data, "key") == "value"

    def test_nested_access(self) -> None:
        """Test nested dict access."""
        data = {"level1": {"level2": {"level3": "value"}}}
        assert safe_get_nested(data, "level1", "level2", "level3") == "value"

    def test_missing_key_returns_default(self) -> None:
        """Test missing key returns default."""
        data = {"key": "value"}
        assert safe_get_nested(data, "missing") is None
        assert safe_get_nested(data, "missing", default="default") == "default"

    def test_missing_nested_returns_default(self) -> None:
        """Test missing nested key returns default."""
        data = {"level1": {"level2": "value"}}
        assert safe_get_nested(data, "level1", "missing", "nested") is None
        assert safe_get_nested(data, "level1", "missing", default=42) == 42

    def test_non_dict_intermediate_returns_default(self) -> None:
        """Test non-dict in path returns default."""
        data = {"key": "string_value"}
        assert safe_get_nested(data, "key", "nested") is None

    def test_typical_kubernetes_access(self) -> None:
        """Test typical Kubernetes resource access pattern."""
        resource = {
            "metadata": {"name": "pod-1", "labels": {"app": "nginx"}},
            "status": {"phase": "Running", "conditions": [{"type": "Ready"}]},
        }
        assert safe_get_nested(resource, "metadata", "name") == "pod-1"
        assert safe_get_nested(resource, "status", "phase") == "Running"
        assert safe_get_nested(resource, "spec", "replicas", default=1) == 1


class TestUtcTimestamp:
    """Tests for utc_timestamp."""

    def test_returns_string(self) -> None:
        """Test returns ISO format string."""
        ts = utc_timestamp()
        assert isinstance(ts, str)
        # Should contain date/time separators
        assert "T" in ts
        # Should have timezone info
        assert "+" in ts or "Z" in ts

    def test_contains_utc_offset(self) -> None:
        """Test timestamp has UTC offset."""
        ts = utc_timestamp()
        # UTC offset should be +00:00
        assert "+00:00" in ts
