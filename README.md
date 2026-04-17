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

## Version popup settings
- The UI shows a release popup once per version using a browser cookie.
- Set your support link in `frontend/.env`:
  - `VITE_BUYMEACOFFEE_URL=https://www.buymeacoffee.com/yourname`

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
5. Login with the seeded admin in `backend/.env.production`.
6. View logs:
   - `make prod-logs`
7. Stop production stack:
   - `make prod-down`

## Changelog (git-cliff)
1. Install `git-cliff`:
   - `brew install git-cliff`
2. Preview unreleased notes in terminal:
   - `make changelog-preview`
3. Generate/update `CHANGELOG.md`:
   - `make changelog`

## Maintainer patch command (version + changelog)
- Run patch bump + changelog update in one command:
  - `make patch NOTE="short summary of what changed"`
- Dry run preview:
  - `make patch-dry-run NOTE="short summary of what changed"`
- This updates:
  - `VERSION`
  - `backend/pyproject.toml`
  - `backend/app/main.py`
  - `frontend/package.json`
  - `frontend/package-lock.json`
  - `CHANGELOG.md`
  - `frontend/public/CHANGELOG.md`

