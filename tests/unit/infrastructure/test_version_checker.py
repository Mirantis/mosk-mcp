"""Tests for MOSK version checker implementation.

Tests the version detection, compatibility checking, and warning functions.
"""

from unittest.mock import AsyncMock

import pytest

from mosk_mcp.infrastructure.version_checker import (
    MIN_SUPPORTED_VERSION,
    MIN_SUPPORTED_VERSION_STR,
    MOSKVersionInfo,
    VersionCompatibility,
    add_version_warning_to_output,
    check_version_compatibility,
    clear_cached_version_info,
    get_cached_version_info,
    get_mosk_version,
    get_version_warning_message,
    parse_mosk_version,
    set_cached_version_info,
)


# =============================================================================
# Constants Tests
# =============================================================================


class TestConstants:
    """Tests for module constants."""

    def test_min_supported_version(self) -> None:
        """Test minimum supported version constant."""
        assert MIN_SUPPORTED_VERSION == (25, 1)

    def test_min_supported_version_str(self) -> None:
        """Test minimum supported version string constant."""
        assert MIN_SUPPORTED_VERSION_STR == "25.1"


# =============================================================================
# Enum Tests
# =============================================================================


class TestVersionCompatibility:
    """Tests for VersionCompatibility enum."""

    def test_all_statuses_defined(self) -> None:
        """Test all compatibility statuses are defined."""
        assert VersionCompatibility.COMPATIBLE == "compatible"
        assert VersionCompatibility.UNSUPPORTED == "unsupported"
        assert VersionCompatibility.UNKNOWN == "unknown"

    def test_status_count(self) -> None:
        """Test expected number of statuses."""
        assert len(VersionCompatibility) == 3


# =============================================================================
# MOSKVersionInfo Tests
# =============================================================================


class TestMOSKVersionInfo:
    """Tests for MOSKVersionInfo dataclass."""

    def test_default_values(self) -> None:
        """Test default field values."""
        info = MOSKVersionInfo()

        assert info.cluster_release is None
        assert info.version_string is None
        assert info.major is None
        assert info.minor is None
        assert info.patch is None
        assert info.compatibility == VersionCompatibility.UNKNOWN
        assert info.raw_data == {}
        assert info.warnings == []

    def test_with_values(self) -> None:
        """Test creating info with specific values."""
        info = MOSKVersionInfo(
            cluster_release="mosk-21-0-2-25-2-2",
            version_string="25.2.2",
            major=25,
            minor=2,
            patch=2,
            compatibility=VersionCompatibility.COMPATIBLE,
        )

        assert info.cluster_release == "mosk-21-0-2-25-2-2"
        assert info.version_string == "25.2.2"
        assert info.major == 25
        assert info.minor == 2
        assert info.patch == 2
        assert info.compatibility == VersionCompatibility.COMPATIBLE


class TestMOSKVersionInfoProperties:
    """Tests for MOSKVersionInfo properties."""

    def test_is_compatible_true(self) -> None:
        """Test is_compatible returns True for compatible versions."""
        info = MOSKVersionInfo(compatibility=VersionCompatibility.COMPATIBLE)
        assert info.is_compatible is True

    def test_is_compatible_false_for_unsupported(self) -> None:
        """Test is_compatible returns False for unsupported versions."""
        info = MOSKVersionInfo(compatibility=VersionCompatibility.UNSUPPORTED)
        assert info.is_compatible is False

    def test_is_compatible_false_for_unknown(self) -> None:
        """Test is_compatible returns False for unknown versions."""
        info = MOSKVersionInfo(compatibility=VersionCompatibility.UNKNOWN)
        assert info.is_compatible is False

    def test_is_unsupported_true(self) -> None:
        """Test is_unsupported returns True for unsupported versions."""
        info = MOSKVersionInfo(compatibility=VersionCompatibility.UNSUPPORTED)
        assert info.is_unsupported is True

    def test_is_unsupported_false(self) -> None:
        """Test is_unsupported returns False for other statuses."""
        info = MOSKVersionInfo(compatibility=VersionCompatibility.COMPATIBLE)
        assert info.is_unsupported is False

    def test_version_tuple_with_all_parts(self) -> None:
        """Test version_tuple with all version parts."""
        info = MOSKVersionInfo(major=25, minor=2, patch=3)
        assert info.version_tuple == (25, 2, 3)

    def test_version_tuple_with_no_patch(self) -> None:
        """Test version_tuple defaults patch to 0."""
        info = MOSKVersionInfo(major=25, minor=2)
        assert info.version_tuple == (25, 2, 0)

    def test_version_tuple_none_when_incomplete(self) -> None:
        """Test version_tuple returns None when major/minor missing."""
        info = MOSKVersionInfo(major=25)
        assert info.version_tuple is None

        info = MOSKVersionInfo(minor=2)
        assert info.version_tuple is None

        info = MOSKVersionInfo()
        assert info.version_tuple is None


class TestMOSKVersionInfoToDict:
    """Tests for MOSKVersionInfo.to_dict method."""

    def test_to_dict_with_values(self) -> None:
        """Test to_dict with populated values."""
        info = MOSKVersionInfo(
            cluster_release="mosk-21-0-2-25-2-2",
            version_string="25.2.2",
            major=25,
            minor=2,
            patch=2,
            compatibility=VersionCompatibility.COMPATIBLE,
            warnings=["test warning"],
        )

        result = info.to_dict()

        assert result["cluster_release"] == "mosk-21-0-2-25-2-2"
        assert result["version_string"] == "25.2.2"
        assert result["major"] == 25
        assert result["minor"] == 2
        assert result["patch"] == 2
        assert result["compatibility"] == "compatible"
        assert result["is_compatible"] is True
        assert result["warnings"] == ["test warning"]

    def test_to_dict_with_defaults(self) -> None:
        """Test to_dict with default values."""
        info = MOSKVersionInfo()

        result = info.to_dict()

        assert result["cluster_release"] is None
        assert result["version_string"] is None
        assert result["major"] is None
        assert result["minor"] is None
        assert result["patch"] is None
        assert result["compatibility"] == "unknown"
        assert result["is_compatible"] is False
        assert result["warnings"] == []


# =============================================================================
# parse_mosk_version Tests
# =============================================================================


class TestParseMoskVersion:
    """Tests for parse_mosk_version function."""

    def test_parse_standard_release_name(self) -> None:
        """Test parsing standard MOSK release name."""
        major, minor, patch_ver = parse_mosk_version("mosk-21-0-2-25-2-2")

        assert major == 25
        assert minor == 2
        assert patch_ver == 2

    def test_parse_uppercase_release_name(self) -> None:
        """Test parsing uppercase release name."""
        major, minor, patch_ver = parse_mosk_version("MOSK-21-0-2-25-2-2")

        assert major == 25
        assert minor == 2
        assert patch_ver == 2

    def test_parse_release_without_prefix(self) -> None:
        """Test parsing release name without mosk- prefix."""
        major, minor, patch_ver = parse_mosk_version("21-0-2-25-2-2")

        assert major == 25
        assert minor == 2
        assert patch_ver == 2

    def test_parse_empty_string(self) -> None:
        """Test parsing empty string."""
        major, minor, patch_ver = parse_mosk_version("")

        assert major is None
        assert minor is None
        assert patch_ver is None

    def test_parse_dotted_version(self) -> None:
        """Test parsing dotted version format."""
        major, minor, patch_ver = parse_mosk_version("25.2.3")

        assert major == 25
        assert minor == 2
        assert patch_ver == 3

    def test_parse_dotted_version_no_patch(self) -> None:
        """Test parsing dotted version without patch."""
        major, minor, patch_ver = parse_mosk_version("25.2")

        assert major == 25
        assert minor == 2
        assert patch_ver == 0

    def test_parse_mosk_25_1(self) -> None:
        """Test parsing minimum supported version."""
        major, minor, patch_ver = parse_mosk_version("mosk-21-0-0-25-1-0")

        assert major == 25
        assert minor == 1
        assert patch_ver == 0

    def test_parse_version_at_boundary(self) -> None:
        """Test parsing version exactly at 25."""
        major, minor, patch_ver = parse_mosk_version("mosk-21-0-0-25-0-0")

        assert major == 25
        assert minor == 0
        assert patch_ver == 0

    def test_parse_future_version(self) -> None:
        """Test parsing future version."""
        major, minor, patch_ver = parse_mosk_version("mosk-22-0-0-26-0-0")

        assert major == 26
        assert minor == 0
        assert patch_ver == 0

    def test_parse_invalid_format(self) -> None:
        """Test parsing invalid format returns None."""
        major, minor, patch_ver = parse_mosk_version("invalid-release-name")

        assert major is None
        assert minor is None
        assert patch_ver is None


# =============================================================================
# check_version_compatibility Tests
# =============================================================================


class TestCheckVersionCompatibility:
    """Tests for check_version_compatibility function."""

    def test_compatible_exact_minimum(self) -> None:
        """Test exactly at minimum version is compatible."""
        result = check_version_compatibility(25, 1)
        assert result == VersionCompatibility.COMPATIBLE

    def test_compatible_above_minimum(self) -> None:
        """Test above minimum version is compatible."""
        result = check_version_compatibility(25, 2)
        assert result == VersionCompatibility.COMPATIBLE

        result = check_version_compatibility(26, 0)
        assert result == VersionCompatibility.COMPATIBLE

    def test_unsupported_below_minimum(self) -> None:
        """Test below minimum version is unsupported."""
        result = check_version_compatibility(25, 0)
        assert result == VersionCompatibility.UNSUPPORTED

        result = check_version_compatibility(24, 5)
        assert result == VersionCompatibility.UNSUPPORTED

    def test_unknown_when_major_none(self) -> None:
        """Test unknown when major is None."""
        result = check_version_compatibility(None, 1)
        assert result == VersionCompatibility.UNKNOWN

    def test_unknown_when_minor_none(self) -> None:
        """Test unknown when minor is None."""
        result = check_version_compatibility(25, None)
        assert result == VersionCompatibility.UNKNOWN

    def test_unknown_when_both_none(self) -> None:
        """Test unknown when both are None."""
        result = check_version_compatibility(None, None)
        assert result == VersionCompatibility.UNKNOWN


# =============================================================================
# get_mosk_version Tests
# =============================================================================


class TestGetMoskVersion:
    """Tests for get_mosk_version async function."""

    @pytest.mark.asyncio
    async def test_get_version_success(self) -> None:
        """Test successful version detection."""
        mock_adapter = AsyncMock()
        mock_adapter.get_custom_resource = AsyncMock(
            return_value={"spec": {"providerSpec": {"value": {"release": "mosk-21-0-2-25-2-2"}}}}
        )

        result = await get_mosk_version(mock_adapter, "mos", "default")

        assert result.cluster_release == "mosk-21-0-2-25-2-2"
        assert result.major == 25
        assert result.minor == 2
        assert result.patch == 2
        assert result.version_string == "25.2.2"
        assert result.compatibility == VersionCompatibility.COMPATIBLE

    @pytest.mark.asyncio
    async def test_get_version_unsupported(self) -> None:
        """Test unsupported version detection adds warning."""
        mock_adapter = AsyncMock()
        # Use 25.0 which is parseable but below minimum 25.1
        mock_adapter.get_custom_resource = AsyncMock(
            return_value={"spec": {"providerSpec": {"value": {"release": "mosk-21-0-0-25-0-0"}}}}
        )

        result = await get_mosk_version(mock_adapter)

        assert result.compatibility == VersionCompatibility.UNSUPPORTED
        assert len(result.warnings) > 0
        assert "not supported" in result.warnings[0]

    @pytest.mark.asyncio
    async def test_get_version_unknown(self) -> None:
        """Test unknown version when cluster has no release."""
        mock_adapter = AsyncMock()
        mock_adapter.get_custom_resource = AsyncMock(
            return_value={"spec": {"providerSpec": {"value": {}}}}
        )

        result = await get_mosk_version(mock_adapter)

        assert result.compatibility == VersionCompatibility.UNKNOWN
        assert len(result.warnings) > 0
        assert "Could not determine" in result.warnings[0]

    @pytest.mark.asyncio
    async def test_get_version_cluster_not_found(self) -> None:
        """Test handling when cluster CR not found."""
        mock_adapter = AsyncMock()
        mock_adapter.get_custom_resource = AsyncMock(return_value=None)

        result = await get_mosk_version(mock_adapter)

        assert result.compatibility == VersionCompatibility.UNKNOWN

    @pytest.mark.asyncio
    async def test_get_version_exception_handling(self) -> None:
        """Test exception handling during version detection."""
        mock_adapter = AsyncMock()
        mock_adapter.get_custom_resource = AsyncMock(side_effect=Exception("Connection failed"))

        result = await get_mosk_version(mock_adapter)

        assert result.compatibility == VersionCompatibility.UNKNOWN
        assert len(result.warnings) > 0
        assert "Could not detect" in result.warnings[0]

    @pytest.mark.asyncio
    async def test_get_version_fetches_cluster_release(self) -> None:
        """Test fetching ClusterRelease for additional details."""
        mock_adapter = AsyncMock()

        # First call for Cluster CR
        # Second call for ClusterRelease CR
        mock_adapter.get_custom_resource = AsyncMock(
            side_effect=[
                {"spec": {"providerSpec": {"value": {"release": "mosk-21-0-2-25-2-2"}}}},
                {"spec": {"version": "25.2.2"}},
            ]
        )

        result = await get_mosk_version(mock_adapter)

        assert result.cluster_release == "mosk-21-0-2-25-2-2"
        assert result.raw_data.get("cluster_release_version") == "25.2.2"

    @pytest.mark.asyncio
    async def test_get_version_cluster_release_fetch_fails(self) -> None:
        """Test graceful handling when ClusterRelease fetch fails."""
        mock_adapter = AsyncMock()

        mock_adapter.get_custom_resource = AsyncMock(
            side_effect=[
                {"spec": {"providerSpec": {"value": {"release": "mosk-21-0-2-25-2-2"}}}},
                Exception("ClusterRelease not found"),
            ]
        )

        result = await get_mosk_version(mock_adapter)

        # Should still have version from Cluster CR
        assert result.major == 25
        assert result.minor == 2
        assert result.compatibility == VersionCompatibility.COMPATIBLE


# =============================================================================
# get_version_warning_message Tests
# =============================================================================


class TestGetVersionWarningMessage:
    """Tests for get_version_warning_message function."""

    def test_no_warning_for_compatible(self) -> None:
        """Test no warning returned for compatible versions."""
        info = MOSKVersionInfo(
            compatibility=VersionCompatibility.COMPATIBLE,
            version_string="25.2",
        )

        result = get_version_warning_message(info)

        assert result is None

    def test_warning_for_unsupported(self) -> None:
        """Test warning returned for unsupported versions."""
        info = MOSKVersionInfo(
            compatibility=VersionCompatibility.UNSUPPORTED,
            version_string="24.0",
        )

        result = get_version_warning_message(info)

        assert result is not None
        assert "not supported" in result
        assert "24.0" in result
        assert MIN_SUPPORTED_VERSION_STR in result

    def test_warning_for_unknown(self) -> None:
        """Test warning returned for unknown versions."""
        info = MOSKVersionInfo(
            compatibility=VersionCompatibility.UNKNOWN,
        )

        result = get_version_warning_message(info)

        assert result is not None
        assert "Could not determine" in result
        assert MIN_SUPPORTED_VERSION_STR in result


# =============================================================================
# add_version_warning_to_output Tests
# =============================================================================


class TestAddVersionWarningToOutput:
    """Tests for add_version_warning_to_output function."""

    def test_no_modification_for_compatible(self) -> None:
        """Test output unchanged for compatible versions."""
        output = {"data": "test"}
        info = MOSKVersionInfo(compatibility=VersionCompatibility.COMPATIBLE)

        result = add_version_warning_to_output(output, info)

        assert result == {"data": "test"}
        assert "warnings" not in result

    def test_adds_warnings_key_for_unsupported(self) -> None:
        """Test warnings key added for unsupported versions."""
        output = {"data": "test"}
        info = MOSKVersionInfo(
            compatibility=VersionCompatibility.UNSUPPORTED,
            version_string="24.0",
        )

        result = add_version_warning_to_output(output, info)

        assert "warnings" in result
        assert len(result["warnings"]) == 1
        assert "not supported" in result["warnings"][0]

    def test_prepends_to_existing_warnings(self) -> None:
        """Test warning prepended to existing warnings."""
        output = {"data": "test", "warnings": ["existing warning"]}
        info = MOSKVersionInfo(
            compatibility=VersionCompatibility.UNSUPPORTED,
            version_string="24.0",
        )

        result = add_version_warning_to_output(output, info)

        assert len(result["warnings"]) == 2
        assert "not supported" in result["warnings"][0]
        assert result["warnings"][1] == "existing warning"

    def test_handles_non_list_warnings(self) -> None:
        """Test handling when warnings is not a list."""
        output = {"data": "test", "warnings": "not a list"}
        info = MOSKVersionInfo(
            compatibility=VersionCompatibility.UNSUPPORTED,
            version_string="24.0",
        )

        # Should not raise, but won't modify non-list warnings
        result = add_version_warning_to_output(output, info)

        assert result["warnings"] == "not a list"


# =============================================================================
# Cache Functions Tests
# =============================================================================


class TestCacheFunctions:
    """Tests for version info cache functions."""

    def test_get_cached_version_info_initially_none(self) -> None:
        """Test cache is initially None."""
        clear_cached_version_info()

        result = get_cached_version_info()

        assert result is None

    def test_set_and_get_cached_version_info(self) -> None:
        """Test setting and getting cached version info."""
        clear_cached_version_info()

        info = MOSKVersionInfo(
            cluster_release="mosk-test",
            major=25,
            minor=2,
        )
        set_cached_version_info(info)

        result = get_cached_version_info()

        assert result is info
        assert result.cluster_release == "mosk-test"

        # Cleanup
        clear_cached_version_info()

    def test_clear_cached_version_info(self) -> None:
        """Test clearing cached version info."""
        info = MOSKVersionInfo(major=25, minor=2)
        set_cached_version_info(info)

        clear_cached_version_info()

        result = get_cached_version_info()
        assert result is None

    def test_cache_can_be_overwritten(self) -> None:
        """Test cache can be overwritten with new value."""
        clear_cached_version_info()

        info1 = MOSKVersionInfo(major=25, minor=1)
        info2 = MOSKVersionInfo(major=25, minor=2)

        set_cached_version_info(info1)
        assert get_cached_version_info().minor == 1

        set_cached_version_info(info2)
        assert get_cached_version_info().minor == 2

        # Cleanup
        clear_cached_version_info()


# =============================================================================
# Edge Cases and Integration Tests
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and integration scenarios."""

    def test_version_comparison_boundary(self) -> None:
        """Test version comparison at the boundary."""
        # 25.0 should be unsupported
        result = check_version_compatibility(25, 0)
        assert result == VersionCompatibility.UNSUPPORTED

        # 25.1 should be compatible
        result = check_version_compatibility(25, 1)
        assert result == VersionCompatibility.COMPATIBLE

    def test_parse_various_release_formats(self) -> None:
        """Test parsing various release name formats."""
        # Standard format
        assert parse_mosk_version("mosk-21-0-2-25-2-2") == (25, 2, 2)

        # Without patch
        assert parse_mosk_version("mosk-21-0-25-1") == (25, 1, 0)

        # Just version numbers
        assert parse_mosk_version("25.2.3") == (25, 2, 3)

        # Empty
        assert parse_mosk_version("") == (None, None, None)

    def test_version_info_immutability_of_lists(self) -> None:
        """Test that default lists are independent per instance."""
        info1 = MOSKVersionInfo()
        info2 = MOSKVersionInfo()

        info1.warnings.append("warning1")

        assert len(info1.warnings) == 1
        assert len(info2.warnings) == 0

    def test_version_info_immutability_of_dicts(self) -> None:
        """Test that default dicts are independent per instance."""
        info1 = MOSKVersionInfo()
        info2 = MOSKVersionInfo()

        info1.raw_data["key"] = "value"

        assert "key" in info1.raw_data
        assert "key" not in info2.raw_data
