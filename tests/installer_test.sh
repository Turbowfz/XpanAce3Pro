#!/usr/bin/env bash
# Local protocol tests ONLY: fake PM/dumpsys/getprop, no adb/Android/config mounts.
# Run: bash tests/installer_test.sh. Not real module/config end-to-end validation.
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SOURCE=$SCRIPT_DIR/../module/lifecycle.sh
UNINSTALL=$SCRIPT_DIR/../hook/uninstall.sh
[ ! -e /system/bin/pm ] || { printf 'Refuse to test on Android.\n' >&2; exit 1; }
ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
mkdir -p "$ROOT/_tmp"
SUITE=$(mktemp -d "$ROOT/_tmp/xpan-runtime-test.XXXXXXXX")
: > "$SUITE/.xpan-runtime-fixture"
trap 'rm -rf -- "$SUITE"' EXIT
export XPAN_TEST_MODE=1
unset XPAN_JOB_TOKEN XPAN_JOB_IDENTITY
COUNT=0
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
assert_file() { [ -f "$1" ] || fail "missing $1"; }
assert_no_file() { [ ! -e "$1" ] || fail "unexpected $1"; }
assert_log() { grep -Fq "$1" "$XPAN_TEST_ROOT/install.log" || fail "missing log: $1"; }
assert_no_install() { ! grep -q '^install ' "$XPAN_TEST_ROOT/pm.calls" || fail 'unexpected install'; }
assert_no_uninstall() { ! grep -q '^uninstall ' "$XPAN_TEST_ROOT/pm.calls" || fail 'unexpected uninstall'; }
expect_rc() {
    if [ "$RC" != "$1" ]; then
        printf '%s\n' "$(<"$XPAN_TEST_ROOT/stdout")" >&2
        [ ! -f "$XPAN_TEST_ROOT/install.log" ] || printf '%s\n' "$(<"$XPAN_TEST_ROOT/install.log")" >&2
        fail "expected rc=$1, got $RC"
    fi
}
hash() { local out; out=$(sha256sum "$1"); printf '%s' "${out%% *}"; }
next_uuid() { printf '12345678-1234-4234-8234-%012d\n' "$1" > "$XPAN_TEST_ROOT/uuid.source"; }
receipt() {
    local destination=$1 name role
    {
        printf 'qualification_version=1\nstatus=qualified\nmodel=PJX110\n'
        printf 'camera_version=%s\n' "$(<"$XPAN_TEST_ROOT/camera-version")"
        printf 'build_fingerprint=fixture/ROM:exact\napk_package=com.xpanport.hook\n'
        printf 'apk_sha256=%s\n' "$(hash "$destination/payload/XpanHook.apk")"
        for role in unit feature vendor; do
            case "$role" in
                unit) name=camera_unit_config ;;
                feature) name=camera_unit_feature_config.protobuf ;;
                vendor) name=oplus_camera_config ;;
            esac
            printf 'original_%s_sha256=%s\n' "$role" "$(hash "$XPAN_TEST_ROOT/stock/$name")"
            printf 'output_%s_sha256=%s\n' "$role" "$(hash "$destination/payload/qualified-config/$name")"
        done
    } > "$destination/payload/lifecycle.inputs"
}
fixture() {
    COUNT=$((COUNT+1))
    export XPAN_TEST_ROOT=$SUITE/$COUNT
    ACTIVE=$XPAN_TEST_ROOT/data/adb/modules/XpanAce3Pro
    STAGED=$XPAN_TEST_ROOT/data/adb/modules_update/XpanAce3Pro
    STATE=$XPAN_TEST_ROOT/data/adb/xpan-lifecycle
    JOB=$XPAN_TEST_ROOT/data/adb/service.d/xpan_cleanup.sh
    MODPATH=$ACTIVE
    mkdir -p "$XPAN_TEST_ROOT/bin" "$ACTIVE/payload/qualified-config" "$STATE" \
        "$XPAN_TEST_ROOT/data/adb/service.d" "$XPAN_TEST_ROOT/stock" \
        "$XPAN_TEST_ROOT/data/app/old/com.xpanport.hook" \
        "$XPAN_TEST_ROOT/protected/data/adb/modules/other" \
        "$XPAN_TEST_ROOT/protected/lsposed" "$XPAN_TEST_ROOT/protected/camera" \
        "$XPAN_TEST_ROOT/protected/photos"
    : > "$XPAN_TEST_ROOT/.xpan-uninstall-fixture"
    : > "$XPAN_TEST_ROOT/.xpan-installer-fixture"
    : > "$XPAN_TEST_ROOT/pm.calls"
    printf 'id=XpanAce3Pro\nversion=v1.3\nversionCode=13\n' > "$ACTIVE/module.prop"
    printf 'old-instance\n' > "$ACTIVE/.xpan-instance"
    printf 'old-instance\n' > "$STATE/current.token"
    printf 'fixture hook APK, not real dex\n' > "$ACTIVE/payload/XpanHook.apk"
    cp "$ACTIVE/payload/XpanHook.apk" "$XPAN_TEST_ROOT/data/app/old/com.xpanport.hook/base.apk"
    printf 'old\n' > "$XPAN_TEST_ROOT/generation"
    printf 'ready\n' > "$XPAN_TEST_ROOT/pm-mode"
    printf 'success\n' > "$XPAN_TEST_ROOT/install-mode"
    printf 'success\n' > "$XPAN_TEST_ROOT/uninstall-mode"
    printf '1\n' > "$XPAN_TEST_ROOT/installed.0"
    printf 'PJX110\n' > "$XPAN_TEST_ROOT/model"
    printf '6.020.876\n' > "$XPAN_TEST_ROOT/camera-version"
    printf 'fixture/ROM:exact\n' > "$XPAN_TEST_ROOT/fingerprint"
    next_uuid 1
    cp "$UNINSTALL" "$ACTIVE/uninstall.sh"
    for name in camera_unit_config camera_unit_feature_config.protobuf oplus_camera_config; do
        printf 'fake original %s\n' "$name" > "$XPAN_TEST_ROOT/stock/$name"
        printf 'fake qualified output %s\n' "$name" > "$ACTIVE/payload/qualified-config/$name"
    done
    receipt "$ACTIVE"
    for name in lsposed/modules_config.db camera/preferences.xml camera/oc_active.enc \
        photos/photo.jpg data/adb/modules/other/mount-source apk-settings; do
        printf 'must-survive\n' > "$XPAN_TEST_ROOT/protected/$name"
    done
    cat > "$XPAN_TEST_ROOT/bin/id" <<'FAKE'
#!/bin/sh
printf '0\n'
FAKE
    cat > "$XPAN_TEST_ROOT/bin/getprop" <<'FAKE'
#!/bin/sh
case "$1" in
    ro.product.model) cat "$XPAN_TEST_ROOT/model" ;;
    ro.build.fingerprint) cat "$XPAN_TEST_ROOT/fingerprint" ;;
    *) exit 85 ;;
esac
FAKE
    cat > "$XPAN_TEST_ROOT/bin/pm" <<'FAKE'
#!/bin/sh
R=$XPAN_TEST_ROOT
printf '%s\n' "$*" >> "$R/pm.calls"
IFS= read -r MODE < "$R/pm-mode"
case "$MODE" in
    unavailable) printf 'Error: package service not found\n'; exit 1 ;;
    hung)
        printf '%s\n' "$(date +%s)" > "$R/hung.started"
        sleep 10
        printf '%s\n' natural-exit > "$R/hung.natural"
        exit 1
        ;;
    empty-framework) [ "$*" != 'path android' ] || exit 0 ;;
esac
case "$*" in
    'path android') printf 'package:/system/framework/framework-res.apk\n' ;;
    'list users')
        [ "$MODE" != users-failure ] || { printf 'Error: users unavailable\n'; exit 1; }
        [ "$MODE" != bad-users ] || { printf 'Users:\nUserInfo{not-id:Owner:13}\n'; exit 0; }
        printf 'Users:\n\tUserInfo{0:Owner:13} running\n'
        if [ -f "$R/multiuser" ]; then printf '\tUserInfo{10:Work:30} running\n'; fi
        ;;
    'list packages --user '*" com.xpanport.hook")
        [ "$MODE" != target-failure ] || { printf 'Error: query failed\n'; exit 1; }
        [ "$MODE" != target-text-error ] || { printf 'Error: query failed\n'; exit 0; }
        [ "$MODE" != secondary-error ] || [ "$4" != 10 ] || { printf 'Error: work profile unavailable\n'; exit 1; }
        if [ -f "$R/installed.$4" ]; then printf 'package:com.xpanport.hook\n'; fi
        ;;
    'path --user '*" com.xpanport.hook")
        IFS= read -r GEN < "$R/generation"
        if [ -f "$R/path-mismatch" ]; then GEN=wrong; fi
        printf 'package:/data/app/%s/com.xpanport.hook/base.apk\n' "$GEN"
        if [ -f "$R/split-path" ]; then printf 'package:/data/app/%s/com.xpanport.hook/split.apk\n' "$GEN"; fi
        ;;
    'install -r '*)
        APK=$3
        M=${APK%/payload/XpanHook.apk}
        [ -d "$R/data/adb/xpan-lifecycle/lock" ] || exit 86
        IFS= read -r CURRENT < "$R/data/adb/xpan-lifecycle/current.token"
        IFS= read -r INSTANCE < "$M/.xpan-instance"
        IFS= read -r UUID < "$R/uuid.source"
        [ "$CURRENT" = "$INSTANCE" ] && [ "$INSTANCE" = "$UUID" ] || exit 86
        [ ! -e "$M/.xpan-apk-identity" ] || exit 86
        printf 'lock+fresh-token-before-pm\n' >> "$R/protocol.calls"
        IFS= read -r ACTION < "$R/install-mode"
        case "$ACTION" in
            failure) printf 'Failure [INSTALL_FAILED_INTERNAL_ERROR]\n'; exit 1 ;;
            wrong-key)
                if [ ! -f "$R/signature-old-removed" ]; then
                    printf 'PackageInstaller: updating com.xpanport.hook\n' >&2
                    printf 'Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: signatures do not match]\n'; exit 1
                fi ;;
            retry-failure)
                if [ ! -f "$R/signature-old-removed" ]; then
                    printf 'Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: signatures do not match]\n'
                else printf 'Failure [INSTALL_FAILED_INTERNAL_ERROR: retry failed]\n'; fi
                exit 1 ;;
            mismatch-text) printf 'Error: INSTALL_FAILED_UPDATE_INCOMPATIBLE appeared in diagnostic text\n'; exit 1 ;;
            full-failure) printf 'Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE]\nSuccess\n'; exit 0 ;;
            misleading) printf 'Unsuccessful: not Success\n'; exit 0 ;;
            nonzero-success) printf 'Success\n'; exit 1 ;;
            hung-install)
                printf '%s\n' "$(date +%s)" > "$R/hung.started"
                sleep 10
                printf '%s\n' natural-exit > "$R/hung.natural"
                exit 1
                ;;
        esac
        N=0
        [ ! -f "$R/install-count" ] || IFS= read -r N < "$R/install-count"
        N=$((N+1))
        printf '%s\n' "$N" > "$R/install-count"
        GEN=new-$N
        printf '%s\n' "$GEN" > "$R/generation"
        mkdir -p "$R/data/app/$GEN/com.xpanport.hook"
        cp "$APK" "$R/data/app/$GEN/com.xpanport.hook/base.apk"
        if [ "$ACTION" = installed-hash ]; then printf 'different bytes\n' >> "$R/data/app/$GEN/com.xpanport.hook/base.apk"; fi
        if [ "$ACTION" = source-changed ]; then printf 'changed source\n' >> "$APK"; fi
        if [ "$ACTION" = output-changed ]; then printf 'changed output\n' >> "$M/payload/qualified-config/camera_unit_config"; fi
        if [ "$ACTION" = profile-changed ]; then printf 'changed/ROM\n' > "$R/fingerprint"; fi
        if [ "$ACTION" = success-but-absent ]; then
            rm -f "$R/installed.0" "$R/installed.10"
            : > "$R/uninstalled-only"
        else
            [ -f "$R/keep-user-selection" ] || printf '1\n' > "$R/installed.0"
        fi
        printf 'Success\n'
        ;;
    'uninstall com.xpanport.hook')
        [ -d "$R/data/adb/xpan-lifecycle/lock" ] || exit 86
        IFS= read -r CURRENT < "$R/data/adb/xpan-lifecycle/current.token"
        B=$R/data/adb/xpan-lifecycle/apk-backup/$CURRENT
        [ -f "$B/base.apk" ] && [ -f "$B/identity" ] && [ -f "$B/receipt" ] || exit 86
        IFS= read -r GEN < "$R/generation"
        cmp -s "$B/base.apk" "$R/data/app/$GEN/com.xpanport.hook/base.apk" || exit 86
        printf 'complete-backup-before-uninstall\n' >> "$R/protocol.calls"
        IFS= read -r ACTION < "$R/uninstall-mode"
        if [ "$ACTION" = failure ]; then printf 'Failure [DELETE_FAILED_INTERNAL_ERROR]\n'; exit 1; fi
        rm -f "$R/installed.0" "$R/installed.10"
        : > "$R/signature-old-removed"
        printf 'Success\n'
        ;;
    'uninstall -k com.xpanport.hook')
        IFS= read -r ACTION < "$R/uninstall-mode"
        if [ "$ACTION" = failure ]; then printf 'Failure [DELETE_FAILED_INTERNAL_ERROR]\n'; exit 1; fi
        rm -f "$R/installed.0" "$R/installed.10"
        printf 'Success\n'
        ;;
    *) printf 'Unexpected fake pm invocation\n' >&2; exit 85 ;;
esac
FAKE
    cat > "$XPAN_TEST_ROOT/bin/dumpsys" <<'FAKE'
#!/bin/sh
R=$XPAN_TEST_ROOT
case "$*" in
    'package com.oplus.camera')
        printf '  Package [com.oplus.camera] (1234):\n'
        printf '    versionName='; cat "$R/camera-version"
        if [ -f "$R/duplicate-camera" ]; then printf '    versionName=7.013.51\n'; fi
        ;;
    'package com.xpanport.hook')
        [ ! -f "$R/identity-failure" ] || { printf 'identity unavailable\n'; exit 1; }
        IFS= read -r GEN < "$R/generation"
        N=0
        [ ! -f "$R/identity-count" ] || IFS= read -r N < "$R/identity-count"
        N=$((N+1)); printf '%s\n' "$N" > "$R/identity-count"
        if [ -f "$R/identity-race" ] && [ "$N" -gt 1 ]; then GEN=raced; fi
        if [ -f "$R/wrong-package" ]; then
            printf '  Package [com.unrelated.app] (1234):\n'
        else
            printf '  Package [com.xpanport.hook] (1234):\n'
        fi
        printf '    codePath=/data/app/%s/com.xpanport.hook\n' "$GEN"
        printf '    firstInstallTime=2026-11-13 12:00:00\n'
        printf '    lastUpdateTime=2026-11-13 12:00:00-%s\n' "$GEN"
        if [ -f "$R/duplicate-identity" ]; then printf '    codePath=/data/app/duplicate\n'; fi
        ;;
    *) exit 85 ;;
esac
FAKE
    for forbidden in adb am mount umount sqlite3 app_process apksigner keytool; do
        cat > "$XPAN_TEST_ROOT/bin/$forbidden" <<'FAKE'
#!/bin/sh
printf '%s %s\n' "$0" "$*" >> "$XPAN_TEST_ROOT/forbidden.calls"
exit 85
FAKE
    done
    chmod +x "$XPAN_TEST_ROOT/bin/"*
}
# Isolate each script command in a subshell so its EXIT trap cannot affect the suite.
run_install() {
    RC=0
    ( /usr/bin/sh "$SOURCE" install "$MODPATH" "$XPAN_TEST_ROOT/uuid.source" ) > "$XPAN_TEST_ROOT/stdout" 2>&1 || RC=$?
}
run_hook() {
    RC=0
    /usr/bin/sh "$ACTIVE/uninstall.sh" > "$XPAN_TEST_ROOT/stdout" 2>&1 || RC=$?
}
run_job() {
    RC=0
    /usr/bin/sh "$1" > "$XPAN_TEST_ROOT/stdout" 2>&1 || RC=$?
}
verified() {
    assert_file "$MODPATH/.xpan-apk-identity"
    local actual normalized
    normalized=$("$XPAN_TEST_ROOT/bin/dumpsys" package com.xpanport.hook | \
        grep -E '^    (codePath|firstInstallTime|lastUpdateTime)=' | cut -c 5- | sha256sum)
    actual=$(<"$MODPATH/.xpan-apk-identity")
    [ "$actual" = "${normalized%% *}" ] || fail 'identity differs from uninstall normalization'
    [ "$(<"$STATE/current.token")" = "$(<"$MODPATH/.xpan-instance")" ] || fail 'token mismatch'
    assert_no_file "$STATE/lock"
    grep -Fq 'lock+fresh-token-before-pm' "$XPAN_TEST_ROOT/protocol.calls" || fail 'PM protocol missing'
    [ "${1:-}" = migrated ] || assert_no_uninstall
}
prepare_job() {
    printf 'failure\n' > "$XPAN_TEST_ROOT/uninstall-mode"
    run_hook; expect_rc 1; assert_file "$JOB"
    cp "$JOB" "$XPAN_TEST_ROOT/old-worker.sh"
    : > "$XPAN_TEST_ROOT/pm.calls"
}
stage() {
    mkdir -p "${STAGED%/*}"
    cp -R "$ACTIVE" "$STAGED"
    rm -f "$STAGED/remove" "$STAGED/disable"
    MODPATH=$STAGED
}
keep_sentinels() {
    assert_no_file "$XPAN_TEST_ROOT/forbidden.calls"
    assert_no_file "$STATE/lock"
    for name in lsposed/modules_config.db camera/preferences.xml camera/oc_active.enc \
        photos/photo.jpg data/adb/modules/other/mount-source apk-settings; do
        grep -qx must-survive "$XPAN_TEST_ROOT/protected/$name" || fail "changed protected file: $name"
    done
}
ok() { keep_sentinels; printf 'PASS: %s\n' "$1"; }
unchanged_token() { [ "$(<"$STATE/current.token")" = old-instance ] || fail 'published token before valid preflight'; }

/usr/bin/sh -n "$SOURCE"
fixture
rm -f "$ACTIVE/.xpan-instance" "$STATE/current.token"
run_install; expect_rc 0; verified
next_uuid 2
run_install; expect_rc 0; verified
[ "$(grep -c '^install -r ' "$XPAN_TEST_ROOT/pm.calls")" = 2 ] || fail 'second install not executed'
run_hook; expect_rc 0; assert_no_file "$JOB"; assert_no_file "$XPAN_TEST_ROOT/installed.0"
ok 'legacy first install then second install rotates and uninstall consumes new identity'

fixture
prepare_job
run_install; expect_rc 0; verified; assert_no_file "$JOB"
: > "$XPAN_TEST_ROOT/pm.calls"
run_job "$XPAN_TEST_ROOT/old-worker.sh"; expect_rc 0; assert_no_uninstall; assert_file "$XPAN_TEST_ROOT/installed.0"
[ ! -s "$XPAN_TEST_ROOT/pm.calls" ] || fail 'stale worker reached PM'
ok 'matching old cleanup cancelled; old worker cannot uninstall reinstalled APK'

fixture
prepare_job
printf "#!/bin/sh\nXPAN_JOB_TOKEN='newer-job'\n" > "$JOB"
run_install; expect_rc 0; verified; assert_file "$JOB"
: > "$XPAN_TEST_ROOT/pm.calls"
run_job "$XPAN_TEST_ROOT/old-worker.sh"; expect_rc 0
assert_file "$JOB"; grep -q newer-job "$JOB" || fail 'deleted another token job'
assert_no_uninstall
ok 'installer and stale worker both retain another token replacement job'

fixture
printf '# unrelated same-name job\n' > "$JOB"
BEFORE=$(hash "$JOB")
run_install; expect_rc 0; verified
[ "$(hash "$JOB")" = "$BEFORE" ] || fail 'deleted unrelated same-name job'
ok 'unrelated same-name service job is preserved'

fixture
prepare_job
stage
run_install; expect_rc 0; verified; assert_no_file "$JOB"
: > "$XPAN_TEST_ROOT/pm.calls"
run_job "$XPAN_TEST_ROOT/old-worker.sh"; expect_rc 0; assert_no_uninstall
assert_file "$STAGED/.xpan-apk-identity"
[ "$(<"$ACTIVE/.xpan-instance")" = old-instance ] || fail 'changed active instance during staging'
# Model a newer staged worker occupying the shared path. The old worker must
# retain it even though STAGED causes old ownership cancellation.
printf "#!/bin/sh\nXPAN_JOB_TOKEN='%s'\n" "$(<"$STAGED/.xpan-instance")" > "$JOB"
run_job "$XPAN_TEST_ROOT/old-worker.sh"; expect_rc 0; assert_file "$JOB"; assert_no_uninstall
ok 'qualified staged install cancels only old job; staged token job retained'

for mode in failure full-failure misleading nonzero-success mismatch-text; do
    fixture
    prepare_job
    printf '%064d\n' 0 > "$ACTIVE/.xpan-apk-identity"
    printf '%s\n' "$mode" > "$XPAN_TEST_ROOT/install-mode"
    run_install; expect_rc 1; assert_no_uninstall
    assert_no_file "$JOB"; assert_no_file "$ACTIVE/.xpan-apk-identity"
    assert_file "$XPAN_TEST_ROOT/installed.0"
    [ "$(<"$STATE/current.token")" = "$(<"$XPAN_TEST_ROOT/uuid.source")" ] || fail 'failed install rolled token back'
    assert_log 'cancellation token retained'
    if [ "$mode" = wrong-key ] || [ "$mode" = full-failure ]; then assert_log 'INSTALL_FAILED_UPDATE_INCOMPATIBLE'; fi
    : > "$XPAN_TEST_ROOT/pm.calls"
    run_job "$XPAN_TEST_ROOT/old-worker.sh"; expect_rc 0; assert_no_uninstall
    ok "install $mode fails fully, preserves old APK/data, retains cancellation token"
done

fixture
prepare_job
printf 'success\n' > "$XPAN_TEST_ROOT/uninstall-mode"
printf 'old APK signed by prior certificate\n' > "$XPAN_TEST_ROOT/data/app/old/com.xpanport.hook/base.apk"
OLD_APK=$(hash "$XPAN_TEST_ROOT/data/app/old/com.xpanport.hook/base.apk")
printf 'wrong-key\n' > "$XPAN_TEST_ROOT/install-mode"
run_install; expect_rc 0; verified migrated; assert_no_file "$JOB"
BACKUP=$STATE/apk-backup/$(<"$XPAN_TEST_ROOT/uuid.source")
assert_file "$BACKUP/base.apk"; assert_file "$BACKUP/identity"; assert_file "$BACKUP/receipt"
[ "$(hash "$BACKUP/base.apk")" = "$OLD_APK" ] || fail 'cross-signature backup bytes changed'
[ "$(grep -c '^uninstall com.xpanport.hook$' "$XPAN_TEST_ROOT/pm.calls")" = 1 ] || fail 'cross-signature migration did not uninstall exactly once'
[ "$(grep -c '^install -r ' "$XPAN_TEST_ROOT/pm.calls")" = 2 ] || fail 'cross-signature migration did not retry exactly once'
assert_log 'PackageInstaller: updating com.xpanport.hook'
assert_log 'INSTALL_FAILED_UPDATE_INCOMPATIBLE'
grep -Fq complete-backup-before-uninstall "$XPAN_TEST_ROOT/protocol.calls" || fail 'old APK removed before complete backup'
: > "$XPAN_TEST_ROOT/pm.calls"
run_job "$XPAN_TEST_ROOT/old-worker.sh"; expect_rc 0; assert_no_uninstall
assert_file "$XPAN_TEST_ROOT/installed.0"
ok 'real cross-signature failure backs up only old Hook APK, migrates and protects new instance'

for fault in retry-failure remove-failure missing-apk split-path identity-race backup-blocked; do
    fixture
    printf 'wrong-key\n' > "$XPAN_TEST_ROOT/install-mode"
    case "$fault" in
        retry-failure) printf 'retry-failure\n' > "$XPAN_TEST_ROOT/install-mode" ;;
        remove-failure) printf 'failure\n' > "$XPAN_TEST_ROOT/uninstall-mode" ;;
        missing-apk) rm "$XPAN_TEST_ROOT/data/app/old/com.xpanport.hook/base.apk" ;;
        split-path|identity-race) : > "$XPAN_TEST_ROOT/$fault" ;;
        backup-blocked) printf 'occupied backup path\n' > "$STATE/apk-backup" ;;
    esac
    run_install; expect_rc 1; assert_no_file "$ACTIVE/.xpan-apk-identity"
    case "$fault" in
        retry-failure|remove-failure)
            assert_file "$STATE/apk-backup/$(<"$XPAN_TEST_ROOT/uuid.source")/base.apk"
            assert_log '备份保留'
            ;;
        *) assert_no_uninstall; assert_file "$XPAN_TEST_ROOT/installed.0" ;;
    esac
    [ "$(<"$STATE/current.token")" = "$(<"$XPAN_TEST_ROOT/uuid.source")" ] || fail 'migration failure lost cancellation token'
    ok "cross-signature $fault preserves backup or old APK and does not publish new identity"
done

for fault in missing-receipt missing-output wrong-output wrong-original pending unknown-key duplicate-key; do
    fixture
    case "$fault" in
        missing-receipt) rm "$ACTIVE/payload/lifecycle.inputs" ;;
        missing-output) rm "$ACTIVE/payload/qualified-config/oplus_camera_config" ;;
        wrong-output) printf 'changed\n' >> "$ACTIVE/payload/qualified-config/camera_unit_config" ;;
        wrong-original) printf 'changed\n' >> "$XPAN_TEST_ROOT/stock/camera_unit_feature_config.protobuf" ;;
        pending) printf 'status=pending\n' >> "$ACTIVE/payload/lifecycle.inputs" ;;
        unknown-key) printf 'command=must-not-execute\n' >> "$ACTIVE/payload/lifecycle.inputs" ;;
        duplicate-key) printf 'camera_version=6.020.876\n' >> "$ACTIVE/payload/lifecycle.inputs" ;;
    esac
    run_install; expect_rc 1; assert_no_install; unchanged_token
    assert_no_file "$ACTIVE/.xpan-apk-identity"; assert_log 'config generation not ready'
    ok "$fault aborts before token publication or PM install"
done

for fault in model camera-near-match camera-unknown ROM duplicate-camera; do
    fixture
    case "$fault" in
        model) printf 'CPH2667\n' > "$XPAN_TEST_ROOT/model" ;;
        camera-near-match) printf '6.020.876.1\n' > "$XPAN_TEST_ROOT/camera-version" ;;
        camera-unknown) printf '7.013.52\n' > "$XPAN_TEST_ROOT/camera-version" ;;
        ROM) printf 'updated/ROM\n' > "$XPAN_TEST_ROOT/fingerprint" ;;
        duplicate-camera) : > "$XPAN_TEST_ROOT/duplicate-camera" ;;
    esac
    run_install; expect_rc 1; assert_no_install; unchanged_token
    ok "real $fault change rejects qualified inputs, no old/static mounts"
done

fixture
printf '7.013.51\n' > "$XPAN_TEST_ROOT/camera-version"
receipt "$ACTIVE"
run_install; expect_rc 0; verified
ok 'exact C17 profile allowed only with explicit synthetic qualified receipt, no generation claim'

for fault in installed-hash source-changed output-changed profile-changed success-but-absent; do
    fixture
    printf '%s\n' "$fault" > "$XPAN_TEST_ROOT/install-mode"
    run_install; expect_rc 1; assert_no_uninstall; assert_no_file "$ACTIVE/.xpan-apk-identity"
    assert_log 'post-install verification failed'
    ok "PM Success with $fault does not publish APK identity/readiness"
done

for fault in path-mismatch split-path identity-failure duplicate-identity wrong-package identity-race; do
    fixture
    : > "$XPAN_TEST_ROOT/$fault"
    run_install; expect_rc 1; assert_no_file "$ACTIVE/.xpan-apk-identity"; assert_no_uninstall
    ok "post-install $fault rejected without identity publication"
done

for mode in empty-framework users-failure bad-users; do
    fixture
    printf '%s\n' "$mode" > "$XPAN_TEST_ROOT/pm-mode"
    run_install; expect_rc 1; unchanged_token; assert_no_install
    ok "PM $mode preflight failure cannot rotate token/install"
done

for mode in target-failure target-text-error secondary-error; do
    fixture
    : > "$XPAN_TEST_ROOT/multiuser"
    printf '%s\n' "$mode" > "$XPAN_TEST_ROOT/pm-mode"
    run_install; expect_rc 1; assert_no_file "$ACTIVE/.xpan-apk-identity"; assert_no_uninstall
    ok "PM $mode cannot falsely verify installed package across users"
done

fixture
: > "$XPAN_TEST_ROOT/multiuser"
: > "$XPAN_TEST_ROOT/keep-user-selection"
rm "$XPAN_TEST_ROOT/installed.0"
printf '1\n' > "$XPAN_TEST_ROOT/installed.10"
run_install; expect_rc 0; verified
assert_no_file "$XPAN_TEST_ROOT/installed.0"
grep -q '^path --user 10 com.xpanport.hook$' "$XPAN_TEST_ROOT/pm.calls" || fail 'work profile path not checked'
ok 'actual secondary-only install verified without restoring owner user selection'

fixture
mkdir "$STATE/lock"
run_install; expect_rc 1; assert_no_install; unchanged_token
rmdir "$STATE/lock"
ok 'shared live uninstall lock is not stolen'

for fault in no-uuid bad-uuid same-uuid; do
    fixture
    case "$fault" in
        no-uuid) rm "$XPAN_TEST_ROOT/uuid.source" ;;
        bad-uuid) printf 'date-or-pid-token\n' > "$XPAN_TEST_ROOT/uuid.source" ;;
        same-uuid) cp "$XPAN_TEST_ROOT/uuid.source" "$STATE/current.token" ;;
    esac
    BEFORE=$(hash "$STATE/current.token")
    run_install; expect_rc 1; assert_no_install
    [ "$(hash "$STATE/current.token")" = "$BEFORE" ] || fail 'token changed without fresh UUID'
    ok "$fault rejects rotation with no time/PID fallback"
done

fixture
prepare_job
printf 'hung-install\n' > "$XPAN_TEST_ROOT/install-mode"
START=$SECONDS
run_install; expect_rc 1; assert_no_file "$ACTIVE/.xpan-apk-identity"; assert_no_uninstall
# Measure the hung PM command itself, not Windows shell/preflight process startup.
assert_file "$XPAN_TEST_ROOT/hung.started"
[ "$(($(date +%s)-$(<"$XPAN_TEST_ROOT/hung.started")))" -lt 8 ] || {
    printf '%s\n' "$(<"$XPAN_TEST_ROOT/stdout")" "$(<"$XPAN_TEST_ROOT/install.log")" >&2
    fail 'install timeout not bounded'
}
assert_no_file "$XPAN_TEST_ROOT/hung.natural"
assert_no_file "$JOB"; assert_log 'install rc=137'
ok 'hung PM install bounded and failure diagnostics retained'

fixture
printf 'hung\n' > "$XPAN_TEST_ROOT/pm-mode"
START=$SECONDS
run_install; expect_rc 1; unchanged_token; assert_no_install
assert_file "$XPAN_TEST_ROOT/hung.started"
[ "$(($(date +%s)-$(<"$XPAN_TEST_ROOT/hung.started")))" -lt 8 ] || fail 'preflight timeout not bounded'
assert_no_file "$XPAN_TEST_ROOT/hung.natural"
ok 'hung framework query fails closed before token rotation'

for marker in disable remove; do
    fixture
    touch "$ACTIVE/$marker"
    run_install; expect_rc 1; assert_no_install; unchanged_token
    ok "$marker marker preserves user choice"
done

fixture
stage
MODPATH=$ACTIVE
run_install; expect_rc 1; assert_no_install; unchanged_token
ok 'active install cannot bypass a staged replacement'

fixture
RC=0
XPAN_TEST_ROOT=/tmp/untrusted-fixture /usr/bin/sh "$SOURCE" install "$ACTIVE" > /dev/null 2>&1 || RC=$?
expect_rc 1; assert_no_install
ok 'unmarked/outside fixture root rejected'

fixture
# Import only the exact inert legacy fixture bytes from the existing test source.
IN_LEGACY=0
while IFS= read -r LINE; do
    if [ "$IN_LEGACY" = 1 ]; then
        [ "$LINE" != LEGACY ] || break
        printf '%s\n' "$LINE" >> "$JOB"
    elif [[ "$LINE" == *"cat >"*"<<'LEGACY'"* ]]; then
        IN_LEGACY=1
    fi
done < "$SCRIPT_DIR/uninstall_test.sh"
[ "$(hash "$JOB")" = 0261785d42aadaf66423999f8478f409202b86046df4848bb03eb45fb0dd1952 ] || fail 'legacy fixture hash changed'
run_install; expect_rc 0; verified; assert_no_file "$JOB"
assert_file "$STATE/legacy-cleanup.$(<"$XPAN_TEST_ROOT/uuid.source")"
[ "$(hash "$STATE/legacy-cleanup.$(<"$XPAN_TEST_ROOT/uuid.source")")" = 0261785d42aadaf66423999f8478f409202b86046df4848bb03eb45fb0dd1952 ] || fail 'legacy archive bytes changed'
assert_log '旧无锁清理任务已结束'
ok 'exact lockless legacy cleanup is archived automatically without manual approval'

fixture
IN_LEGACY=0
while IFS= read -r LINE; do
    if [ "$IN_LEGACY" = 1 ]; then
        [ "$LINE" != LEGACY ] || break
        printf '%s\n' "$LINE" >> "$JOB"
    elif [[ "$LINE" == *"cat >"*"<<'LEGACY'"* ]]; then
        IN_LEGACY=1
    fi
done < "$SCRIPT_DIR/uninstall_test.sh"
mkdir -p "$XPAN_TEST_ROOT/proc/123"
printf '/system/bin/sh\000%s\000' "$JOB" > "$XPAN_TEST_ROOT/proc/123/cmdline"
printf '#!/bin/sh\nrm -f "$XPAN_TEST_ROOT/proc/123/cmdline"\nprintf "legacy-worker-finished\\n" >> "$XPAN_TEST_ROOT/protocol.calls"\n' > "$XPAN_TEST_ROOT/bin/sleep"
chmod +x "$XPAN_TEST_ROOT/bin/sleep"
run_install; expect_rc 0; verified; assert_no_file "$JOB"
grep -Fq legacy-worker-finished "$XPAN_TEST_ROOT/protocol.calls" || fail 'did not wait for running legacy copy'
ok 'automatic migration waits for already-running legacy cleanup instead of killing app processes'

fixture
IN_LEGACY=0
while IFS= read -r LINE; do
    if [ "$IN_LEGACY" = 1 ]; then
        [ "$LINE" != LEGACY ] || break
        printf '%s\n' "$LINE" >> "$JOB"
    elif [[ "$LINE" == *"cat >"*"<<'LEGACY'"* ]]; then
        IN_LEGACY=1
    fi
done < "$SCRIPT_DIR/uninstall_test.sh"
mkdir -p "$XPAN_TEST_ROOT/proc/123"
printf '/system/bin/sh\000%s\000' "$JOB" > "$XPAN_TEST_ROOT/proc/123/cmdline"
run_install; expect_rc 1; assert_no_install; unchanged_token; assert_no_file "$JOB"
assert_file "$STATE/legacy-cleanup.$(<"$XPAN_TEST_ROOT/uuid.source")"
run_install; expect_rc 1; assert_no_install; unchanged_token
rm "$XPAN_TEST_ROOT/proc/123/cmdline"
run_install; expect_rc 0; verified
ok 'retries cannot bypass an archived legacy worker still running; installation resumes after exit'

fixture
# Cross-protocol test of early-boot uninstall using identity published by helper.
run_install; expect_rc 0; verified
printf 'unavailable\n' > "$XPAN_TEST_ROOT/pm-mode"
run_hook; expect_rc 1; assert_file "$JOB"
rm -rf "$ACTIVE"
printf 'ready\n' > "$XPAN_TEST_ROOT/pm-mode"
run_job "$JOB"; expect_rc 0; assert_no_file "$JOB"; assert_no_file "$XPAN_TEST_ROOT/installed.0"
ok 'helper identity consumed by unchanged uninstall deferred early-boot protocol'

printf '\nAll %s local installer/shared-ownership scenarios passed. No device commands executed.\n' "$COUNT"
