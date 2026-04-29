#!/usr/bin/env bash
# Orbit Frontend Dev Server (React 18 + TypeScript + Vite 6)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
FRONTEND_DIR="${ROOT_DIR}/frontend"

NODE_MIN_MAJOR=18

if ! command -v npm >/dev/null 2>&1; then
  echo "npm not found. Install Node.js ${NODE_MIN_MAJOR} or newer first." >&2
  exit 1
fi

NODE_MAJOR="$(node -v | sed 's/^v//; s/\..*//')"
if [[ "${NODE_MAJOR}" -lt "${NODE_MIN_MAJOR}" ]]; then
  echo "Node.js ${NODE_MIN_MAJOR}+ required (found v${NODE_MAJOR})." >&2
  exit 1
fi

if [[ ! -f "${FRONTEND_DIR}/package.json" ]]; then
  echo "Frontend package.json not found: ${FRONTEND_DIR}/package.json" >&2
  exit 1
fi

if [[ -f "${FRONTEND_DIR}/vite.config.js" ]]; then
  echo "Legacy vite.config.js found — TypeScript migration uses vite.config.ts." >&2
  echo "Remove the old file: rm frontend/vite.config.js" >&2
  exit 1
fi

if [[ ! -d "${FRONTEND_DIR}/node_modules" ]]; then
  echo "Frontend dependencies not found." >&2
  if [[ "${1:-}" == "--install" ]]; then
    echo "Installing dependencies..." >&2
    (cd "${FRONTEND_DIR}" && npm install)
  else
    echo "Install them with: cd frontend && npm install" >&2
    echo "Or re-run with: ./scripts/start-frontend.sh --install" >&2
    exit 1
  fi
fi

cd "${FRONTEND_DIR}"

HOST="${ORBIT_FRONTEND_HOST:-127.0.0.1}"
PORT="${ORBIT_FRONTEND_PORT:-5173}"

exec npm run dev -- --host "${HOST}" --port "${PORT}"
