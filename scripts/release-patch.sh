#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION_FILE="$ROOT_DIR/VERSION"
CHANGELOG_FILE="$ROOT_DIR/CHANGELOG.md"

DRY_RUN=false
NOTE_PARTS=()
for arg in "$@"; do
  if [[ "$arg" == "--dry-run" ]]; then
    DRY_RUN=true
  else
    NOTE_PARTS+=("$arg")
  fi
done
NOTE="${NOTE_PARTS[*]:-}"

read_version_from_pyproject() {
  python3 - "$ROOT_DIR/backend/pyproject.toml" <<'PY'
import re
import sys
from pathlib import Path

pyproject_path = Path(sys.argv[1])
text = pyproject_path.read_text(encoding="utf-8")
match = re.search(r'(?m)^version\s*=\s*"(\d+\.\d+\.\d+)"\s*$', text)
if not match:
    raise SystemExit("Could not find version in backend/pyproject.toml")
print(match.group(1))
PY
}

if [[ -f "$VERSION_FILE" ]]; then
  CURRENT_VERSION="$(tr -d '[:space:]' < "$VERSION_FILE")"
else
  CURRENT_VERSION="$(read_version_from_pyproject)"
fi

if [[ ! "$CURRENT_VERSION" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
  echo "Unsupported version format: $CURRENT_VERSION (expected x.y.z)" >&2
  exit 1
fi

MAJOR="${BASH_REMATCH[1]}"
MINOR="${BASH_REMATCH[2]}"
PATCH="${BASH_REMATCH[3]}"
NEW_VERSION="${MAJOR}.${MINOR}.$((PATCH + 1))"
TODAY="$(date +%Y-%m-%d)"

if $DRY_RUN; then
  echo "Dry run:"
  echo "  current version: $CURRENT_VERSION"
  echo "  new version:     $NEW_VERSION"
  echo "  note:            ${NOTE:-<none>}"
  exit 0
fi

printf "%s\n" "$NEW_VERSION" > "$VERSION_FILE"

python3 - "$ROOT_DIR" "$NEW_VERSION" <<'PY'
import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
version = sys.argv[2]

pyproject_path = root / "backend/pyproject.toml"
pyproject_text = pyproject_path.read_text(encoding="utf-8")
pyproject_updated, pyproject_count = re.subn(
    r'(?m)^version\s*=\s*"\d+\.\d+\.\d+"\s*$',
    f'version = "{version}"',
    pyproject_text,
    count=1,
)
if pyproject_count != 1:
    raise SystemExit("Failed to update backend/pyproject.toml version")
pyproject_path.write_text(pyproject_updated, encoding="utf-8")

main_path = root / "backend/app/main.py"
main_text = main_path.read_text(encoding="utf-8")
main_updated, main_count = re.subn(
    r'version\s*=\s*"\d+\.\d+\.\d+"',
    f'version="{version}"',
    main_text,
    count=1,
)
if main_count != 1:
    raise SystemExit("Failed to update backend/app/main.py FastAPI version")
main_path.write_text(main_updated, encoding="utf-8")

package_json_path = root / "frontend/package.json"
package_json = json.loads(package_json_path.read_text(encoding="utf-8"))
package_json["version"] = version
package_json_path.write_text(json.dumps(package_json, indent=2) + "\n", encoding="utf-8")

lock_path = root / "frontend/package-lock.json"
lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
lock_data["version"] = version
if isinstance(lock_data.get("packages"), dict) and isinstance(lock_data["packages"].get(""), dict):
    lock_data["packages"][""]["version"] = version
lock_path.write_text(json.dumps(lock_data, indent=2) + "\n", encoding="utf-8")
PY

CHANGELOG_UPDATED_WITH="fallback"
if command -v git-cliff >/dev/null 2>&1 && git -C "$ROOT_DIR" rev-parse --verify HEAD >/dev/null 2>&1; then
  if git-cliff \
    --repository "$ROOT_DIR" \
    --config "$ROOT_DIR/cliff.toml" \
    --unreleased \
    --tag "v$NEW_VERSION" \
    --prepend "$CHANGELOG_FILE"; then
    CHANGELOG_UPDATED_WITH="git-cliff"
  fi
fi

if [[ "$CHANGELOG_UPDATED_WITH" == "fallback" ]]; then
  python3 - "$CHANGELOG_FILE" "$NEW_VERSION" "$TODAY" "$NOTE" <<'PY'
import sys
from pathlib import Path

changelog_path = Path(sys.argv[1])
version = sys.argv[2]
date = sys.argv[3]
note = sys.argv[4].strip() or f"Version bumped to {version}"
release_heading = f"## [{version}] - {date}"
release_block = f"{release_heading}\n- {note}\n"

if not changelog_path.exists() or not changelog_path.read_text(encoding="utf-8").strip():
    changelog_path.write_text(
        "# Changelog\n"
        "All notable changes to this project are documented in this file.\n\n"
        "## [Unreleased]\n\n"
        f"{release_block}\n",
        encoding="utf-8",
    )
    raise SystemExit

content = changelog_path.read_text(encoding="utf-8")
if release_heading in content:
    raise SystemExit

marker = "## [Unreleased]"
if marker in content:
    insertion_index = content.find(marker) + len(marker)
    content = content[:insertion_index] + "\n\n" + release_block + content[insertion_index:]
else:
    content = content.rstrip() + "\n\n## [Unreleased]\n\n" + release_block

if not content.endswith("\n"):
    content += "\n"
changelog_path.write_text(content, encoding="utf-8")
PY
fi

echo "Bumped patch version: $CURRENT_VERSION -> $NEW_VERSION"
echo "Updated files:"
echo "  - VERSION"
echo "  - backend/pyproject.toml"
echo "  - backend/app/main.py"
echo "  - frontend/package.json"
echo "  - frontend/package-lock.json"
echo "  - CHANGELOG.md (${CHANGELOG_UPDATED_WITH})"
