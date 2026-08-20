#!/bin/bash
set -euo pipefail

usage() {
    cat <<'USAGE'
Usage:
  install-macos.sh \
    --vps-host HOST \
    [--vps-user USER] \
    --remote-port PORT \
    --local-port PORT \
    --ssh-key-path PATH \
    --known-hosts-path PATH \
    [--browser-path /path/to/Browser.app] \
    [--profile-dir PATH] \
    [--attach-existing] \
    [--start-now] \
    [--disable-autostart]

The default VPS user is hermes-browser. Autostart is enabled unless
--disable-autostart is supplied. Only one device should autostart a given
remote port.
USAGE
}

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

require_port() {
    local name=$1
    local value=$2
    [[ $value =~ ^[0-9]+$ ]] || die "$name must be an integer"
    (( value >= 1 && value <= 65535 )) || die "$name must be between 1 and 65535"
}

canonical_file() {
    local path=$1
    local dir
    local base
    dir=$(dirname -- "$path")
    base=$(basename -- "$path")
    (cd -P -- "$dir" && printf '%s/%s\n' "$(pwd -P)" "$base")
}

xml_escape() {
    local input=$1
    local output=""
    local char
    while [[ -n $input ]]; do
        char=${input%"${input#?}"}
        input=${input#?}
        case $char in
            '&') output+="&amp;" ;;
            '<') output+="&lt;" ;;
            '>') output+="&gt;" ;;
            '"') output+="&quot;" ;;
            "'") output+="&apos;" ;;
            *) output+=$char ;;
        esac
    done
    printf '%s' "$output"
}

find_browser() {
    local candidate
    for candidate in \
        "/Applications/Google Chrome.app" \
        "/Applications/Brave Browser.app" \
        "$HOME/Applications/Google Chrome.app" \
        "$HOME/Applications/Brave Browser.app"; do
        if [[ -d $candidate ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

VPS_HOST=""
VPS_USER="hermes-browser"
REMOTE_PORT=""
LOCAL_PORT=""
SSH_KEY_PATH=""
KNOWN_HOSTS_PATH=""
BROWSER_PATH=""
PROFILE_DIR=""
ATTACH_EXISTING=false
START_NOW=false
AUTOSTART=true

while (( $# > 0 )); do
    case $1 in
        --vps-host) [[ $# -ge 2 ]] || die "$1 requires a value"; VPS_HOST=$2; shift 2 ;;
        --vps-user) [[ $# -ge 2 ]] || die "$1 requires a value"; VPS_USER=$2; shift 2 ;;
        --remote-port) [[ $# -ge 2 ]] || die "$1 requires a value"; REMOTE_PORT=$2; shift 2 ;;
        --local-port) [[ $# -ge 2 ]] || die "$1 requires a value"; LOCAL_PORT=$2; shift 2 ;;
        --ssh-key-path) [[ $# -ge 2 ]] || die "$1 requires a value"; SSH_KEY_PATH=$2; shift 2 ;;
        --known-hosts-path) [[ $# -ge 2 ]] || die "$1 requires a value"; KNOWN_HOSTS_PATH=$2; shift 2 ;;
        --browser-path) [[ $# -ge 2 ]] || die "$1 requires a value"; BROWSER_PATH=$2; shift 2 ;;
        --profile-dir) [[ $# -ge 2 ]] || die "$1 requires a value"; PROFILE_DIR=$2; shift 2 ;;
        --attach-existing) ATTACH_EXISTING=true; shift ;;
        --start-now) START_NOW=true; shift ;;
        --disable-autostart) AUTOSTART=false; shift ;;
        --help|-h) usage; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

[[ -n $VPS_HOST ]] || die "--vps-host is required"
[[ -n $REMOTE_PORT ]] || die "--remote-port is required"
[[ -n $LOCAL_PORT ]] || die "--local-port is required"
[[ -n $SSH_KEY_PATH ]] || die "--ssh-key-path is required"
[[ -n $KNOWN_HOSTS_PATH ]] || die "--known-hosts-path is required"
[[ $VPS_HOST =~ ^[A-Za-z0-9._:-]+$ ]] || die "--vps-host must be a hostname or IP address without an ssh:// prefix"
[[ $VPS_USER =~ ^[A-Za-z0-9._-]+$ ]] || die "--vps-user contains unsupported characters"
require_port "remote port" "$REMOTE_PORT"
require_port "local port" "$LOCAL_PORT"

[[ -f $SSH_KEY_PATH && -s $SSH_KEY_PATH ]] || die "SSH private key is missing, empty, or not a file"
[[ -f $KNOWN_HOSTS_PATH && -s $KNOWN_HOSTS_PATH ]] || die "strict known-hosts file is missing, empty, or not a file"
SSH_KEY_PATH=$(canonical_file "$SSH_KEY_PATH")
KNOWN_HOSTS_PATH=$(canonical_file "$KNOWN_HOSTS_PATH")

key_mode=$(/usr/bin/stat -f '%Lp' "$SSH_KEY_PATH")
key_mode_value=$((8#$key_mode))
if (( (key_mode_value & 8#077) != 0 )); then
    die "SSH private key permissions are too broad. Run: chmod 600 '$SSH_KEY_PATH'"
fi

SSH_PATH=$(command -v ssh) || die "OpenSSH client was not found"
CURL_PATH=$(command -v curl) || die "curl was not found"
OPEN_PATH=$(command -v open) || die "open was not found"
LSOF_PATH=/usr/sbin/lsof
[[ -x $LSOF_PATH ]] || die "lsof was not found at $LSOF_PATH"

INSTALL_DIR="$HOME/Library/Application Support/HermesBrowserConnector"
LOG_DIR="$HOME/Library/Logs/HermesBrowserConnector"
WRAPPER_PATH="$INSTALL_DIR/start-connector.sh"
SSH_LOG_PATH="$LOG_DIR/ssh.log"
PLIST_PATH="$HOME/Library/LaunchAgents/com.nousresearch.hermes-browser-connector.plist"
LABEL="com.nousresearch.hermes-browser-connector"
mkdir -p -- "$INSTALL_DIR" "$LOG_DIR" "$(dirname -- "$PLIST_PATH")"

if [[ $ATTACH_EXISTING == true ]]; then
    BROWSER_PATH=""
    PROFILE_DIR=""
else
    if [[ -z $BROWSER_PATH ]]; then
        BROWSER_PATH=$(find_browser) || die "Chrome or Brave was not found. Pass --browser-path or use --attach-existing"
    fi
    [[ -d $BROWSER_PATH ]] || die "browser app bundle does not exist: $BROWSER_PATH"
    BROWSER_PATH=$(cd -P -- "$(dirname -- "$BROWSER_PATH")" && printf '%s/%s\n' "$(pwd -P)" "$(basename -- "$BROWSER_PATH")")

    if [[ -z $PROFILE_DIR ]]; then
        PROFILE_DIR="$INSTALL_DIR/profile"
    fi
    profile_sentinel="$PROFILE_DIR/.hermes-browser-connector-profile"
    if [[ -d $PROFILE_DIR && ! -f $profile_sentinel ]]; then
        existing_item=$(/usr/bin/find "$PROFILE_DIR" -mindepth 1 -maxdepth 1 -print -quit)
        [[ -z $existing_item ]] || die "profile directory already contains data and is not owned by this connector: $PROFILE_DIR"
    fi
    mkdir -p -- "$PROFILE_DIR"
    printf '%s\n' 'Hermes browser connector profile' > "$profile_sentinel"
    chmod 600 "$profile_sentinel"
    PROFILE_DIR=$(cd -P -- "$PROFILE_DIR" && pwd -P)
fi

{
    cat <<'WRAPPER_HEADER'
#!/bin/bash
set -u
WRAPPER_HEADER
    printf 'VPS_HOST=%q\n' "$VPS_HOST"
    printf 'VPS_USER=%q\n' "$VPS_USER"
    printf 'REMOTE_PORT=%q\n' "$REMOTE_PORT"
    printf 'LOCAL_PORT=%q\n' "$LOCAL_PORT"
    printf 'SSH_KEY_PATH=%q\n' "$SSH_KEY_PATH"
    printf 'KNOWN_HOSTS_PATH=%q\n' "$KNOWN_HOSTS_PATH"
    printf 'SSH_PATH=%q\n' "$SSH_PATH"
    printf 'CURL_PATH=%q\n' "$CURL_PATH"
    printf 'OPEN_PATH=%q\n' "$OPEN_PATH"
    printf 'LSOF_PATH=%q\n' "$LSOF_PATH"
    printf 'SSH_LOG_PATH=%q\n' "$SSH_LOG_PATH"
    printf 'BROWSER_PATH=%q\n' "$BROWSER_PATH"
    printf 'PROFILE_DIR=%q\n' "$PROFILE_DIR"
    printf 'ATTACH_EXISTING=%q\n' "$ATTACH_EXISTING"
    cat <<'WRAPPER_BODY'

log() {
    printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" >&2
}

if [[ ! -f $SSH_KEY_PATH ]]; then
    log "Connector failed: SSH private key is missing."
    exit 1
fi
if [[ ! -f $KNOWN_HOSTS_PATH ]]; then
    log "Connector failed: strict known-hosts file is missing."
    exit 1
fi

read_websocket_url() {
    local version_payload
    local websocket_url
    version_payload=$(
        "$CURL_PATH" --fail --silent --show-error --noproxy '*' \
            --max-time 3 "http://127.0.0.1:$LOCAL_PORT/json/version" 2>/dev/null
    ) || version_payload=""
    websocket_url=$(
        printf '%s' "$version_payload" | \
            /usr/bin/plutil -extract webSocketDebuggerUrl raw -o - - 2>/dev/null
    ) || websocket_url=""
    printf '%s' "$websocket_url"
}

listener_is_loopback_only() {
    local listener
    local found=false
    while IFS= read -r listener; do
        [[ $listener == n* ]] || continue
        found=true
        case ${listener#n} in
            "127.0.0.1:$LOCAL_PORT"|"[::1]:$LOCAL_PORT") ;;
            *) return 1 ;;
        esac
    done < <("$LSOF_PATH" -nP -a -iTCP:"$LOCAL_PORT" -sTCP:LISTEN -F n 2>/dev/null)
    [[ $found == true ]]
}

existing_websocket_url=$(read_websocket_url)
if [[ $ATTACH_EXISTING != true && ( $existing_websocket_url == ws://* || $existing_websocket_url == wss://* ) ]]; then
    log "Connector failed: Dedicated connector port is already serving CDP. Use attach-existing explicitly or choose a free local port."
    exit 1
fi
if [[ $ATTACH_EXISTING != true ]]; then
    mkdir -p -- "$PROFILE_DIR"
    "$OPEN_PATH" -na "$BROWSER_PATH" --args \
        "--remote-debugging-address=127.0.0.1" \
        "--remote-debugging-port=$LOCAL_PORT" \
        "--user-data-dir=$PROFILE_DIR" \
        "--no-first-run" \
        "--no-default-browser-check"
fi

healthy=false
for ((attempt = 1; attempt <= 30; attempt++)); do
    websocket_url=$(read_websocket_url)
    if [[ $websocket_url == ws://* || $websocket_url == wss://* ]]; then
        healthy=true
        break
    fi
    sleep 1
done
if [[ $healthy != true ]]; then
    log "Connector failed: local CDP health check failed on loopback port $LOCAL_PORT. SSH was not started."
    exit 1
fi
if ! listener_is_loopback_only; then
    log "Connector failed: Local CDP listener is not loopback-only. SSH was not started."
    exit 1
fi

log "Local CDP health check passed. Starting reverse tunnel."
exec "$SSH_PATH" \
    -N \
    -T \
    -o BatchMode=yes \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -o ConnectTimeout=10 \
    -o ConnectionAttempts=1 \
    -o RequestTTY=no \
    -o StrictHostKeyChecking=yes \
    -o "UserKnownHostsFile=$KNOWN_HOSTS_PATH" \
    -o IdentitiesOnly=yes \
    -E "$SSH_LOG_PATH" \
    -v \
    -i "$SSH_KEY_PATH" \
    -R "127.0.0.1:$REMOTE_PORT:127.0.0.1:$LOCAL_PORT" \
    "$VPS_USER@$VPS_HOST"
WRAPPER_BODY
} > "$WRAPPER_PATH"
chmod 700 "$WRAPPER_PATH"

if [[ $AUTOSTART == true ]]; then
    run_at_load=true
    keep_alive=true
else
    run_at_load=false
    keep_alive=false
fi

wrapper_xml=$(xml_escape "$WRAPPER_PATH")
stdout_xml=$(xml_escape "$LOG_DIR/connector.stdout.log")
stderr_xml=$(xml_escape "$LOG_DIR/connector.stderr.log")
cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$wrapper_xml</string>
    </array>
    <key>LimitLoadToSessionType</key>
    <string>Aqua</string>
    <key>RunAtLoad</key>
    <$run_at_load/>
    <key>KeepAlive</key>
    <$keep_alive/>
    <key>ThrottleInterval</key>
    <integer>30</integer>
    <key>StandardOutPath</key>
    <string>$stdout_xml</string>
    <key>StandardErrorPath</key>
    <string>$stderr_xml</string>
</dict>
</plist>
PLIST
chmod 600 "$PLIST_PATH"
/usr/bin/plutil -lint "$PLIST_PATH" >/dev/null

if /bin/launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1; then
    /bin/launchctl bootout "gui/$UID/$LABEL"
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        if ! /bin/launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1; then
            break
        fi
        sleep 0.5
    done
    if /bin/launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1; then
        die "existing connector did not stop, so it was not replaced"
    fi
fi

if [[ $START_NOW == true ]]; then
    : > "$SSH_LOG_PATH"
    /bin/launchctl bootstrap "gui/$UID" "$PLIST_PATH"
    /bin/launchctl kickstart -k "gui/$UID/$LABEL"
    ready=false
    for ((attempt = 1; attempt <= 45; attempt++)); do
        if /usr/bin/grep -Fq 'remote forward success' "$SSH_LOG_PATH" 2>/dev/null; then
            ready=true
            break
        fi
        sleep 1
    done
    if [[ $ready != true ]]; then
        /bin/launchctl bootout "gui/$UID/$LABEL" >/dev/null 2>&1 || true
        die "connector did not prove the remote forward. Review $LOG_DIR/connector.stderr.log and $SSH_LOG_PATH"
    fi
fi

printf 'Installed LaunchAgent: %s\n' "$PLIST_PATH"
printf 'Generated wrapper: %s\n' "$WRAPPER_PATH"
printf 'Logs: %s\n' "$LOG_DIR"
if [[ $AUTOSTART == true ]]; then
    printf 'Autostart is enabled for remote loopback port %s.\n' "$REMOTE_PORT"
else
    printf 'Autostart is disabled. Enable only the preferred device for remote port %s.\n' "$REMOTE_PORT"
fi
