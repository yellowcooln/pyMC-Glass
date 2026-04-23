#!/usr/bin/env bash
set -Eeuo pipefail

APP_REPO_URL="${APP_REPO_URL:-${REPO_URL:-https://github.com/pyMC-dev/pyMC-Glass.git}}"
APP_REPO_BRANCH="${APP_REPO_BRANCH:-${REPO_BRANCH:-main}}"
APP_DIR="${APP_DIR:-/opt/pymc-glass}"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-pymc-glass}"
FRONTEND_PORT="${FRONTEND_PORT:-80}"
API_PORT="${API_PORT:-8080}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@pymc.glass}"
ADMIN_DISPLAY_NAME="${ADMIN_DISPLAY_NAME:-Admin}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-admin12345678}"
INSTALL_LOG_FILE="${INSTALL_LOG_FILE:-/var/log/pymc-glass-install.log}"

exec 3>&1

if [[ "${DEBUG:-0}" == "1" ]]; then
  set -x
else
  mkdir -p "$(dirname "${INSTALL_LOG_FILE}")"
  : >"${INSTALL_LOG_FILE}"
  chmod 0600 "${INSTALL_LOG_FILE}"
  exec >>"${INSTALL_LOG_FILE}" 2>&1
fi

log() {
  printf '[pyMC_Glass] %s\n' "$*" >&3
}

fail() {
  printf '[pyMC_Glass] ERROR: %s\n' "$*" >&3
  if [[ "${DEBUG:-0}" != "1" ]]; then
    printf '[pyMC_Glass] Log: %s\n' "${INSTALL_LOG_FILE}" >&3
  fi
  exit 1
}

on_error() {
  printf '[pyMC_Glass] ERROR: failed at line %s\n' "$1" >&3
  if [[ "${DEBUG:-0}" != "1" ]]; then
    printf '[pyMC_Glass] Log: %s\n' "${INSTALL_LOG_FILE}" >&3
  fi
}

trap 'on_error $LINENO' ERR

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    fail "run this script as root inside the LXC"
  fi
}

detect_os() {
  if [[ ! -r /etc/os-release ]]; then
    fail "unable to detect operating system"
  fi

  # shellcheck disable=SC1091
  source /etc/os-release
  case "${ID}" in
    ubuntu|debian)
      OS_ID="${ID}"
      OS_CODENAME="${VERSION_CODENAME:-}"
      ;;
    *)
      fail "unsupported distribution: ${ID}. Use Ubuntu or Debian in the LXC."
      ;;
  esac

  if [[ -z "${OS_CODENAME}" ]]; then
    fail "unable to determine distribution codename"
  fi
}

ensure_command() {
  command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"
}

random_password() {
  local length="${1}"
  local bytes
  bytes=$(((length + 1) / 2))
  openssl rand -hex "${bytes}" | cut -c "1-${length}"
}

replace_env_value() {
  local file="${1}"
  local key="${2}"
  local value="${3}"
  local escaped_value
  escaped_value="$(printf '%s' "${value}" | sed 's/[\/&]/\\&/g')"

  if grep -q "^${key}=" "${file}"; then
    sed -i "s/^${key}=.*/${key}=${escaped_value}/" "${file}"
  else
    printf '%s=%s\n' "${key}" "${value}" >>"${file}"
  fi
}

copy_file() {
  local source="${1}"
  local target="${2}"
  install -D -m 0644 "${source}" "${target}"
}

install_base_packages() {
  log "Installing base packages"
  apt-get update
  apt-get install -y ca-certificates curl git gnupg make openssl
}

write_custom_profile() {
  local profile_file
  profile_file="/etc/profile.d/00_lxc-details.sh"
  local motd_file
  motd_file="/etc/motd"

  log "Writing pyMC-Glass container profile"
  cat >"${profile_file}" <<'EOF'
echo ""
echo "pyMC-Glass LXC Container"
echo "  GitHub: https://github.com/pyMC-dev/pyMC-Glass"
echo ""
echo "  OS: $(grep ^NAME /etc/os-release | cut -d= -f2 | tr -d '"') - Version: $(grep ^VERSION_ID /etc/os-release | cut -d= -f2 | tr -d '"')"
echo "  Hostname: $(hostname)"
echo "  IP Address: $(hostname -I | awk '{print $1}')"
EOF
  chmod 0644 "${profile_file}"

  cat >"${motd_file}" <<'EOF'
pyMC-Glass LXC Container
  GitHub: https://github.com/pyMC-dev/pyMC-Glass
EOF
  chmod 0644 "${motd_file}"
}

install_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    log "Docker Engine already present"
    return
  fi

  log "Installing Docker Engine"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/${OS_ID}/gpg" | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg

  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/%s %s stable\n' \
    "$(dpkg --print-architecture)" "${OS_ID}" "${OS_CODENAME}" \
    >/etc/apt/sources.list.d/docker.list

  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
}

checkout_repo() {
  if [[ -d "${APP_DIR}/.git" ]]; then
    log "Updating repository in ${APP_DIR}"
    git -C "${APP_DIR}" fetch --depth 1 origin "${APP_REPO_BRANCH}"
    git -C "${APP_DIR}" checkout -f "${APP_REPO_BRANCH}"
    git -C "${APP_DIR}" reset --hard "origin/${APP_REPO_BRANCH}"
    return
  fi

  log "Cloning ${APP_REPO_URL} (${APP_REPO_BRANCH}) into ${APP_DIR}"
  rm -rf "${APP_DIR}"
  git clone --branch "${APP_REPO_BRANCH}" --depth 1 "${APP_REPO_URL}" "${APP_DIR}"
}

prepare_env_files() {
  local compose_env backend_env backend_runtime_env
  compose_env="${APP_DIR}/.env.production"
  backend_env="${APP_DIR}/backend/.env.production"
  backend_runtime_env="${APP_DIR}/backend/.env"

  log "Preparing production environment files"
  [[ -f "${compose_env}" ]] || copy_file "${APP_DIR}/.env.production.example" "${compose_env}"
  [[ -f "${backend_env}" ]] || copy_file "${APP_DIR}/backend/.env.production.example" "${backend_env}"

  if [[ -z "${POSTGRES_PASSWORD}" ]]; then
    POSTGRES_PASSWORD="$(random_password 32)"
  fi
  replace_env_value "${compose_env}" "POSTGRES_PASSWORD" "${POSTGRES_PASSWORD}"
  replace_env_value "${backend_env}" "DATABASE_URL" "postgresql+psycopg://postgres:${POSTGRES_PASSWORD}@postgres:5432/pymc_glass"
  replace_env_value "${backend_env}" "BOOTSTRAP_SEED_ADMIN_EMAIL" "${ADMIN_EMAIL}"
  replace_env_value "${backend_env}" "BOOTSTRAP_SEED_ADMIN_PASSWORD" "${ADMIN_PASSWORD}"
  replace_env_value "${backend_env}" "BOOTSTRAP_SEED_ADMIN_DISPLAY_NAME" "${ADMIN_DISPLAY_NAME}"

  # The base compose file still references backend/.env for pki-init and backend.
  # Mirror the production env so docker compose has the file it expects.
  copy_file "${backend_env}" "${backend_runtime_env}"
}

write_lxc_override() {
  log "Writing LXC-specific compose override"
  cat >"${APP_DIR}/docker-compose.lxc.yml" <<EOF
services:
  backend:
    ports: !override
      - "${API_PORT}:8080"

  frontend:
    ports: !override
      - "${FRONTEND_PORT}:80"
EOF
}

write_update_helper() {
  log "Installing update helper"
  cat >/usr/local/bin/pymc-glass-refresh-profile <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

cat >/etc/profile.d/00_lxc-details.sh <<'INNER'
echo ""
echo "pyMC-Glass LXC Container"
echo "  GitHub: https://github.com/pyMC-dev/pyMC-Glass"
echo ""
echo "  OS: $(grep ^NAME /etc/os-release | cut -d= -f2 | tr -d '"') - Version: $(grep ^VERSION_ID /etc/os-release | cut -d= -f2 | tr -d '"')"
echo "  Hostname: $(hostname)"
echo "  IP Address: $(hostname -I | awk '{print $1}')"
INNER
chmod 0644 /etc/profile.d/00_lxc-details.sh

cat >/etc/motd <<'INNER'
pyMC-Glass LXC Container
  GitHub: https://github.com/pyMC-dev/pyMC-Glass
INNER
chmod 0644 /etc/motd
EOF
  chmod 0755 /usr/local/bin/pymc-glass-refresh-profile

  cat >/usr/local/bin/pymc-glass-update <<EOF
#!/usr/bin/env bash
set -euo pipefail

cd "${APP_DIR}"
git fetch --depth 1 origin "${APP_REPO_BRANCH}"
git checkout -f "${APP_REPO_BRANCH}"
git reset --hard "origin/${APP_REPO_BRANCH}"
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.lxc.yml up -d --build
/usr/local/bin/pymc-glass-refresh-profile
EOF
  chmod 0755 /usr/local/bin/pymc-glass-update

  cat >/usr/local/bin/update <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exec /usr/local/bin/pymc-glass-update "$@"
EOF
  chmod 0755 /usr/local/bin/update

  cat >/usr/bin/update <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exec /usr/local/bin/pymc-glass-update "$@"
EOF
  chmod 0755 /usr/bin/update
}

deploy_stack() {
  log "Building and starting the production stack"
  (
    cd "${APP_DIR}"
    COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME}" \
      docker compose \
      --env-file .env.production \
      -f docker-compose.yml \
      -f docker-compose.prod.yml \
      -f docker-compose.lxc.yml \
      up -d --build
  )
}

wait_for_health() {
  log "Waiting for the web UI to become available"
  for _ in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:${FRONTEND_PORT}/healthz" >/dev/null 2>&1; then
      return
    fi
    sleep 5
  done

  fail "the stack did not become healthy in time; inspect with docker compose ps"
}

print_summary() {
  local ip
  ip="$(hostname -I | awk '{print $1}')"

  cat >&3 <<EOF

pyMC_Glass is installed.

URL: http://${ip}:${FRONTEND_PORT}
API: http://${ip}:${API_PORT}/healthz
Admin email: ${ADMIN_EMAIL}
Admin password: ${ADMIN_PASSWORD}

Useful commands:
  cd ${APP_DIR}
  docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.lxc.yml ps
  update
  pymc-glass-update
EOF
}

main() {
  log "Starting in-container installer"
  require_root
  detect_os
  install_base_packages
  install_docker
  ensure_command git
  ensure_command docker
  checkout_repo
  prepare_env_files
  write_lxc_override
  write_update_helper
  deploy_stack
  wait_for_health
  write_custom_profile
  print_summary
}

main "$@"
