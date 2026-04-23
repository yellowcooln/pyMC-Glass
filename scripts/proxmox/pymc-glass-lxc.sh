#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="pyMC_Glass"
APP_REPO_URL="${APP_REPO_URL:-${REPO_URL:-https://github.com/pyMC-dev/pyMC-Glass.git}}"
APP_REPO_BRANCH="${APP_REPO_BRANCH:-${REPO_BRANCH:-main}}"
SCRIPT_REPO_BRANCH="${SCRIPT_REPO_BRANCH:-dev}"
SCRIPT_RAW_BASE_URL="${SCRIPT_RAW_BASE_URL:-https://raw.githubusercontent.com/yellowcooln/pyMC-Glass/${SCRIPT_REPO_BRANCH}}"
INSTALL_SCRIPT_URL="${INSTALL_SCRIPT_URL:-${SCRIPT_RAW_BASE_URL}/scripts/proxmox/install-pymc-glass-lxc.sh}"
CTID="${CTID:-}"
CT_HOSTNAME="${CT_HOSTNAME:-pymc-glass}"
CORES="${CORES:-2}"
MEMORY="${MEMORY:-4096}"
DISK_GB="${DISK_GB:-16}"
SWAP="${SWAP:-1024}"
BRIDGE="${BRIDGE:-vmbr0}"
STORAGE="${STORAGE:-}"
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-}"
LXC_OSTYPE="${LXC_OSTYPE:-ubuntu}"
LXC_OSVERSION="${LXC_OSVERSION:-24.04}"
UNPRIVILEGED="${UNPRIVILEGED:-1}"
ONBOOT="${ONBOOT:-1}"
IP_CONFIG="${IP_CONFIG:-dhcp}"
NAMESERVER="${NAMESERVER:-}"
SEARCHDOMAIN="${SEARCHDOMAIN:-}"
FEATURES="${FEATURES:-nesting=1,keyctl=1}"
PASSWORD="${PASSWORD:-}"
START_AFTER_CREATE="${START_AFTER_CREATE:-1}"
APP_DIR="${APP_DIR:-/opt/pymc-glass}"
FRONTEND_PORT="${FRONTEND_PORT:-80}"
API_PORT="${API_PORT:-8080}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@pymc.glass}"
ADMIN_DISPLAY_NAME="${ADMIN_DISPLAY_NAME:-Admin}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"

if [[ "${DEBUG:-0}" == "1" ]]; then
  set -x
fi

log() {
  printf '[%s LXC] %s\n' "${APP_NAME}" "$*"
}

fail() {
  printf '[%s LXC] ERROR: %s\n' "${APP_NAME}" "$*" >&2
  exit 1
}

on_error() {
  printf '[%s LXC] ERROR: failed at line %s\n' "${APP_NAME}" "$1" >&2
}

trap 'on_error $LINENO' ERR

require_host_tools() {
  local cmd
  for cmd in pct pveam pvesh pvesm awk curl; do
    command -v "${cmd}" >/dev/null 2>&1 || fail "missing required Proxmox command: ${cmd}"
  done
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    fail "run this script as root on the Proxmox host"
  fi
}

choose_storage() {
  if [[ -z "${STORAGE}" ]]; then
    STORAGE="$(pvesm status -content rootdir | awk 'NR>1 {print $1; exit}')"
  fi
  if [[ -z "${TEMPLATE_STORAGE}" ]]; then
    TEMPLATE_STORAGE="$(pvesm status -content vztmpl | awk 'NR>1 {print $1; exit}')"
  fi
  [[ -n "${STORAGE}" ]] || fail "unable to auto-detect rootdir storage; set STORAGE=<name>"
  [[ -n "${TEMPLATE_STORAGE}" ]] || fail "unable to auto-detect template storage; set TEMPLATE_STORAGE=<name>"
}

resolve_ctid() {
  if [[ -z "${CTID}" ]]; then
    CTID="$(pvesh get /cluster/nextid)"
  fi
  if pct status "${CTID}" >/dev/null 2>&1; then
    fail "container ${CTID} already exists"
  fi
}

resolve_template() {
  TEMPLATE_NAME="$(pveam available --section system | awk -v os="${LXC_OSTYPE}" -v ver="${LXC_OSVERSION}" '$2 ~ ("^" os "-" ver "-standard_") {print $2}' | tail -n1)"
  if [[ -z "${TEMPLATE_NAME}" ]]; then
    log "Refreshing template catalog"
    pveam update
    TEMPLATE_NAME="$(pveam available --section system | awk -v os="${LXC_OSTYPE}" -v ver="${LXC_OSVERSION}" '$2 ~ ("^" os "-" ver "-standard_") {print $2}' | tail -n1)"
  fi
  [[ -n "${TEMPLATE_NAME}" ]] || fail "unable to find ${LXC_OSTYPE} ${LXC_OSVERSION} template via pveam"

  if ! pveam list "${TEMPLATE_STORAGE}" | grep -Fq "${TEMPLATE_NAME}"; then
    log "Downloading ${TEMPLATE_NAME} to ${TEMPLATE_STORAGE}"
    pveam download "${TEMPLATE_STORAGE}" "${TEMPLATE_NAME}"
  fi

  TEMPLATE_PATH="${TEMPLATE_STORAGE}:vztmpl/${TEMPLATE_NAME}"
}

create_container() {
  log "Creating CT ${CTID} (${CT_HOSTNAME})"

  local args=(
    "${CTID}" "${TEMPLATE_PATH}"
    --hostname "${CT_HOSTNAME}"
    --cores "${CORES}"
    --memory "${MEMORY}"
    --swap "${SWAP}"
    --rootfs "${STORAGE}:${DISK_GB}"
    --net0 "name=eth0,bridge=${BRIDGE},ip=${IP_CONFIG}"
    --features "${FEATURES}"
    --unprivileged "${UNPRIVILEGED}"
    --onboot "${ONBOOT}"
    --ostype "${LXC_OSTYPE}"
  )

  if [[ -n "${PASSWORD}" ]]; then
    args+=(--password "${PASSWORD}")
  fi
  if [[ -n "${NAMESERVER}" ]]; then
    args+=(--nameserver "${NAMESERVER}")
  fi
  if [[ -n "${SEARCHDOMAIN}" ]]; then
    args+=(--searchdomain "${SEARCHDOMAIN}")
  fi

  pct create "${args[@]}"
}

start_container() {
  if [[ "${START_AFTER_CREATE}" != "1" ]]; then
    return
  fi

  log "Starting CT ${CTID}"
  pct start "${CTID}"
}

wait_for_network() {
  log "Waiting for container networking"
  for _ in $(seq 1 30); do
    if pct exec "${CTID}" -- bash -lc 'getent hosts github.com >/dev/null 2>&1'; then
      return
    fi
    sleep 2
  done

  fail "container networking did not come up in time"
}

run_installer() {
  log "Running the in-container installer"
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
    bash -lc 'curl -fsSL "$0" | bash' "${INSTALL_SCRIPT_URL}"
}

container_ip() {
  pct exec "${CTID}" -- bash -lc "hostname -I | awk '{print \$1}'" 2>/dev/null | tr -d '\r'
}

print_summary() {
  local ip
  ip="$(container_ip || true)"

  cat <<EOF

${APP_NAME} LXC created successfully.

CTID: ${CTID}
Hostname: ${CT_HOSTNAME}
URL: http://${ip:-<container-ip>}:${FRONTEND_PORT}
API: http://${ip:-<container-ip>}:${API_PORT}/healthz

Useful commands:
  pct enter ${CTID}
  pct stop ${CTID}
  pct start ${CTID}
EOF
}

main() {
  log "Starting Proxmox host installer"
  require_root
  require_host_tools
  choose_storage
  resolve_ctid
  resolve_template
  create_container
  start_container
  wait_for_network
  run_installer
  print_summary
}

main "$@"
