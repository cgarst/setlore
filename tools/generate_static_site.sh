#!/usr/bin/env bash
# ==============================================================================
# Generate / Update Static Demo Site (GitHub Pages)
# ==============================================================================
# Builds a self-contained, interactive static demo build of Setlore using the
# Django management command (freeze_demo / freeze).
#
# Usage:
#   ./tools/generate_static_site.sh [OPTIONS]
#
# Examples:
#   ./tools/generate_static_site.sh
#   ./tools/generate_static_site.sh --user Zathu
#   ./tools/generate_static_site.sh --output-dir dist
#   ./tools/generate_static_site.sh --serve
# ==============================================================================

set -euo pipefail

# Resolve repository root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/.." && pwd))"

# Helper logging functions
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

# Determine Python executable
find_python() {
    if [[ -n "${PYTHON_EXEC:-}" && -x "$PYTHON_EXEC" ]]; then
        echo "$PYTHON_EXEC"
        return 0
    fi
    if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
        echo "${REPO_ROOT}/.venv/bin/python"
        return 0
    fi
    if [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
        echo "${VIRTUAL_ENV}/bin/python"
        return 0
    fi
    if command -v python3 >/dev/null 2>&1; then
        echo "$(command -v python3)"
        return 0
    fi
    if command -v python >/dev/null 2>&1; then
        echo "$(command -v python)"
        return 0
    fi
    return 1
}

# Help text
show_help() {
    cat << EOF
Usage: $(basename "$0") [OPTIONS] [-- DJANGO_OPTIONS]

Generates or updates the static demo site for Setlore (GitHub Pages / static hosting).

Options:
  -u, --user, --username USER  Target username whose public profile is frozen
  -o, --output-dir DIR         Destination directory (default: docs)
  -r, --repo-url URL           GitHub repository URL for installation links
  --no-clean                   Do not clean output directory before building
  -s, --serve [PORT]           Start a local preview HTTP server after build (default port: 8000)
  -h, --help                   Show this help message and exit

All other options are forwarded directly to 'manage.py freeze_demo'.

Examples:
  ./tools/generate_static_site.sh
  ./tools/generate_static_site.sh -u demouser
  ./tools/generate_static_site.sh -o dist
  ./tools/generate_static_site.sh --serve
  ./tools/generate_static_site.sh --serve 8080
EOF
}

# Parse custom wrapper arguments
SERVE_AFTER=false
SERVE_PORT=8000
OUTPUT_DIR="docs"
PASSTHROUGH_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            show_help
            exit 0
            ;;
        -s|--serve)
            SERVE_AFTER=true
            if [[ $# -gt 1 && "$2" =~ ^[0-9]+$ ]]; then
                SERVE_PORT="$2"
                shift 2
            else
                shift 1
            fi
            ;;
        -o|--output-dir)
            if [[ $# -gt 1 ]]; then
                OUTPUT_DIR="$2"
                PASSTHROUGH_ARGS+=("$1" "$2")
                shift 2
            else
                PASSTHROUGH_ARGS+=("$1")
                shift 1
            fi
            ;;
        *)
            PASSTHROUGH_ARGS+=("$1")
            shift 1
            ;;
    esac
done

# Locate python
PYTHON_BIN="$(find_python || true)"
if [[ -z "$PYTHON_BIN" ]]; then
    log_error "Could not find a Python interpreter. Please activate a virtual environment or create .venv."
    exit 1
fi

log_info "Using Python: $PYTHON_BIN"
log_info "Running static site generator (manage.py freeze_demo)..."

cd "$REPO_ROOT"
"$PYTHON_BIN" manage.py freeze_demo ${PASSTHROUGH_ARGS[@]+"${PASSTHROUGH_ARGS[@]}"}

log_success "Static site successfully updated in '${OUTPUT_DIR}'!"

# Optional local preview server
if [[ "$SERVE_AFTER" == true ]]; then
    log_info "Starting local preview server on http://localhost:${SERVE_PORT} (serving ${OUTPUT_DIR})..."
    log_info "Press Ctrl+C to stop."
    "$PYTHON_BIN" -m http.server -d "$OUTPUT_DIR" "$SERVE_PORT"
fi
