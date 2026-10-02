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

# Backup existing local DB and associated WAL/SHM files if they exist
if [[ -f "$LOCAL_PATH" ]]; then
    BACKUP_PATH="${LOCAL_PATH}.bak.$(date +%Y%m%d_%H%M%S)"
    log_warn "Existing database found at $LOCAL_PATH. Creating backup at $BACKUP_PATH..."
    cp "$LOCAL_PATH" "$BACKUP_PATH"
    if [[ -f "${LOCAL_PATH}-wal" ]]; then
        cp "${LOCAL_PATH}-wal" "${BACKUP_PATH}-wal" 2>/dev/null || true
    fi
    if [[ -f "${LOCAL_PATH}-shm" ]]; then
        cp "${LOCAL_PATH}-shm" "${BACKUP_PATH}-shm" 2>/dev/null || true
    fi
fi

# Temp file for download
TEMP_DEST="${LOCAL_PATH}.tmp.$$"
REMOTE_TMP_BACKUP="/tmp/setlore_prod_clone_$$.sqlite3"

cleanup() {
    if [[ -f "$TEMP_DEST" ]]; then
        rm -f "$TEMP_DEST"
    fi
    # Clean up remote temporary backup if created
    if [[ -n "${REMOTE_BACKUP_CREATED:-}" ]]; then
        ssh "$REMOTE_HOST" "rm -f '$REMOTE_TMP_BACKUP'" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

log_info "Initiating clone from ${REMOTE_HOST}:${REMOTE_PATH}..."

# Step 1: Attempt remote online atomic backup (handles remote WAL checkpoint cleanly)
REMOTE_BACKUP_CREATED=""
if ssh -o BatchMode=yes -o ConnectTimeout=5 "$REMOTE_HOST" "command -v sqlite3 >/dev/null 2>&1 && sqlite3 '$REMOTE_PATH' '.backup $REMOTE_TMP_BACKUP'" 2>/dev/null; then
    log_info "Created consistent online snapshot on remote host."
    REMOTE_BACKUP_CREATED="1"
    SOURCE_PATH="$REMOTE_TMP_BACKUP"
else
    log_warn "Remote online snapshot via sqlite3 .backup unavailable; falling back to direct SCP."
    SOURCE_PATH="$REMOTE_PATH"
fi

# Step 2: Download database file to temporary local destination
log_info "Transferring database from ${REMOTE_HOST}:${SOURCE_PATH} to temporary staging..."
if scp "${REMOTE_HOST}:${SOURCE_PATH}" "$TEMP_DEST"; then
    log_success "Transfer complete."
else
    log_error "Failed to copy database from ${REMOTE_HOST}:${SOURCE_PATH}"
    exit 1
fi

# Step 3: Verify integrity on the staging database BEFORE swapping into destination
if command -v sqlite3 >/dev/null 2>&1; then
    log_info "Verifying downloaded database integrity..."
    INTEGRITY_CHECK=$(sqlite3 "$TEMP_DEST" "PRAGMA integrity_check;" 2>&1 || true)
    if [[ "$INTEGRITY_CHECK" != "ok" ]]; then
        log_error "Integrity check FAILED on downloaded database:"
        echo "$INTEGRITY_CHECK" >&2
        log_error "Aborting swap to prevent corrupting local environment."
        exit 1
    fi
    log_success "Database integrity verified (PRAGMA integrity_check: ok)."
fi

# Step 4: Safely remove existing WAL and SHM files to prevent WAL replay corruption
if [[ -f "${LOCAL_PATH}-wal" || -f "${LOCAL_PATH}-shm" ]]; then
    log_info "Removing existing local WAL and SHM files to prevent transaction log replay mismatch..."
    rm -f "${LOCAL_PATH}-wal" "${LOCAL_PATH}-shm"
fi

# Step 5: Atomic swap into place
mv "$TEMP_DEST" "$LOCAL_PATH"
log_success "Database cloned successfully to $LOCAL_PATH"

# Step 6: Post-clone housekeeping - Clear stale Django sessions to prevent 500 auth/session lookup errors
if command -v sqlite3 >/dev/null 2>&1; then
    log_info "Clearing stale user sessions from clone to prevent invalid session errors..."
    sqlite3 "$LOCAL_PATH" "DELETE FROM django_session;" 2>/dev/null || true
fi

if [[ -f "${REPO_ROOT}/manage.py" ]]; then
    log_info "Running Django system check..."
    python3 "${REPO_ROOT}/manage.py" check --fail-level ERROR 2>/dev/null || true
fi

DB_SIZE=$(du -h "$LOCAL_PATH" | cut -f1)
log_success "Done! Local database is ready ($DB_SIZE)."
