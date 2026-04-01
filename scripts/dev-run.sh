#!/bin/bash

# Script to spin up a debug environment for MCP server, connecting to 
# a lab MOSK cluster via SSH dynamic SOCKS proxy, and run the MCP inspector
# and MCP server in the foreground.

# Source the environment variables
source "${DOTENV_PATH:-.env}"

SOCKS_PROXY_PORT="${SOCKS_PROXY_PORT:-1080}"
SOCKS_PROXY_URL=socks5://127.0.0.1:$SOCKS_PROXY_PORT

MCP_TRANSPORT=streamable-http
MCP_HTTP_HOST=127.0.0.1
MCP_HTTP_PORT="${MCP_HTTP_PORT:-8080}"
MCP_URL=http://$MCP_HTTP_HOST:$MCP_HTTP_PORT/mcp

SSH_PROXY_PID=""
INSPECTOR_PID=""

if ! command -v npx &>/dev/null; then
    echo "Error: npx is not installed. Please install Node.js and npx to run the MCP inspector."
    exit 1
fi

cleanup_jobs() {
    trap - EXIT TERM
    [ -n "${SSH_PROXY_PID}" ] && kill "${SSH_PROXY_PID}" 2>/dev/null || true
    [ -n "${INSPECTOR_PID}" ] && kill "${INSPECTOR_PID}" 2>/dev/null || true
    [ -n "${SSH_PROXY_PID}" ] && wait "${SSH_PROXY_PID}" 2>/dev/null || true
    [ -n "${INSPECTOR_PID}" ] && wait "${INSPECTOR_PID}" 2>/dev/null || true
}
trap cleanup_jobs EXIT TERM

echo "Starting SSH Dynamic SOCKS Proxy..."
ssh $MOSK_LAB_JUMP_SSH -N -D :$SOCKS_PROXY_PORT &
SSH_PROXY_PID=$!

npx @modelcontextprotocol/inspector --server-url $MCP_URL &
INSPECTOR_PID=$!

HTTP_PROXY=$SOCKS_PROXY_URL HTTPS_PROXY=$SOCKS_PROXY_URL \
    python3 -m mosk_mcp --transport $MCP_TRANSPORT --host $MCP_HTTP_HOST --port $MCP_HTTP_PORT

