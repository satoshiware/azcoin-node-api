#!/usr/bin/env bash
set -euo pipefail

# -----------------------------------------------------------------------------
# AZCoin Node API - GitHub bootstrap installer for Ubuntu/Debian
#
# Usage examples:
#   sudo bash install-azcoin-node-api.sh
#   sudo BRANCH=my-feature-branch bash install-azcoin-node-api.sh
#   sudo REPO_URL=https://github.com/<you>/azcoin-node-api.git BRANCH=sc-node-api bash install-azcoin-node-api.sh
#
# Notes:
# - This script clones from GitHub onto the target machine.
# - It does NOT require the repo to already exist locally.
# - It creates:
#     /opt/azcoin-node-api
#     /etc/azcoin-node-api/azcoin-node-api.env
#     /var/log/azcoin-node-api
#     /etc/systemd/system/azcoin-node-api.service
# -----------------------------------------------------------------------------

REPO_URL="${REPO_URL:-https://github.com/satoshiware/azcoin-node-api.git}"
BRANCH="${BRANCH:-main}"

APP_NAME="azcoin-node-api"
APP_USER="azcoinapi"
APP_GROUP="azcoinapi"

APP_DIR="/opt/${APP_NAME}"
ETC_DIR="/etc/${APP_NAME}"
LOG_DIR="/var/log/${APP_NAME}"
ENV_FILE="${ETC_DIR}/${APP_NAME}.env"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"

PYTHON_BIN="${PYTHON_BIN:-python3}"
UVICORN_HOST="${UVICORN_HOST:-0.0.0.0}"
UVICORN_PORT="${UVICORN_PORT:-8080}"

log() {
  printf '\n[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"
}

die() {
  printf '\nERROR: %s\n' "$*" >&2
  exit 1
}

need_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    die "Run this script with sudo or as root."
  fi
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

install_os_packages() {
  log "Installing OS packages..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y \
    ca-certificates \
    curl \
    git \
    rsync \
    "${PYTHON_BIN}" \
    python3-pip \
    python3-venv
}

verify_platform() {
  [[ -f /etc/debian_version ]] || die "This installer supports Ubuntu/Debian-style systems only."
  need_cmd apt-get
  need_cmd systemctl
}

ensure_service_account() {
  log "Ensuring service group/user exist..."
  if ! getent group "${APP_GROUP}" >/dev/null 2>&1; then
    groupadd --system "${APP_GROUP}"
  fi

  if ! id -u "${APP_USER}" >/dev/null 2>&1; then
    useradd \
      --system \
      --gid "${APP_GROUP}" \
      --home-dir "${APP_DIR}" \
      --create-home \
      --shell /usr/sbin/nologin \
      "${APP_USER}"
  fi
}

prepare_directories() {
  log "Preparing directories..."
  mkdir -p "${APP_DIR}" "${ETC_DIR}" "${LOG_DIR}"
  chown -R "${APP_USER}:${APP_GROUP}" "${APP_DIR}" "${LOG_DIR}"
  chmod 755 "${APP_DIR}" "${LOG_DIR}"
  chmod 755 "${ETC_DIR}"
}

clone_or_update_repo() {
  log "Cloning/updating repo from GitHub..."
  if [[ -d "${APP_DIR}/.git" ]]; then
    git -C "${APP_DIR}" remote set-url origin "${REPO_URL}"
    git -C "${APP_DIR}" fetch --prune origin
    git -C "${APP_DIR}" checkout "${BRANCH}"
    git -C "${APP_DIR}" reset --hard "origin/${BRANCH}"
    git -C "${APP_DIR}" clean -fdx \
      -e .venv \
      -e .env \
      -e __pycache__ \
      -e .pytest_cache \
      -e .ruff_cache
  else
    rm -rf "${APP_DIR}"
    git clone --branch "${BRANCH}" --single-branch "${REPO_URL}" "${APP_DIR}"
  fi

  chown -R "${APP_USER}:${APP_GROUP}" "${APP_DIR}"
}

create_venv_and_install() {
  log "Creating Python virtualenv..."
  "${PYTHON_BIN}" -m venv "${APP_DIR}/.venv"

  log "Installing Python dependencies..."
  "${APP_DIR}/.venv/bin/python" -m pip install --upgrade pip wheel

  if [[ -f "${APP_DIR}/requirements.txt" ]]; then
    "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"
  else
    die "requirements.txt not found in ${APP_DIR}"
  fi

  if [[ -f "${APP_DIR}/requirements-dev.txt" ]]; then
    "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements-dev.txt"
  fi

  chown -R "${APP_USER}:${APP_GROUP}" "${APP_DIR}/.venv"
}

create_env_file_if_missing() {
  if [[ -f "${ENV_FILE}" ]]; then
    log "Env file already exists; leaving it in place: ${ENV_FILE}"
    return
  fi

  log "Creating initial env file: ${ENV_FILE}"
  cat > "${ENV_FILE}" <<'EOF'
# AZCoin Node API bare-metal testing env
# This is for testing on bare metal. Replace placeholders before starting.

APP_ENV=dev
AUTH_MODE=dev_token
AZ_API_DEV_TOKEN=change-me-now

API_V1_PREFIX=/v1
LOG_LEVEL=INFO
PORT=8080

AZ_RPC_URL=http://127.0.0.1:19332
AZ_RPC_USER=azrpc
AZ_RPC_PASSWORD=change-me
AZ_RPC_TIMEOUT_SECONDS=5
AZ_EXPECTED_CHAIN=micro

BTC_RPC_URL=http://127.0.0.1:8332
BTC_RPC_USER=bitcoinrpc
BTC_RPC_PASSWORD=change-me
BTC_RPC_TIMEOUT_SECONDS=5

# Optional translator observability
TRANSLATOR_LOG_PATH=
TRANSLATOR_LOG_DEFAULT_LINES=200
TRANSLATOR_LOG_MAX_LINES=1000

TRANSLATOR_MONITORING_BASE_URL=
TRANSLATOR_MONITORING_TIMEOUT_SECS=3.0
EOF

  chown root:"${APP_GROUP}" "${ENV_FILE}"
  chmod 640 "${ENV_FILE}"
}

create_systemd_unit() {
  log "Writing systemd unit: ${SERVICE_FILE}"
  cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=AZCoin Node API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${APP_DIR}
Environment=PYTHONPATH=${APP_DIR}/src
EnvironmentFile=-${ENV_FILE}
ExecStart=${APP_DIR}/.venv/bin/uvicorn node_api.main:app --host ${UVICORN_HOST} --port ${UVICORN_PORT}
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

  chmod 644 "${SERVICE_FILE}"
  systemctl daemon-reload
  systemctl enable "${APP_NAME}"
}

env_has_placeholders() {
  grep -Eq 'change-me|change-me-now|=$' "${ENV_FILE}"
}

print_next_steps() {
  cat <<EOF

Install complete.

Repo:
  ${APP_DIR}
Env file:
  ${ENV_FILE}
Logs:
  ${LOG_DIR}
Service:
  ${APP_NAME}

Edit env file:
  sudoedit ${ENV_FILE}

Start service:
  sudo systemctl start ${APP_NAME}

Stop service:
  sudo systemctl stop ${APP_NAME}

Restart service:
  sudo systemctl restart ${APP_NAME}

Status:
  sudo systemctl status ${APP_NAME} --no-pager

Tail logs:
  sudo journalctl -u ${APP_NAME} -f

Health check:
  curl http://127.0.0.1:${UVICORN_PORT}/v1/health

Protected AZ endpoint:
  curl -H "Authorization: Bearer <AZ_API_DEV_TOKEN>" http://127.0.0.1:${UVICORN_PORT}/v1/az/node/info

Translator status (if configured):
  curl -H "Authorization: Bearer <AZ_API_DEV_TOKEN>" http://127.0.0.1:${UVICORN_PORT}/v1/translator/status

Optional validation:
  cd ${APP_DIR}
  sudo -u ${APP_USER} ${APP_DIR}/.venv/bin/python -m pytest -q
  sudo -u ${APP_USER} ${APP_DIR}/.venv/bin/python -m ruff check .
EOF
}

main() {
  need_root
  verify_platform
  install_os_packages
  need_cmd git
  need_cmd rsync
  need_cmd "${PYTHON_BIN}"
  need_cmd systemctl

  ensure_service_account
  prepare_directories
  clone_or_update_repo
  create_venv_and_install
  create_env_file_if_missing
  create_systemd_unit

  if env_has_placeholders; then
    log "Env file still contains placeholders or blank required values."
    log "Service was enabled but not started automatically."
  else
    log "Env file looks non-placeholder; starting service..."
    systemctl restart "${APP_NAME}"
    systemctl --no-pager --full status "${APP_NAME}" || true
  fi

  print_next_steps
}

main "$@"