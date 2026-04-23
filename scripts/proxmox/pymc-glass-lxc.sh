#!/usr/bin/env bash
source <(curl -fsSL https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main/misc/build.func)
# Copyright (c) 2026 yellowcooln
# License: MIT

APP="pyMC-Glass"
APP_REPO_URL="${APP_REPO_URL:-${REPO_URL:-https://github.com/pyMC-dev/pyMC-Glass.git}}"
APP_REPO_BRANCH="${APP_REPO_BRANCH:-${REPO_BRANCH:-main}}"
SCRIPT_REPO_BRANCH="${SCRIPT_REPO_BRANCH:-dev}"
SCRIPT_RAW_BASE_URL="${SCRIPT_RAW_BASE_URL:-https://raw.githubusercontent.com/yellowcooln/pyMC-Glass/${SCRIPT_REPO_BRANCH}}"
INSTALL_SCRIPT_URL="${INSTALL_SCRIPT_URL:-${SCRIPT_RAW_BASE_URL}/scripts/proxmox/install-pymc-glass-lxc.sh}"
FRONTEND_PORT="${FRONTEND_PORT:-80}"
API_PORT="${API_PORT:-8080}"
APP_DIR="${APP_DIR:-/opt/pymc-glass}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@pymc.glass}"
ADMIN_DISPLAY_NAME="${ADMIN_DISPLAY_NAME:-Admin}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-admin12345678}"
mode="${mode:-advanced}"

# Community-scripts LXC defaults. Storage/template/network selection is handled by build.func.
var_tags="${var_tags:-docker;pymc-glass}"
var_cpu="${var_cpu:-2}"
var_ram="${var_ram:-4096}"
var_disk="${var_disk:-16}"
var_os="${var_os:-ubuntu}"
var_version="${var_version:-24.04}"
var_hostname="${var_hostname:-pymc-glass}"
var_unprivileged="${var_unprivileged:-1}"
var_nesting="${var_nesting:-1}"
var_keyctl="${var_keyctl:-1}"
var_tun="${var_tun:-yes}"

header_info() {
  clear
  echo
  echo "pyMC-Glass LXC Installer"
  echo
}

header_info "$APP"
variables

# Reuse the official docker-install stage so the CT is created with the same
# Proxmox helper flow and gets a Docker-ready userspace before app deployment.
var_install="docker-install"

color
catch_errors

plain_output_theme() {
  TAB="  "
  TAB3="      "
  CM="[OK]"
  CROSS="[ERROR]"
  INFO="[INFO]"
  OS="OS:"
  HOSTNAME="Hostname:"
  NETWORK="Network:"
  GATEWAY="URL:"
  DEFAULT="[DEFAULT]"
  ADVANCED="[ADVANCED]"
  CREATING=""
}

msg_info() {
  local msg="$1"
  [[ -z "$msg" ]] && return
  if ! declare -p MSG_INFO_SHOWN &>/dev/null || ! declare -A MSG_INFO_SHOWN &>/dev/null; then
    declare -gA MSG_INFO_SHOWN=()
  fi
  [[ -n "${MSG_INFO_SHOWN["$msg"]+x}" ]] && return
  MSG_INFO_SHOWN["$msg"]=1
  log_msg "[INFO] $msg"
  stop_spinner
  echo "[INFO] $msg"
}

msg_ok() {
  local msg="$1"
  [[ -z "$msg" ]] && return
  stop_spinner
  clear_line
  echo "[OK] $msg"
  log_msg "[OK] $msg"
}

msg_warn() {
  local msg="$1"
  [[ -z "$msg" ]] && return
  stop_spinner
  echo "[WARN] $msg" >&2
  log_msg "[WARN] $msg"
}

msg_error() {
  local msg="$1"
  [[ -z "$msg" ]] && return
  stop_spinner
  echo "[ERROR] $msg" >&2
  log_msg "[ERROR] $msg"
}

msg_custom() {
  local _symbol="${1:-}"
  local _color="${2:-}"
  local msg="${3:-}"
  [[ -z "$msg" ]] && return
  stop_spinner
  echo "$msg"
  log_msg "$msg"
}

plain_output_theme

run_app_installer() {
  msg_info "Deploying pyMC-Glass into CT ${CTID}"
  pct exec "${CTID}" -- env \
    APP_REPO_URL="${APP_REPO_URL}" \
    APP_REPO_BRANCH="${APP_REPO_BRANCH}" \
    APP_DIR="${APP_DIR}" \
    FRONTEND_PORT="${FRONTEND_PORT}" \
    API_PORT="${API_PORT}" \
    ADMIN_EMAIL="${ADMIN_EMAIL}" \
    ADMIN_DISPLAY_NAME="${ADMIN_DISPLAY_NAME}" \
    POSTGRES_PASSWORD="${POSTGRES_PASSWORD}" \
    ADMIN_PASSWORD="${ADMIN_PASSWORD}" \
    bash -c 'curl -fsSL "$0" | bash' "${INSTALL_SCRIPT_URL}" || {
    msg_error "pyMC-Glass deployment failed inside CT ${CTID}"
    exit 1
  }
  msg_ok "pyMC-Glass deployed"
}

function update_script() {
  header_info
  check_container_storage
  check_container_resources
  if [[ ! -x /usr/local/bin/pymc-glass-update ]]; then
    msg_error "No ${APP} installation found!"
    exit
  fi
  msg_info "Updating ${APP}"
  $STD /usr/local/bin/pymc-glass-update
  msg_ok "Updated ${APP}"
  msg_ok "Updated successfully!"
  exit
}

start
build_container
run_app_installer
description

msg_ok "Completed successfully!\n"
echo "pyMC-Glass setup has been successfully initialized."
echo "URL:"
echo "  http://${IP}:${FRONTEND_PORT}"
echo "Backend health endpoint:"
echo "  http://${IP}:${API_PORT}/healthz"
echo "Default login:"
echo "  Username: ${ADMIN_EMAIL}"
echo "  Password: ${ADMIN_PASSWORD}"
