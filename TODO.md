# TODO

- [ ] **Raise the issue with the developers of FastMCP** — Nested JSON Schema `object` with only `additionalProperties` is converted to an empty dataclass instead of `dict[str, Any]`. See [NOTES.md](NOTES.md#fastmcp-bug-json-schema-for-objects-with-arbitrary-fields) for details and reproduction.
- [ ] **Remove the `fastmcp.utilities.json_schema_type` monkey patching logic** from tests (conftest.py) See [NOTES.md](NOTES.md#fastmcp-bug-json-schema-for-objects-with-arbitrary-fields) for details and reproduction..
