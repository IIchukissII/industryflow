#!/bin/bash
# rebuild_spark_services.sh
# Rebuild Spark Streaming and Aggregations services with updated code
# Usage: ./rebuild_spark_services.sh [streaming|aggregations|both]

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
    exit $?
fi

# Determine what to rebuild
SERVICE="${1:-both}"

# Version info
BUILD_DATE=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
VERSION="2.0.0"
VCS_REF=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")

log_info "IndustryFlow Spark Services Rebuild"
log_info "Build Date: $BUILD_DATE"
log_info "Version: $VERSION"
log_info "Git Commit: $VCS_REF"
log_info "Rebuilding: $SERVICE"
echo ""

# Function to rebuild streaming service
rebuild_streaming() {
    log_info "=== Rebuilding Spark Streaming Service ==="

    # Step 1: Stop the service
    log_info "Step 1/5: Stopping spark-streaming container..."
    docker-compose stop spark-streaming || log_warn "Service not running"
    sleep 2

    # Step 2: Remove old container
    log_info "Step 2/5: Removing old container..."
    docker-compose rm -f spark-streaming 2>/dev/null || true

    # Step 3: Remove old image
    log_info "Step 3/5: Removing old image..."
    docker rmi industryflow-spark-streaming:latest 2>/dev/null || \
        docker rmi $(docker images -q --filter "reference=*spark-streaming*" | head -1) 2>/dev/null || \
        log_warn "No old image found"

    # Step 4: Build new image with version info
    log_info "Step 4/5: Building new image..."
    docker-compose build \
        --build-arg BUILD_DATE="$BUILD_DATE" \
        --build-arg VERSION="$VERSION" \
        --build-arg VCS_REF="$VCS_REF" \
        --no-cache \
        spark-streaming

    if [ $? -ne 0 ]; then
        log_error "Build failed!"
        exit 1
    fi

    # Step 5: Start the service
    log_info "Step 5/5: Starting spark-streaming service..."
    docker-compose up -d spark-streaming

    # Wait for startup
    sleep 5

    # Verify
    log_info "Verifying service status..."
    docker ps | grep spark-streaming

    log_info "✅ Spark Streaming rebuilt successfully"
    log_info "Check logs: sudo docker logs -f industryflow-spark-streaming"
    echo ""
}

# Function to rebuild aggregations service
rebuild_aggregations() {
    log_info "=== Rebuilding Spark Aggregations Service ==="

    # Step 1: Stop the service
    log_info "Step 1/5: Stopping spark-aggregations container..."
    docker-compose stop spark-aggregations || log_warn "Service not running"
    sleep 2

    # Step 2: Remove old container
    log_info "Step 2/5: Removing old container..."
    docker-compose rm -f spark-aggregations 2>/dev/null || true

    # Step 3: Remove old image
    log_info "Step 3/5: Removing old image..."
    docker rmi industryflow-spark-aggregations:latest 2>/dev/null || \
        docker rmi $(docker images -q --filter "reference=*spark-aggregations*" | head -1) 2>/dev/null || \
        log_warn "No old image found"

    # Step 4: Build new image with version info
    log_info "Step 4/5: Building new image..."
    docker-compose build \
        --build-arg BUILD_DATE="$BUILD_DATE" \
        --build-arg VERSION="$VERSION" \
        --build-arg VCS_REF="$VCS_REF" \
        --no-cache \
        spark-aggregations

    if [ $? -ne 0 ]; then
        log_error "Build failed!"
        exit 1
    fi

    # Step 5: Start the service
    log_info "Step 5/5: Starting spark-aggregations service..."
    docker-compose up -d spark-aggregations

    # Wait for startup
    sleep 5

    # Verify
    log_info "Verifying service status..."
    docker ps | grep spark-aggregations

    log_info "✅ Spark Aggregations rebuilt successfully"
    log_info "Check logs: sudo docker logs -f industryflow-spark-aggregations"
    echo ""
}

# Main logic
case $SERVICE in
    streaming)
        rebuild_streaming
        ;;
    aggregations)
        rebuild_aggregations
        ;;
    both)
        rebuild_streaming
        rebuild_aggregations
        ;;
    *)
        log_error "Invalid argument. Use: streaming, aggregations, or both"
        exit 1
        ;;
esac

log_info "🎉 Rebuild complete!"
log_info ""
log_info "Next steps:"
log_info "1. Check logs for any errors"
log_info "2. Verify data is flowing to TimescaleDB"
log_info "3. Monitor Spark UI at http://localhost:8080 (if master running)"