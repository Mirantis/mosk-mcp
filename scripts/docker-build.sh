#!/bin/bash
# Docker build script for MOSK MCP Server
# Supports multi-platform builds (amd64, arm64) for Mac, Linux, x86
#
# Usage: ./scripts/docker-build.sh [options]
#
# Environment variables:
#   REGISTRY      - Docker registry (default: reddydodda)
#   IMAGE_NAME    - Image name (default: mosk-mcp)
#   IMAGE_TAG     - Image tag (default: latest)
#   PUSH          - Push to registry after build (default: false)
#   TARGET        - Build target: production or development (default: production)

set -e

# Default values
REGISTRY="${REGISTRY:-reddydodda}"
IMAGE_NAME="${IMAGE_NAME:-mosk-mcp}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
PUSH="${PUSH:-false}"
TARGET="${TARGET:-production}"
# Default to multi-platform for all common architectures
DEFAULT_PLATFORMS="linux/amd64,linux/arm64"
PLATFORM="${PLATFORM:-}"
LOCAL_BUILD="${LOCAL_BUILD:-false}"

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_step() {
    echo -e "${CYAN}[STEP]${NC} $1"
}

show_help() {
    cat << EOF
Docker Build Script for MOSK MCP Server
Supports multi-platform builds for Mac (ARM), Linux (x86/ARM)

Usage: $(basename "$0") [options]

Options:
    -r, --registry REGISTRY    Docker registry (default: reddydodda)
    -n, --name IMAGE_NAME      Image name (default: mosk-mcp)
    -t, --tag IMAGE_TAG        Image tag (default: latest)
    -p, --push                 Push to registry after build (enables multi-platform)
    -d, --dev                  Build development image
    -l, --local                Build for local platform only (faster, no push)
    --platform PLATFORM        Target platform(s), comma-separated
                               (default for push: linux/amd64,linux/arm64)
    --no-cache                 Build without cache
    -h, --help                 Show this help message

Supported Platforms:
    linux/amd64     x86_64 / Intel / AMD (most Linux servers, Intel Macs)
    linux/arm64     ARM64 / aarch64 (Apple M1/M2/M3, ARM servers, Raspberry Pi 4)
    linux/arm/v7    ARM 32-bit (older Raspberry Pi)

Examples:
    # Build for local platform only (fast, for testing)
    ./scripts/docker-build.sh --local

    # Build multi-platform and push to registry
    ./scripts/docker-build.sh --push

    # Build with custom registry and tag, push multi-platform
    ./scripts/docker-build.sh -r myregistry -t v1.0.0 --push

    # Build only for specific platforms
    ./scripts/docker-build.sh --platform linux/amd64 --push

    # Build for all platforms including ARM v7
    ./scripts/docker-build.sh --platform linux/amd64,linux/arm64,linux/arm/v7 --push

    # Build development image for local testing
    ./scripts/docker-build.sh -d --local

Environment Variables:
    REGISTRY      Docker registry (overridden by -r)
    IMAGE_NAME    Image name (overridden by -n)
    IMAGE_TAG     Image tag (overridden by -t)
    PUSH          Push after build (overridden by -p)
    TARGET        Build target (overridden by -d)
    PLATFORM      Target platform(s) (overridden by --platform)
    LOCAL_BUILD   Build for local platform only (overridden by -l)
EOF
}

setup_buildx() {
    log_step "Setting up Docker Buildx for multi-platform builds..."

    # Check if buildx is available
    if ! docker buildx version > /dev/null 2>&1; then
        log_error "Docker Buildx is required for multi-platform builds"
        log_error "Install Docker Desktop or enable buildx plugin"
        exit 1
    fi

    # Check if our builder exists and is running
    BUILDER_NAME="mosk-multiplatform"
    if docker buildx inspect "$BUILDER_NAME" > /dev/null 2>&1; then
        log_info "Using existing builder: $BUILDER_NAME"
        docker buildx use "$BUILDER_NAME"
    else
        log_info "Creating new buildx builder: $BUILDER_NAME"
        docker buildx create \
            --name "$BUILDER_NAME" \
            --driver docker-container \
            --platform linux/amd64,linux/arm64,linux/arm/v7 \
            --use

        # Bootstrap the builder
        log_info "Bootstrapping builder (this may take a moment)..."
        docker buildx inspect --bootstrap "$BUILDER_NAME"
    fi

    log_info "Builder ready: $BUILDER_NAME"
}

# Parse arguments
NO_CACHE=""
while [[ $# -gt 0 ]]; do
    case $1 in
        -r|--registry)
            REGISTRY="$2"
            shift 2
            ;;
        -n|--name)
            IMAGE_NAME="$2"
            shift 2
            ;;
        -t|--tag)
            IMAGE_TAG="$2"
            shift 2
            ;;
        -p|--push)
            PUSH="true"
            shift
            ;;
        -d|--dev)
            TARGET="development"
            shift
            ;;
        -l|--local)
            LOCAL_BUILD="true"
            shift
            ;;
        --platform)
            PLATFORM="$2"
            shift 2
            ;;
        --no-cache)
            NO_CACHE="--no-cache"
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

# Determine platform strategy
if [ "$LOCAL_BUILD" = "true" ]; then
    # Local build - use native platform
    PLATFORM=""
    if [ "$PUSH" = "true" ]; then
        log_warn "Local build mode ignores --push flag"
        PUSH="false"
    fi
elif [ -z "$PLATFORM" ] && [ "$PUSH" = "true" ]; then
    # Push without explicit platform - use default multi-platform
    PLATFORM="$DEFAULT_PLATFORMS"
fi

# Construct full image name
FULL_IMAGE_NAME="${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"

echo ""
log_info "Docker Build Configuration"
echo "  ─────────────────────────────────────"
echo "  Registry:     ${REGISTRY}"
echo "  Image:        ${IMAGE_NAME}"
echo "  Tag:          ${IMAGE_TAG}"
echo "  Target:       ${TARGET}"
echo "  Full name:    ${FULL_IMAGE_NAME}"
echo "  App version:  ${APP_VERSION}"
if [ -n "$PLATFORM" ]; then
    echo "  Platforms:    ${PLATFORM}"
else
    echo "  Platform:     native (local)"
fi
echo "  Push:         ${PUSH}"
if [ -n "$NO_CACHE" ]; then
    echo "  Cache:        disabled"
fi
echo "  ─────────────────────────────────────"
echo ""

# Change to project root
cd "$PROJECT_ROOT"

# OCI image version (single source: src/mosk_mcp/_version.py)
APP_VERSION="$(python3 -c "from pathlib import Path; p = Path('src/mosk_mcp/_version.py'); exec(p.read_text()); print(__version__)")"

# Determine build strategy
if [ -n "$PLATFORM" ]; then
    # Multi-platform build with buildx
    setup_buildx

    log_step "Building multi-platform image..."

    BUILD_CMD=(
        docker buildx build
        --target "$TARGET"
        --platform "$PLATFORM"
        --build-arg "APP_VERSION=${APP_VERSION}"
        -t "$FULL_IMAGE_NAME"
        -f "Dockerfile"
    )

    if [ -n "$NO_CACHE" ]; then
        BUILD_CMD+=("--no-cache")
    fi

    if [ "$PUSH" = "true" ]; then
        BUILD_CMD+=("--push")
    else
        # For multi-platform without push, we can only output to registry or tar
        log_warn "Multi-platform build without --push will not load to local docker"
        log_warn "Use --push to push to registry, or --local for local testing"
        BUILD_CMD+=("--push")
        PUSH="true"
    fi

    BUILD_CMD+=(".")

    echo ""
    log_info "Running: ${BUILD_CMD[*]}"
    echo ""

    "${BUILD_CMD[@]}"

    echo ""
    log_info "Multi-platform build complete!"
    echo ""
    echo "  Platforms built:"
    for plat in ${PLATFORM//,/ }; do
        echo "    ✓ $plat"
    done
    echo ""

    if [ "$PUSH" = "true" ]; then
        log_info "Image pushed to: ${FULL_IMAGE_NAME}"
        echo ""
        echo "  Pull on different architectures:"
        echo "    docker pull ${FULL_IMAGE_NAME}"
        echo ""
        echo "  Docker will automatically select the right architecture."
    fi

else
    # Local single-platform build
    log_step "Building for local platform..."

    BUILD_CMD=(
        docker build
        --target "$TARGET"
        --build-arg "APP_VERSION=${APP_VERSION}"
        -t "$FULL_IMAGE_NAME"
        -f "Dockerfile"
    )

    if [ -n "$NO_CACHE" ]; then
        BUILD_CMD+=("--no-cache")
    fi

    BUILD_CMD+=(".")

    "${BUILD_CMD[@]}"

    log_info "Local build complete: ${FULL_IMAGE_NAME}"

    if [ "$PUSH" = "true" ]; then
        log_step "Pushing image to registry..."
        docker push "$FULL_IMAGE_NAME"
        log_info "Image pushed: ${FULL_IMAGE_NAME}"
    fi
fi

# Also tag as latest if not already latest
if [ "$IMAGE_TAG" != "latest" ] && [ "$TARGET" = "production" ]; then
    LATEST_TAG="${REGISTRY}/${IMAGE_NAME}:latest"

    if [ -n "$PLATFORM" ] && [ "$PUSH" = "true" ]; then
        # For multi-platform, we need to create a manifest for latest tag
        log_step "Creating 'latest' tag..."
        docker buildx imagetools create -t "$LATEST_TAG" "$FULL_IMAGE_NAME" 2>/dev/null || true
    else
        log_info "Also tagging as: ${LATEST_TAG}"
        docker tag "$FULL_IMAGE_NAME" "$LATEST_TAG" 2>/dev/null || true

        if [ "$PUSH" = "true" ]; then
            docker push "$LATEST_TAG"
        fi
    fi
fi

# Show summary
echo ""
echo "════════════════════════════════════════════════════════════"
log_info "BUILD COMPLETE"
echo "════════════════════════════════════════════════════════════"
echo ""

if [ "$LOCAL_BUILD" = "true" ] || [ -z "$PLATFORM" ]; then
    echo "Local images:"
    docker images | grep "${REGISTRY}/${IMAGE_NAME}" | head -5 || true
    echo ""
    echo "To run locally:"
    echo "  docker run --rm -it ${FULL_IMAGE_NAME}"
fi

if [ "$PUSH" = "true" ]; then
    echo ""
    echo "To pull on any platform:"
    echo "  docker pull ${FULL_IMAGE_NAME}"
fi

echo ""
echo "To run with multi-cluster support (recommended):"
echo "  ./scripts/docker-run.sh start"
echo ""
echo "To run with single cluster (legacy):"
echo "  ./scripts/docker-run.sh start --mcc-url https://your-mcc-url"
echo ""
echo "For more options:"
echo "  ./scripts/docker-run.sh help"
echo ""
