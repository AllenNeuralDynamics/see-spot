#!/bin/bash
set -e

# SeeSpot Uninstaller
# Removes SeeSpot installation

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

SEESPOT_DIR="$HOME/.seespot"
LAUNCHER="$HOME/.local/bin/seespot"
PID_FILE="$SEESPOT_DIR/seespot.pid"

echo ""
echo "=========================================="
echo "  SeeSpot Uninstaller"
echo "=========================================="
echo ""

# Check if SeeSpot is installed
if [ ! -d "$SEESPOT_DIR" ]; then
    log_error "SeeSpot does not appear to be installed"
    log_error "Directory not found: $SEESPOT_DIR"
    exit 1
fi

# Show what will be removed
echo "This will remove:"
echo "  - SeeSpot configuration: $SEESPOT_DIR"
echo "  - Launcher script: $LAUNCHER"
echo ""

# Load config to show cache directory
CONFIG_FILE="$SEESPOT_DIR/config.yaml"
if [ -f "$CONFIG_FILE" ]; then
    CACHE_DIR=$(grep "cache_dir:" "$CONFIG_FILE" | awk '{print $2}')
    if [ ! -z "$CACHE_DIR" ]; then
        CACHE_DIR="${CACHE_DIR/#\~/$HOME}"
        echo "  - Cache directory: $CACHE_DIR"
        echo ""
    fi
fi

read -p "Are you sure you want to uninstall SeeSpot? [y/N]: " CONFIRM
if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
    log_info "Uninstall cancelled"
    exit 0
fi

# Stop server if running
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        log_info "Stopping SeeSpot server..."
        kill "$PID"
        log_success "Server stopped"
    fi
fi

# Remove launcher
if [ -f "$LAUNCHER" ]; then
    log_info "Removing launcher script..."
    rm "$LAUNCHER"
    log_success "Launcher removed"
fi

# Ask about cache directory
if [ ! -z "$CACHE_DIR" ] && [ -d "$CACHE_DIR" ]; then
    echo ""
    read -p "Remove cache directory ($CACHE_DIR)? This contains downloaded S3 data. [y/N]: " REMOVE_CACHE
    if [[ "$REMOVE_CACHE" =~ ^[Yy]$ ]]; then
        log_info "Removing cache directory..."
        rm -rf "$CACHE_DIR"
        log_success "Cache directory removed"
    else
        log_info "Cache directory preserved: $CACHE_DIR"
    fi
fi

# Remove SeeSpot directory
log_info "Removing SeeSpot configuration..."
rm -rf "$SEESPOT_DIR"
log_success "Configuration removed"

echo ""
log_success "=========================================="
log_success "SeeSpot uninstalled successfully"
log_success "=========================================="
echo ""
log_info "To reinstall, run: ./install.sh"
echo ""
