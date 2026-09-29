#!/usr/bin/env bash
# ==============================================================================
# Clone Production SQLite Database (Sub-prod cloning)
# ==============================================================================
# Copies the SQLite database file from prod to the local environment.
#
# Usage:
#   ./tools/clone_prod_db.sh [REMOTE_PATH] [LOCAL_PATH] [REMOTE_HOST]
#
# Environment variables (.env or shell):
#   PROD_DB_HOST / REMOTE_HOST   - Remote SSH host (loaded from .env)
#   PROD_DB_PATH / REMOTE_PATH   - Remote SQLite file path
#   LOCAL_PATH                   - Local destination path (default: "<repo_root>/data/db.sqlite3")
# ==============================================================================

set -euo pipefail

# Resolve repository root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/.." && pwd))"

# Helper functions
log_info() {
    echo -e "\033[1;34m[INFO]\033[0m $1"
}

log_success() {
    echo -e "\033[1;32m[SUCCESS]\033[0m $1"
}

log_warn() {
    echo -e "\033[1;33m[WARN]\033[0m $1"
}

log_error() {
    echo -e "\033[1;31m[ERROR]\033[0m $1" >&2
}

# Load configuration from .env if present
ENV_FILE="${REPO_ROOT}/.env"
if [[ -f "$ENV_FILE" ]]; then
    # Safely export variables from .env without executing arbitrary commands
    while IFS='=' read -r key value || [[ -n "$key" ]]; do
        # Trim leading/trailing whitespace
        key="$(echo "$key" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
        # Skip empty lines and comment lines
        if [[ -z "$key" || "$key" == \#* ]]; then
            continue
        fi
        # Remove surrounding single/double quotes from value
        value="$(echo "$value" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^["'"'"']//' -e 's/["'"'"']$//')"
        # Only set if not already defined in current shell environment
        if [[ -z "${!key:-}" ]]; then
            export "$key"="$value"
        fi
    done < "$ENV_FILE"
fi

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    # Remote host preview for help text
    HOST_PREVIEW="${PROD_DB_HOST:-${REMOTE_HOST:-<your-server.com>}}"
    PATH_PREVIEW="${PROD_DB_PATH:-${REMOTE_PATH:-~/concerts/data/db.sqlite3}}"
    cat << EOF
Usage: $(basename "$0") [OPTIONS] [REMOTE_PATH] [LOCAL_PATH] [REMOTE_HOST]

Clones the production SQLite database from the remote host to the local filesystem.

Arguments:
  REMOTE_PATH   Path to the sqlite database on the remote server (default: ${PATH_PREVIEW})
  LOCAL_PATH    Local destination path (default: <repo_root>/data/db.sqlite3)
  REMOTE_HOST   SSH host or user@host for the remote server (default: ${HOST_PREVIEW})

Options:
  -h, --help    Show this help message and exit

Environment Variables / .env:
  PROD_DB_HOST  Remote host in .env (current: ${HOST_PREVIEW})
  PROD_DB_PATH  Remote database file path in .env (current: ${PATH_PREVIEW})
  LOCAL_PATH    Local destination path (default: ${REPO_ROOT}/data/db.sqlite3)

Examples:
  ./tools/clone_prod_db.sh
  ./tools/clone_prod_db.sh /path/to/remote/db.sqlite3
  ./tools/clone_prod_db.sh /path/to/remote/db.sqlite3 ./data/db.sqlite3 user@server.com
EOF
    exit 0
fi

# Configuration precedence: CLI args -> Environment variables -> .env defaults -> Fallback defaults
REMOTE_HOST="${3:-${PROD_DB_HOST:-${REMOTE_HOST:-}}}"
REMOTE_PATH="${1:-${PROD_DB_PATH:-${REMOTE_PATH:-~/concerts/data/db.sqlite3}}}"
LOCAL_PATH="${2:-${LOCAL_PATH:-${REPO_ROOT}/data/db.sqlite3}}"

if [[ -z "$REMOTE_HOST" ]]; then
    log_error "PROD_DB_HOST is not set in .env or environment!"
    exit 1
fi

# Ensure target local directory exists
LOCAL_DIR="$(dirname "$LOCAL_PATH")"
if [[ ! -d "$LOCAL_DIR" ]]; then
    log_info "Creating destination directory: $LOCAL_DIR"
    mkdir -p "$LOCAL_DIR"
fi

# Backup existing local DB if it exists
if [[ -f "$LOCAL_PATH" ]]; then
    BACKUP_PATH="${LOCAL_PATH}.bak.$(date +%Y%m%d_%H%M%S)"
    log_warn "Existing database found at $LOCAL_PATH. Creating backup at $BACKUP_PATH..."
    cp "$LOCAL_PATH" "$BACKUP_PATH"
fi

# Temp file for download
TEMP_DEST="${LOCAL_PATH}.tmp.$$"

cleanup() {
    if [[ -f "$TEMP_DEST" ]]; then
        rm -f "$TEMP_DEST"
    fi
}
trap cleanup EXIT INT TERM

log_info "Copying database from ${REMOTE_HOST}:${REMOTE_PATH} to ${LOCAL_PATH}..."

# Perform SCP transfer
if scp "${REMOTE_HOST}:${REMOTE_PATH}" "$TEMP_DEST"; then
    mv "$TEMP_DEST" "$LOCAL_PATH"
    log_success "Database cloned successfully to $LOCAL_PATH"
else
    log_error "Failed to copy database from ${REMOTE_HOST}:${REMOTE_PATH}"
    exit 1
fi

# Quick SQLite integrity verification if sqlite3 CLI tool is installed
if command -v sqlite3 >/dev/null 2>&1; then
    log_info "Verifying database integrity..."
    INTEGRITY_CHECK=$(sqlite3 "$LOCAL_PATH" "PRAGMA quick_check;" 2>&1 || true)
    if [[ "$INTEGRITY_CHECK" == "ok" ]]; then
        log_success "Database integrity verified (PRAGMA quick_check: ok)."
    else
        log_warn "Integrity check output: $INTEGRITY_CHECK"
    fi
fi

DB_SIZE=$(du -h "$LOCAL_PATH" | cut -f1)
log_success "Done! Local database is ready ($DB_SIZE)."
