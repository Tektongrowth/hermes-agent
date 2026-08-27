# CJS SynkedUP Read-Only Operations

## Immediate state

The code and service layout are ready for one CJS-isolated SynkedUP connection. Live data cannot be verified until Alyssa or another authorized CJS account owner completes the initial SynkedUP login on the VPS browser lane.

Do not send passwords or MFA codes through Discord, email, TaskTracker, logs, or this repository.

## Architecture

- `cjs-synkedup-display.service`: isolated X display for the browser.
- `cjs-synkedup-browser.service`: persistent Chrome profile under `/var/lib/cjs-synkedup/chrome`.
- `cjs-synkedup-mcp.service`: read-only FastMCP service on `127.0.0.1:9342`.
- `cjs-synkedup-vnc.service`: temporary VNC listener on loopback for login and reauthentication. It is never enabled at boot.
- `cjs-mason-gateway.service`: CJS-only Hermes and Discord gateway. It remains disabled until SynkedUP authentication is complete.

Chrome DevTools listens only on `127.0.0.1:9341`. VNC listens only on `127.0.0.1:5909`. The MCP service is blocked from connecting anywhere except localhost. Only the browser service can connect to SynkedUP over the internet.

The MCP exposes 45 named read-only tools. The complete catalog and toolset assignment are in `config/mason-config.example.yaml`.

## Permission model

One CJS SynkedUP browser session contains the complete read-only catalog. Discord permission policy controls which tool schemas each user receives:

- `none`: no SynkedUP tools.
- `operations`: reference and operational reads.
- `sales`: reference and sales reads.
- `financial`: reference and financial reads.
- `admin-read`: reference, operations, sales, and financial reads.

Policy keys are immutable Discord user or role snowflake IDs. Names are not accepted as authority. The default grant is empty. Exact user mappings override role mappings. Conflicting role grants fail closed.

The gateway reloads the policy and rechecks the authenticated Discord user, CJS guild, allowed channel or parent channel, and toolset immediately before every tool execution. Removing a grant blocks the next tool call, including one inside an active conversation.

Alyssa and Nick have admin-read access to the complete read-only SynkedUP catalog, including reference, operations, sales, and financial reads. This does not grant browser control, write actions, Mason administration, credentials, or billing access.

Before changing permissions:

```bash
sudo cp -a /var/lib/cjs-whiteout/hermes/config.yaml \
  /var/backups/cjs-whiteout/mason-config-$(date -u +%Y%m%dT%H%M%SZ).yaml
sudoedit /var/lib/cjs-whiteout/hermes/config.yaml
sudo systemctl restart cjs-mason-gateway.service
```

## Install

The installer deploys a committed Git release. It refuses relevant uncommitted CJS or shared MCP changes and leaves unrelated Composio work untouched.

```bash
cd /home/nick/Projects/cjs-connector-portal
sudo deployments/cjs_whiteout/install-synkedup.sh <approved-commit>
```

The installer:

1. Creates the isolated `cjs-synkedup` Linux account and persistent directories.
2. Archives the approved commit into `/opt/cjs-whiteout/releases/<commit>`.
3. Installs a dedicated Python environment for the MCP.
4. Installs and starts the display, browser, and MCP services.
5. Copies the CJS Hermes profile only when one does not already exist.
6. Leaves the temporary VNC and Mason gateway stopped.
7. Verifies the display, browser, MCP, and loopback listeners.

## Authorized login handoff

Use an operator-controlled SSH tunnel. Do not expose VNC or Chrome DevTools on a public interface.

1. Start the temporary loopback VNC service:

```bash
sudo systemctl start cjs-synkedup-vnc.service
```

2. On Nick's approved workstation, open an SSH tunnel to the VPS:

```bash
ssh -N -L 5909:127.0.0.1:5909 nick@<tekton-vps-host>
```

3. Open `vnc://127.0.0.1:5909` in a VNC viewer.
4. During a live screen share, Alyssa enters the SynkedUP password and MFA directly into the VPS browser. Do not ask her to read a code into chat or save it anywhere.
5. Confirm the SynkedUP application has loaded, then stop VNC immediately:

```bash
sudo systemctl stop cjs-synkedup-vnc.service
```

6. Check the lane without returning page content:

```bash
sudo /opt/cjs-whiteout/bin/cjs-synkedup-status
```

7. After authentication and read-only smoke tests pass, start Mason:

```bash
sudo systemctl enable --now cjs-mason-gateway.service
```

Use the same handoff for session expiration or MFA reauthentication. Stop Mason first if the active session is no longer authenticated.

## Read-only enforcement

The connector does not expose arbitrary URLs, selectors, scripts, browser sessions, tenant IDs, account IDs, CDP endpoints, or secret paths. Every browser route and extraction script is defined by the server catalog.

The service blocks:

- Cross-origin navigation and redirects.
- Caller-selected browser or tenant state.
- Create, update, edit, save, delete, send, approve, schedule-change, charge, refund, void, submit, and similar mutation paths or controls.
- Dangerous form controls and write-oriented links in returned data.
- Financial fields in nonfinancial responses.
- Page content, cookies, credentials, tokens, raw queries, and CDP payloads in audit logs.

Browser reads are serialized and rate-limited. A failed or expired session returns `reauthentication_required` instead of login data.

## Health and verification

```bash
sudo systemctl is-active cjs-synkedup-display.service
sudo systemctl is-active cjs-synkedup-browser.service
sudo systemctl is-active cjs-synkedup-mcp.service
sudo /opt/cjs-whiteout/bin/cjs-synkedup-status
sudo ss -ltnp | grep -E ':(5909|9341|9342)\b'
```

Expected before login:

- Display, browser, and MCP services are active.
- Ports `9341` and `9342` listen only on `127.0.0.1`.
- Port `5909` is absent unless an authorized login session is in progress.
- Status may report `reauthentication_required` until login is complete.

After login, verify representative tools from each granted profile. Use known CJS records supplied by the account owner. Check that operations users cannot discover or execute financial tools. Do not use any control that writes to SynkedUP.

## Audit and troubleshooting

Audit events are written to:

```text
/var/log/cjs-synkedup/audit.jsonl
```

Each event contains the fixed CJS tenant tag, tool, sanitized scope, decision or outcome, timing, and error class. It does not contain returned business data or secrets.

Service logs:

```bash
sudo journalctl -u cjs-synkedup-browser.service -u cjs-synkedup-mcp.service --since today
```

Do not print service environments, cookies, command lines containing tokens, or decrypted SSM values while troubleshooting.
