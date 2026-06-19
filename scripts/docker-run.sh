#!/bin/bash
# Docker run script for MOSK MCP Server with multi-cluster support
#
# Usage: ./scripts/docker-run.sh [command] [options]
#
# Commands:
#   start      Start the MCP server container
#   stop       Stop the running container
#   status     Show container status
#   logs       Show container logs
#   shell      Open a shell in the container
#   config     Show or validate cluster configuration
#
# Examples:
#   # Start with default management cluster URL (from environment or clusters.yaml)
#   ./scripts/docker-run.sh start
#
#   # Start with specific management cluster URL (legacy single-cluster mode)
#   ./scripts/docker-run.sh start --mgmt-url https://mcc.example.com
#
#   # Start with multi-cluster config file
#   ./scripts/docker-run.sh start --config ~/.config/mosk-mcp/clusters.yaml
#
#   # Start with specific cluster profile
#   MCP_PROFILE=prod ./scripts/docker-run.sh start

set -e

# Default values
REGISTRY="${REGISTRY:-reddydodda}"
IMAGE_NAME="${IMAGE_NAME:-mosk-mcp}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
CONTAINER_NAME="${CONTAINER_NAME:-mosk-mcp}"

# Ports
HTTP_PORT="${MCP_HTTP_PORT:-8080}"
METRICS_PORT="${MCP_METRICS_PORT:-9090}"

# Transport (stdio, http, streamable_http)
TRANSPORT="${MCP_TRANSPORT:-http}"

# Config paths
CONFIG_DIR="${MCP_CONFIG_DIR:-$HOME/.config/mosk-mcp}"
CONFIG_FILE="${CONFIG_DIR}/clusters.yaml"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

show_help() {
    cat << 'EOF'
MOSK MCP Server - Docker Run Script

USAGE:
    ./scripts/docker-run.sh [COMMAND] [OPTIONS]

COMMANDS:
    start       Start the MCP server container
    stop        Stop the running container
    restart     Restart the container
    status      Show container status
    logs        Show container logs (use -f for follow)
    shell       Open a shell in the running container
    config      Show or validate cluster configuration
    help        Show this help message

OPTIONS:
    --mgmt-url URL           Management cluster URL (single-cluster mode - no config needed)
    --ssl-verify true|false SSL verification (default: true)
    --config FILE           Path to clusters.yaml config file
    --profile NAME          Cluster profile to activate (from config)
    --transport TYPE        Transport type: stdio, http (default: http)
    --port PORT             HTTP port (default: 8080)
    --metrics-port PORT     Metrics port (default: 9090)
    --detach, -d            Run in detached mode
    --no-metrics            Disable metrics/health server
    --dev                   Use development image
    --debug                 Enable debug logging

SINGLE-CLUSTER MODE (simplest):
    Use --mgmt-url to connect to one cluster. No config file needed.

    ./scripts/docker-run.sh start --mgmt-url https://mcc.example.com
    ./scripts/docker-run.sh start --mgmt-url https://172.16.166.22 --ssl-verify false

MULTI-CLUSTER MODE:
    Configure multiple clusters in ~/.config/mosk-mcp/clusters.yaml
    Cluster IDs can be any name (customer names, regions, etc.)

    Example clusters.yaml:
    ---
    active: internal-cloud-us
    confirm_production_switch: true
    clusters:
      internal-cloud-us:
        url: "https://mcc-us.internal.example.com"
        name: "Internal Cloud US"
        environment: production
      internal-cloud-eu:
        url: "https://mcc-eu.internal.example.com"
        name: "Internal Cloud EU"
        environment: production
      pre-sales-demo:
        url: "https://presales.example.com"
        name: "Pre-Sales Demo"
        environment: staging
        ssl_verify: false
      customer-abc:
        url: "https://abc-cloud.example.com"
        name: "ABC Cloud"
        environment: production
        is_locked: true  # Prevent accidental switches

    Environment types (safety levels):
      - development: HTTP allowed, SSL verify optional
      - staging: HTTPS recommended
      - production: HTTPS required, SSL verify required

ENVIRONMENT VARIABLES:
    MCP_MGMT_URL             Single-cluster mode (bypasses config file)
    MCP_SSL_VERIFY          SSL verification (true/false)
    MCP_PROFILE             Active cluster from config file
    MCP_AUTH_ENABLED        Enable authentication (default: true)
    MCP_TRANSPORT           Transport type
    MCP_HTTP_PORT           HTTP port
    MCP_METRICS_PORT        Metrics/health port

EXAMPLES:
    # Single cluster - quickest way to get started
    ./scripts/docker-run.sh start --mgmt-url https://mcc.example.com

    # Single cluster with SSL disabled (for self-signed certs)
    ./scripts/docker-run.sh start --mgmt-url https://172.16.166.22 --ssl-verify false

    # Multi-cluster - use config file
    ./scripts/docker-run.sh start

    # Multi-cluster - select specific cluster
    ./scripts/docker-run.sh start --profile internal-cloud-eu
    # or
    MCP_PROFILE=internal-cloud-eu ./scripts/docker-run.sh start

    # Start in background
    ./scripts/docker-run.sh start -d --mgmt-url https://mcc.example.com

    # View logs
    ./scripts/docker-run.sh logs -f

    # Show cluster configuration
    ./scripts/docker-run.sh config

EOF
}

ensure_config_dir() {
    if [ ! -d "$CONFIG_DIR" ]; then
        log_info "Creating config directory: $CONFIG_DIR"
        mkdir -p "$CONFIG_DIR"
    fi
}

create_sample_config() {
    if [ ! -f "$CONFIG_FILE" ]; then
        log_info "Creating sample cluster configuration: $CONFIG_FILE"
        cat > "$CONFIG_FILE" << 'YAML'
# MOSK MCP Server - Multi-Cluster Configuration
#
# This file defines management clusters you can connect to.
# Use 'list_clusters' and 'switch_cluster' tools to manage clusters.
#
# SINGLE-CLUSTER MODE:
#   If you only use one cluster, you can skip this file entirely and use:
#   ./scripts/docker-run.sh start --mgmt-url https://your-mgmt-url
#
# MULTI-CLUSTER MODE:
#   Define your clusters below. Cluster IDs can be any name you want.
#   The 'environment' field controls safety level (not the cluster name):
#     - development: Allows HTTP, no SSL verify required
#     - staging: HTTPS recommended
#     - production: HTTPS required, SSL verify required, confirmation needed
#
# Environment variables:
#   MCP_PROFILE - Override active cluster (e.g., MCP_PROFILE=internal-cloud-eu)
#   MCP_MGMT_URL - Single-cluster mode (bypasses this config file)

# Currently active cluster (can be overridden by MCP_PROFILE env var)
active: null  # Set to your default cluster ID

# Require explicit confirmation when switching to production clusters
confirm_production_switch: true

# Cluster definitions - customize for your environment
clusters: {}
  # Example configurations (uncomment and modify):
  #
  # internal-cloud-us:
  #   url: "https://mcc-us.internal.example.com"
  #   name: "Internal Cloud US"
  #   environment: production
  #   description: "US region internal cloud"
  #
  # internal-cloud-eu:
  #   url: "https://mcc-eu.internal.example.com"
  #   name: "Internal Cloud EU"
  #   environment: production
  #   description: "EU region internal cloud"
  #
  # pre-sales-us:
  #   url: "https://presales.example.com"
  #   name: "Pre-Sales Demo US"
  #   environment: staging
  #   ssl_verify: false  # OK for demos
  #   description: "Pre-sales demonstration environment"
  #
  # customer-abc:
  #   url: "https://abc-cloud.example.com"
  #   name: "ABC Customer Cloud"
  #   environment: production
  #   is_locked: true  # Prevent accidental switches
  #   description: "ABC customer production - handle with care"
  #
  # local-dev:
  #   url: "https://172.16.166.22"
  #   name: "Local Development"
  #   environment: development
  #   ssl_verify: false
YAML
        log_info "Sample config created: $CONFIG_FILE"
        log_info "For single-cluster use: ./scripts/docker-run.sh start --mgmt-url https://your-url"
        log_info "For multi-cluster use: Edit $CONFIG_FILE and add your clusters"
    fi
}

cmd_start() {
    local DETACH=""
    local MGMT_URL=""
    local SSL_VERIFY=""
    local PROFILE=""
    local DEV_IMAGE=""
    local DEBUG=""
    local NO_METRICS=""

    # Parse options
    while [[ $# -gt 0 ]]; do
        case $1 in
            --mgmt-url)
                MGMT_URL="$2"
                shift 2
                ;;
            --ssl-verify)
                SSL_VERIFY="$2"
                shift 2
                ;;
            --config)
                CONFIG_FILE="$2"
                shift 2
                ;;
            --profile)
                PROFILE="$2"
                shift 2
                ;;
            --transport)
                TRANSPORT="$2"
                shift 2
                ;;
            --port)
                HTTP_PORT="$2"
                shift 2
                ;;
            --metrics-port)
                METRICS_PORT="$2"
                shift 2
                ;;
            -d|--detach)
                DETACH="-d"
                shift
                ;;
            --no-metrics)
                NO_METRICS="true"
                shift
                ;;
            --dev)
                DEV_IMAGE=":development"
                shift
                ;;
            --debug)
                DEBUG="true"
                shift
                ;;
            *)
                log_error "Unknown option: $1"
                exit 1
                ;;
        esac
    done

    # Ensure config directory exists
    ensure_config_dir
    create_sample_config

    # Stop existing container if running
    if docker ps -q -f name="^${CONTAINER_NAME}$" | grep -q .; then
        log_warn "Stopping existing container..."
        docker stop "$CONTAINER_NAME" >/dev/null 2>&1 || true
    fi
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

    # Build docker run command
    local DOCKER_CMD=(
        docker run
        --name "$CONTAINER_NAME"
        -p "${HTTP_PORT}:8080"
    )

    # Add metrics port if enabled
    if [ -z "$NO_METRICS" ]; then
        DOCKER_CMD+=(-p "${METRICS_PORT}:9090")
        DOCKER_CMD+=(-e "MCP_METRICS_ENABLED=true")
    else
        DOCKER_CMD+=(-e "MCP_METRICS_ENABLED=false")
    fi

    # Mount config directory
    DOCKER_CMD+=(-v "${CONFIG_DIR}:/home/mosk-mcp/.config/mosk-mcp:ro")

    # Add environment variables
    DOCKER_CMD+=(-e "MCP_TRANSPORT=${TRANSPORT}")
    DOCKER_CMD+=(-e "MCP_AUTH_ENABLED=true")

    # Single-cluster mode with --mgmt-url
    if [ -n "$MGMT_URL" ]; then
        DOCKER_CMD+=(-e "MCP_MGMT_URL=${MGMT_URL}")
        log_info "Using single-cluster mode"
    fi

    # SSL verification (from --ssl-verify or environment)
    if [ -n "$SSL_VERIFY" ]; then
        DOCKER_CMD+=(-e "MCP_SSL_VERIFY=${SSL_VERIFY}")
    elif [ -n "${MCP_SSL_VERIFY:-}" ]; then
        DOCKER_CMD+=(-e "MCP_SSL_VERIFY=${MCP_SSL_VERIFY}")
    fi

    # Profile override (for multi-cluster mode)
    if [ -n "$PROFILE" ]; then
        DOCKER_CMD+=(-e "MCP_PROFILE=${PROFILE}")
    elif [ -n "${MCP_PROFILE:-}" ]; then
        DOCKER_CMD+=(-e "MCP_PROFILE=${MCP_PROFILE}")
    fi

    # Debug logging
    if [ -n "$DEBUG" ]; then
        DOCKER_CMD+=(-e "MCP_LOG_LEVEL=DEBUG")
        DOCKER_CMD+=(-e "MCP_LOG_FORMAT=console")
    fi

    # Detached mode
    if [ -n "$DETACH" ]; then
        DOCKER_CMD+=(-d)
    else
        DOCKER_CMD+=(--rm -it)
    fi

    # Image name
    local IMAGE="${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}${DEV_IMAGE}"
    DOCKER_CMD+=("$IMAGE")

    # Show configuration
    echo ""
    log_info "Starting MOSK MCP Server"
    echo "  ─────────────────────────────────────"
    echo "  Image:          ${IMAGE}"
    echo "  Container:      ${CONTAINER_NAME}"
    echo "  Transport:      ${TRANSPORT}"
    echo "  HTTP Port:      ${HTTP_PORT}"
    [ -z "$NO_METRICS" ] && echo "  Metrics Port:   ${METRICS_PORT}"
    if [ -n "$MGMT_URL" ]; then
        echo "  Mode:           Single-cluster"
        echo "  Mgmt URL:       ${MGMT_URL}"
        echo "  SSL Verify:     ${SSL_VERIFY:-true}"
    else
        echo "  Mode:           Multi-cluster"
        echo "  Config Dir:     ${CONFIG_DIR}"
        [ -n "$PROFILE" ] && echo "  Profile:        ${PROFILE}"
    fi
    echo "  ─────────────────────────────────────"
    echo ""

    # Run the container
    "${DOCKER_CMD[@]}"

    if [ -n "$DETACH" ]; then
        echo ""
        log_info "Container started in background"
        echo ""
        echo "  View logs:      ./scripts/docker-run.sh logs"
        echo "  Stop:           ./scripts/docker-run.sh stop"
        echo "  Health check:   curl http://localhost:${METRICS_PORT}/health"
        echo ""
    fi
}

cmd_stop() {
    if docker ps -q -f name="^${CONTAINER_NAME}$" | grep -q .; then
        log_info "Stopping container: ${CONTAINER_NAME}"
        docker stop "$CONTAINER_NAME"
        log_info "Container stopped"
    else
        log_warn "Container not running: ${CONTAINER_NAME}"
    fi
}

cmd_restart() {
    cmd_stop
    sleep 1
    cmd_start "$@"
}

cmd_status() {
    echo ""
    log_info "Container Status"
    echo "  ─────────────────────────────────────"

    if docker ps -q -f name="^${CONTAINER_NAME}$" | grep -q .; then
        echo "  Status:     RUNNING"
        docker ps --format "  ID:         {{.ID}}\n  Image:      {{.Image}}\n  Ports:      {{.Ports}}\n  Created:    {{.RunningFor}}" -f name="^${CONTAINER_NAME}$"

        # Health check
        echo ""
        if curl -sf "http://localhost:${METRICS_PORT}/health" >/dev/null 2>&1; then
            echo "  Health:     ✓ Healthy"
        else
            echo "  Health:     ✗ Unhealthy or metrics disabled"
        fi
    else
        echo "  Status:     STOPPED"
    fi
    echo "  ─────────────────────────────────────"
    echo ""
}

cmd_logs() {
    local FOLLOW=""
    local TAIL="100"

    while [[ $# -gt 0 ]]; do
        case $1 in
            -f|--follow)
                FOLLOW="-f"
                shift
                ;;
            --tail)
                TAIL="$2"
                shift 2
                ;;
            *)
                shift
                ;;
        esac
    done

    docker logs $FOLLOW --tail "$TAIL" "$CONTAINER_NAME"
}

cmd_shell() {
    if docker ps -q -f name="^${CONTAINER_NAME}$" | grep -q .; then
        docker exec -it "$CONTAINER_NAME" /bin/bash
    else
        log_error "Container not running: ${CONTAINER_NAME}"
        exit 1
    fi
}

cmd_config() {
    echo ""
    log_info "Cluster Configuration"
    echo "  ─────────────────────────────────────"
    echo "  Config Dir:   ${CONFIG_DIR}"
    echo "  Config File:  ${CONFIG_FILE}"
    echo ""

    if [ -f "$CONFIG_FILE" ]; then
        echo "  Contents:"
        echo "  ─────────────────────────────────────"
        cat "$CONFIG_FILE" | sed 's/^/  /'
    else
        log_warn "Config file not found"
        echo ""
        echo "  Run 'start' to create a sample configuration"
    fi
    echo ""
}

# Main command dispatcher
COMMAND="${1:-help}"
shift || true

case "$COMMAND" in
    start)
        cmd_start "$@"
        ;;
    stop)
        cmd_stop
        ;;
    restart)
        cmd_restart "$@"
        ;;
    status)
        cmd_status
        ;;
    logs)
        cmd_logs "$@"
        ;;
    shell)
        cmd_shell
        ;;
    config)
        cmd_config
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        log_error "Unknown command: $COMMAND"
        echo "Run './scripts/docker-run.sh help' for usage"
        exit 1
        ;;
esac
