# pyMC_Glass
Simple management stack for pyMC repeaters (API + DB + MQTT + frontend).

## First setup (local)
1. One-command setup/start:
   - `make easy-start`
2. (Manual equivalent) Copy local env files:
   - `make init-env`
3. Start services:
   - `make dev-up`
4. Open the apps:
   - frontend: `http://localhost:5173`
   - backend health: `http://localhost:8080/healthz`
5. Login with the seeded default admin:
   - email: `admin@pymc.glass`
   - password: `admin12345678`
6. Stop services:
   - `make dev-down`

## Default login
- Email: `admin@pymc.glass`
- Password: `admin12345678`
- The account is auto-created on backend startup when no users exist.

## Change default credentials
- Edit `backend/.env` (or `backend/.env.production`) and set:
  - `BOOTSTRAP_SEED_ADMIN_EMAIL`
  - `BOOTSTRAP_SEED_ADMIN_PASSWORD`
- Seeding only runs when the users table is empty.


## Production (Docker Compose)
1. Initialize production env files:
   - `make init-prod-env`
2. Set secrets/config:
   - `.env.production` (compose-level secrets like `POSTGRES_PASSWORD`)
   - `backend/.env.production` (backend runtime config)
3. Start production stack:
   - `make prod-up`
4. Open the app:
   - frontend: `http://localhost:5173`
   - frontend is served by nginx using a built Vite `dist/` bundle (not `npm run dev`)
5. Login with the seeded admin in `backend/.env.production`.
6. View logs:
   - `make prod-logs`
7. Stop production stack:
   - `make prod-down`

## Proxmox LXC helper
Run this on the Proxmox host to create an Ubuntu 24.04 LXC and deploy `pyMC_Glass` inside it:

`bash -c "$(curl -fsSL https://raw.githubusercontent.com/yellowcooln/pyMC-Glass/dev/scripts/proxmox/pymc-glass-lxc.sh)"`

The host-side script now uses the same `community-scripts` Proxmox helper flow for:
- template storage selection
- container storage selection
- full advanced install prompts for bridge/network mode, DHCP vs static IP, gateway, VLAN, MTU, DNS, and related LXC settings
- Docker-safe LXC settings such as `nesting` and `keyctl`

Common overrides:
- `CTID=123 HN=pymc-glass bash -c "$(curl -fsSL https://raw.githubusercontent.com/yellowcooln/pyMC-Glass/dev/scripts/proxmox/pymc-glass-lxc.sh)"`
- `APP_REPO_BRANCH=main FRONTEND_PORT=8081 API_PORT=8080 bash -c "$(curl -fsSL https://raw.githubusercontent.com/yellowcooln/pyMC-Glass/dev/scripts/proxmox/pymc-glass-lxc.sh)"`
- `mode=default bash -c "$(curl -fsSL https://raw.githubusercontent.com/yellowcooln/pyMC-Glass/dev/scripts/proxmox/pymc-glass-lxc.sh)"` if you want the shorter helper flow instead

Defaults:
- Hostname `pymc-glass`
- Ubuntu `24.04` template
- Unprivileged LXC with `nesting=1,keyctl=1`
- TUN/TAP enabled by default for Tailscale/VPN use
- `2` cores, `4096` MB RAM, `16` GB disk
- Frontend on port `80`, backend health/API on port `8080`
- Script source from `yellowcooln/pyMC-Glass` on `dev`
- App deployment source from `pyMC-dev/pyMC-Glass` on `main`

The host-side helper first provisions the container through the official community-scripts Docker install flow, then runs [`scripts/proxmox/install-pymc-glass-lxc.sh`](scripts/proxmox/install-pymc-glass-lxc.sh) inside the container. That second stage clones `pyMC-Glass`, sets production env files, builds the stack, and creates a `pymc-glass-update` helper inside the LXC.
