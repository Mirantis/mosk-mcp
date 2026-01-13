# MOSK MCP Server Dockerfile
# Multi-stage build for minimal production image

# =============================================================================
# Stage 1: Build dependencies
# =============================================================================
FROM python:3.11-slim AS builder

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies
WORKDIR /build

# Copy files needed for build (pyproject.toml references README.md)
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install the package and its dependencies
RUN pip install --upgrade pip && \
    pip install .

# =============================================================================
# Stage 2: Production image
# =============================================================================
FROM python:3.11-slim AS production

# Labels for container metadata
LABEL org.opencontainers.image.title="MOSK MCP Server" \
    org.opencontainers.image.description="MCP Server for Mirantis OpenStack for Kubernetes operations" \
    org.opencontainers.image.version="0.1.0" \
    org.opencontainers.image.vendor="Mirantis" \
    org.opencontainers.image.source="https://github.com/mirantis/mosk-mcp"

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    # Application settings
    MCP_LOG_FORMAT=json \
    MCP_LOG_LEVEL=INFO \
    MCP_TRANSPORT=stdio \
    MCP_HTTP_HOST=0.0.0.0 \
    MCP_HTTP_PORT=8080 \
    # Metrics and health settings
    MCP_METRICS_ENABLED=true \
    MCP_METRICS_HOST=0.0.0.0 \
    MCP_METRICS_PORT=9090 \
    MCP_HEALTH_CHECK_K8S_ENABLED=true \
    MCP_HEALTH_CHECK_TIMEOUT_SECONDS=10 \
    # Multi-cluster configuration
    MCP_CONFIG_PATH=/home/mosk-mcp/.config/mosk-mcp/clusters.yaml \
    # User settings
    APP_USER=mosk-mcp \
    APP_UID=1000 \
    APP_GID=1000

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    # For health checks
    curl \
    # For proper signal handling
    tini \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create non-root user
RUN groupadd --gid ${APP_GID} ${APP_USER} && \
    useradd --uid ${APP_UID} --gid ${APP_GID} --shell /bin/bash --create-home ${APP_USER}

# Create application directories including cluster config directory
RUN mkdir -p /app /var/log/mosk-mcp /var/run/mosk-mcp /home/${APP_USER}/.config/mosk-mcp && \
    chown -R ${APP_USER}:${APP_USER} /app /var/log/mosk-mcp /var/run/mosk-mcp /home/${APP_USER}/.config

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
WORKDIR /app
COPY --chown=${APP_USER}:${APP_USER} src/ ./src/
COPY --chown=${APP_USER}:${APP_USER} pyproject.toml README.md ./

# Install the application in the virtual environment (no-deps since deps already in venv)
RUN pip install --no-deps -e .

# Switch to non-root user
USER ${APP_USER}

# Expose HTTP port (only used when MCP_TRANSPORT=http)
EXPOSE 8080

# Expose metrics port (used when MCP_METRICS_ENABLED=true)
EXPOSE 9090

# Volume for cluster configuration
VOLUME /home/mosk-mcp/.config/mosk-mcp

# Health check - uses the metrics/health port which runs in all transport modes
# The auxiliary server (health + metrics) runs on MCP_METRICS_PORT regardless of transport
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD if [ "$MCP_METRICS_ENABLED" = "true" ]; then \
    curl -f http://localhost:${MCP_METRICS_PORT}/health/ready || exit 1; \
    else exit 0; fi

# Use tini as init system for proper signal handling
ENTRYPOINT ["/usr/bin/tini", "--"]

# Default command runs the MCP server
CMD ["python", "-m", "mosk_mcp"]

# =============================================================================
# Stage 3: Development image (optional)
# =============================================================================
FROM production AS development

# Switch to root for installing dev dependencies
USER root

# Install development dependencies
RUN pip install pytest pytest-asyncio pytest-cov ruff mypy

# Switch back to non-root user
USER ${APP_USER}

# Override command for development
CMD ["python", "-m", "mosk_mcp", "--log-format", "console", "--log-level", "DEBUG"]
