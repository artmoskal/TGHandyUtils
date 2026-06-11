#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -n "${AI_WORKFLOW_ENGINE_STANDALONE_VENV:-}" ]]; then
  VENV_DIR="${AI_WORKFLOW_ENGINE_STANDALONE_VENV}"
  CLEANUP_VENV=0
else
  VENV_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ai-workflow-engine-venv.XXXXXX")"
  CLEANUP_VENV=1
fi

cleanup() {
  if [[ "${CLEANUP_VENV}" == "1" ]]; then
    rm -rf "${VENV_DIR}"
  fi
}
trap cleanup EXIT

python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/python" -m pip install --upgrade pip setuptools wheel
"${VENV_DIR}/bin/python" -m pip install -e "${PACKAGE_DIR}[test]"

cd "${PACKAGE_DIR}"
"${VENV_DIR}/bin/python" -m pytest tests/
