#!/bin/bash
set -euo pipefail

usage() {
    cat <<'USAGE'
Usage: uninstall-macos.sh [--remove-profile PATH] [--remove-logs]

By default, this removes only the connector LaunchAgent and generated wrapper.
It preserves browser profiles, logs, SSH keys, and known-hosts files.
USAGE
}

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

REMOVE_PROFILE=""
REMOVE_LOGS=false
while (( $# > 0 )); do
    case $1 in
        --remove-profile)
            [[ $# -ge 2 ]] || die "$1 requires the exact profile path"
            REMOVE_PROFILE=$2
            shift 2
            ;;
        --remove-logs)
            REMOVE_LOGS=true
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            die "unknown argument: $1"
            ;;
    esac
done

LABEL="com.nousresearch.hermes-browser-connector"
INSTALL_DIR="$HOME/Library/Application Support/HermesBrowserConnector"
WRAPPER_PATH="$INSTALL_DIR/start-connector.sh"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/HermesBrowserConnector"

if /bin/launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1; then
    /bin/launchctl bootout "gui/$UID/$LABEL"
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        if ! /bin/launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1; then
            break
        fi
        sleep 0.5
    done
    if /bin/launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1; then
        die "LaunchAgent did not stop. Files were preserved so the tunnel remains manageable"
    fi
fi

rm -f -- "$PLIST_PATH" "$WRAPPER_PATH"

if [[ -n $REMOVE_PROFILE ]]; then
    [[ -d $REMOVE_PROFILE ]] || die "profile directory does not exist: $REMOVE_PROFILE"
    profile_real=$(cd -P -- "$REMOVE_PROFILE" && pwd -P)
    home_real=$(cd -P -- "$HOME" && pwd -P)
    case $profile_real in
        "$home_real"/*) ;;
        *) die "refusing to remove a profile outside the current user's home directory: $profile_real" ;;
    esac
    [[ $profile_real != "$home_real" && $profile_real != "$home_real/Library" ]] || \
        die "refusing to remove a protected home directory: $profile_real"
    [[ -f "$profile_real/.hermes-browser-connector-profile" ]] || \
        die "refusing to remove a profile without the connector ownership sentinel: $profile_real"
    rm -rf -- "$profile_real"
fi

if [[ $REMOVE_LOGS == true ]]; then
    rm -rf -- "$LOG_DIR"
fi

rmdir -- "$INSTALL_DIR" >/dev/null 2>&1 || true

printf 'Removed the Hermes browser connector LaunchAgent and generated wrapper.\n'
if [[ -z $REMOVE_PROFILE ]]; then
    printf 'Browser profile data was preserved. Use --remove-profile PATH to delete it explicitly.\n'
fi
printf 'SSH keys and known-hosts files were not changed.\n'
