# Hermes teammate browser connector

This package connects a teammate's headed Chrome or Brave browser to Hermes through an outbound SSH reverse tunnel. The browser and the VPS listener both bind only to loopback. No SSH key or SSH host key is included in this package.

## Security properties

- Chrome or Brave exposes CDP only on `127.0.0.1` and uses a dedicated profile when the connector launches it.
- Attach-existing mode never launches or changes the managed browser. Rico uses this mode for the approved local lane at `127.0.0.1:9227`.
- SSH requests the remote listener as `127.0.0.1:<remote-port>`. It never requests `0.0.0.0`.
- SSH uses `BatchMode=yes`, `ExitOnForwardFailure=yes`, `ServerAliveInterval=30`, `ServerAliveCountMax=3`, `ConnectTimeout=10`, `ConnectionAttempts=1`, `RequestTTY=no`, `-N`, and `-T`.
- Strict host verification is mandatory. Installers reject a missing or empty private key or known-hosts file. They always pass `StrictHostKeyChecking=yes` and the supplied `UserKnownHostsFile`.
- The SSH key should be restricted server-side to one exact loopback listener.
- Installers and generated files are per-user. Administrator access is not normally required on Windows or macOS.
- Uninstallers preserve browser profiles, private keys, known-hosts files, and, by default, logs.

CDP grants full control over the browser profile. Loopback TCP is not isolated by desktop account, so other local users and processes may be able to reach the local CDP port. Any process with access to the trusted VPS account can reach the VPS loopback listener. Use only trusted, single-user workstations or add OS-level filtering. Never expose either listener publicly.

## Package contents

| File | Purpose |
| --- | --- |
| `install-windows.ps1` | Installs a generated wrapper and current-user interactive Scheduled Task |
| `uninstall-windows.ps1` | Removes only that task and wrapper unless extra removal switches are supplied |
| `install-macos.sh` | Installs a generated wrapper and per-user LaunchAgent |
| `uninstall-macos.sh` | Removes only that LaunchAgent and wrapper unless extra removal options are supplied |
| `verify_connector.py` | Checks `/json/version` and performs a WebSocket Upgrade handshake without printing endpoint tokens |

## Fixed enrollment plan

| Teammate | Discord user ID | VPS listener | Expected device setup |
| --- | --- | --- | --- |
| Rico | `1378208835302592534` | `127.0.0.1:9241` | Windows attach-existing to `127.0.0.1:9227` |
| Jakob | `1512179714947678422` | `127.0.0.1:9242` | Windows or macOS, configurable local port and dedicated profile |

Only one Jakob device may own remote port `9242` at a time. Both installers can be present, but autostart must be enabled only on the preferred device. `ExitOnForwardFailure=yes` causes the second device to exit and log the port conflict instead of silently running without a tunnel.

## 1. Prepare the VPS SSH account

Use a dedicated account that cannot authenticate by password. The exact account-management command depends on the VPS distribution. Keep the account's `~/.ssh` directory owned by the dedicated account and set modes `0700` on the directory and `0600` on `authorized_keys`.

Recommended `sshd_config` policy:

```text
Match User hermes-browser
    AuthenticationMethods publickey
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AllowTcpForwarding remote
    GatewayPorts no
    PermitTTY no
    X11Forwarding no
    MaxSessions 0
```

Validate and reload SSH after editing it:

```bash
sudo sshd -t
sudo systemctl reload ssh
```

`GatewayPorts no` is a second server-side guard against public listeners. The client still explicitly requests a loopback listener. `MaxSessions 0` blocks shell, login, and subsystem sessions while still allowing forwarding, so a connector key cannot be used to run commands on the VPS.

### Restrict each public key

Use a different client key per teammate or device. Put the options and public key on one physical line. Rico's exact restriction is:

```text
restrict,port-forwarding,permitlisten="127.0.0.1:9241" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA... rico-browser-connector
```

Jakob's exact restriction is:

```text
restrict,port-forwarding,permitlisten="127.0.0.1:9242" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA... jakob-browser-connector
```

`restrict` disables forwarding by default, `port-forwarding` enables forwarding for this key, and `permitlisten` limits remote forwarding to the one loopback socket. The client requests no command and no TTY. Keep `AllowTcpForwarding remote` and `GatewayPorts no` in the server policy.

If both Jakob devices receive separate keys, each key may use the same `9242` restriction. Do not enable both connectors at once.

## 2. Provision keys and trusted host keys

Generate a dedicated key on each teammate device. Do not copy a general-purpose login key into this connector.

```bash
ssh-keygen -t ed25519 -f "$HOME/.ssh/hermes-browser" -C "hermes-browser-connector"
chmod 600 "$HOME/.ssh/hermes-browser"
```

On Windows, `ssh-keygen.exe` from the OpenSSH Client capability can create the key. Restrict the private key ACL to the current user according to your organization's Windows key-management policy.

Create a connector-specific known-hosts file from a host key obtained through a trusted channel. If `ssh-keyscan` is used to collect the public key, compare its `ssh-keygen -lf` fingerprint out of band with the VPS administrator before trusting it. An unauthenticated key scan alone does not prove server identity.

Example paths:

- Windows: `%USERPROFILE%\.ssh\hermes-browser` and `%USERPROFILE%\.ssh\hermes-browser-known-hosts`
- macOS: `~/.ssh/hermes-browser` and `~/.ssh/hermes-browser-known-hosts`

The installers never add a host key and never allow `StrictHostKeyChecking=no`, `accept-new`, or a null known-hosts file.

## 3. Install on Windows

Run PowerShell as the teammate, not as Administrator. The task uses `Interactive` logon and `Limited` run level so the headed browser remains visible in that user's desktop session.

### Rico: attach to the approved local GHL Team Browser lane

First confirm the managed browser answers locally:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:9227/json/version | Select-Object StatusCode
```

Install and start the connector:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install-windows.ps1 `
  -VpsHost browser-vps.example.com `
  -RemotePort 9241 `
  -LocalPort 9227 `
  -SshKeyPath "$env:USERPROFILE\.ssh\hermes-browser" `
  -KnownHostsPath "$env:USERPROFILE\.ssh\hermes-browser-known-hosts" `
  -AttachExisting `
  -StartNow
```

`-VpsUser` defaults to `hermes-browser`. Attach-existing mode ignores browser and profile arguments. It waits for local `/json/version`, confirms the listener is loopback-only, then invokes SSH.

### Jakob: launch a dedicated Windows browser

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install-windows.ps1 `
  -VpsHost browser-vps.example.com `
  -RemotePort 9242 `
  -LocalPort 9222 `
  -SshKeyPath "$env:USERPROFILE\.ssh\hermes-browser" `
  -KnownHostsPath "$env:USERPROFILE\.ssh\hermes-browser-known-hosts" `
  -StartNow
```

The installer detects Chrome or Brave. `-BrowserPath` can select an executable explicitly, and `-ProfileDir` can select a dedicated profile. The default profile is `%LOCALAPPDATA%\HermesBrowserConnector\profile`. Dedicated mode refuses to reuse a port that already serves CDP. Use `-AttachExisting` only when attaching to an approved existing lane.

On a secondary Jakob Windows device, add `-DisableAutostart` and omit `-StartNow`. Rerun the installer without `-DisableAutostart` when making that device preferred.

Generated Windows files:

```text
%LOCALAPPDATA%\HermesBrowserConnector\start-connector.ps1
%LOCALAPPDATA%\HermesBrowserConnector\connector.log
%LOCALAPPDATA%\HermesBrowserConnector\ssh.log
Scheduled Task: HermesBrowserConnector
```

A `-StartNow` installation waits up to 45 seconds for OpenSSH to confirm the remote forward. Inspect `connector.log` and `ssh.log` for strict host-key failures, authentication failures, local health failures, and duplicate remote listener failures.

## 4. Install on macOS

Run as the logged-in GUI user. Do not use `sudo`. The LaunchAgent runs in the Aqua session, and the wrapper uses `open -na` so Chrome or Brave is headed and visible.

```bash
chmod 700 install-macos.sh uninstall-macos.sh
./install-macos.sh \
  --vps-host browser-vps.example.com \
  --remote-port 9242 \
  --local-port 9222 \
  --ssh-key-path "$HOME/.ssh/hermes-browser" \
  --known-hosts-path "$HOME/.ssh/hermes-browser-known-hosts" \
  --start-now
```

`--vps-user` defaults to `hermes-browser`. The installer detects `/Applications/Google Chrome.app` or `/Applications/Brave Browser.app`. Use `--browser-path` for another app bundle and `--profile-dir` for another dedicated profile. The default profile is:

```text
~/Library/Application Support/HermesBrowserConnector/profile
```

For a browser already managed on a local CDP port, pass `--attach-existing`. Dedicated mode refuses to reuse a port that already serves CDP. For a secondary Jakob Mac, add `--disable-autostart` and omit `--start-now`. Rerun without `--disable-autostart` when making that Mac preferred.

Generated macOS files:

```text
~/Library/Application Support/HermesBrowserConnector/start-connector.sh
~/Library/LaunchAgents/com.nousresearch.hermes-browser-connector.plist
~/Library/Logs/HermesBrowserConnector/
```

When `--start-now` is requested, the installer uses:

```bash
launchctl bootstrap "gui/$UID" "$HOME/Library/LaunchAgents/com.nousresearch.hermes-browser-connector.plist"
launchctl kickstart -k "gui/$UID/com.nousresearch.hermes-browser-connector"
```

The installer unloads an older instance before replacing it. The generated wrapper validates that local `/json/version` contains a CDP WebSocket URL and confirms the listener is loopback-only before starting SSH. A `--start-now` installation waits up to 45 seconds for OpenSSH to confirm the remote forward.

## 5. Enroll and verify each endpoint before adding mappings

Follow this order for each teammate:

1. Install the server-side public key restriction.
2. Install the device connector with the exact local and remote ports.
3. Confirm the client-side CDP listener uses loopback and that `/json/version` succeeds.
4. Start the connector.
5. Confirm the VPS SSH listener is exactly `127.0.0.1:9241` or `127.0.0.1:9242`, never `0.0.0.0` or `[::]`.
6. Run the verifier on the VPS.
7. Only after the endpoint passes, add the environment variables and Hermes mappings.
8. Restart the Hermes gateway and test from the matching Discord account.

VPS listener checks:

```bash
sudo ss -ltnp | grep -E '127\.0\.0\.1:(9241|9242)'
```

Treat any `0.0.0.0:9241`, `0.0.0.0:9242`, `[::]:9241`, or `[::]:9242` result as a deployment failure. Stop the connector and correct SSH server policy before continuing.

Run the token-safe verifier from this directory on the VPS:

```bash
python3 verify_connector.py \
  http://127.0.0.1:9241 \
  http://127.0.0.1:9242
```

The verifier fetches `/json/version`, reads `webSocketDebuggerUrl`, replaces Chrome's client-side authority with the endpoint under test, and performs a real WebSocket Upgrade handshake. It prints only an endpoint index and pass or token-safe failure text. It never prints endpoint URLs, WebSocket paths, or CDP tokens.

## 6. Configure Hermes routing

Set these in the Hermes gateway service environment:

```bash
RICO_BROWSER_CDP_URL=http://127.0.0.1:9241
JAKOB_BROWSER_CDP_URL=http://127.0.0.1:9242
```

Add the named endpoints and immutable Discord IDs to `~/.hermes/config.yaml`:

```yaml
browser:
  cdp_endpoints:
    rico_windows:
      url: ${RICO_BROWSER_CDP_URL}
    jakob_preferred:
      url: ${JAKOB_BROWSER_CDP_URL}
  cdp_routes:
    discord:
      "1378208835302592534": rico_windows
      "1512179714947678422": jakob_preferred
```

Restart the gateway using its normal service manager so it receives both environment variables. Test a harmless browser operation from Rico's Discord account and then from Jakob's Discord account. Confirm each operation affects only the assigned profile.

**Important fail-closed warning:** enabling either Discord mapping before its endpoint is enrolled and healthy intentionally blocks that teammate's browser calls with `browser_route_unavailable`. It does not fall back to the Mac Mini, the global CDP endpoint, Camofox, a cloud browser, or another teammate's profile.

## Routine health checks

Client-side checks:

```powershell
# Windows
Get-ScheduledTask -TaskName HermesBrowserConnector
Get-ScheduledTaskInfo -TaskName HermesBrowserConnector
Get-NetTCPConnection -LocalPort 9227 -State Listen | Select-Object LocalAddress,LocalPort
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:9227/json/version | Select-Object StatusCode
```

```bash
# macOS
launchctl print "gui/$UID/com.nousresearch.hermes-browser-connector"
/usr/sbin/lsof -nP -a -iTCP:9222 -sTCP:LISTEN
curl --fail --silent --output /dev/null http://127.0.0.1:9222/json/version
```

Every client-side listener row must show `127.0.0.1` or `::1`. Treat wildcard or non-loopback addresses as a deployment failure.

Server-side checks:

```bash
sudo ss -ltnp | grep -E '127\.0\.0\.1:(9241|9242)'
python3 verify_connector.py http://127.0.0.1:9241
python3 verify_connector.py http://127.0.0.1:9242
```

If Jakob changes preferred devices, stop or disable the old connector first. Verify that port `9242` disappears, then start the new preferred connector and run the verifier again.

## Uninstall and rollback

### Windows uninstall

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\uninstall-windows.ps1
```

To explicitly delete the default profile too:

```powershell
.\uninstall-windows.ps1 -RemoveProfile
```

For a custom profile, use `-RemoveProfile -ProfileDir 'C:\exact\profile\path'`. Add `-RemoveLogs` only if connector logs should also be deleted.

### macOS uninstall

```bash
./uninstall-macos.sh
```

To explicitly delete a profile and logs:

```bash
./uninstall-macos.sh \
  --remove-profile "$HOME/Library/Application Support/HermesBrowserConnector/profile" \
  --remove-logs
```

Neither uninstaller removes SSH private keys or known-hosts files. Profile deletion requires the ownership sentinel created by the installer and is refused for arbitrary directories. Uninstalling the tunnel does not close a connector-launched browser. Close that dedicated browser to remove its local CDP listener before deleting its profile.

### Server rollback order

1. Remove the affected Discord ID from `browser.cdp_routes` and restart Hermes. This removes the mandatory mapped route. Decide whether restoring the global Mac Mini behavior is acceptable before doing this.
2. Stop and uninstall the client connector.
3. Confirm the VPS loopback listener has disappeared.
4. Remove that device's public key line from `authorized_keys`.
5. Remove its named `cdp_endpoints` entry and service environment variable if no longer needed.
6. Run `ss` and the verifier again for all remaining enrolled endpoints.

If isolation must remain fail closed during rollback, leave the mapping in place while the endpoint is offline. Browser calls for that Discord ID will remain blocked rather than using the Mac Mini.
