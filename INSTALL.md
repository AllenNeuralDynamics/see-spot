# SeeSpot Installation Guide

This guide walks you through installing SeeSpot on your system.

## Prerequisites

- **Operating System**: Linux (Ubuntu 20.04+, CentOS 7+, or similar)
- **Disk Space**: At least 10 GB free for cache directory (more recommended for large datasets)
- **AWS Credentials**: Valid AWS credentials with read access to `s3://aind-open-data`

The installer will automatically install `uv` (Python package manager) if not present. Python 3.11+ will be installed by `uv` during the installation process.

## Quick Start (Recommended)

Run the installer with default settings:

```bash
cd /home/matt.davis/code/see-spot
./install.sh
```

The installer will:
1. Check system requirements
2. Install `uv` if needed
3. Validate AWS credentials
4. Use default configuration (cache directory, server port, etc.)
5. Set up Python environment
6. Install dependencies
7. Create launcher script

## Interactive Installation

If you want to customize settings during installation, use the `--interactive` flag:

```bash
./install.sh --interactive
```

This will prompt you for configuration values.
- Cache directory: `~/.seespot/cache`
- Server host: `0.0.0.0`
- Server port: `5555`

Examples:

```bash
# Standard installation (non-interactive, default)
./install.sh

# Interactive installation with prompts
./install.sh --interactive

# Verbose installation
./install.sh --verbose

# Dry run to preview changes
./install.sh --dry-run

# Interactive with verbose output
./install.sh --interactive --verbose
```

## Installation Options

The `install.sh` script supports the following options:

- `--interactive` or `-i`: Prompt for configuration values
- `--yes` or `-y`: Explicitly use non-interactive mode (default behavior)
- `--verbose` or `-v`: Show detailed output
- `--dry-run`: Show what would be done without making changes
- `--help` or `-h`: Display help message

**Default values used in non-interactive mode:**

## AWS Credentials Setup

SeeSpot requires AWS credentials to access data. The installer checks for credentials in this order:

1. **Environment Variables**: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`
2. **AWS CLI Config**: `~/.aws/credentials` (default profile)
3. **IAM Role**: For EC2 instances with attached IAM roles

### Setting up AWS CLI credentials:

```bash
# Install AWS CLI if not present
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install

# Configure credentials
aws configure
```

You'll be prompted for:
- AWS Access Key ID
- AWS Secret Access Key
- Default region (e.g., `us-west-2`)
- Output format (e.g., `json`)

### Using environment variables:

```bash
export AWS_ACCESS_KEY_ID="your-access-key"
export AWS_SECRET_ACCESS_KEY="your-secret-key"
export AWS_SESSION_TOKEN="your-session-token"  # Optional
```

## Configuration

After installation, SeeSpot's configuration is stored in `~/.seespot/config.yaml`.

### Configuration Options

```yaml
# Cache directory for downloaded S3 data
cache_dir: /path/to/cache

# Server settings
server:
  host: 0.0.0.0  # Listen on all interfaces
  port: 5555     # Server port

# AWS settings (optional overrides)
aws:
  profile: default
  region: us-west-2
  bucket: aind-open-data
```

### Configuration Precedence

Configuration values are resolved in this order (highest to lowest priority):

1. **Environment Variables**: `SEESPOT_CACHE_DIR`, `SEESPOT_HOST`, `SEESPOT_PORT`, `SEESPOT_BUCKET`
2. **Command-Line Arguments**: Passed to the `seespot` launcher
3. **Config File**: `~/.seespot/config.yaml` (user config) or `/etc/seespot/config.yaml` (system config)
4. **Defaults**: Built-in fallback values

### Example: Override port via environment variable

```bash
SEESPOT_PORT=8080 seespot start
```

## Using SeeSpot

After installation, use the `seespot` command to manage the server:

```bash
# Start the server
seespot start

# Stop the server
seespot stop

# Check server status
seespot status

# View logs
seespot logs
```

The server will be available at `http://localhost:5555` (or your configured port).

## Accessing the Web Interface

Once the server is running:

1. Open a web browser
2. Navigate to `http://localhost:5555`
3. You'll see the SeeSpot visualization interface

### First-Time Setup

On first use:
1. Click "Manage Datasets" in the sidebar
2. Download a dataset from the list
3. Set it as active
4. The visualization will load automatically


## Uninstallation

To remove SeeSpot:

```bash
cd /home/matt.davis/code/see-spot
./uninstall.sh
```

The uninstaller will:
1. Stop any running servers
2. Remove the launcher script
3. Optionally remove cache directory
4. Remove configuration directory


## What Gets Installed

After installation, you'll have:

- **Python Environment**: `.venv/` in the repository
- **Launcher Script**: `~/.local/bin/seespot`
- **Config Directory**: `~/.seespot/`
  - `config.yaml` - Configuration file
  - `seespot.pid` - Server process ID
  - `seespot.log` - Server logs
- **Cache Directory**: `~/.seespot/cache/` (or your configured location)
  - Downloads from S3 are cached here
