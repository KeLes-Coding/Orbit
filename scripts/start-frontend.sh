#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
FRONTEND_DIR="${ROOT_DIR}/frontend"

if ! command -v npm >/dev/null 2>&1; then
  echo "npm not found. Install Node.js 18 or newer first." >&2
  exit 1
fi

if [[ ! -f "${FRONTEND_DIR}/package.json" ]]; then
  echo "Frontend package.json not found: ${FRONTEND_DIR}/package.json" >&2
  exit 1
fi

if [[ ! -d "${FRONTEND_DIR}/node_modules" ]]; then
  echo "Frontend dependencies not found." >&2
  echo "Install them first with: cd frontend && npm install" >&2
  exit 1
fi

cd "${FRONTEND_DIR}"

HOST="${ORBIT_FRONTEND_HOST:-127.0.0.1}"
PORT="${ORBIT_FRONTEND_PORT:-5173}"

exec npm run dev -- --host "${HOST}" --port "${PORT}"
