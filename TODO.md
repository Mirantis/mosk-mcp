# TODO

- [ ] **Raise the issue with the developers of FastMCP** — Nested JSON Schema `object` with only `additionalProperties` is converted to an empty dataclass instead of `dict[str, Any]`. See [NOTES.md](NOTES.md#fastmcp-bug-json-schema-for-objects-with-arbitrary-fields) for details and reproduction.
- [ ] **Remove the `fastmcp.utilities.json_schema_type` monkey patching logic** from tests (conftest.py) See [NOTES.md](NOTES.md#fastmcp-bug-json-schema-for-objects-with-arbitrary-fields) for details and reproduction..

## `oidc_client_id` setting — behavioral caveats

The following issues remain; fixing them is separate work.

- [ ] **Wire `oidc_client_id` into device flow** — `UserSession._oidc_client_id_override` is set from `settings.oidc_client_id` in [`src/mosk_mcp/auth/session.py`](src/mosk_mcp/auth/session.py) but is never read after `__init__`.
- [ ] **Stop hardcoding `kaas` in device flow** — [`src/mosk_mcp/tools/auth/device_flow_login.py`](src/mosk_mcp/tools/auth/device_flow_login.py) `_initiate_device_flows()` and `_establish_session()` use literal `client_id="kaas"` instead of the settings override or discovered value.
- [ ] **Use auto-discovered client ID** — `discover_mcc_endpoints()` parses `clientId` from management cluster `config.js` into `MCCEndpoints.keycloak_client_id` ([`src/mosk_mcp/auth/keycloak_client.py`](src/mosk_mcp/auth/keycloak_client.py)), but device flow ignores it.
- [ ] **Apply Keycloak URL/realm overrides in device flow** — `_keycloak_url_override` and `_realm_override` are stored on `UserSession` but device flow uses only discovered `_mcc_endpoints.keycloak_url` / `keycloak_realm`.
- [ ] **Consolidate or remove `device_flow_client_id`** — [`src/mosk_mcp/core/config.py`](src/mosk_mcp/core/config.py) defines `device_flow_client_id` (default `"kaas"`) but it is never referenced anywhere in the codebase.
- [ ] **Document MOSK vs management cluster clients** — MOSK cluster auth always uses hardcoded `"k8s"`; `oidc_client_id` only applies to the management cluster (`kaas`) side.
- [ ] **Clarify effective default** — auto-discovery fallback is `"kaas"` in `discover_mcc_endpoints()`; device flow always uses `"kaas"` regardless of `oidc_client_id` or discovery.
