#!/usr/bin/env bash
set -euo pipefail
set +x
umask 027

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "run this installer as root" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
RELEASE_REF="${1:-HEAD}"
GIT_DIR="$REPO_ROOT/.git"
BACKUP_ROOT="/var/backups/cjs-whiteout/$(date -u +%Y%m%dT%H%M%SZ)"
SERVICE_DIR="$REPO_ROOT/deployments/cjs_whiteout/systemd"
CONFIG_SOURCE="$REPO_ROOT/deployments/cjs_whiteout/config/mason-config.example.yaml"
SOUL_SOURCE="$REPO_ROOT/deployments/cjs_whiteout/SOUL.md"
BIN_SOURCE="$REPO_ROOT/deployments/cjs_whiteout/bin"
SKILLS_SOURCE="$REPO_ROOT/deployments/cjs_whiteout/skills"
HOOKS_SOURCE="$REPO_ROOT/deployments/cjs_whiteout/hooks"

if ! git -C "$REPO_ROOT" cat-file -e "$RELEASE_REF^{commit}"; then
  echo "release ref is not a commit: $RELEASE_REF" >&2
  exit 1
fi

# The release is built from Git, not the working tree. Relevant uncommitted
# changes are rejected while unrelated in-progress work remains untouched.
if ! git -C "$REPO_ROOT" diff --quiet "$RELEASE_REF" -- \
  deployments/cjs_whiteout tools/mcp_tool.py; then
  echo "commit the CJS SynkedUP release files before installing" >&2
  exit 1
fi

RELEASE_ID="$(git -C "$REPO_ROOT" rev-parse --short=12 "$RELEASE_REF")"
RELEASE_DIR="/opt/cjs-whiteout/releases/$RELEASE_ID"

backup_path() {
  local path="$1"
  if [[ -e "$path" || -L "$path" ]]; then
    mkdir -p "$BACKUP_ROOT$(dirname "$path")"
    cp -a "$path" "$BACKUP_ROOT$path"
  fi
}

getent group cjs-synkedup >/dev/null || groupadd --system cjs-synkedup
if ! id cjs-synkedup >/dev/null 2>&1; then
  useradd --system --gid cjs-synkedup --home-dir /var/lib/cjs-synkedup \
    --create-home --shell /usr/sbin/nologin cjs-synkedup
fi
usermod -a -G cjs-synkedup nick

install -d -m 0755 -o root -g root /opt/cjs-whiteout /opt/cjs-whiteout/releases /opt/cjs-whiteout/bin
install -d -m 0750 -o cjs-synkedup -g cjs-synkedup /var/lib/cjs-synkedup /var/lib/cjs-synkedup/chrome /var/lib/cjs-synkedup/cache
install -d -m 0770 -o cjs-synkedup -g cjs-synkedup /var/log/cjs-synkedup
install -d -m 0750 -o nick -g cjs-synkedup /var/lib/cjs-whiteout /var/lib/cjs-whiteout/hermes
install -d -m 0750 -o nick -g cjs-synkedup /var/lib/cjs-whiteout/hermes/state /var/lib/cjs-whiteout/hermes/logs
install -d -m 0750 -o nick -g cjs-synkedup /var/lib/cjs-whiteout/hermes/skills /var/lib/cjs-whiteout/hermes/hooks

if [[ ! -d "$RELEASE_DIR" ]]; then
  install -d -m 0755 -o root -g root "$RELEASE_DIR"
  git -C "$REPO_ROOT" archive "$RELEASE_REF" | tar -x -C "$RELEASE_DIR"
fi
backup_path /opt/cjs-whiteout/current
ln -sfn "$RELEASE_DIR" /opt/cjs-whiteout/current

if [[ ! -x /opt/cjs-whiteout/venv/bin/python ]]; then
  /usr/bin/python3 -m venv /opt/cjs-whiteout/venv
fi
/opt/cjs-whiteout/venv/bin/python -m pip install --disable-pip-version-check --quiet \
  -r /opt/cjs-whiteout/current/deployments/cjs_whiteout/requirements-synkedup.txt
chgrp -R cjs-synkedup /opt/cjs-whiteout/venv
chmod -R g+rX /opt/cjs-whiteout/venv

for name in wait-for-local-port cjs-synkedup-status run-mason-gateway; do
  backup_path "/opt/cjs-whiteout/bin/$name"
  install -m 0750 -o root -g cjs-synkedup "$BIN_SOURCE/$name" "/opt/cjs-whiteout/bin/$name"
done

for unit in \
  cjs-synkedup-display.service \
  cjs-synkedup-browser.service \
  cjs-synkedup-vnc.service \
  cjs-synkedup-mcp.service \
  cjs-mason-gateway.service; do
  backup_path "/etc/systemd/system/$unit"
  install -m 0644 -o root -g root "$SERVICE_DIR/$unit" "/etc/systemd/system/$unit"
done

if [[ ! -f /var/lib/cjs-whiteout/hermes/config.yaml ]]; then
  install -m 0640 -o nick -g cjs-synkedup "$CONFIG_SOURCE" /var/lib/cjs-whiteout/hermes/config.yaml
else
  backup_path /var/lib/cjs-whiteout/hermes/config.yaml
fi
backup_path /var/lib/cjs-whiteout/hermes/SOUL.md
install -m 0640 -o nick -g cjs-synkedup "$SOUL_SOURCE" /var/lib/cjs-whiteout/hermes/SOUL.md
backup_path /var/lib/cjs-whiteout/hermes/skills
backup_path /var/lib/cjs-whiteout/hermes/hooks
rm -rf /var/lib/cjs-whiteout/hermes/skills /var/lib/cjs-whiteout/hermes/hooks
install -d -m 0750 -o nick -g cjs-synkedup /var/lib/cjs-whiteout/hermes/skills /var/lib/cjs-whiteout/hermes/hooks
cp -a "$SKILLS_SOURCE/." /var/lib/cjs-whiteout/hermes/skills/
cp -a "$HOOKS_SOURCE/." /var/lib/cjs-whiteout/hermes/hooks/
chown -R nick:cjs-synkedup /var/lib/cjs-whiteout/hermes/skills /var/lib/cjs-whiteout/hermes/hooks
find /var/lib/cjs-whiteout/hermes/skills /var/lib/cjs-whiteout/hermes/hooks -type d -exec chmod 0750 {} +
find /var/lib/cjs-whiteout/hermes/skills /var/lib/cjs-whiteout/hermes/hooks -type f -exec chmod 0640 {} +
HERMES_HOME=/var/lib/cjs-whiteout/hermes /home/nick/.hermes/hermes-agent/venv/bin/python - <<'PY'
from pathlib import Path

import yaml

from tools.skills_tool import _find_all_skills

allowed = {
    "daily-operations-briefing",
    "cjs-job-lookup",
    "whiteout-account-lookup",
    "job-cost-project-review",
    "schedule-crew-planning",
    "hit-lists-reminders",
    "procurement-invoice-review",
    "project-changes-closeout",
    "snow-material-contract-operations",
    "workforce-directory-rewards",
}
config_path = Path("/var/lib/cjs-whiteout/hermes/config.yaml")
config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
discovered = {skill["name"] for skill in _find_all_skills(skip_disabled=True)}
missing = allowed - discovered
if missing:
    raise SystemExit(f"missing reviewed Mason skills: {sorted(missing)}")
config.setdefault("skills", {})["disabled"] = sorted(discovered - allowed)
composio_tools = (
    config.setdefault("mcp_servers", {})
    .setdefault("cjs-composio", {})
    .setdefault("tools", {})
    .setdefault("toolsets", {})
    .setdefault("composio-approved", [])
)
if "composio_read_outlook_email" not in composio_tools:
    composio_tools.insert(
        composio_tools.index("composio_read_drive_pdf"),
        "composio_read_outlook_email",
    )
if "composio_read_drive_text_document" not in composio_tools:
    composio_tools.insert(
        composio_tools.index("composio_read_drive_spreadsheet"),
        "composio_read_drive_text_document",
    )
config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
PY
chown nick:cjs-synkedup /var/lib/cjs-whiteout/hermes/config.yaml
chmod 0640 /var/lib/cjs-whiteout/hermes/config.yaml

systemctl daemon-reload
systemctl enable --now cjs-synkedup-display.service
systemctl enable --now cjs-synkedup-browser.service
systemctl enable --now cjs-synkedup-mcp.service
systemctl restart cjs-synkedup-mcp.service
systemctl stop cjs-synkedup-vnc.service cjs-mason-gateway.service 2>/dev/null || true
systemctl disable cjs-mason-gateway.service 2>/dev/null || true

systemctl is-active --quiet cjs-synkedup-display.service
systemctl is-active --quiet cjs-synkedup-browser.service
systemctl is-active --quiet cjs-synkedup-mcp.service
/opt/cjs-whiteout/bin/cjs-synkedup-status

printf 'Installed CJS SynkedUP release %s\n' "$RELEASE_ID"
printf 'Mason gateway remains disabled until the authorized SynkedUP login is complete.\n'
