#!/usr/bin/env bash
# Pure local regression tests; NEVER uses adb, a device, or Android's real pm.
# Run: bash tests/uninstall_test.sh
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SOURCE=$SCRIPT_DIR/../hook/uninstall.sh
[ ! -e /system/bin/pm ] || { printf 'Refuse to test on Android.\n' >&2; exit 1; }
ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
mkdir -p "$ROOT/_tmp"
SUITE=$(mktemp -d "$ROOT/_tmp/xpan-runtime-test.XXXXXXXX")
: > "$SUITE/.xpan-runtime-fixture"
trap 'rm -rf -- "$SUITE"' EXIT
export XPAN_TEST_MODE=1
unset XPAN_JOB_TOKEN XPAN_JOB_IDENTITY
COUNT=0
fail() {
    printf 'FAIL: %s\n' "$*" >&2
    [ ! -f "${XPAN_TEST_ROOT:-}/stdout" ] || cat "${XPAN_TEST_ROOT}/stdout" >&2
    [ ! -f "${XPAN_TEST_ROOT:-}/uninstall.log" ] || cat "${XPAN_TEST_ROOT}/uninstall.log" >&2
    [ ! -f "${XPAN_TEST_ROOT:-}/pm.calls" ] || cat "${XPAN_TEST_ROOT}/pm.calls" >&2
    exit 1
}
assert_file() { [ -f "$1" ] || fail "missing $1"; }
assert_no_file() { [ ! -e "$1" ] || fail "unexpected $1"; }
assert_log() { grep -Fq "$1" "$XPAN_TEST_ROOT/uninstall.log" || fail "missing log: $1"; }
assert_no_uninstall() {
    ! grep -q '^uninstall ' "$XPAN_TEST_ROOT/pm.calls" || fail 'unexpected APK uninstall'
}
fixture() {
    COUNT=$((COUNT+1))
    export XPAN_TEST_ROOT=$SUITE/$COUNT
    mkdir -p "$XPAN_TEST_ROOT/bin" "$XPAN_TEST_ROOT/data/adb/modules/XpanAce3Pro" \
        "$XPAN_TEST_ROOT/data/adb/xpan-lifecycle" "$XPAN_TEST_ROOT/data/adb/service.d" \
        "$XPAN_TEST_ROOT/data/adb/modules/another-module" "$XPAN_TEST_ROOT/camera-files" \
        "$XPAN_TEST_ROOT/photos" "$XPAN_TEST_ROOT/lsposed"
    ACTIVE=$XPAN_TEST_ROOT/data/adb/modules/XpanAce3Pro
    STATE=$XPAN_TEST_ROOT/data/adb/xpan-lifecycle
    JOB=$XPAN_TEST_ROOT/data/adb/service.d/xpan_cleanup.sh
    : > "$XPAN_TEST_ROOT/.xpan-uninstall-fixture"
    : > "$XPAN_TEST_ROOT/pm.calls"
    printf 'id=XpanAce3Pro\nversion=v1.3\n' > "$ACTIVE/module.prop"
    printf 'instance-old\n' > "$ACTIVE/.xpan-instance"
    printf 'instance-old\n' > "$STATE/current.token"
    printf '1\n' > "$XPAN_TEST_ROOT/installed.0"
    printf 'old\n' > "$XPAN_TEST_ROOT/generation"
    printf 'ready\n' > "$XPAN_TEST_ROOT/pm-mode"
    printf 'success\n' > "$XPAN_TEST_ROOT/uninstall-mode"
    cp "$SOURCE" "$ACTIVE/uninstall.sh"
    for file in photos/photo.jpg camera-files/preferences.xml camera-files/xpan_cropzoom \
        camera-files/xpan_cropzoom_busy oc_active.enc lsposed/modules_config.db \
        data/adb/modules/another-module/mount-source; do
        printf 'must-survive\n' > "$XPAN_TEST_ROOT/$file"
    done
    cp "$XPAN_TEST_ROOT/data/adb/modules/another-module/mount-source" "$XPAN_TEST_ROOT/mounted-config"
    cat > "$XPAN_TEST_ROOT/bin/id" <<'FAKE'
#!/bin/sh
printf '0\n'
FAKE
    cat > "$XPAN_TEST_ROOT/bin/getprop" <<'FAKE'
#!/bin/sh
[ "$1" = sys.boot_completed ] && printf '1\n'
FAKE
    cat > "$XPAN_TEST_ROOT/bin/pm" <<'FAKE'
#!/bin/sh
R=$XPAN_TEST_ROOT
printf '%s\n' "$*" >> "$R/pm.calls"
IFS= read -r MODE < "$R/pm-mode"
case "$MODE" in
    unavailable) printf 'Error: package service not found\n'; exit 1 ;;
    empty-framework) [ "$*" != 'path android' ] || exit 0 ;;
    hung) sleep 10; exit 1 ;;
    fail-once)
        printf 'ready\n' > "$R/pm-mode"
        printf 'Error: early boot\n'; exit 1 ;;
esac
case "$*" in
    'path android') printf 'package:/system/framework/framework-res.apk\n' ;;
    'list users')
        [ "$MODE" != users-failure ] || { printf 'Error: users unavailable\n'; exit 1; }
        printf 'Users:\n\tUserInfo{0:Owner:13} running\n'
        if [ -f "$R/multiuser" ]; then printf '\tUserInfo{10:Work:30} running\n'; fi
        ;;
    'list packages --user '*" com.xpanport.hook")
        [ "$MODE" != target-failure ] || { printf 'Error: query failed\n'; exit 1; }
        [ "$MODE" != target-text-error ] || { printf 'Error: query failed\n'; exit 0; }
        USER=$4
        if [ -f "$R/installed.$USER" ]; then printf 'package:com.xpanport.hook\n'; fi
        ;;
    'list packages --user '*" com.oplus.camera")
        USER=$4
        [ ! -f "$R/camera-absent.$USER" ] || exit 0
        printf 'package:com.oplus.camera\n'
        ;;
    'clear --user '*" com.oplus.camera")
        USER=$3
        [ "$MODE" != clear-failure ] || { printf 'Error: clear failed\n'; exit 1; }
        : > "$R/camera-cleared.$USER"
        printf 'Success\n'
        ;;
    'uninstall -k com.xpanport.hook')
        printf 'legacy -k invocation is no longer accepted\n' >&2; exit 85
        ;;
    'uninstall com.xpanport.hook')
        IFS= read -r ACTION < "$R/uninstall-mode"
        case "$ACTION" in
            failure) printf 'Failure [DELETE_FAILED_INTERNAL_ERROR]\n'; exit 1 ;;
            misleading) printf 'Unsuccessful: not Success\n'; exit 0 ;;
            false-success) printf 'Success\n'; exit 0 ;;
            partial) rm -f "$R/installed.0"; printf 'Success\n'; exit 0 ;;
            verify-failure)
                rm -f "$R/installed.0" "$R/installed.10"
                printf 'unavailable\n' > "$R/pm-mode"; printf 'Success\n'; exit 0 ;;
            success)
                rm -f "$R/installed.0" "$R/installed.10"; printf 'Success\n' ;;
            *) exit 85 ;;
        esac
        ;;
    *) printf 'Unexpected fake pm invocation\n' >&2; exit 85 ;;
esac
FAKE
    cat > "$XPAN_TEST_ROOT/bin/dumpsys" <<'FAKE'
#!/bin/sh
[ "$*" = 'package com.xpanport.hook' ] || exit 85
[ ! -f "$XPAN_TEST_ROOT/identity-failure" ] || exit 1
IFS= read -r GEN < "$XPAN_TEST_ROOT/generation"
printf '  Package [com.xpanport.hook] (1234):\n'
printf '    codePath=/data/app/%s/com.xpanport.hook\n' "$GEN"
printf '    firstInstallTime=2026-11-13 12:00:00\n'
printf '    lastUpdateTime=2026-11-13 12:00:00-%s\n' "$GEN"
FAKE
    for forbidden in adb am umount mount sqlite3; do
        cat > "$XPAN_TEST_ROOT/bin/$forbidden" <<'FAKE'
#!/bin/sh
printf '%s %s\n' "$0" "$*" >> "$XPAN_TEST_ROOT/forbidden.calls"
exit 85
FAKE
    done
    chmod +x "$XPAN_TEST_ROOT/bin/"*
}
run_hook() {
    RC=0
    /usr/bin/sh "$ACTIVE/uninstall.sh" > "$XPAN_TEST_ROOT/stdout" 2>&1 || RC=$?
}
run_job() {
    assert_file "$JOB"
    RC=0
    /usr/bin/sh "$JOB" > "$XPAN_TEST_ROOT/stdout" 2>&1 || RC=$?
}
expect_rc() { [ "$RC" = "$1" ] || fail "expected rc=$1, got $RC"; }
keep_sentinels() {
    assert_no_file "$XPAN_TEST_ROOT/forbidden.calls"
    for file in photos/photo.jpg camera-files/preferences.xml camera-files/xpan_cropzoom \
        camera-files/xpan_cropzoom_busy oc_active.enc lsposed/modules_config.db \
        mounted-config data/adb/modules/another-module/mount-source; do
        grep -qx must-survive "$XPAN_TEST_ROOT/$file" || fail "changed protected file: $file"
    done
}
ok() { keep_sentinels; printf 'PASS: %s\n' "$1"; }
prepare_failure() {
    printf 'failure\n' > "$XPAN_TEST_ROOT/uninstall-mode"
    run_hook; expect_rc 1; assert_file "$JOB"
    touch "$ACTIVE/remove"
}

/usr/bin/sh -n "$SOURCE"
fixture
run_hook; expect_rc 0; assert_no_file "$JOB"; assert_no_file "$XPAN_TEST_ROOT/installed.0"
ok 'immediate verified success leaves no deferred job and preserves data'

fixture
prepare_failure
printf 'success\n' > "$XPAN_TEST_ROOT/uninstall-mode"
run_hook; expect_rc 0; assert_no_file "$JOB"
ok 'immediate success cancels the same-instance pending job'

fixture
mkdir "$ACTIVE/payload"
cp "$SOURCE" "$ACTIVE/payload/uninstall-apk.sh"
printf '#!/system/bin/sh\n. "${0%%/*}/payload/uninstall-apk.sh"\n' > "$ACTIVE/uninstall.sh"
printf 'failure\n' > "$XPAN_TEST_ROOT/uninstall-mode"
run_hook; expect_rc 1; assert_file "$JOB"
! grep -Fq '. "${0%/*}/payload/uninstall-apk.sh"' "$JOB" || fail 'deferred job copied wrapper'
rm -rf "$ACTIVE"
printf 'success\n' > "$XPAN_TEST_ROOT/uninstall-mode"
run_job; expect_rc 0; assert_no_file "$JOB"; assert_no_file "$XPAN_TEST_ROOT/installed.0"
ok 'sourced wrapper publishes a self-contained helper surviving module removal'

fixture
printf 'unavailable\n' > "$XPAN_TEST_ROOT/pm-mode"
run_hook; expect_rc 1; assert_file "$JOB"; assert_no_uninstall
rm -rf "$ACTIVE"
run_job; expect_rc 1; assert_file "$JOB"; assert_no_uninstall
assert_log 'PM readiness timeout'
! grep -q 'PM ready$\|verified absent' "$XPAN_TEST_ROOT/uninstall.log" || fail 'false readiness/absence'
[ "$(grep -c '^path android$' "$XPAN_TEST_ROOT/pm.calls")" = 4 ] || fail 'readiness loop not bounded'
ok 'early-boot PM failure is not absence; timeout retains self-contained job'

fixture
printf 'unavailable\n' > "$XPAN_TEST_ROOT/pm-mode"
run_hook; expect_rc 1
rm -rf "$ACTIVE"
rm -f "$XPAN_TEST_ROOT/installed.0"
printf 'fail-once\n' > "$XPAN_TEST_ROOT/pm-mode"
run_job; expect_rc 0; assert_no_file "$JOB"; assert_no_uninstall
assert_log 'PM ready'; assert_log 'verified absent for every current user'
ok 'PM becomes ready, truly not-installed package completes without uninstall'

fixture
prepare_failure
rm -rf "$ACTIVE"
run_job; expect_rc 1; assert_file "$JOB"; assert_log 'uninstall attempts exhausted'
[ "$(grep -c '^uninstall ' "$XPAN_TEST_ROOT/pm.calls")" = 4 ] || {
    printf '%s\n' "$(<"$XPAN_TEST_ROOT/pm.calls")" "$(<"$XPAN_TEST_ROOT/uninstall.log")" >&2
    fail 'retry attempts not bounded'
}
printf 'success\n' > "$XPAN_TEST_ROOT/uninstall-mode"
run_job; expect_rc 0; assert_no_file "$JOB"
ok 'delayed failure persists; a later retry verifies success and self-removes'

fixture
prepare_failure
printf 'instance-new\n' > "$STATE/current.token"
printf 'instance-new\n' > "$ACTIVE/.xpan-instance"
printf 'new\n' > "$XPAN_TEST_ROOT/generation"
: > "$XPAN_TEST_ROOT/pm.calls"
run_job; expect_rc 0; assert_no_file "$JOB"; assert_no_uninstall
assert_file "$XPAN_TEST_ROOT/installed.0"
ok 'reinstalled module token cancels old cleanup before any APK removal'

fixture
prepare_failure
rm -rf "$ACTIVE"
printf 'new\n' > "$XPAN_TEST_ROOT/generation"
: > "$XPAN_TEST_ROOT/pm.calls"
run_job; expect_rc 0; assert_no_file "$JOB"; assert_no_uninstall
assert_file "$XPAN_TEST_ROOT/installed.0"; assert_log 'installed APK identity changed'
ok 'APK-only reinstall with unchanged token is protected by APK identity'

fixture
prepare_failure
mkdir -p "$XPAN_TEST_ROOT/data/adb/modules_update/XpanAce3Pro"
: > "$XPAN_TEST_ROOT/pm.calls"
run_job; expect_rc 0; assert_no_file "$JOB"; assert_no_uninstall
ok 'staged replacement cancels old worker even without a rotated token'

fixture
prepare_failure
rm -f "$ACTIVE/remove"
touch "$ACTIVE/disable"
: > "$XPAN_TEST_ROOT/pm.calls"
run_job; expect_rc 0; assert_no_file "$JOB"; assert_no_uninstall
ok 'disabled original module is not a removed module'

fixture
prepare_failure
rm -f "$ACTIVE/remove"
: > "$XPAN_TEST_ROOT/pm.calls"
run_job; expect_rc 0; assert_no_file "$JOB"; assert_no_uninstall
ok 'active original module without remove marker cancels deferred cleanup'

fixture
: > "$XPAN_TEST_ROOT/multiuser"
printf '1\n' > "$XPAN_TEST_ROOT/installed.10"
rm -f "$XPAN_TEST_ROOT/installed.0"
run_hook; expect_rc 0; assert_no_file "$JOB"; assert_no_file "$XPAN_TEST_ROOT/installed.10"
[ "$(grep -c '^list packages --user 10 com.xpanport.hook$' "$XPAN_TEST_ROOT/pm.calls")" = 2 ] || fail 'secondary user not verified'
ok 'secondary/work-profile-only install removed globally without -k'

fixture
: > "$XPAN_TEST_ROOT/multiuser"
printf '1\n' > "$XPAN_TEST_ROOT/installed.10"
printf 'partial\n' > "$XPAN_TEST_ROOT/uninstall-mode"
run_hook; expect_rc 1; assert_file "$JOB"; assert_file "$XPAN_TEST_ROOT/installed.10"
ok 'partial user removal is not falsely reported as success'

for mode in empty-framework users-failure target-failure target-text-error; do
    fixture
    printf '%s\n' "$mode" > "$XPAN_TEST_ROOT/pm-mode"
    run_hook; expect_rc 1; assert_file "$JOB"; assert_no_uninstall
    ok "PM $mode is not not-installed"
done

for mode in misleading false-success verify-failure; do
    fixture
    printf '%s\n' "$mode" > "$XPAN_TEST_ROOT/uninstall-mode"
    run_hook; expect_rc 1; assert_file "$JOB"
    ok "uninstall $mode retains retry rather than trusting output"
done

fixture
printf 'unavailable\n' > "$XPAN_TEST_ROOT/pm-mode"
run_hook; expect_rc 1
rm -rf "$ACTIVE"
printf 'ready\n' > "$XPAN_TEST_ROOT/pm-mode"
printf 'new\n' > "$XPAN_TEST_ROOT/generation"
: > "$XPAN_TEST_ROOT/pm.calls"
run_job; expect_rc 1; assert_file "$JOB"; assert_no_uninstall
assert_log 'no uninstall-time APK identity'
ok 'legacy early-boot job lacking identity never uninstalls an unknown/new APK'

fixture
IDENTITY=$("$XPAN_TEST_ROOT/bin/dumpsys" package com.xpanport.hook | \
    grep -E '^    (codePath|firstInstallTime|lastUpdateTime)=' | \
    cut -c 5- | sha256sum)
printf '%s\n' "${IDENTITY%% *}" > "$ACTIVE/.xpan-apk-identity"
printf 'unavailable\n' > "$XPAN_TEST_ROOT/pm-mode"
run_hook; expect_rc 1
rm -rf "$ACTIVE"
printf 'ready\n' > "$XPAN_TEST_ROOT/pm-mode"
run_job; expect_rc 0; assert_no_file "$JOB"
ok 'installer identity enables safe early-boot deferred removal'

fixture
rm -f "$ACTIVE/.xpan-instance" "$STATE/current.token"
run_hook; expect_rc 0; assert_no_file "$JOB"
assert_file "$ACTIVE/.xpan-instance"; assert_file "$STATE/current.token"
ok 'legacy module is assigned an instance token before immediate cleanup'

fixture
mkdir "$STATE/lock"
printf '#!/bin/sh\nrmdir "$XPAN_TEST_ROOT/data/adb/xpan-lifecycle/lock"\n' > "$XPAN_TEST_ROOT/bin/sleep"
chmod +x "$XPAN_TEST_ROOT/bin/sleep"
run_hook; expect_rc 0; assert_no_file "$JOB"; assert_no_file "$XPAN_TEST_ROOT/installed.0"
ok 'immediate uninstall waits for lifecycle lock without stealing it'

fixture
mkdir "$STATE/lock"
printf '#!/bin/sh\nprintf "instance-new\\n" > "$XPAN_TEST_ROOT/data/adb/modules/XpanAce3Pro/.xpan-instance"\nprintf "instance-new\\n" > "$XPAN_TEST_ROOT/data/adb/xpan-lifecycle/current.token"\nrmdir "$XPAN_TEST_ROOT/data/adb/xpan-lifecycle/lock"\n' > "$XPAN_TEST_ROOT/bin/sleep"
chmod +x "$XPAN_TEST_ROOT/bin/sleep"
run_hook; expect_rc 0; assert_no_uninstall; assert_file "$XPAN_TEST_ROOT/installed.0"
ok 'token replacement during lock wait cancels old immediate uninstall'

fixture
prepare_failure
mkdir "$STATE/lock"
: > "$XPAN_TEST_ROOT/pm.calls"
run_job; expect_rc 1; assert_file "$JOB"; assert_no_uninstall
rmdir "$STATE/lock"
ok 'shared installer lock prevents concurrent cleanup mutation'

fixture
prepare_failure
cp "$JOB" "$XPAN_TEST_ROOT/old-worker.sh"
printf "#!/bin/sh\nXPAN_JOB_TOKEN='instance-new'\n" > "$JOB"
printf 'instance-new\n' > "$STATE/current.token"
RC=0
/usr/bin/sh "$XPAN_TEST_ROOT/old-worker.sh" > "$XPAN_TEST_ROOT/stdout" 2>&1 || RC=$?
expect_rc 0; assert_file "$JOB"
grep -q instance-new "$JOB" || fail 'old worker removed replacement job'
ok 'cancelled old worker does not delete a newer token job'

fixture
printf 'hung\n' > "$XPAN_TEST_ROOT/pm-mode"
START=$SECONDS
run_hook; expect_rc 1; assert_file "$JOB"
[ $((SECONDS-START)) -lt 8 ] || fail 'individual PM command did not time out'
ok 'hung PM call is bounded with timeout, not false success'

fixture
RC=0
XPAN_TEST_ROOT=/tmp/untrusted-fixture /usr/bin/sh "$ACTIVE/uninstall.sh" > /dev/null 2>&1 || RC=$?
expect_rc 1; assert_no_uninstall
ok 'test path injection rejects paths outside the explicitly marked fixture'

fixture
# Exact pre-fix worker: content fingerprint, not filename, proves ownership.
cat > "$JOB" <<'LEGACY'
#!/system/bin/sh
# X-Pan 模块卸载兜底清理：等系统起完，把 hook APK 卸掉，然后自删。幂等。
LOG=/data/local/tmp/xpan_uninstall.log
PKG=com.xpanport.hook

log() { echo "$(date '+%m-%d %H:%M:%S') cleanup: $1" >> "$LOG"; }
log "start (pid $$)"

# 等包服务就绪：boot_completed + pm 能应答，最多等 10 分钟
i=0
while [ "$i" -lt 120 ]; do
    i=$((i+1))
    if [ "$(getprop sys.boot_completed 2>/dev/null)" = "1" ] \
        && pm path android >/dev/null 2>&1; then
        break
    fi
    sleep 5
done
log "package service ready after $((i*5))s"
sleep 20

# 相机必须停掉：它进程里还挂着模块，卸载可能被拒
am force-stop com.oplus.camera >/dev/null 2>&1
sleep 2

n=0
while [ "$n" -lt 20 ]; do
    n=$((n+1))
    if ! pm path "$PKG" >/dev/null 2>&1; then
        log "not installed (try $n) - nothing to do"
        break
    fi
    OUT="$(pm uninstall --user 0 "$PKG" 2>&1)"
    log "try $n (--user 0): $OUT"
    case "$OUT" in *uccess*) break ;; esac
    OUT="$(pm uninstall "$PKG" 2>&1)"
    log "try $n (plain): $OUT"
    case "$OUT" in *uccess*) break ;; esac
    sleep 8
done

if pm path "$PKG" >/dev/null 2>&1; then
    log "GAVE UP after $n tries - uninstall manually from LSPosed/KernelSU"
else
    log "PKG removed"
fi

rm -f /data/adb/service.d/xpan_cleanup.sh
log "done, self removed"
LEGACY
run_hook; expect_rc 0; assert_no_file "$JOB"
ok 'immediate success removes only the exact fingerprinted legacy worker'

fixture
printf '# unrelated module job\n' > "$JOB"
run_hook; expect_rc 0; assert_file "$JOB"
grep -qx '# unrelated module job' "$JOB" || fail 'changed unrelated job'
ok 'immediate success leaves an unrelated same-name service job untouched'

fixture
printf '# unrelated module job\n' > "$JOB"
printf 'failure\n' > "$XPAN_TEST_ROOT/uninstall-mode"
run_hook; expect_rc 1; assert_file "$JOB"
grep -qx '# unrelated module job' "$JOB" || fail 'overwrote unrelated job'
assert_log 'refuse to overwrite'
ok 'failed cleanup does not overwrite an unrelated service job'

fixture
: > "$XPAN_TEST_ROOT/multiuser"
printf '1\n' > "$XPAN_TEST_ROOT/installed.10"
printf 'success\n' > "$XPAN_TEST_ROOT/uninstall-mode"
cp "$SOURCE" "$ACTIVE/payload/uninstall-apk.sh" 2>/dev/null || { mkdir -p "$ACTIVE/payload"; cp "$SOURCE" "$ACTIVE/payload/uninstall-apk.sh"; }
printf '%s\n' 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef > "$ACTIVE/.xpan-apk-identity"
GUARD_ID=$(printf 'codePath=/data/app/old/com.xpanport.hook\nfirstInstallTime=2026-11-13 12:00:00\nlastUpdateTime=2026-11-13 12:00:00-old\n' | sha256sum)
printf '%s\n' "${GUARD_ID%% *}" > "$ACTIVE/.xpan-apk-identity"
XPAN_ARM=1 XPAN_ARM_LOCKED=1 XPAN_TEST_ROOT=$XPAN_TEST_ROOT /usr/bin/sh "$ACTIVE/payload/uninstall-apk.sh" >/dev/null 2>&1 || fail 'guard arm failed'
GUARDJOB=$XPAN_TEST_ROOT/data/adb/service.d/xpan_guard.sh
assert_file "$GUARDJOB"
grep -qx "XPAN_JOB_MODE='guard'" "$GUARDJOB" || fail 'guard mode not published'
RC=0; /usr/bin/sh "$GUARDJOB" >/dev/null 2>&1 || RC=$?
expect_rc 0; assert_file "$GUARDJOB"; assert_no_uninstall
ok 'boot guard keeps job while module directory still exists'

fixture
: > "$XPAN_TEST_ROOT/multiuser"
printf '1\n' > "$XPAN_TEST_ROOT/installed.10"
printf 'success\n' > "$XPAN_TEST_ROOT/uninstall-mode"
mkdir -p "$ACTIVE/payload"
GUARDJOB=$XPAN_TEST_ROOT/data/adb/service.d/xpan_guard.sh
cp "$SOURCE" "$ACTIVE/payload/uninstall-apk.sh"
printf '%s\n' 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef > "$ACTIVE/.xpan-apk-identity"
GUARD_ID=$(printf 'codePath=/data/app/old/com.xpanport.hook\nfirstInstallTime=2026-11-13 12:00:00\nlastUpdateTime=2026-11-13 12:00:00-old\n' | sha256sum)
printf '%s\n' "${GUARD_ID%% *}" > "$ACTIVE/.xpan-apk-identity"
XPAN_ARM=1 XPAN_ARM_LOCKED=1 XPAN_TEST_ROOT=$XPAN_TEST_ROOT /usr/bin/sh "$ACTIVE/payload/uninstall-apk.sh" >/dev/null 2>&1 || fail 'guard arm failed'
rm -rf "$ACTIVE"
RC=0; /usr/bin/sh "$GUARDJOB" >/dev/null 2>&1 || RC=$?
expect_rc 0
assert_no_file "$GUARDJOB"
assert_no_file "$XPAN_TEST_ROOT/installed.0"
assert_no_file "$XPAN_TEST_ROOT/installed.10"
assert_file "$XPAN_TEST_ROOT/camera-cleared.0"
assert_file "$XPAN_TEST_ROOT/camera-cleared.10"
assert_log 'camera clear user=0'
ok 'boot guard cleans after module removal even when uninstall.sh never ran'

fixture
printf 'success\n' > "$XPAN_TEST_ROOT/uninstall-mode"
mkdir -p "$ACTIVE/payload"
GUARDJOB=$XPAN_TEST_ROOT/data/adb/service.d/xpan_guard.sh
cp "$SOURCE" "$ACTIVE/payload/uninstall-apk.sh"
printf '%s\n' 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef > "$ACTIVE/.xpan-apk-identity"
GUARD_ID=$(printf 'codePath=/data/app/old/com.xpanport.hook\nfirstInstallTime=2026-11-13 12:00:00\nlastUpdateTime=2026-11-13 12:00:00-old\n' | sha256sum)
printf '%s\n' "${GUARD_ID%% *}" > "$ACTIVE/.xpan-apk-identity"
XPAN_ARM=1 XPAN_ARM_LOCKED=1 XPAN_TEST_ROOT=$XPAN_TEST_ROOT /usr/bin/sh "$ACTIVE/payload/uninstall-apk.sh" >/dev/null 2>&1 || fail 'guard arm failed'
printf 'instance-new\n' > "$STATE/current.token"
rm -rf "$ACTIVE"
: > "$XPAN_TEST_ROOT/pm.calls"
RC=0; /usr/bin/sh "$GUARDJOB" >/dev/null 2>&1 || RC=$?
expect_rc 0; assert_file "$GUARDJOB"; assert_no_uninstall
assert_file "$XPAN_TEST_ROOT/installed.0"
ok 'boot guard with stale token cannot touch a reinstalled instance'

fixture
: > "$XPAN_TEST_ROOT/multiuser"
printf '1\n' > "$XPAN_TEST_ROOT/installed.10"
printf 'success\n' > "$XPAN_TEST_ROOT/uninstall-mode"
mkdir -p "$ACTIVE/payload"
GUARDJOB=$XPAN_TEST_ROOT/data/adb/service.d/xpan_guard.sh
cp "$SOURCE" "$ACTIVE/payload/uninstall-apk.sh"
GUARD_ID=$(printf 'codePath=/data/app/old/com.xpanport.hook\nfirstInstallTime=2026-11-13 12:00:00\nlastUpdateTime=2026-11-13 12:00:00-old\n' | sha256sum)
printf '%s\n' "${GUARD_ID%% *}" > "$ACTIVE/.xpan-apk-identity"
# OPCameraPro 改动现场：SDK 被换、mirror 素材被换、原厂同名文件被改成 hidden。
OC=$XPAN_TEST_ROOT/data/adb/modules/OPCameraPro
BACK=$XPAN_TEST_ROOT/data/adb/xpan-runtime/op-compat-backup
mkdir -p "$OC/system/product/framework" "$OC/system/odm/etc/camera/meishe_lut" "$BACK/mirror/odm/etc/camera/meishe_lut"
printf 'op-sdk-original\n' > "$BACK/com.oplus.camera.unit.sdk.jar"
printf 'xpan-sdk-c17\n' > "$OC/system/product/framework/com.oplus.camera.unit.sdk.jar"
printf 'op-lut-original\n' > "$BACK/mirror/odm/etc/camera/meishe_lut/a.bin"
printf 'xpan-lut\n' > "$OC/system/odm/etc/camera/meishe_lut/a.bin"
printf 'op-hidden-original\n' > "$OC/system/odm/etc/camera/meishe_lut/b.bin.xpan-c16-hidden"
# 用户后改且无备份的路径必须原样保留。
printf 'user-changed\n' > "$OC/system/odm/etc/camera/meishe_lut/c.bin"
XPAN_ARM=1 XPAN_ARM_LOCKED=1 XPAN_TEST_ROOT=$XPAN_TEST_ROOT /usr/bin/sh "$ACTIVE/payload/uninstall-apk.sh" >/dev/null 2>&1 || fail 'guard arm failed'
rm -rf "$ACTIVE"
RC=0; /usr/bin/sh "$GUARDJOB" >/dev/null 2>&1 || RC=$?
expect_rc 0; assert_no_file "$GUARDJOB"
grep -qx op-sdk-original "$OC/system/product/framework/com.oplus.camera.unit.sdk.jar" || fail 'SDK 未还原'
grep -qx op-lut-original "$OC/system/odm/etc/camera/meishe_lut/a.bin" || fail 'mirror 素材未还原'
grep -qx op-hidden-original "$OC/system/odm/etc/camera/meishe_lut/b.bin" || fail 'hidden 未恢复'
[ ! -e "$OC/system/odm/etc/camera/meishe_lut/b.bin.xpan-c16-hidden" ] || fail 'hidden 残留'
grep -qx user-changed "$OC/system/odm/etc/camera/meishe_lut/c.bin" || fail '覆盖了用户后改文件'
ok 'boot guard restores backed-up OPCameraPro bytes and preserves untracked user edits'

printf '\nAll %s local uninstall scenarios passed. No device commands executed.\n' "$COUNT"
