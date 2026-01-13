# MOSK MCP Server

> **Minimum Requirements:** MCC 2.28+ | MOSK Platform 25.1+ | Python 3.11+

An MCP (Model Context Protocol) server that enables AI assistants like Claude to manage Mirantis OpenStack for Kubernetes (MOSK) clusters through natural conversation. Query cluster health, troubleshoot issues, generate infrastructure templates, and perform operations - all by simply asking.

**Version:** 0.1.0 | **Tools:** 81 | **Categories:** 11

---

## What Can This MCP Do?

| Capability | Description |
|------------|-------------|
| **Cluster Health Monitoring** | Get unified health scores across Kubernetes, OpenStack, Ceph, and RabbitMQ |
| **Troubleshooting & Diagnostics** | Query logs with natural language, trace requests, diagnose VM/network/storage failures |
| **Node Lifecycle Management** | Track provisioning, check readiness, manage maintenance requests |
| **Template Generation** | Generate YAML manifests for machines, BMH profiles, L2 templates, and more |
| **Operations Visibility** | Monitor upgrades, track migrations, view rollout status |
| **Alerts & Observability** | List active alerts, explain them, get remediation suggestions |
| **Validation & Testing** | Run smoke tests, validate post-upgrade, check service availability |

---

## Quick Start

> **⚠️ Prerequisite:** Before using the MCP server, enable OAuth 2.0 Device Authorization Grant on your MCC Keycloak `kaas` and `k8s` clients. See [Keycloak Configuration](#keycloak-configuration-device-flow) for steps.

### Option 1: Claude Desktop with Docker (Recommended)

This is the simplest way to get started. Docker handles all dependencies.

**Step 1: Create cluster configuration**

Create `~/.config/mosk-mcp/clusters.yaml`:

```yaml
config_version: '1.0'
active: lab
clusters:
  lab:
    url: https://172.16.166.22
    name: LAB
    description: Local development/lab cluster
    environment: development
    ssl_verify: false
  production:
    url: https://mcc.example.com
    name: Production
    description: Production MCC cluster
    environment: production
    ssl_verify: true
confirm_production_switch: true
allow_http_clusters: true
```

**Step 2: Configure Claude Desktop**

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "mosk-mcp": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-v", "~/.config/mosk-mcp:/home/mosk-mcp/.config/mosk-mcp:rw",
        "-e", "MCP_TRANSPORT=stdio",
        "-e", "MCP_METRICS_ENABLED=false",
        "-e", "MCP_AUTH_ENABLED=true",
        "reddydodda/mosk-mcp:latest"
      ]
    }
  }
}
```

> **Note:** On macOS, replace `~` with full path: `/Users/YOUR_USERNAME/.config/mosk-mcp`

**Step 3: Restart Claude Desktop and authenticate**

```
You: "Log me in"
Claude: [Opens browser for secure authentication]
        Complete login in your browser...

You: "What's the cluster health?"
Claude: Overall health: 94% (Healthy)
        - Kubernetes: 98% (All nodes ready)
        - OpenStack: 92% (2 minor alerts)
        - Ceph: 95% (HEALTH_OK)
```

### Option 2: Build and Run Your Own Docker Image

**Step 1: Build the image**
```bash
git clone https://github.com/Mirantis/mosk-mcp.git
cd mosk-mcp
./scripts/docker-build.sh --local
```

**Step 2: Create cluster config and Claude Desktop config**

Same as Option 1, but use your local image name instead of `reddydodda/mosk-mcp:latest`.

### Option 3: Claude Desktop Native (No Docker)

**Step 1: Install the package**
```bash
git clone https://github.com/Mirantis/mosk-mcp.git
cd mosk-mcp
pip install -e .
```

**Step 2: Create cluster configuration**

Create `~/.config/mosk-mcp/clusters.yaml` (same as Option 1).

**Step 3: Configure Claude Desktop**

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mosk-mcp": {
      "command": "python3",
      "args": ["-m", "mosk_mcp"],
      "env": {
        "PYTHONPATH": "/path/to/mosk-mcp/src",
        "MCP_TRANSPORT": "stdio",
        "MCP_AUTH_ENABLED": "true"
      }
    }
  }
}
```

### Option 4: Claude CLI

```bash
claude mcp add mosk-mcp \
  -e MCP_TRANSPORT=stdio \
  -e MCP_AUTH_ENABLED=true \
  -- python3 -m mosk_mcp
```

---

## Supported Tools (81 Total)

### Authentication (5 tools)
Secure OAuth 2.0 Device Flow authentication - no passwords in chat.

| Tool | Description |
|------|-------------|
| `login_secure` | Browser-based authentication with MFA support |
| `login_start` | Start Device Flow with automatic polling |
| `login_complete` | Check authentication status |
| `logout` | End session and clear tokens |
| `session_status` | View token expiry, roles, connected clusters |

### Cluster Management (5 tools)
Manage multiple MCC clusters safely.

| Tool | Description |
|------|-------------|
| `list_clusters` | List all configured clusters with status |
| `current_cluster` | Show active cluster details |
| `switch_cluster` | Switch clusters (clears session for safety) |
| `add_cluster` | Add new cluster configuration |
| `lock_cluster` | Prevent accidental cluster switches |

### Template Generation (7 tools)
Generate infrastructure-as-code templates without applying them.

| Tool | Description |
|------|-------------|
| `generate_bmhi` | BareMetalHostInventory CR for server registration |
| `generate_bmhp` | BareMetalHostProfile CR for disk/RAID config |
| `generate_machine` | Machine CR for node provisioning |
| `generate_node_templates` | Complete node setup (Secret + BMHi + Machine) |
| `generate_l2template` | L2Template CR for network config |
| `generate_osdpl_patch` | JSON patch for OpenStackDeployment |
| `validate_template` | Validate YAML syntax and schema |

### Ceph Storage (7 tools)
Monitor distributed storage health and capacity.

| Tool | Description |
|------|-------------|
| `get_ceph_status` | Cluster health, OSD count, PG status |
| `list_osds` | All OSDs with status, host, utilization |
| `get_osd_details` | Detailed OSD info including PG distribution |
| `get_ceph_capacity` | Storage breakdown by pool and device class |
| `get_pg_status` | Placement group health and distribution |
| `get_recovery_status` | Rebalancing progress and ETA |
| `predict_capacity` | Forecast when thresholds will be reached |

### RabbitMQ Messaging (4 tools)
Monitor OpenStack message queues.

| Tool | Description |
|------|-------------|
| `get_rabbitmq_status` | Cluster health, alarms, partitions |
| `list_rabbitmq_queues` | Queue depths, consumers, backlogs |
| `get_rabbitmq_connections` | Connection pool utilization |
| `diagnose_rabbitmq_issue` | Comprehensive diagnosis with known issues |

### Node Lifecycle (11 tools)
Manage bare metal servers and Kubernetes nodes.

| Tool | Description |
|------|-------------|
| `list_machines` | Machine CRs with status and role |
| `get_machine_details` | Detailed machine info with events |
| `list_bmh` | BareMetalHost resources |
| `list_bmhp` | BareMetalHostProfile resources |
| `list_l2templates` | L2Template configurations |
| `get_node_readiness` | Check if node is ready for operations |
| `get_migration_status` | Track Nova live migrations |
| `get_node_provision_progress` | BMHi -> BMH -> Machine -> Node tracking |
| `get_ipamhost_details` | Network config (IPs, bonds, VLANs) |
| `create_maintenance_request` | Generate maintenance CR (dry-run) |
| `apply_machine` | Provision node (requires CRQ) |

### Operations Visibility (16 tools)
Monitor deployments, upgrades, and platform operations.

| Tool | Description |
|------|-------------|
| `list_osdpl` | OpenStackDeployment resources |
| `get_openstack_deployment_status` | OSDPL phase and service status |
| `get_openstack_upgrade_progress` | Per-component upgrade tracking |
| `get_component_versions` | Current vs target versions |
| `get_mosk_platform_status` | Cluster CR status from MCC |
| `get_mosk_platform_upgrade_progress` | K8s/LCM layer upgrade tracking |
| `list_available_releases` | Available MOSK releases |
| `list_live_migrations` | Active VM migrations |
| `get_migration_eta` | Migration completion estimates |
| `list_maintenance_requests` | Maintenance CR status |
| `get_rollout_status` | Deployment/StatefulSet rollouts |
| `get_node_conditions` | Node health and taints |
| `monitor_operation` | Track long-running operations |
| `apply_osdpl_patch` | Modify OSDPL (requires CRQ) |
| `apply_cluster_release_patch` | Change MOSK version (requires CRQ) |
| `commence_cluster_upgrade` | Start upgrade (requires CRQ) |

### Cluster Health (8 tools)
Unified health across all components.

| Tool | Description |
|------|-------------|
| `get_mosk_cluster_health` | Combined health score across all layers |
| `get_kubernetes_health` | API server, nodes, system pods |
| `get_openstack_health` | Control plane and hypervisors |
| `get_ceph_health` | Storage cluster health |
| `list_active_alerts` | Firing Prometheus alerts |
| `get_alert_details` | Alert context and remediation |
| `run_preflight_check` | Pre-operation validation |
| `get_resource_utilization` | CPU, memory, storage usage |

### Troubleshooting (11 tools)
Diagnose issues and find solutions.

| Tool | Description |
|------|-------------|
| `query_logs` | Natural language log search |
| `get_pod_logs` | Live Kubernetes pod logs |
| `correlate_events` | Find related events in time window |
| `explain_alert` | Alert explanation and remediation |
| `trace_request` | Trace OpenStack request by ID |
| `diagnose_vm_failure` | Analyze VM spawn/boot/migration failures |
| `diagnose_network_issue` | Debug connectivity problems |
| `diagnose_storage_issue` | Investigate volume/Ceph issues |
| `get_known_issues` | Search knowledge base |
| `suggest_resolution` | AI-powered fix suggestions |
| `create_diagnostic_bundle` | Generate support bundle |

### Validation (4 tools)
Test and validate cluster functionality.

| Tool | Description |
|------|-------------|
| `check_service_availability` | Probe OpenStack API endpoints |
| `run_smoke_test` | VM lifecycle, storage, full stack tests |
| `run_post_upgrade_validation` | Post-OpenStack upgrade validation |
| `run_mosk_platform_validation` | Post-MOSK upgrade validation |

### Utility (3 tools)
Server health and diagnostics.

| Tool | Description |
|------|-------------|
| `health_check` | MCP server health |
| `server_info` | Server version and capabilities |
| `echo` | Test connectivity |

---

## Supported Workflows

### Node Provisioning
```
1. generate_node_templates() → Create BMHi + BMHp + Machine templates
2. validate_template() → Validate all templates
3. apply_machine(crq=CRQ...) → Provision the node
4. get_node_provision_progress() → Track BMHi→BMH→Machine→Node
5. get_node_readiness() → Verify node is ready
```

### Cluster Upgrade (MOSK Platform)
```
1. list_available_releases() → See available MOSK versions
2. run_preflight_check(type=upgrade) → Validate readiness
3. apply_cluster_release_patch(crq=CRQ...) → Change version
4. get_mosk_platform_upgrade_progress() → Track upgrade
5. run_mosk_platform_validation() → Validate post-upgrade
```

### OpenStack Service Upgrade
```
1. get_openstack_deployment_status() → Check current state
2. apply_osdpl_patch(crq=CRQ...) → Change OpenStack version
3. get_openstack_upgrade_progress() → Track per-component progress
4. run_post_upgrade_validation() → Smoke tests and validation
```

### Troubleshooting
```
1. list_active_alerts() → Find current issues
2. get_alert_details() → Understand what's wrong
3. query_logs() → Search relevant logs
4. correlate_events() → Find related issues
5. diagnose_vm_failure() / diagnose_network_issue() → Specific diagnosis
6. suggest_resolution() → Get AI-powered fixes
```

### Node Maintenance
```
1. get_node_readiness(check_type=maintenance) → Verify safe to maintain
2. create_maintenance_request() → Generate maintenance CR
3. get_migration_status() → Track VM migrations off node
4. list_maintenance_requests() → Monitor maintenance status
```

---

## Configuration

All settings use the `MCP_` prefix as environment variables.

### Core Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_MCC_URL` | - | MCC URL (not needed if using `clusters.yaml`) |
| `MCP_SSL_VERIFY` | `true` | Verify SSL certificates |
| `MCP_LOG_LEVEL` | `INFO` | DEBUG, INFO, WARNING, ERROR |
| `MCP_TRANSPORT` | `stdio` | stdio, http, streamable-http |
| `MCP_AUTH_ENABLED` | `true` | Enable OAuth 2.0 Device Flow authentication |
| `MCP_METRICS_ENABLED` | `true` | Enable Prometheus metrics endpoint |

### Device Flow (OAuth 2.0)

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_DEVICE_FLOW_ENABLED` | `true` | Enable browser-based auth |
| `MCP_DEVICE_FLOW_CLIENT_ID` | `kaas` | Keycloak client ID |
| `MCP_DEVICE_FLOW_CODE_LIFESPAN` | `600` | Login code validity (seconds) |

### Privacy Protection

Automatic data redaction when using cloud LLM providers.

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_PRIVACY_ENABLED` | `false` | Enable privacy protection |
| `MCP_PRIVACY_LEVEL` | `standard` | none, minimal, standard, aggressive |

---

## Keycloak Configuration (Device Flow)

The MOSK MCP Server uses OAuth 2.0 Device Authorization Flow for secure browser-based authentication. This requires enabling a single setting on two MCC Keycloak clients.

### Enable Device Authorization Grant

**Step 1: Access Keycloak Admin Console**

```
https://<mcc-url>/auth/admin/
```

Login with Keycloak admin credentials.

**Step 2: Enable Device Flow on `kaas` Client**

1. Select the **iam** realm
2. Go to **Clients** → Select **kaas**
3. In **Settings** tab, find **Capability config** section
4. Enable ✅ **OAuth 2.0 Device Authorization Grant**
5. Click **Save**

**Step 3: Enable Device Flow on `k8s` Client**

1. Go to **Clients** → Select **k8s**
2. In **Settings** tab, find **Capability config** section
3. Enable ✅ **OAuth 2.0 Device Authorization Grant**
4. Click **Save**

> **Note:** Both clients must have this setting enabled for full MCC/MOSK authentication support.

**Step 4: Verify Configuration**

Test the device authorization endpoint:
```bash
curl -k https://<mcc-url>/auth/realms/iam/protocol/openid-connect/auth/device
```

A successful response returns JSON (not a 404 error).

### Keycloak Endpoints Reference

| Endpoint | URL |
|----------|-----|
| **Device Authorization** | `https://<mcc>/auth/realms/iam/protocol/openid-connect/auth/device` |
| **Token** | `https://<mcc>/auth/realms/iam/protocol/openid-connect/token` |
| **UserInfo** | `https://<mcc>/auth/realms/iam/protocol/openid-connect/userinfo` |

### Token Lifespans

Default MCC Keycloak settings:
- Access token: 5 minutes
- Refresh token: 30 minutes

The MCP server automatically handles token refresh.

---

## Multi-Cluster Management

Manage multiple MCC clusters with safe switching.

### Configuration File

Create `~/.config/mosk-mcp/clusters.yaml`:

```yaml
config_version: '1.0'
active: lab
confirm_production_switch: true
allow_http_clusters: true

clusters:
  lab:
    url: https://172.16.166.22
    name: LAB
    description: Local development/lab cluster
    environment: development
    ssl_verify: false
    is_locked: false

  production-us:
    url: https://mcc-us.example.com
    name: Production US
    description: US production MCC cluster
    environment: production
    ssl_verify: true
    is_locked: false

  production-eu:
    url: https://mcc-eu.example.com
    name: Production EU
    description: EU production MCC cluster
    environment: production
    ssl_verify: true
    is_locked: false
```

### Example Usage
```
You: "List my clusters"
Claude: You have 3 configured clusters:
        - lab (active, development)
        - production-us (production)
        - production-eu (production)

You: "Switch to production-us"
Claude: Switching from internal-cloud-us to internal-cloud-eu.
        This is a PRODUCTION cluster. Session will be cleared.
        Confirm switch? [Yes/No]
```

---

## Privileged Operations

Operations that modify cluster state require a **Change Request (CRQ)** number for audit compliance.

### CRQ Format
`CRQ` followed by 9 digits: `CRQ123456789`

### Operations Requiring CRQ

| Operation | Tool |
|-----------|------|
| Provision machines | `apply_machine` |
| Modify OpenStack deployment | `apply_osdpl_patch` |
| Change platform version | `apply_cluster_release_patch` |
| Start cluster upgrades | `commence_cluster_upgrade` |

All privileged operations default to **dry-run mode**. Pass `dry_run=false` to execute.

---

## Docker Usage

### For Claude Desktop (Recommended)

Claude Desktop manages the container lifecycle automatically. Configure via `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mosk-mcp": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-v", "/Users/YOUR_USERNAME/.config/mosk-mcp:/home/mosk-mcp/.config/mosk-mcp:rw",
        "-e", "MCP_TRANSPORT=stdio",
        "-e", "MCP_METRICS_ENABLED=false",
        "-e", "MCP_AUTH_ENABLED=true",
        "reddydodda/mosk-mcp:latest"
      ]
    }
  }
}
```

### For Manual Testing / HTTP Mode

```bash
# Build local image
./scripts/docker-build.sh --local

# Start in HTTP mode for testing
./scripts/docker-run.sh start --mcc-url https://mcc.example.com

# Status, logs, stop
./scripts/docker-run.sh status
./scripts/docker-run.sh logs -f
./scripts/docker-run.sh stop
```

---

## Architecture

```
┌─────────────────┐     ┌─────────────────┐
│   Claude/MCP    │     │    Keycloak     │
│     Client      │────▶│   (OIDC Auth)   │
└────────┬────────┘     └─────────────────┘
         │
         ▼
┌─────────────────┐
│  MOSK MCP       │
│  Server         │
└────────┬────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌───────┐ ┌───────┐
│  MCC  │ │ MOSK  │
│Cluster│ │Cluster│
└───────┘ └───────┘
```

**MCC (Management Cluster):** Runs LCM controllers, hosts Machine/BMHi/BMHp CRs
**MOSK (Workload Cluster):** Runs OpenStack services, hosts OSDPL, Ceph

---

## Development

```bash
# Run tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=mosk_mcp

# Code quality
ruff format src tests
ruff check src tests
mypy src
```

---

## Troubleshooting

**Connection refused:**
```bash
curl -k https://your-mcc-url/config.js
```

**SSL certificate errors:**
```bash
export MCP_SSL_VERIFY=false
```

**Authentication failures:**
```
You: "Check session status"
You: "Log me in"  # If not authenticated
```

---

## Known Limitations

- OSD removal tools deferred to future release
- Machine deletion tool deferred to future release
- Upgrade rollback not supported through MCP tools

---

## License

Apache License 2.0
