#!/usr/bin/env bash
# Install Orbit backend and frontend dependencies for local or server deployment.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
FRONTEND_DIR="${ROOT_DIR}/frontend"
VENV_DIR="${ORBIT_VENV_DIR:-${ROOT_DIR}/Orbit}"

PYTHON_BIN="${PYTHON_BIN:-python3}"
PYTHON_MIN_MAJOR=3
PYTHON_MIN_MINOR=10
NODE_MIN_MAJOR=18

INSTALL_BACKEND=true
INSTALL_FRONTEND=true
BUILD_FRONTEND=false
RUN_MIGRATIONS=false

usage() {
  cat <<'USAGE'
Usage: ./scripts/install-env.sh [options]

Install backend Python dependencies and frontend npm dependencies.

Options:
  --backend-only       Install backend dependencies only
  --frontend-only      Install frontend dependencies only
  --build-frontend     Run npm build after frontend install
  --migrate            Run backend database creation and Alembic migrations
  --venv PATH          Python virtual environment path (default: ./Orbit)
  -h, --help           Show this help

Environment:
  PYTHON_BIN           Python executable to use (default: python3)
  ORBIT_VENV_DIR       Virtual environment path (default: ./Orbit)
USAGE
}

log() {
  printf '\n==> %s\n' "$*"
}

fail() {
  echo "Error: $*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backend-only)
      INSTALL_BACKEND=true
      INSTALL_FRONTEND=false
      ;;
    --frontend-only)
      INSTALL_BACKEND=false
      INSTALL_FRONTEND=true
      ;;
    --build-frontend)
      BUILD_FRONTEND=true
      ;;
    --migrate)
      RUN_MIGRATIONS=true
      ;;
    --venv)
      [[ $# -ge 2 ]] || fail "--venv requires a path"
      VENV_DIR="$2"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "Unknown option: $1"
      ;;
  esac
  shift
done

check_python() {
  command -v "${PYTHON_BIN}" >/dev/null 2>&1 || fail "${PYTHON_BIN} not found. Install Python ${PYTHON_MIN_MAJOR}.${PYTHON_MIN_MINOR}+ first."

  "${PYTHON_BIN}" - <<PY
import sys
required = (${PYTHON_MIN_MAJOR}, ${PYTHON_MIN_MINOR})
current = sys.version_info[:2]
if current < required:
    raise SystemExit(f"Python {required[0]}.{required[1]}+ required, found {sys.version.split()[0]}")
PY
}

check_node() {
  command -v npm >/dev/null 2>&1 || fail "npm not found. Install Node.js ${NODE_MIN_MAJOR}+ first."
  command -v node >/dev/null 2>&1 || fail "node not found. Install Node.js ${NODE_MIN_MAJOR}+ first."

  local node_major
  node_major="$(node -v | sed 's/^v//; s/\..*//')"
  [[ "${node_major}" =~ ^[0-9]+$ ]] || fail "Unable to parse Node.js version: $(node -v)"
  if (( node_major < NODE_MIN_MAJOR )); then
    fail "Node.js ${NODE_MIN_MAJOR}+ required, found $(node -v)"
  fi
}

install_backend() {
  [[ -f "${BACKEND_DIR}/pyproject.toml" ]] || fail "Backend pyproject.toml not found: ${BACKEND_DIR}/pyproject.toml"

  check_python

  if [[ ! -d "${VENV_DIR}" ]]; then
    log "Creating Python virtual environment at ${VENV_DIR}"
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
  fi

  local venv_python="${VENV_DIR}/bin/python"
  [[ -x "${venv_python}" ]] || fail "Virtual environment python not found: ${venv_python}"

  log "Installing backend dependencies"
  "${venv_python}" -m pip install --upgrade pip
  "${venv_python}" -m pip install -e "${BACKEND_DIR}"

  if [[ "${RUN_MIGRATIONS}" == "true" ]]; then
    log "Creating database if needed"
    "${venv_python}" "${SCRIPT_DIR}/ensure-database.py"

    log "Running database migrations"
    (cd "${BACKEND_DIR}" && "${venv_python}" -m alembic upgrade head)
  fi
}

install_frontend() {
  [[ -f "${FRONTEND_DIR}/package.json" ]] || fail "Frontend package.json not found: ${FRONTEND_DIR}/package.json"

  check_node

  log "Installing frontend dependencies"
  if [[ -f "${FRONTEND_DIR}/package-lock.json" ]]; then
    (cd "${FRONTEND_DIR}" && npm ci)
  else
    (cd "${FRONTEND_DIR}" && npm install)
  fi

  if [[ "${BUILD_FRONTEND}" == "true" ]]; then
    log "Building frontend"
    (cd "${FRONTEND_DIR}" && npm run build)
  fi
}

main() {
  log "Orbit environment installer"

  if [[ "${INSTALL_BACKEND}" == "true" ]]; then
    install_backend
  fi

  if [[ "${INSTALL_FRONTEND}" == "true" ]]; then
    install_frontend
  fi

  log "Done"
  echo "Backend: ./scripts/start-backend.sh"
  echo "Frontend: ./scripts/start-frontend.sh"
}

main "$@"
