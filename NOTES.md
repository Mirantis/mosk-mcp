# Notes

Project notes, known issues, and workarounds.

---

## FastMCP bug: JSON Schema for objects with arbitrary fields

### What it is

In FastMCP’s `json_schema_to_type` (from `fastmcp.utilities.json_schema_type`), a **nested** `"object"` schema that has only `additionalProperties: true` and no fixed `"properties"` is converted to an **empty dataclass** instead of `dict[str, Any]`. At the top level the same schema is correctly turned into a dict; only nested properties are affected.

So when a tool’s result schema declares a field as “object with arbitrary keys” (e.g. a `checks` map), the validated value for that field is an empty object with no keys, and any key-based or attribute-based access fails.

### How to reproduce

You can reproduce the behaviour with the script in the repo root, `test_json_schema.py`. Without the patch, the last assertions fail because `data.arbitrary_object` is an empty dataclass instance; with the patch (or after an upstream fix), it is a dict and the assertions pass.

**Example (from `test_json_schema.py`):**

```python
from fastmcp.utilities.json_schema_type import json_schema_to_type
from fastmcp.utilities.types import get_cached_typeadapter

output_schema = {
    "type": "object",
    "properties": {
        "arbitrary_object": {
            "type": "object",
            "additionalProperties": True,
        },
    },
    "required": ["arbitrary_object"],
}

structured_content = {
    "arbitrary_object": {
        "attrA": {"attrA1": "A1"},
        "attrB": {"attrB1": "B1"},
    },
}

output_type = json_schema_to_type(output_schema)
type_adapter = get_cached_typeadapter(output_type)
data = type_adapter.validate_python(structured_content)

# Bug: without patch, data.arbitrary_object is an empty dataclass;
# "attrA" in data.arbitrary_object is False and next line would fail.
assert "attrA" in data.arbitrary_object
assert data.arbitrary_object["attrA"]["attrA1"] == "A1"
```

Run it (without applying the patch inside the script):

```bash
python test_json_schema.py
```

With a buggy FastMCP, the assertions fail; with the monkey patch or a fixed FastMCP, they pass.

### What it affects

- **Tool result schemas** that use an object with only `additionalProperties` (e.g. health check’s `checks` map). The MCP server uses FastMCP’s schema-to-type conversion for tool outputs; when the bug is present, those fields become empty objects.
- **Tests for such tools** (e.g. `tests/test_server.py` for `health_check`) expect the validated result to have a real dict for `checks` (e.g. `"server" in data.checks`). Without the workaround, those tests fail.

### Monkey patch: logic and location

**Location:** `tests/conftest.py`, in the section *“FastMCP json_schema_type monkey patch”*.

**When it runs:** From `pytest_configure(config)`, so once at test collection, before any test runs.

**Logic:**

1. **Bug probe** (`_fastmcp_has_arbitrary_object_bug()`): Builds a small schema with a nested `object` + `additionalProperties: true`, runs `json_schema_to_type` and validates `{"checks": {"server": {...}}}`. If the validated `checks` is a dict and contains the key `"server"`, the bug is considered **absent**.
2. **If the bug is absent:** No patch is applied. A `UserWarning` is emitted suggesting that the FastMCP release is fixed and the patch code in conftest can be removed.
3. **If the bug is present:** `fastmcp.utilities.json_schema_type._schema_to_type` is monkey-patched. The patched function returns `dict[str, Any]` when the schema is `type == "object"`, has no (or empty) `properties`, and has `additionalProperties`; otherwise it delegates to the original `_schema_to_type`. A warning is emitted that the bug was detected and the patch was applied.

So the patch is applied only when the bug is still present and makes tests pass until FastMCP is fixed upstream. After upgrading to a fixed FastMCP, the probe will skip patching and warn that the workaround can be removed.
