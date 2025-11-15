#!/bin/bash
set -e

# SeeSpot Installer
# Linux only - installs SeeSpot visualization server
# Usage: ./install.sh [--interactive] [--verbose] [--dry-run]

VERSION="0.5.1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Parse command line arguments
YES_FLAG=true
VERBOSE=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --interactive|-i)
            YES_FLAG=false
            shift
            ;;
        --yes|-y)
            YES_FLAG=true
            shift
            ;;
        --verbose|-v)
            VERBOSE=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --help|-h)
            echo "SeeSpot Installer"
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --interactive, -i  Interactive mode (prompt for configuration)"
            echo "  --yes, -y          Non-interactive mode (use defaults, same as default)"
            echo "  --verbose, -v      Verbose output"
            echo "  --dry-run          Show what would be done without making changes"
            echo "  --help, -h         Show this help message"
            echo ""
            echo "Note: Non-interactive mode is the default. Use --interactive for prompts."
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Logging functions
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

log_verbose() {
    if [ "$VERBOSE" = true ]; then
        echo -e "${BLUE}[VERBOSE]${NC} $1"
    fi
}

# Check if running on Linux
check_os() {
    log_info "Checking operating system..."
    if [[ "$OSTYPE" != "linux-gnu"* ]]; then
        log_error "This installer currently supports Linux only."
        log_error "Detected OS: $OSTYPE"
        exit 1
    fi
    log_success "Linux detected"
}

# Check for uv and install if missing
check_uv() {
    log_info "Checking for uv..."
    if command -v uv &> /dev/null; then
        UV_VERSION=$(uv --version | awk '{print $2}')
        log_success "uv is installed (version $UV_VERSION)"
        return 0
    else
        log_warning "uv is not installed"
        if [ "$YES_FLAG" = true ]; then
            INSTALL_UV="y"
        else
            read -p "Would you like to install uv? [Y/n]: " INSTALL_UV
            INSTALL_UV=${INSTALL_UV:-y}
        fi
        
        if [[ "$INSTALL_UV" =~ ^[Yy]$ ]]; then
            log_info "Installing uv..."
            if [ "$DRY_RUN" = true ]; then
                log_info "[DRY RUN] Would execute: curl -LsSf https://astral.sh/uv/install.sh | sh"
                return 0
            fi
            curl -LsSf https://astral.sh/uv/install.sh | sh
            # Add to current session PATH
            export PATH="$HOME/.cargo/bin:$PATH"
            log_success "uv installed successfully"
        else
            log_error "uv is required to install SeeSpot. Please install it manually:"
            log_error "  curl -LsSf https://astral.sh/uv/install.sh | sh"
            exit 1
        fi
    fi
}

# Check AWS credentials
check_aws_credentials() {
    log_info "Checking AWS credentials..."
    
    HAS_CREDS=false
    
    # Check environment variables (standard credentials)
    if [ ! -z "$AWS_ACCESS_KEY_ID" ] && [ ! -z "$AWS_SECRET_ACCESS_KEY" ]; then
        log_success "AWS credentials found in environment variables"
        HAS_CREDS=true
        return 0
    fi
    
    # Check for ECS task role (Docker container with task role)
    if [ ! -z "$AWS_CONTAINER_CREDENTIALS_RELATIVE_URI" ]; then
        log_success "ECS task role detected (Docker/ECS container)"
        HAS_CREDS=true
        return 0
    fi
    
    # Check ~/.aws/credentials
    if [ -f "$HOME/.aws/credentials" ]; then
        log_success "AWS credentials file found at ~/.aws/credentials"
        HAS_CREDS=true
        return 0
    fi
    
    # Check for IAM role (EC2 instance)
    if curl -s -f -m 2 http://169.254.169.254/latest/meta-data/iam/security-credentials/ &> /dev/null; then
        log_success "IAM instance role detected (EC2)"
        HAS_CREDS=true
        return 0
    fi
    
    if [ "$HAS_CREDS" = false ]; then
        log_warning "No AWS credentials detected"
        log_warning "SeeSpot requires access to S3 bucket: aind-open-data"
        log_warning "Please set up AWS credentials before using SeeSpot:"
        log_warning "  1. Environment variables: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY"
        log_warning "  2. AWS CLI: aws configure"
        log_warning "  3. IAM role (if running on EC2)"
        log_warning "  4. ECS task role (if running in Docker/ECS)"
        log_warning ""
        log_warning "You can continue installation and set up credentials later."
        
        if [ "$YES_FLAG" = false ]; then
            read -p "Continue anyway? [y/N]: " CONTINUE
            if [[ ! "$CONTINUE" =~ ^[Yy]$ ]]; then
                exit 1
            fi
        fi
    fi
}

# Check disk space
check_disk_space() {
    local TARGET_DIR=$1
    log_info "Checking disk space at $TARGET_DIR..."
    
    # Get available space in GB
    AVAILABLE=$(df -BG "$TARGET_DIR" | tail -1 | awk '{print $4}' | sed 's/G//')
    log_verbose "Available space: ${AVAILABLE}GB"
    
    if [ "$AVAILABLE" -lt 50 ]; then
        log_warning "Low disk space detected: ${AVAILABLE}GB available"
        log_warning "Recommended: 50GB+ for S3 cache"
        if [ "$YES_FLAG" = false ]; then
            read -p "Continue anyway? [y/N]: " CONTINUE
            if [[ ! "$CONTINUE" =~ ^[Yy]$ ]]; then
                exit 1
            fi
        fi
    else
        log_success "Sufficient disk space available: ${AVAILABLE}GB"
    fi
}

# Interactive configuration
configure() {
    log_info "Configuring SeeSpot..."
    
    # Default values
    DEFAULT_CACHE_DIR="$HOME/.seespot/cache"
    DEFAULT_PORT=5555
    DEFAULT_HOST="0.0.0.0"
    
    if [ "$YES_FLAG" = true ]; then
        CACHE_DIR=$DEFAULT_CACHE_DIR
        SERVER_PORT=$DEFAULT_PORT
        SERVER_HOST=$DEFAULT_HOST
    else
        echo ""
        read -p "Cache directory for S3 data [$DEFAULT_CACHE_DIR]: " CACHE_DIR
        CACHE_DIR=${CACHE_DIR:-$DEFAULT_CACHE_DIR}
        
        read -p "Server port [$DEFAULT_PORT]: " SERVER_PORT
        SERVER_PORT=${SERVER_PORT:-$DEFAULT_PORT}
        
        read -p "Server host [$DEFAULT_HOST]: " SERVER_HOST
        SERVER_HOST=${SERVER_HOST:-$DEFAULT_HOST}
    fi
    
    # Expand ~ to actual home directory
    CACHE_DIR="${CACHE_DIR/#\~/$HOME}"
    
    log_verbose "Configuration:"
    log_verbose "  Cache directory: $CACHE_DIR"
    log_verbose "  Server host: $SERVER_HOST"
    log_verbose "  Server port: $SERVER_PORT"
}

# Create directories and config
setup_directories() {
    log_info "Setting up directories..."
    
    SEESPOT_DIR="$HOME/.seespot"
    CONFIG_FILE="$SEESPOT_DIR/config.yaml"
    
    if [ "$DRY_RUN" = true ]; then
        log_info "[DRY RUN] Would create: $SEESPOT_DIR"
        log_info "[DRY RUN] Would create: $CACHE_DIR"
        return 0
    fi
    
    # Create SeeSpot directory
    mkdir -p "$SEESPOT_DIR"
    log_success "Created $SEESPOT_DIR"
    
    # Create cache directory
    mkdir -p "$CACHE_DIR"
    log_success "Created cache directory: $CACHE_DIR"
    
    # Create config file
    cat > "$CONFIG_FILE" << EOF
# SeeSpot Configuration
# Generated by installer on $(date)

cache_dir: $CACHE_DIR

server:
  host: $SERVER_HOST
  port: $SERVER_PORT

aws:
  # S3 bucket for data access
  bucket: aind-open-data
  
  # Optional: specify AWS profile name
  # profile: default
  
  # Optional: specify region
  # region: us-west-2
EOF
    
    log_success "Created config file: $CONFIG_FILE"
    
    # Create install log
    INSTALL_LOG="$SEESPOT_DIR/install.log"
    echo "SeeSpot installation started at $(date)" > "$INSTALL_LOG"
    echo "Version: $VERSION" >> "$INSTALL_LOG"
    echo "Install directory: $SCRIPT_DIR" >> "$INSTALL_LOG"
    echo "Cache directory: $CACHE_DIR" >> "$INSTALL_LOG"
}

# Install Python environment with uv
install_environment() {
    log_info "Installing SeeSpot environment..."
    
    if [ "$DRY_RUN" = true ]; then
        log_info "[DRY RUN] Would create virtual environment with uv"
        log_info "[DRY RUN] Would install dependencies"
        return 0
    fi
    
    cd "$SCRIPT_DIR"
    
    # Create virtual environment with Python 3.11+
    log_info "Creating virtual environment..."
    uv venv .venv --python 3.11
    
    log_success "Virtual environment created"
    
    # Install package
    log_info "Installing SeeSpot package and dependencies..."
    uv pip install -e .
    
    log_success "SeeSpot package installed"
}

# Create launcher script
create_launcher() {
    log_info "Creating launcher script..."

    # try launcher in spot for code-ocean build
    LAUNCHER="/usr/local/sbin/seespot"
    # LAUNCHER="$HOME/.local/bin/seespot"
    
    if [ "$DRY_RUN" = true ]; then
        log_info "[DRY RUN] Would create launcher: $LAUNCHER"
        return 0
    fi
    
    # Create .local/bin if it doesn't exist
    # mkdir -p "$HOME/.local/bin"
    
    # Create launcher script
    cat > "$LAUNCHER" << 'EOF'
#!/bin/bash
# SeeSpot Launcher

SEESPOT_DIR="$HOME/.seespot"
CONFIG="$SEESPOT_DIR/config.yaml"
INSTALL_DIR="__INSTALL_DIR__"
VENV="$INSTALL_DIR/.venv"
PID_FILE="$SEESPOT_DIR/seespot.pid"

# Load config
load_config() {
    if [ ! -f "$CONFIG" ]; then
        echo "Error: Config file not found at $CONFIG"
        exit 1
    fi
    
    # Parse YAML (simple parsing for our specific config)
    SERVER_PORT=$(grep "port:" "$CONFIG" | awk '{print $2}')
    SERVER_HOST=$(grep "host:" "$CONFIG" | awk '{print $2}')
    
    SERVER_PORT=${SERVER_PORT:-5555}
    SERVER_HOST=${SERVER_HOST:-0.0.0.0}
}

start_server() {
    if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
        echo "SeeSpot is already running (PID: $(cat "$PID_FILE"))"
        exit 1
    fi
    
    load_config
    
    echo "Starting SeeSpot on $SERVER_HOST:$SERVER_PORT..."
    
    cd "$INSTALL_DIR/src"
    source "$VENV/bin/activate"
    
    nohup uvicorn see_spot.app:app --host "$SERVER_HOST" --port "$SERVER_PORT" > "$SEESPOT_DIR/seespot.log" 2>&1 &
    echo $! > "$PID_FILE"
    
    echo "SeeSpot started (PID: $(cat "$PID_FILE"))"
    echo "Log file: $SEESPOT_DIR/seespot.log"
    echo "Visit: http://localhost:$SERVER_PORT/unmixed-spots"
}

stop_server() {
    if [ ! -f "$PID_FILE" ]; then
        echo "SeeSpot is not running (no PID file)"
        exit 1
    fi
    
    PID=$(cat "$PID_FILE")
    
    if kill -0 "$PID" 2>/dev/null; then
        echo "Stopping SeeSpot (PID: $PID)..."
        kill "$PID"
        rm "$PID_FILE"
        echo "SeeSpot stopped"
    else
        echo "SeeSpot is not running (stale PID file)"
        rm "$PID_FILE"
    fi
}

status_server() {
    if [ ! -f "$PID_FILE" ]; then
        echo "SeeSpot is not running"
        exit 1
    fi
    
    PID=$(cat "$PID_FILE")
    
    if kill -0 "$PID" 2>/dev/null; then
        load_config
        echo "SeeSpot is running (PID: $PID)"
        echo "URL: http://localhost:$SERVER_PORT/unmixed-spots"
    else
        echo "SeeSpot is not running (stale PID file)"
        rm "$PID_FILE"
        exit 1
    fi
}

show_config() {
    if [ -f "$CONFIG" ]; then
        cat "$CONFIG"
    else
        echo "Config file not found: $CONFIG"
        exit 1
    fi
}

show_logs() {
    LOG_FILE="$SEESPOT_DIR/seespot.log"
    if [ -f "$LOG_FILE" ]; then
        tail -f "$LOG_FILE"
    else
        echo "Log file not found: $LOG_FILE"
        exit 1
    fi
}

case "$1" in
    start)
        start_server
        ;;
    stop)
        stop_server
        ;;
    restart)
        stop_server
        sleep 1
        start_server
        ;;
    status)
        status_server
        ;;
    config)
        show_config
        ;;
    logs)
        show_logs
        ;;
    *)
        echo "SeeSpot Launcher"
        echo "Usage: $0 {start|stop|restart|status|config|logs}"
        echo ""
        echo "Commands:"
        echo "  start    - Start the SeeSpot server"
        echo "  stop     - Stop the SeeSpot server"
        echo "  restart  - Restart the SeeSpot server"
        echo "  status   - Check if SeeSpot is running"
        echo "  config   - Show configuration"
        echo "  logs     - Tail the server logs"
        exit 1
        ;;
esac
EOF
    
    # Replace placeholder with actual install directory
    sed -i "s|__INSTALL_DIR__|$SCRIPT_DIR|g" "$LAUNCHER"
    
    # Make executable
    chmod +x "$LAUNCHER"
    
    log_success "Launcher script created: $LAUNCHER"
    
    # Add ~/.local/bin to PATH if not already present
    if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
        # Detect shell config file
        if [ -f "$HOME/.bashrc" ]; then
            SHELL_CONFIG="$HOME/.bashrc"
        elif [ -f "$HOME/.bash_profile" ]; then
            SHELL_CONFIG="$HOME/.bash_profile"
        elif [ -f "$HOME/.zshrc" ]; then
            SHELL_CONFIG="$HOME/.zshrc"
        elif [ -f "$HOME/.profile" ]; then
            SHELL_CONFIG="$HOME/.profile"
        else
            SHELL_CONFIG=""
        fi
        
        if [ -n "$SHELL_CONFIG" ] && [ -t 1 ]; then
            # Interactive mode - ask user
            log_info "~/.local/bin is not in your PATH"
            if [ "$YES_FLAG" = false ]; then
                read -p "Add PATH export to $SHELL_CONFIG? [Y/n]: " ADD_PATH
                if [[ ! "$ADD_PATH" =~ ^[Nn]$ ]]; then
                    echo "" >> "$SHELL_CONFIG"
                    echo "# Added by SeeSpot installer" >> "$SHELL_CONFIG"
                    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_CONFIG"
                    log_success "Added PATH export to $SHELL_CONFIG"
                    log_info "Run: source $SHELL_CONFIG"
                fi
            else
                # Non-interactive - add automatically
                if ! grep -q "export PATH=\"\$HOME/.local/bin:\$PATH\"" "$SHELL_CONFIG" 2>/dev/null; then
                    echo "" >> "$SHELL_CONFIG"
                    echo "# Added by SeeSpot installer" >> "$SHELL_CONFIG"
                    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_CONFIG"
                    log_success "Added PATH export to $SHELL_CONFIG"
                fi
            fi
        elif [ ! -t 1 ]; then
            # Non-interactive (Docker/CI) - just log for reference
            log_verbose "For Docker, add to Dockerfile: ENV PATH=\"\$HOME/.local/bin:\$PATH\""
        fi
    fi
}

# Print success message
print_success() {
    echo ""
    log_success "=========================================="
    log_success "SeeSpot installation complete!"
    log_success "=========================================="
    echo ""
    log_info "Configuration file: $HOME/.seespot/config.yaml"
    log_info "Cache directory: $CACHE_DIR"
    echo ""
    log_info "To start SeeSpot:"
    echo "  seespot start"
    echo ""
    log_info "Other commands:"
    echo "  seespot stop     - Stop the server"
    echo "  seespot status   - Check server status"
    echo "  seespot logs     - View server logs"
    echo "  seespot config   - Show configuration"
    echo ""
    log_info "Server will be available at:"
    echo "  http://localhost:$SERVER_PORT/unmixed-spots"
    echo ""
    
    if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
        log_warning "Note: Add ~/.local/bin to your PATH:"
        echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
    fi
}

# Main installation flow
main() {
    echo ""
    echo "=========================================="
    echo "  SeeSpot Installer v${VERSION}"
    echo "  Visualization for HCR Spot Data"
    echo "=========================================="
    echo ""
    
    if [ "$DRY_RUN" = true ]; then
        log_warning "Running in DRY RUN mode - no changes will be made"
        echo ""
    fi
    
    check_os
    check_uv
    check_aws_credentials
    configure
    check_disk_space "$(dirname "$CACHE_DIR")"
    setup_directories
    install_environment
    create_launcher
    
    if [ "$DRY_RUN" = false ]; then
        print_success
    else
        log_info "[DRY RUN] Installation simulation complete"
    fi
}

# Run installer
main
