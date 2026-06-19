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

if [ -z "${MOSK_LAB_JUMP_SSH:-}" ]; then
    echo "Error: MOSK_LAB_JUMP_SSH is not set. Configure it in .env."
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
if ! ssh \
    -f \
    -o BatchMode=yes \
    -o ConnectTimeout=10 \
    -o ExitOnForwardFailure=yes \
    -N -D ":${SOCKS_PROXY_PORT}" \
    $MOSK_LAB_JUMP_SSH; then
    echo "Error: SSH SOCKS proxy failed to start." >&2
    echo "Check MOSK_LAB_JUMP_SSH and that a usable SSH key is loaded (e.g. ssh-add -l)." >&2
    exit 1
fi

SSH_PROXY_PID=$(lsof -ti "tcp:${SOCKS_PROXY_PORT}" -sTCP:LISTEN 2>/dev/null | head -1)
if [ -z "${SSH_PROXY_PID}" ]; then
    echo "Error: SSH SOCKS proxy did not bind to port ${SOCKS_PROXY_PORT}." >&2
    exit 1
fi

npx @modelcontextprotocol/inspector --server-url $MCP_URL &
INSPECTOR_PID=$!

HTTP_PROXY=$SOCKS_PROXY_URL HTTPS_PROXY=$SOCKS_PROXY_URL \
    python3 -m mosk_mcp --transport $MCP_TRANSPORT --host $MCP_HTTP_HOST --port $MCP_HTTP_PORT

