#!/system/bin/sh
# Xpan uninstall: keep app data, photos, camera settings and LSPosed's DB untouched.
# APK-only cleanup helper. runtime.sh performs journal-proven mount teardown;
# this helper never unmounts or deletes camera-private files or oc_active.enc.
# It only removes com.xpanport.hook's installed APK for the owning instance.
#
# Installer contract (installer NOT implemented here):
#  * Serialize with mkdir /data/adb/xpan-lifecycle/lock (never steal a live lock).
#  * BEFORE pm install, atomically publish a fresh UUID in current.token, cancel
#    service.d/xpan_cleanup.sh, and set MODPATH/.xpan-instance to the same UUID.
#    Keep the lock through installation; an old worker checks it before EVERY PM
#    mutation. Also rotate the token for an APK-only reinstall/update.
#  * After installation, write MODPATH/.xpan-apk-identity: the SHA256 of the
#    normalized dumpsys lines below (legacy three lines, per-user v2 four).
#    This is required for safe early-boot deferral when PM cannot supply identity.
#  * A staged modules_update/XpanAce3Pro instance also cancels old cleanup, even
#    without tokens. Disabled != removed: only a remove marker permits deferral
#    while the original module directory still exists.
# Legacy installations get a token on uninstall. If both PM and the install-time
# identity are unavailable, retain the job for manual retry, NOT a blind uninstall.

PKG=com.xpanport.hook
BASE=/data/adb
LOG=/data/local/tmp/xpan_uninstall.log
READY_TRIES=120
UNINSTALL_TRIES=20
READY_DELAY=5
RETRY_DELAY=8
COMMAND_SECONDS=15
LOCK_TRIES=120
LOCK_DELAY=1

# The only path override is an explicit LOCAL test fixture. Never use on Android.
# No runtime-controlled pm/adb paths or arbitrary root overrides are supported.
if [ "${XPAN_TEST_MODE:-}" = 1 ]; then
    [ ! -e /system/bin/pm ] || exit 1
    case "${XPAN_TEST_ROOT:-}" in
        /tmp/xpan-uninstall-test.*|/var/tmp/xpan-uninstall-test.*) ;;
        */_tmp/xpan-runtime-test.*/*)
            case "${XPAN_TEST_ROOT##*/}" in ''|*[!0-9]*) exit 1 ;; esac
            [ -f "${XPAN_TEST_ROOT%/*}/.xpan-runtime-fixture" ] &&
                [ ! -L "${XPAN_TEST_ROOT%/*}/.xpan-runtime-fixture" ] || exit 1
            ;;
        *) exit 1 ;;
    esac
    case "$XPAN_TEST_ROOT" in *..*|*\'*|*'
'*) exit 1 ;; esac
    [ -f "$XPAN_TEST_ROOT/.xpan-uninstall-fixture" ] || exit 1
    BASE=$XPAN_TEST_ROOT/data/adb
    LOG=$XPAN_TEST_ROOT/uninstall.log
    PATH=$XPAN_TEST_ROOT/bin:/usr/bin:/bin
    READY_TRIES=3
    UNINSTALL_TRIES=3
    READY_DELAY=0
    RETRY_DELAY=0
    COMMAND_SECONDS=1
    LOCK_TRIES=3
    LOCK_DELAY=0
else
    [ -z "${XPAN_TEST_ROOT:-}" ] || exit 1
    PATH=/system/bin:/system/xbin:/data/adb/magisk:/data/adb/ksu/bin
fi
export PATH
umask 077
STATE=$BASE/xpan-lifecycle
LOCK=$STATE/lock
JOB=$BASE/service.d/xpan_cleanup.sh
ACTIVE=$BASE/modules/XpanAce3Pro
STAGED=$BASE/modules_update/XpanAce3Pro
log() { printf '%s\n' "xpan cleanup: $*" >> "$LOG"; }
read_file() { [ -f "$1" ] && IFS= read -r VALUE < "$1"; }
valid_token() {
    case "$1" in ''|*[!a-zA-Z0-9_-]*) return 1 ;; esac
    [ "${#1}" -le 80 ]
}
valid_identity() {
    case "$1" in *[!0-9a-f]*|'') return 1 ;; esac
    [ "${#1}" -eq 64 ]
}
pm_run() { timeout -s KILL "$COMMAND_SECONDS" pm "$@"; }
unlock() {
    if [ "${HAVE_LOCK:-0}" = 1 ]; then
        rmdir "$LOCK" 2>/dev/null
        HAVE_LOCK=0
    fi
}
lock() {
    mkdir "$LOCK" 2>/dev/null || return 1
    HAVE_LOCK=1
}
trap 'unlock' EXIT
trap 'unlock; exit 1' HUP INT TERM

# A successful framework query AND a successful parsed user enumeration are
# required. A failing/empty pm path of the target is never evidence of absence.
package_users() {
    FRAMEWORK=$(pm_run path android 2>/dev/null) || return 1
    case "$FRAMEWORK" in package:/*) ;; *) return 1 ;; esac
    USER_OUT=$(pm_run list users 2>/dev/null) || return 1
    USERS=$(printf '%s\n' "$USER_OUT" | awk '
        /^Users:$/ { header=1; next }
        /UserInfo\{/ {
            s=$0; sub(/^.*UserInfo\{/, "", s); sub(/:.*$/, "", s)
            if (s !~ /^[0-9]+$/) bad=1; else print s
            next
        }
        /[^[:space:]]/ { bad=1 }
        END { if (!header || bad) exit 1 }
    ') || return 1
    [ -n "$USERS" ]
}
package_present() {
    PRESENT=0
    for USER in $USERS; do
        LIST=$(pm_run list packages --user "$USER" "$PKG" 2>/dev/null) || return 1
        case "$LIST" in
            "package:$PKG") PRESENT=1 ;;
            '') ;;
            *) return 1 ;;
        esac
    done
}
# Same normalization as lifecycle.sh and runtime.sh: legacy global timestamps
# retain three hash lines (supplied user times must agree). Per-user v2 uses
# owner 0 even if installed=false, otherwise the numeric lowest user with a time.
# User flags are not APK-instance identity; package_present separately proves
# installation. A present owner with no time is unknown, not a fallback to others.
package_identity() {
    IDENTITY=; ID_LINES=
    DUMP=$(timeout -s KILL "$COMMAND_SECONDS" dumpsys package "$PKG" 2>/dev/null) || return 1
    [ "${#DUMP}" -le 1048576 ] || return 1
    ID_LINES=$(printf '%s\n' "$DUMP" | LC_ALL=C awk '
        function reject() { bad=1; exit 1 }
        BEGIN { global_indent=-1; user=-1 }
        {
            bytes+=length($0)+1
            if (NR>8192 || length($0)>4096 || bytes>1048576) reject()
            s=$0; sub(/^[[:space:]]*/, "", s); indent=length($0)-length(s)
            if (s ~ /^Package([[:space:]\[]|$)/) {
                if (++packages!=1 || $0 ~ /\t/ ||
                    s !~ /^Package \[com\.xpanport\.hook\]( \([^[:space:]()]+\))?:?$/) reject()
                active=1; package_indent=indent; user=-1; next
            }
            if (active && s!="" && indent<=package_indent) {
                if (s ~ /^User/) reject()
                active=0; user=-1
            }
            if (!active) {
                if (s ~ /^(codePath|firstInstallTime|lastUpdateTime)/) reject()
                next
            }
            if (global_indent<0 && s!="") global_indent=indent
            if (s ~ /^User/) {
                if ($0 ~ /[[:cntrl:]]/ || global_indent<0 || indent!=global_indent ||
                    s !~ /^User (0|[1-9][0-9]*):([ ]+[^[:space:]].*)?$/) reject()
                id=s; sub(/^User /, "", id); sub(/:.*/, "", id)
                if (length(id)>10 || id+0>2147483647 || user_seen[id]++ || ++users>64) reject()
                user=id; user_indent=indent; child_indent=-1; next
            }
            if (s!="" && user>=0) {
                if (indent<=user_indent) user=-1
                else if (child_indent<0) child_indent=indent
            }
            if (s ~ /^(codePath|firstInstallTime|lastUpdateTime)/) {
                if ($0 ~ /\t/ || s !~ /^(codePath|firstInstallTime|lastUpdateTime)=/) reject()
                key=s; sub(/=.*/, "", key); value=substr(s, length(key)+2)
                if (value=="" || value ~ /[[:cntrl:]]/ ||
                    value ~ /^[[:space:]]/ || value ~ /[[:space:]]$/) reject()
                if (key=="firstInstallTime" && user>=0) {
                    if (indent!=child_indent || time_seen[user]++) reject()
                    times[user]=value
                } else {
                    if (user>=0 || users || global_seen[key]++ ||
                        (global_indent>=0 && indent!=global_indent)) reject()
                    global_indent=indent; fields[key]=value
                }
            }
        }
        END {
            if (bad || packages!=1 || global_seen["codePath"]!=1 ||
                global_seen["lastUpdateTime"]!=1) exit 1
            path=fields["codePath"]
            if (path !~ /^\/data\/app\/[^[:space:]]+$/ ||
                path ~ /\/\// || path ~ /\.\./ || path ~ /\\/) exit 1
            if (global_seen["firstInstallTime"]==1) {
                first=fields["firstInstallTime"]
                for (id in times) if (times[id]!=first) exit 1
            } else {
                if (user_seen["0"]) {
                    if (time_seen["0"]!=1) exit 1
                    selected="0"
                } else {
                    selected=-1
                    for (id in times) if (selected<0 || id+0<selected+0) selected=id
                    if (selected<0) exit 1
                }
                first=times[selected]
            }
            print "codePath=" path
            print "firstInstallTime=" first
            print "lastUpdateTime=" fields["lastUpdateTime"]
            if (global_seen["firstInstallTime"]!=1)
                print "identity_version=2;firstInstallTimeUser=" selected
        }
    ') || return 1
    IDENTITY=$(printf '%s\n' "$ID_LINES" | sha256sum) || return 1
    IDENTITY=${IDENTITY%% *}
    valid_identity "$IDENTITY"
}

# Called under the shared lock; token change, active replacement, staging or
# disable cancel this job. No token/versionCode alone can identify a new APK;
# compare codePath + firstInstallTime + lastUpdateTime as well.
still_owned() {
    VALUE=
    read_file "$STATE/current.token" || return 1
    [ "$VALUE" = "$TOKEN" ] || return 1
    [ ! -e "$STAGED" ] || return 1
    if [ -e "$ACTIVE" ]; then
        VALUE=
        read_file "$ACTIVE/.xpan-instance" || return 1
        [ "$VALUE" = "$TOKEN" ] || return 1
        [ ! -e "$ACTIVE/disable" ] || [ -e "$ACTIVE/remove" ] || return 1
        if [ "$DEFERRED" = 1 ]; then
            [ -e "$ACTIVE/remove" ] || return 1
        fi
    fi
}
owns_job() {
    [ -f "$JOB" ] || return 1
    grep -Fqx "XPAN_JOB_TOKEN='$TOKEN'" "$JOB" && return 0
    # Migration: only the EXACT shipped legacy Xpan worker is ours. A name
    # or package string alone is not ownership of a service.d script.
    JOB_HASH=$(sha256sum "$JOB" 2>/dev/null) || return 1
    JOB_HASH=${JOB_HASH%% *}
    [ "$JOB_HASH" = 0261785d42aadaf66423999f8478f409202b86046df4848bb03eb45fb0dd1952 ]
}
remove_own_job() {
    # Never remove another token's replacement job (including after reinstall).
    if owns_job; then rm -f "$JOB" || return 1; fi
}

# Return 0: verified absent, 1: retry, 2: cancelled by replacement/reinstall.
attempt() {
    still_owned || { log 'cancelled: module instance changed/active/disabled'; return 2; }
    package_users || { log 'PM unavailable (framework/users); retain retry'; return 1; }
    package_present || { log 'PM package query failed; retain retry'; return 1; }
    if [ "$PRESENT" = 0 ]; then
        log 'verified absent for every current user'
        return 0
    fi
    package_identity || { log 'APK identity unavailable; retain retry'; return 1; }
    if [ -z "$EXPECTED" ] && [ "$DEFERRED" = 0 ]; then
        EXPECTED=$IDENTITY
    fi
    [ -n "$EXPECTED" ] || { log 'no uninstall-time APK identity; retain for manual retry'; return 1; }
    [ "$IDENTITY" = "$EXPECTED" ] || { log 'cancelled: installed APK identity changed'; return 2; }
    still_owned || return 2
    # Global uninstall covers secondary/work-profile users too. -k preserves app
    # data/settings. No --user 0 fallback that can falsely claim global success.
    OUT=$(pm_run uninstall -k "$PKG" 2>&1)
    RESULT=$?
    log "uninstall rc=$RESULT: $OUT"
    [ "$RESULT" = 0 ] && [ "$OUT" = Success ] || return 1
    package_users && package_present || { log 'post-uninstall PM verification failed'; return 1; }
    [ "$PRESENT" = 0 ] || { log 'still installed for at least one user'; return 1; }
    log 'APK removal verified for every current user'
    return 0
}
write_job() {
    SCRIPT_SOURCE=$0
    if [ "$DEFERRED" = 0 ] && [ -e "$ACTIVE/payload/uninstall-apk.sh" ]; then
        # The root wrapper sources this packaged helper to preserve $0 identity.
        # Copy the helper, not the wrapper which needs the soon-removed MODPATH.
        [ "${0%/*}" = "$ACTIVE" ] && [ ! -L "$ACTIVE/payload" ] &&
            [ -f "$ACTIVE/payload/uninstall-apk.sh" ] &&
            [ ! -L "$ACTIVE/payload/uninstall-apk.sh" ] || return 1
        SCRIPT_SOURCE=$ACTIVE/payload/uninstall-apk.sh
    fi
    [ -f "$SCRIPT_SOURCE" ] && [ ! -L "$SCRIPT_SOURCE" ] || return 1
    if [ -e "$JOB" ] && ! owns_job; then
        log 'retry path occupied by unowned job; refuse to overwrite'
        return 1
    fi
    mkdir -p "$BASE/service.d" || return 1
    # Self-contained copy survives removal of the original module directory.
    # The temporary file is non-executable and published only under the lock.
    TEMP=$JOB.tmp
    {
        printf '#!/system/bin/sh\nXPAN_JOB_TOKEN=\047%s\047\nXPAN_JOB_IDENTITY=\047%s\047\n' "$TOKEN" "$EXPECTED"
        cat "$SCRIPT_SOURCE"
    } > "$TEMP" || { rm -f "$TEMP"; return 1; }
    chmod 0700 "$TEMP" && mv -f "$TEMP" "$JOB" || { rm -f "$TEMP"; return 1; }
    log 'retry job retained in service.d (bounded, no background worker)'
}

[ "$(id -u)" = 0 ] || exit 1
mkdir -p "$STATE" || exit 1
# Without timeout every PM query fails closed; the immediate path still writes
# a retry job rather than dropping the uninstall request.
DEFERRED=0
TOKEN=
EXPECTED=
if [ -n "${XPAN_JOB_TOKEN:-}" ]; then
    DEFERRED=1
    TOKEN=$XPAN_JOB_TOKEN
    EXPECTED=${XPAN_JOB_IDENTITY:-}
    valid_token "$TOKEN" || exit 1
    [ -z "$EXPECTED" ] || valid_identity "$EXPECTED" || exit 1
else
    # Only a real module instance may initialize legacy lifecycle state.
    [ "${0%/*}" = "$ACTIVE" ] || { log 'not the Xpan module uninstall hook'; exit 1; }
    grep -qx 'id=XpanAce3Pro' "$ACTIVE/module.prop" || exit 1
    WAITED=0
    INITIAL_TOKEN=
    if read_file "$ACTIVE/.xpan-instance"; then
        INITIAL_TOKEN=$VALUE
        valid_token "$INITIAL_TOKEN" || exit 1
    fi
    while ! lock; do
        # Never steal runtime/installer's lock or mutate PM while they own it.
        # A replacement seen while waiting cancels this old uninstall instance.
        [ ! -e "$STAGED" ] && [ ! -L "$STAGED" ] || exit 0
        if read_file "$ACTIVE/.xpan-instance"; then
            WAIT_TOKEN=$VALUE
            valid_token "$WAIT_TOKEN" || exit 1
            [ -z "$INITIAL_TOKEN" ] || [ "$WAIT_TOKEN" = "$INITIAL_TOKEN" ] || exit 0
            if read_file "$STATE/current.token"; then [ "$VALUE" = "$WAIT_TOKEN" ] || exit 0; fi
        fi
        WAITED=$((WAITED+1))
        [ "$WAITED" -lt "$LOCK_TRIES" ] || { log 'lifecycle lock wait exhausted; no APK mutation'; exit 1; }
        sleep "$LOCK_DELAY" || exit 1
    done
    VALUE=
    if read_file "$ACTIVE/.xpan-instance"; then
        TOKEN=$VALUE
        valid_token "$TOKEN" || { unlock; exit 1; }
        [ -z "$INITIAL_TOKEN" ] || [ "$TOKEN" = "$INITIAL_TOKEN" ] || { unlock; exit 0; }
    else
        TOKEN=legacy-$(date +%s)-$$
        printf '%s\n' "$TOKEN" > "$ACTIVE/.xpan-instance" || { unlock; exit 1; }
    fi
    VALUE=
    if read_file "$STATE/current.token"; then
        [ "$VALUE" = "$TOKEN" ] || { log 'cancelled: newer lifecycle token'; unlock; exit 0; }
    else
        printf '%s\n' "$TOKEN" > "$STATE/current.token" || { unlock; exit 1; }
    fi
    VALUE=
    if read_file "$ACTIVE/.xpan-apk-identity"; then
        valid_identity "$VALUE" || { log 'invalid installer APK identity'; unlock; exit 1; }
        EXPECTED=$VALUE
    fi
    attempt
    RESULT=$?
    if [ "$RESULT" = 0 ] || [ "$RESULT" = 2 ]; then
        remove_own_job
        RESULT=$?
    else
        write_job
        # Failure is not success, even when a retry artifact was written.
        RESULT=1
    fi
    unlock
    exit "$RESULT"
fi

# PM readiness waiting is bounded, and timeout is NOT logged as readiness.
I=0
READY=0
while [ "$I" -lt "$READY_TRIES" ]; do
    I=$((I+1))
    lock || { log 'lifecycle lock busy; keep job'; exit 1; }
    if ! still_owned; then
        log 'cancelled before waiting: replacement/active/disabled module'
        remove_own_job
        RESULT=$?
        unlock
        exit "$RESULT"
    fi
    if package_users; then READY=1; fi
    unlock
    [ "$READY" = 1 ] && break
    [ "$I" -ge "$READY_TRIES" ] || sleep "$READY_DELAY"
done
[ "$READY" = 1 ] || { log 'PM readiness timeout; job retained'; exit 1; }
log 'PM ready'
N=0
while [ "$N" -lt "$UNINSTALL_TRIES" ]; do
    N=$((N+1))
    lock || { log 'lifecycle lock busy; keep job'; exit 1; }
    attempt
    RESULT=$?
    if [ "$RESULT" = 0 ] || [ "$RESULT" = 2 ]; then
        remove_own_job
        RESULT=$?
        unlock
        exit "$RESULT"
    fi
    unlock
    [ "$N" -ge "$UNINSTALL_TRIES" ] || sleep "$RETRY_DELAY"
done
log 'uninstall attempts exhausted; job retained for next boot/manual retry'
exit 1
