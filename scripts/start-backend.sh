#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_PYTHON="${ROOT_DIR}/Orbit/bin/python"

if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "Virtual environment not found: ${VENV_PYTHON}" >&2
  echo "Create it first with: python3 -m venv Orbit" >&2
  exit 1
fi

cd "${ROOT_DIR}/backend"

HOST="${ORBIT_BACKEND_HOST:-127.0.0.1}"
PORT="${ORBIT_BACKEND_PORT:-8000}"
RELOAD="${ORBIT_BACKEND_RELOAD:-true}"
MIGRATE="${ORBIT_BACKEND_MIGRATE:-true}"
CREATE_DATABASE="${ORBIT_BACKEND_CREATE_DATABASE:-true}"

if [[ "${CREATE_DATABASE}" == "true" ]]; then
  "${VENV_PYTHON}" "${SCRIPT_DIR}/ensure-database.py"
fi

if [[ "${MIGRATE}" == "true" ]]; then
  "${VENV_PYTHON}" -m alembic upgrade head
fi

ARGS=(
  "${VENV_PYTHON}"
  -m
  uvicorn
  app.main:app
  --host
  "${HOST}"
  --port
  "${PORT}"
)

if [[ "${RELOAD}" == "true" ]]; then
  ARGS+=(--reload)
fi

exec "${ARGS[@]}"
