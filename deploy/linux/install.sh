#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="azcoin-node-api"
SERVICE_USER="azcoinapi"
SERVICE_GROUP="azcoinapi"
APP_DIR="/opt/azcoin-node-api"
CONFIG_DIR="/etc/azcoin-node-api"
LOG_DIR="/var/log/azcoin-node-api"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
ENV_PATH="${CONFIG_DIR}/${SERVICE_NAME}.env"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

require_command() {
    local cmd="$1"
    if ! command -v "${cmd}" >/dev/null 2>&1; then
        echo "Error: required command '${cmd}' was not found in PATH." >&2
        exit 1
    fi
}

copy_repo_contents() {
    local source_dir="$1"
    local target_dir="$2"

    if command -v rsync >/dev/null 2>&1; then
        rsync -a --delete \
            --exclude '.env' \
            --exclude '.git' \
            --exclude '.venv' \
            --exclude '__pycache__' \
            --exclude '.pytest_cache' \
            --exclude '.ruff_cache' \
            "${source_dir}/" "${target_dir}/"
        return
    fi

    echo "rsync not found; falling back to cp -a." >&2

    cp -a "${source_dir}/." "${target_dir}/"

    rm -rf \
        "${target_dir}/.env" \
        "${target_dir}/.git" \
        "${target_dir}/.venv"

    find "${target_dir}" -type d \
        \( -name '__pycache__' -o -name '.pytest_cache' -o -name '.ruff_cache' \) \
        -prune -exec rm -rf {} +
}

env_has_placeholders() {
    if [[ ! -f "${ENV_PATH}" ]]; then
        return 0
    fi

    grep -Eq '^[A-Z0-9_]+=.*change-me' "${ENV_PATH}"
}

if [[ "$(pwd -P)" != "${REPO_ROOT}" ]]; then
    echo "Error: run this installer from the repository root: ${REPO_ROOT}" >&2
    exit 1
fi

if [[ "${EUID}" -ne 0 ]]; then
    echo "Error: this installer must be run as root (for example: sudo bash deploy/linux/install.sh)." >&2
    exit 1
fi

require_command bash
require_command chown
require_command cp
require_command find
require_command getent
require_command grep
require_command groupadd
require_command id
require_command install
require_command mkdir
require_command python3
require_command rm
require_command systemctl
require_command useradd

if ! python3 -c "import venv" >/dev/null 2>&1; then
    echo "Error: python3 is installed but the venv module is unavailable. Install python3-venv first." >&2
    exit 1
fi

if ! getent group "${SERVICE_GROUP}" >/dev/null 2>&1; then
    groupadd --system "${SERVICE_GROUP}"
fi

if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
    useradd \
        --system \
        --gid "${SERVICE_GROUP}" \
        --home-dir "${APP_DIR}" \
        --shell /usr/sbin/nologin \
        "${SERVICE_USER}"
fi

install -d -m 0755 "${APP_DIR}"
install -d -m 0755 "${CONFIG_DIR}"
install -d -m 0755 "${LOG_DIR}"

copy_repo_contents "${REPO_ROOT}" "${APP_DIR}"

python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/python" -m pip install --upgrade pip
"${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

install -m 0644 "${REPO_ROOT}/deploy/linux/azcoin-node-api.service" "${UNIT_PATH}"

if [[ ! -f "${ENV_PATH}" ]]; then
    install -m 0640 "${REPO_ROOT}/deploy/linux/azcoin-node-api.env.example" "${ENV_PATH}"
fi

chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${APP_DIR}" "${CONFIG_DIR}" "${LOG_DIR}"

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"

echo
if env_has_placeholders; then
    echo "Environment file still contains placeholder values. The service was enabled but not started."
else
    echo "Installation completed. The service was enabled but not started."
fi
echo "Edit env file: sudoedit ${ENV_PATH}"
echo "Start service: sudo systemctl start ${SERVICE_NAME}"
echo "Check status: sudo systemctl status ${SERVICE_NAME}"
echo "Tail logs: sudo journalctl -u ${SERVICE_NAME} -f"
