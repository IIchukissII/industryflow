#!/bin/bash

# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# rebuild_spark_services.sh
# Rebuild the Spark jobs with updated code. Each runs in local mode, its executors inside it.
# Usage: ./rebuild_spark_services.sh [streaming|aggregations|all]   ("both" is an alias of "all")

set -euo pipefail  # Exit on error, undefined variable, or pipe failure

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Functions
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running as root, if not re-execute with sudo
if [ "$EUID" -ne 0 ]; then
    log_info "Script requires sudo privileges. Re-executing with sudo..."
    exec sudo bash "$0" "$@"
fi

# Determine what to rebuild
SERVICE="${1:-all}"

# Version info
BUILD_DATE=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
VERSION="0.1.0"
VCS_REF=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")

log_info "IndustryFlow Spark Services Rebuild"
log_info "Build Date: $BUILD_DATE"
log_info "Version: $VERSION"
log_info "Git Commit: $VCS_REF"
log_info "Rebuilding: $SERVICE"
echo ""

# Rebuild one compose service: stop, remove container and image, build without cache, start.
rebuild_service() {
    local service="$1"
    log_info "=== Rebuilding ${service} ==="

    log_info "Step 1/5: Stopping ${service}..."
    docker compose stop "$service" || log_warn "Service not running"
    sleep 2

    log_info "Step 2/5: Removing old container(s)..."
    docker compose rm -f "$service" 2>/dev/null || true

    log_info "Step 3/5: Removing old image..."
    docker rmi "industryflow-${service}:latest" 2>/dev/null || \
        docker rmi $(docker images -q --filter "reference=*${service}*" | head -1) 2>/dev/null || \
        log_warn "No old image found"

    # set -e stops the script here if the build fails. Compose v2 (`docker compose`): the job images
    # take the shared contracts package as a named build context (`additional_contexts`), which the
    # legacy v1 `docker-compose` does not support.
    log_info "Step 4/5: Building new image..."
    docker compose build \
        --build-arg BUILD_DATE="$BUILD_DATE" \
        --build-arg VERSION="$VERSION" \
        --build-arg VCS_REF="$VCS_REF" \
        --no-cache \
        "$service"

    log_info "Step 5/5: Starting ${service}..."
    docker compose up -d "$service"

    # Wait for startup
    sleep 5

    log_info "Verifying service status..."
    docker compose ps "$service"

    log_info "✅ ${service} rebuilt successfully"
    log_info "Check logs: sudo docker compose logs -f ${service}"
    echo ""
}

# Main logic.
case $SERVICE in
    streaming)
        rebuild_service spark-streaming
        ;;
    aggregations)
        rebuild_service spark-aggregations
        ;;
    all|both)
        rebuild_service spark-streaming
        rebuild_service spark-aggregations
        ;;
    *)
        log_error "Invalid argument. Use: streaming, aggregations, or all"
        exit 1
        ;;
esac

log_info "🎉 Rebuild complete!"
log_info ""
log_info "Next steps:"
log_info "1. Check logs for any errors"
log_info "2. Verify data is flowing to TimescaleDB"
log_info "3. Check each job's micro-batches in its log: sudo docker compose logs -f spark-streaming"
