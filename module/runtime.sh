#!/system/bin/sh
# Explicit bind-mount runtime; SKIPMOUNT is mandatory in every entry point.
# Never source profile/receipt/state data. Never replace framework or camera DBs.
# Gate protocol: invalidate active FIRST; publish profile/token, then active=1 LAST.
# The token is lifecycle's UUID, not a readiness attestation by itself.
SKIPMOUNT=true
export SKIPMOUNT
# Private process identity: never accept a caller-supplied worker token.
X_WORKER_TOKEN=

xpan_init() {
    X_BASE=/data/adb
    X_STOCK=/odm/etc/camera/config
    X_PARENT_POINT=/odm/etc
    X_PARTITION_POINT=/odm
    X_PARENT_HASH=
    X_PARENT_INODE=
    X_PARENT_KIND=
    X_PARENT_SEALED_HASH=
    X_PARENT_SEALED_INODE=
    X_PARENT_SEALED_KIND=
    X_PARENT_V2=0
    X_PARENT_PREVIOUS=
    X_PARENT_RECOVERING=0
    X_PARENT_INSTANCE=0
    X_MEMORY_PARENT_HASH=
    X_MEMORY_PARENT_INODE=
    X_INFO=/proc/self/mountinfo
    X_BOOT=/proc/sys/kernel/random/boot_id
    X_NS=/proc/self/ns/mnt
    X_SECONDS=15
    X_DELAY=3
    if [ "${XPAN_TEST_MODE:-}" = 1 ]; then
        [ ! -e /system/bin/pm ] || return 1
        case "${XPAN_TEST_ROOT:-}" in
            /tmp/xpan-uninstall-test.*|/var/tmp/xpan-uninstall-test.*|*/_tmp/xpan-runtime-test.*) ;;
            *) return 1 ;;
        esac
        case "$XPAN_TEST_ROOT" in *..*|*\'*|*'
'*) return 1 ;; esac
        [ -f "$XPAN_TEST_ROOT/.xpan-runtime-fixture" ] &&
            [ ! -L "$XPAN_TEST_ROOT/.xpan-runtime-fixture" ] || return 1
        X_BASE=$XPAN_TEST_ROOT/data/adb
        X_STOCK=$XPAN_TEST_ROOT/stock
        X_PARENT_POINT=$XPAN_TEST_ROOT
        X_PARTITION_POINT=/
        X_INFO=$XPAN_TEST_ROOT/mountinfo
        X_BOOT=$XPAN_TEST_ROOT/boot-id
        X_NS=$XPAN_TEST_ROOT/ns
        X_SECONDS=2
        X_DELAY=0
        PATH=$XPAN_TEST_ROOT/bin:/usr/bin:/bin
    else
        [ -z "${XPAN_TEST_ROOT:-}" ] || return 1
        PATH=/system/bin:/system/xbin:/data/adb/magisk:/data/adb/ksu/bin
    fi
    export PATH
    umask 077
    X_ACTIVE=$X_BASE/modules/XpanAce3Pro
    X_STAGED=$X_BASE/modules_update/XpanAce3Pro
    X_STATE=$X_BASE/xpan-runtime
    X_LIFE=$X_BASE/xpan-lifecycle
    X_LOCKED=0
    X_LIFE_LOCKED=0
    X_NEW=
    X_MEMORY_ROLE=
    X_MEMORY_BOOT=
    X_MEMORY_NS=
    X_MOD=$2
    case "$1:$X_MOD" in
        install:"$X_ACTIVE"|install:"$X_STAGED"|tick:"$X_ACTIVE"|boot:"$X_ACTIVE"|stop:"$X_ACTIVE"|service:"$X_ACTIVE"|uninstall:"$X_ACTIVE") ;;
        *) return 1 ;;
    esac
    [ "$(id -u)" = 0 ] || return 1
    X_CONTEXT_HELPER=${0%/*}/parent-context.sh
    if [ "${XPAN_TEST_MODE:-}" = 1 ]; then
        if [ ! -f "$X_MOD/parent-context.sh" ]; then
            X_CONTEXT_HELPER=${0%/*}/parent-context.sh
        else
            X_CONTEXT_HELPER=$X_MOD/parent-context.sh
        fi
        xpan_safe_path "$X_CONTEXT_HELPER" && [ -f "$X_CONTEXT_HELPER" ] || return 1
    else
        X_CONTEXT_HELPER=$X_MOD/parent-context.sh
        xpan_safe_path "$X_CONTEXT_HELPER" && [ -f "$X_CONTEXT_HELPER" ] || return 1
    fi
    . "$X_CONTEXT_HELPER"
    X_AUTHORIZED=1
    # A root-authorized boot invocation must revoke readiness even if state or
    # namespace metadata is inaccessible. It still has no authority to unmount.
    [ "$1" != boot ] || xpan_inactive || return 1
    xpan_safe_path "$X_BASE" && xpan_safe_path "$X_STATE" && xpan_safe_path "$X_LIFE" || return 1
    mkdir -p "$X_STATE" "$X_LIFE" || return 1
    [ -d "$X_STATE" ] && [ -d "$X_LIFE" ] || return 1
    X_BOOT_ID=$(xpan_bounded cat "$X_BOOT" 2>/dev/null) || return 1
    xpan_uuid "$X_BOOT_ID" || return 1
    X_NAMESPACE=$(xpan_bounded readlink "$X_NS" 2>/dev/null) || return 1
    printf '%s\n' "$X_NAMESPACE" | grep -Eq '^mnt:\[[0-9]+\]$' || return 1
    trap 'xpan_exit' EXIT
    trap 'xpan_inactive; xpan_rollback; xpan_unlock; exit 1' HUP INT TERM
}
xpan_log() { printf '%s\n' "xpan runtime: $*" >&2; }
xpan_bounded() { timeout -s KILL "$X_SECONDS" "$@"; }
xpan_hash_valid() {
    case "$1" in ''|*[!0-9a-f]*) return 1 ;; esac
    [ "${#1}" = 64 ]
}
xpan_uuid() {
    printf '%s\n' "$1" | grep -Eq '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
}
# Check all existing ancestors, not just the leaf. Production /product may be a
# platform symlink: only PM-attested framework files use xpan_framework_hash.
xpan_safe_path() {
    X_P=$1
    case "$X_P" in /*) ;; *) return 1 ;; esac
    case "$X_P" in *..*|*'
'*|*' '*|*'\'*) return 1 ;; esac
    while [ "$X_P" != / ]; do
        [ ! -L "$X_P" ] || return 1
        X_P=${X_P%/*}
        [ -n "$X_P" ] || X_P=/
    done
}
xpan_hash() {
    xpan_safe_path "$1" && [ -s "$1" ] && [ -f "$1" ] || return 1
    X_HASH=$(xpan_bounded sha256sum "$1" 2>/dev/null) || return 1
    X_HASH=${X_HASH%% *}
    xpan_hash_valid "$X_HASH"
}
xpan_device_path() {
    X_DEVICE=$1
    [ "${XPAN_TEST_MODE:-}" != 1 ] || X_DEVICE=$XPAN_TEST_ROOT$1
}
xpan_framework_hash() {
    xpan_device_path "$1"
    [ -f "$X_DEVICE" ] && [ ! -L "$X_DEVICE" ] && [ -s "$X_DEVICE" ] || return 1
    # Read the effective path (including platform partition aliases), not an
    # arbitrary other original which the app did not load.
    X_HASH=$(xpan_bounded sha256sum "$X_DEVICE" 2>/dev/null) || return 1
    X_HASH=${X_HASH%% *}
    xpan_hash_valid "$X_HASH"
}
xpan_read() {
    xpan_safe_path "$1" && [ -f "$1" ] || return 1
    X_VALUE=$(xpan_bounded cat "$1" 2>/dev/null) || return 1
    [ -n "$X_VALUE" ] && [ "${#X_VALUE}" -le 1024 ]
}
xpan_write() {
    xpan_safe_path "$1" || return 1
    [ ! -e "$1" ] || [ -f "$1" ] || return 1
    X_TEMP=$1.tmp.$$
    [ ! -e "$X_TEMP" ] && [ ! -L "$X_TEMP" ] || return 1
    (set -C; printf '%s\n' "$2" > "$X_TEMP") || return 1
    chmod 0600 "$X_TEMP" && mv -f "$X_TEMP" "$1" || { rm -f "$X_TEMP"; return 1; }
}
xpan_prop() {
    xpan_bounded setprop "$1" "$2" || return 1
    X_PROP=$(xpan_bounded getprop "$1" 2>/dev/null) || return 1
    [ "$X_PROP" = "$2" ]
}
xpan_inactive() {
    xpan_worker_guard || return $?
    X_OFF=0
    xpan_prop debug.xpan.active 0 || X_OFF=1
    xpan_prop debug.xpan.profile '' || X_OFF=1
    xpan_prop debug.xpan.token '' || X_OFF=1
    [ "$X_OFF" = 0 ]
}
xpan_lock() {
    mkdir "$X_STATE/lock" 2>/dev/null || return 1
    X_LOCKED=1
    xpan_life_lock || { xpan_unlock; return 1; }
}
xpan_life_lock() {
    mkdir "$X_LIFE/lock" 2>/dev/null || return 1
    X_LIFE_LOCKED=1
}
xpan_life_unlock() {
    [ "$X_LIFE_LOCKED" = 0 ] || rmdir "$X_LIFE/lock" 2>/dev/null
    X_LIFE_LOCKED=0
}
xpan_unlock() {
    xpan_life_unlock
    [ "$X_LOCKED" = 0 ] || rmdir "$X_STATE/lock" 2>/dev/null
    X_LOCKED=0
}
xpan_exit() {
    # A failure between mount and publication must not leave a new partial set.
    if [ -n "$X_NEW" ] || [ -n "${X_MEMORY_ROLE:-}" ]; then xpan_inactive; xpan_rollback; fi
    xpan_unlock
}
xpan_context() {
    X_CONTEXT=$(xpan_bounded ls -Zd "$1" 2>/dev/null | awk '
        { for (i=1;i<=NF;i++) if ($i ~ /^u:object_r:[a-zA-Z0-9_]+:s0$/) { print $i; n++ } }
        END { if (n != 1) exit 1 }
    ') || return 1
    [ -n "$X_CONTEXT" ]
}
xpan_source_access() {
    # Match the current stock target's conventional SELinux label; do not set
    # permissive, add policy rules or substitute an unrelated readable label.
    xpan_context "$X_TARGET" || return 1
    X_STOCK_CONTEXT=$X_CONTEXT
    xpan_context "$X_SOURCE" && [ "$X_CONTEXT" = "$X_STOCK_CONTEXT" ] || return 1
    X_MODE=$(xpan_bounded stat -Lc '%a' "$X_SOURCE" "$X_MOD" "$X_MOD/payload" "$X_MOD/payload/profiles" "$X_PROFILE" 2>/dev/null) || return 1
    [ "$X_MODE" = '644
755
755
755
755' ] || return 1
    # Labels/modes are necessary only; actual camera-domain readability must be
    # verified on the device before compatibility can be claimed.
}
xpan_role() {
    case "$1" in
        unit) X_NAME=camera_unit_config; X_ORIG=$X_O_UNIT; X_OUT=$X_Q_UNIT ;;
        feature) X_NAME=camera_unit_feature_config.protobuf; X_ORIG=$X_O_FEATURE; X_OUT=$X_Q_FEATURE ;;
        vendor) X_NAME=oplus_camera_config; X_ORIG=$X_O_VENDOR; X_OUT=$X_Q_VENDOR ;;
        *) return 1 ;;
    esac
    X_TARGET=$X_STOCK/$X_NAME
    X_SOURCE=$X_PROFILE/$X_NAME
}
xpan_load_profile() {
    X_PROFILE=$X_MOD/payload/profiles/$1
    X_ID=$1
    xpan_hash "$X_PROFILE/profile.props" || return 1
    # Pins generated by tools/make_profiles.py, including fingerprint and SDK
    # evidence. A changed manifest cannot certify arbitrary current output stock.
    case "$X_ID:$X_HASH" in
        c16:d8e68634aba11c8b2666901be014677c235588e1c5795803c366760fada9c243|c17:63cb76ce013418a001d518de814e946a86dc1046356b07c5a90689d24e514773) ;;
        *) return 1 ;;
    esac
    X_SEEN='|'; X_COUNT=0
    while IFS= read -r X_LINE || [ -n "$X_LINE" ]; do
        [ "${#X_LINE}" -le 1024 ] || return 1
        case "$X_LINE" in *=*) ;; *) return 1 ;; esac
        X_KEY=${X_LINE%%=*}; X_VAL=${X_LINE#*=}
        [ -n "$X_VAL" ] || return 1
        case "$X_SEEN" in *"|$X_KEY|"*) return 1 ;; esac
        X_SEEN=$X_SEEN$X_KEY'|'; X_COUNT=$((X_COUNT+1))
        case "$X_KEY" in
            profile_id) [ "$X_VAL" = "$X_ID" ] || return 1 ;;
            model) [ "$X_VAL" = PJX110 ] || return 1 ;;
            camera_version) X_CAMERA=$X_VAL ;;
            build_fingerprint) X_ROM=$X_VAL ;;
            original_unit_sha256) X_O_UNIT=$X_VAL; xpan_hash_valid "$X_VAL" || return 1 ;;
            original_feature_sha256) X_O_FEATURE=$X_VAL; xpan_hash_valid "$X_VAL" || return 1 ;;
            original_vendor_sha256) X_O_VENDOR=$X_VAL; xpan_hash_valid "$X_VAL" || return 1 ;;
            output_unit_sha256) X_Q_UNIT=$X_VAL; xpan_hash_valid "$X_VAL" || return 1 ;;
            output_feature_sha256) X_Q_FEATURE=$X_VAL; xpan_hash_valid "$X_VAL" || return 1 ;;
            output_vendor_sha256) X_Q_VENDOR=$X_VAL; xpan_hash_valid "$X_VAL" || return 1 ;;
            sdk_sha256) X_SDK_HASH=$X_VAL; xpan_hash_valid "$X_VAL" || return 1 ;;
            adapter_sha256) X_ADAPTER_HASH=$X_VAL; xpan_hash_valid "$X_VAL" || return 1 ;;
            *) return 1 ;;
        esac
    done < "$X_PROFILE/profile.props"
    [ "$X_COUNT" = 12 ]
}
# Parse only PM-attested direct files; unrelated Java/native libraries are not SDK
# evidence. Buffer output until the unique, complete field and all rows validate.
parsecamera_libraryfiles() {
    LC_ALL=C xpan_bounded awk '
        function reject() { bad=1; exit 1 }
        function library(p, role) {
            if (++rows>64 || seen[p]++) reject()
            if (p ~ /^\/[A-Za-z0-9_.\/-]+\.(jar|so)$/) {
                if (p ~ /\/\// || p ~ /(^|\/)\.\.?($|\/)/) reject()
            } else if (p !~ /^lib[A-Za-z0-9_.+-]+\.so$/) reject()
            if (p ~ /com\.oplus\.camera\.unit\.sdk/) {
                if (p !~ /^\/(product\/framework|my_product\/product_overlay\/framework)\/com\.oplus\.camera\.unit\.sdk(\.adapter)?\.jar$/) reject()
                role=(p ~ /\.adapter\.jar$/ ? "adapter" : "unit")
                if (roles[role]++) reject()
                result[++found]=p
            }
        }
        {
            bytes+=length($0)+1
            if (NR>8192 || length($0)>4096 || bytes>1048576) reject()
            s=$0; sub(/^[ ]*/, "", s); indent=length($0)-length(s)
            if ($0 ~ /^[[:space:]]*usesLibraryFiles([[:space:]=:\[]|$)/) {
                if (++fields!=1 || block || $0 ~ /\t/) reject()
                if (s == "usesLibraryFiles:") {
                    block=1; base=indent; child=-1; next
                }
                if (s !~ /^usesLibraryFiles=\[.*\]$/) reject()
                sub(/^usesLibraryFiles=\[/, "", s); sub(/\]$/, "", s)
                n=split(s, paths, /,[ ]*/)
                for (i=1; i<=n; i++) library(paths[i])
                next
            }
            if (block) {
                if (s == "" || $0 ~ /\t/) reject()
                if (indent<=base) {
                    if (s !~ /^[A-Za-z][A-Za-z0-9_. -]*[:=]/) reject()
                    if (!rows) reject()
                    block=0
                } else {
                    if (child<0) child=indent
                    if (indent!=child) reject()
                    library(s)
                }
            }
        }
        END {
            if (bad || fields!=1 || block || roles["unit"]!=1) exit 1
            for (i=1; i<=found; i++) print result[i]
        }
    '
}
xpan_qualify() {
    local X_R
    X_REAL_MODEL=$(xpan_bounded getprop ro.product.model 2>/dev/null) || return 1
    [ "$X_REAL_MODEL" = PJX110 ] || return 1
    X_REAL_ROM=$(xpan_bounded getprop ro.build.fingerprint 2>/dev/null) || return 1
    X_DUMP=$(xpan_bounded dumpsys package com.oplus.camera 2>/dev/null) || return 1
    X_REAL_CAMERA=$(printf '%s\n' "$X_DUMP" | awk '
        /^[[:space:]]*Package \[/ { if ($0 !~ /Package \[com\.oplus\.camera\]/) bad=1; pkg++ }
        /^[[:space:]]*versionName=/ { sub(/^[[:space:]]*versionName=/, ""); print; n++ }
        END { if (pkg != 1 || n != 1 || bad) exit 1 }
    ') || return 1
    case "$X_REAL_CAMERA" in
        6.020.876) xpan_load_profile c16 || return 1 ;;
        7.013.51) xpan_load_profile c17 || return 1 ;;
        *) return 1 ;;
    esac
    [ "$X_CAMERA" = "$X_REAL_CAMERA" ] && [ "$X_ROM" = "$X_REAL_ROM" ] || return 1
    # Direct unit SDK fixes the effective parent, including partition aliases.
    # A missing direct adapter is only a pinned same-parent compatibility file,
    # not proof of loading; never substitute PM registration or another original.
    X_LIBS=$(printf '%s\n' "$X_DUMP" | parsecamera_libraryfiles) || return 1
    X_SDK=$(printf '%s\n' "$X_LIBS" | grep '/com.oplus.camera.unit.sdk.jar$') || return 1
    X_ADAPTER=$(printf '%s\n' "$X_LIBS" | grep '/com.oplus.camera.unit.sdk.adapter.jar$') || X_ADAPTER=
    [ -n "$X_ADAPTER" ] || X_ADAPTER=${X_SDK%/*}/com.oplus.camera.unit.sdk.adapter.jar
    [ "${X_SDK%/*}" = "${X_ADAPTER%/*}" ] || return 1
    xpan_framework_hash "$X_SDK" && [ "$X_HASH" = "$X_SDK_HASH" ] || return 1
    xpan_framework_hash "$X_ADAPTER" && [ "$X_HASH" = "$X_ADAPTER_HASH" ] || return 1
    for X_R in unit feature vendor; do
        xpan_role "$X_R"
        xpan_hash "$X_SOURCE" && [ "$X_HASH" = "$X_OUT" ] || return 1
    done
}
xpan_markers() {
    [ -d "$X_MOD" ] && xpan_safe_path "$X_MOD" &&
        xpan_safe_path "$X_MOD/module.prop" && [ -f "$X_MOD/module.prop" ] || return 1
    [ "$(grep -c '^id=' "$X_MOD/module.prop")" = 1 ] &&
        grep -qx 'id=XpanAce3Pro' "$X_MOD/module.prop" || return 1
    [ ! -e "$X_MOD/disable" ] && [ ! -L "$X_MOD/disable" ] &&
        [ ! -e "$X_MOD/remove" ] && [ ! -L "$X_MOD/remove" ] || return 1
    [ "$X_MOD" != "$X_ACTIVE" ] || { [ ! -e "$X_STAGED" ] && [ ! -L "$X_STAGED" ]; }
}
xpan_tokens() {
    xpan_read "$X_MOD/.xpan-instance" && xpan_uuid "$X_VALUE" || return 1
    [ -z "$X_WORKER_TOKEN" ] || [ "$X_VALUE" = "$X_WORKER_TOKEN" ] || return 1
    X_TOKEN=$X_VALUE
    xpan_read "$X_LIFE/current.token" && [ "$X_VALUE" = "$X_TOKEN" ] || return 1
    xpan_read "$X_MOD/.xpan-apk-identity" && xpan_hash_valid "$X_VALUE" || return 1
    X_EXPECT_ID=$X_VALUE
}
xpan_worker_current() {
    [ -n "$X_WORKER_TOKEN" ] || return 0
    # Status 2 is terminal: lost/replaced lifecycle authority, never adopt it.
    xpan_read "$X_LIFE/current.token" && [ "$X_VALUE" = "$X_WORKER_TOKEN" ] || return 2
    if [ -e "$X_MOD/.xpan-instance" ] || [ -L "$X_MOD/.xpan-instance" ]; then
        xpan_read "$X_MOD/.xpan-instance" && [ "$X_VALUE" = "$X_WORKER_TOKEN" ] || return 2
    fi
    # Deleted MODDIR needs no new external identity: current.token and each
    # independent journal must still match the UUID captured at service start.
}
xpan_worker_bind() {
    [ -z "$X_WORKER_TOKEN" ] || return 1
    [ "$X_LOCKED:$X_LIFE_LOCKED" = 1:1 ] || return 1
    xpan_read "$X_MOD/.xpan-instance" && xpan_uuid "$X_VALUE" || return 1
    X_START_TOKEN=$X_VALUE
    xpan_read "$X_LIFE/current.token" && [ "$X_VALUE" = "$X_START_TOKEN" ] || return 1
    X_WORKER_TOKEN=$X_START_TOKEN
}
xpan_worker_guard() {
    [ -n "$X_WORKER_TOKEN" ] || return 0
    [ "$X_LOCKED:$X_LIFE_LOCKED" = 1:1 ] || return 1
    xpan_worker_current
}
xpan_receipt() {
    local X_R
    X_RECEIPT=$X_MOD/payload/lifecycle.inputs
    xpan_safe_path "$X_RECEIPT" && [ -f "$X_RECEIPT" ] || return 1
    X_SEEN='|'; X_COUNT=0; X_APK_HASH=
    while IFS= read -r X_LINE || [ -n "$X_LINE" ]; do
        [ "${#X_LINE}" -le 1024 ] || return 1
        case "$X_LINE" in *=*) ;; *) return 1 ;; esac
        X_KEY=${X_LINE%%=*}; X_VAL=${X_LINE#*=}
        case "$X_SEEN" in *"|$X_KEY|"*) return 1 ;; esac
        X_SEEN=$X_SEEN$X_KEY'|'; X_COUNT=$((X_COUNT+1))
        case "$X_KEY" in
            qualification_version) [ "$X_VAL" = 1 ] || return 1 ;;
            status) [ "$X_VAL" = qualified ] || return 1 ;;
            model) [ "$X_VAL" = PJX110 ] || return 1 ;;
            camera_version) [ "$X_VAL" = "$X_CAMERA" ] || return 1 ;;
            build_fingerprint) [ "$X_VAL" = "$X_ROM" ] || return 1 ;;
            apk_package) [ "$X_VAL" = com.xpanport.hook ] || return 1 ;;
            apk_sha256) X_APK_HASH=$X_VAL; xpan_hash_valid "$X_VAL" || return 1 ;;
            original_unit_sha256) [ "$X_VAL" = "$X_O_UNIT" ] || return 1 ;;
            original_feature_sha256) [ "$X_VAL" = "$X_O_FEATURE" ] || return 1 ;;
            original_vendor_sha256) [ "$X_VAL" = "$X_O_VENDOR" ] || return 1 ;;
            output_unit_sha256) [ "$X_VAL" = "$X_Q_UNIT" ] || return 1 ;;
            output_feature_sha256) [ "$X_VAL" = "$X_Q_FEATURE" ] || return 1 ;;
            output_vendor_sha256) [ "$X_VAL" = "$X_Q_VENDOR" ] || return 1 ;;
            *) return 1 ;;
        esac
    done < "$X_RECEIPT"
    [ "$X_COUNT" = 13 ] || return 1
    xpan_hash "$X_MOD/payload/XpanHook.apk" && [ "$X_HASH" = "$X_APK_HASH" ] || return 1
    for X_R in unit feature vendor; do
        xpan_role "$X_R"
        xpan_hash "$X_MOD/payload/qualified-config/$X_NAME" && [ "$X_HASH" = "$X_OUT" ] || return 1
    done
}
xpan_apk() {
    X_FRAME=$(xpan_bounded pm path android 2>/dev/null) || return 1
    case "$X_FRAME" in package:/*) ;; *) return 1 ;; esac
    case "$X_FRAME" in *'
'*) return 1 ;; esac
    X_USERS_OUT=$(xpan_bounded pm list users 2>/dev/null) || return 1
    X_USERS=$(printf '%s\n' "$X_USERS_OUT" | awk '
        /^Users:$/ { h++; next }
        /UserInfo\{/ { s=$0; sub(/^.*UserInfo\{/, "", s); sub(/:.*$/, "", s)
            if (s !~ /^[0-9]+$/ || seen[s]++ || ++n>64) bad=1; else print s; next }
        /[^[:space:]]/ { bad=1 }
        END { if (h != 1 || bad || !n) exit 1 }
    ') || return 1
    # Identical normalization to lifecycle/uninstall. Legacy global identity
    # remains three lines; per-user v2 selects owner 0 regardless of installed
    # flag, else the lowest numeric user with a time. PM/path/bytes below are
    # the independent installation proof; unknown/duplicate metadata is rejected.
    X_IDENTITY=; X_ID_LINES=
    X_HOOK=$(xpan_bounded dumpsys package com.xpanport.hook 2>/dev/null) || return 1
    [ "${#X_HOOK}" -le 1048576 ] || return 1
    X_ID_LINES=$(printf '%s\n' "$X_HOOK" | LC_ALL=C awk '
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
    X_IDENTITY=$(printf '%s\n' "$X_ID_LINES" | xpan_bounded sha256sum) || return 1
    [ "${X_IDENTITY%% *}" = "$X_EXPECT_ID" ] || return 1
    X_CODE=$(printf '%s\n' "$X_ID_LINES" | grep '^codePath=') || return 1
    X_CODE=${X_CODE#codePath=}
    case "$X_CODE" in /data/app/*) ;; *) return 1 ;; esac
    xpan_safe_path "$X_CODE" || return 1
    X_PRESENT=0
    for X_USER in $X_USERS; do
        X_LIST=$(xpan_bounded pm list packages --user "$X_USER" com.xpanport.hook 2>/dev/null) || return 1
        case "$X_LIST" in '') continue ;; package:com.xpanport.hook) ;; *) return 1 ;; esac
        X_PRESENT=1
        X_PATH=$(xpan_bounded pm path --user "$X_USER" com.xpanport.hook 2>/dev/null) || return 1
        [ "$X_PATH" = "package:$X_CODE/base.apk" ] || return 1
        xpan_device_path "$X_CODE/base.apk"
        xpan_hash "$X_DEVICE" && [ "$X_HASH" = "$X_APK_HASH" ] || return 1
    done
    [ "$X_PRESENT" = 1 ] || return 1
    X_HOOK_AGAIN=$(xpan_bounded dumpsys package com.xpanport.hook 2>/dev/null) || return 1
    [ "$X_HOOK_AGAIN" = "$X_HOOK" ]
}
# A top-level module-owned journal records the exact observed mount, inode,
# boot and namespace. Output hash or a familiar mount source is NEVER ownership.
# If a foreign mount covers ours, retain both the foreign mount and our journal.
xpan_mount_line() {
    X_MLINE=$(xpan_bounded awk -v t="$X_TARGET" '
        $5==t { n++; print }
        END { if (n>1) exit 1 }
    ' "$X_INFO" 2>/dev/null) || return 1
    X_MHASH=
    [ -n "$X_MLINE" ] || return 0
    X_MHASH=$(printf '%s\n' "$X_MLINE" | xpan_bounded sha256sum) || return 1
    X_MHASH=${X_MHASH%% *}
    xpan_hash_valid "$X_MHASH"
}
xpan_inode() {
    X_INODE=$(xpan_bounded stat -Lc '%d:%i' "$1" 2>/dev/null) || return 1
    printf '%s\n' "$X_INODE" | grep -Eq '^[0-9]+:[0-9]+$'
}
xpan_journal() {
    X_J=$X_STATE/$1.mount
    xpan_safe_path "$X_J" && [ -f "$X_J" ] || return 1
    X_JSEEN='|'; X_JCOUNT=0
    X_JBOOT=; X_JNS=; X_JTARGET=; X_JHASH=; X_JINODE=; X_JTOKEN=; X_JVERSION=; X_JPARENT=; X_JPINO=
    while IFS= read -r X_JLINE || [ -n "$X_JLINE" ]; do
        [ "${#X_JLINE}" -le 1024 ] || return 1
        case "$X_JLINE" in *=*) ;; *) return 1 ;; esac
        X_JKEY=${X_JLINE%%=*}; X_JVAL=${X_JLINE#*=}
        case "$X_JSEEN" in *"|$X_JKEY|"*) return 1 ;; esac
        X_JSEEN=$X_JSEEN$X_JKEY'|'; X_JCOUNT=$((X_JCOUNT+1))
        case "$X_JKEY" in
            ownership_version) X_JVERSION=$X_JVAL; case "$X_JVAL" in 1|2) ;; *) return 1 ;; esac ;;
            boot_id) X_JBOOT=$X_JVAL; xpan_uuid "$X_JVAL" || return 1 ;;
            namespace) X_JNS=$X_JVAL ;;
            target) X_JTARGET=$X_JVAL ;;
            mount_sha256) X_JHASH=$X_JVAL; xpan_hash_valid "$X_JVAL" || return 1 ;;
            inode) X_JINODE=$X_JVAL; printf '%s\n' "$X_JVAL" | grep -Eq '^[0-9]+:[0-9]+$' || return 1 ;;
            token) X_JTOKEN=$X_JVAL; xpan_uuid "$X_JVAL" || return 1 ;;
            parent_mount_sha256) X_JPARENT=$X_JVAL; xpan_hash_valid "$X_JVAL" || return 1 ;;
            parent_inode) X_JPINO=$X_JVAL; printf '%s\n' "$X_JVAL" | grep -Eq '^[0-9]+:[0-9]+$' || return 1 ;;
            *) return 1 ;;
        esac
    done < "$X_J"
    [ "$X_JTARGET" = "$X_TARGET" ] || return 1
    case "$X_JVERSION:$X_JCOUNT" in
        1:7) [ -z "$X_JPARENT" ] && [ -z "$X_JPINO" ] ;;
        2:9) [ -n "$X_JPARENT" ] && [ -n "$X_JPINO" ] ;;
        *) return 1 ;;
    esac
}
xpan_journal_parent() {
    xpan_parent_mounts || return 1
    if [ "$X_JVERSION" = 1 ]; then [ "$X_PARENT_KIND" = normal ]; return $?; fi
    [ "$X_PARENT_INSTANCE" = 1 ] && [ "$X_JPARENT" = "$X_PARENT_HASH" ] &&
        [ "$X_JPINO" = "$X_PARENT_INODE" ]
}
xpan_owned() {
    xpan_journal "$1" && [ "$X_JBOOT" = "$X_BOOT_ID" ] &&
        [ "$X_JNS" = "$X_NAMESPACE" ] && xpan_journal_parent || return 1
    xpan_mount_line && [ -n "$X_MLINE" ] && [ "$X_MHASH" = "$X_JHASH" ] || return 1
    xpan_inode "$X_TARGET" && [ "$X_INODE" = "$X_JINODE" ]
}
xpan_unmount_role() {
    xpan_worker_guard || return $?
    xpan_role "$1"
    [ ! -e "$X_STATE/$1.mount" ] && [ ! -L "$X_STATE/$1.mount" ] && return 0
    xpan_journal "$1" || { xpan_log "untrusted journal retained: $1"; return 1; }
    [ -z "$X_WORKER_TOKEN" ] || [ "$X_JTOKEN" = "$X_WORKER_TOKEN" ] || return 1
    # Stale-boot records cannot authorize unmounting current mounts. A different
    # namespace record is retained (e.g. KSU isolation), not stolen or repurposed.
    if [ "$X_JBOOT" != "$X_BOOT_ID" ]; then rm -f "$X_J"; return $?; fi
    [ "$X_JNS" = "$X_NAMESPACE" ] || return 1
    xpan_journal_parent || { xpan_log "parent context changed; journal retained: $1"; return 1; }
    xpan_mount_line || { xpan_log "covered/ambiguous mount retained: $1"; return 1; }
    if [ -z "$X_MLINE" ]; then rm -f "$X_J"; return $?; fi
    xpan_owned "$1" || { xpan_log "foreign mount retained: $1"; return 1; }
    # No lazy/forced unmount: those can detach things outside this transaction.
    xpan_parent_mounts || return 1
    xpan_bounded umount "$X_TARGET" || return 1
    xpan_parent_mounts && xpan_mount_line && [ -z "$X_MLINE" ] || return 1
    rm -f "$X_J"
}
xpan_cleanup() {
    X_CLEAN=0
    for X_C in vendor feature unit; do xpan_unmount_role "$X_C" || X_CLEAN=1; done
    [ "$X_CLEAN" = 0 ]
}
xpan_memory_unmount() {
    [ -n "${X_MEMORY_ROLE:-}" ] || return 0
    xpan_worker_guard || return $?
    xpan_role "$X_MEMORY_ROLE"
    X_MEMORY_NOW_BOOT=$(xpan_bounded cat "$X_BOOT" 2>/dev/null) || return 1
    X_MEMORY_NOW_NS=$(xpan_bounded readlink "$X_NS" 2>/dev/null) || return 1
    [ "$X_MEMORY_NOW_BOOT" = "$X_MEMORY_BOOT" ] && [ "$X_MEMORY_NOW_NS" = "$X_MEMORY_NS" ] || return 1
    # Proof exists only inside this invocation, captured after source/root/inode
    # verification and before publication. Recheck exact mount before retracting.
    xpan_parent_mounts && [ "$X_PARENT_HASH" = "$X_MEMORY_PARENT_HASH" ] &&
        [ "$X_PARENT_INODE" = "$X_MEMORY_PARENT_INODE" ] || return 1
    xpan_mount_line && [ "$X_MHASH" = "$X_MEMORY_HASH" ] &&
        xpan_inode "$X_TARGET" && [ "$X_INODE" = "$X_MEMORY_INODE" ] || return 1
    xpan_bounded umount "$X_TARGET" || return 1
    xpan_parent_mounts && xpan_mount_line && [ -z "$X_MLINE" ] || return 1
    X_MEMORY_ROLE=
}
xpan_rollback() {
    X_ROLL=0
    xpan_memory_unmount || X_ROLL=1
    for X_C in $X_NEW; do xpan_unmount_role "$X_C" || X_ROLL=1; done
    X_NEW=
    [ "$X_ROLL" = 0 ]
}
xpan_parent_mounts() {
    xpan_parent_observe "$X_INFO" "$X_STOCK" "$X_PARENT_POINT" "$X_PARTITION_POINT" || return 1
    if [ -z "$X_PARENT_HASH" ]; then
        [ "$XP_PARENT_KIND" = normal ] || return 1
        X_PARENT_HASH=$XP_PARENT_HASH
        X_PARENT_INODE=$XP_PARENT_INODE
        X_PARENT_KIND=$XP_PARENT_KIND
    fi
    [ "$XP_PARENT_HASH" = "$X_PARENT_HASH" ] && [ "$XP_PARENT_INODE" = "$X_PARENT_INODE" ] &&
        [ "$XP_PARENT_KIND" = "$X_PARENT_KIND" ]
}
xpan_parent_pin() {
    local X_PKEY X_PVAL X_PLINE X_PSEEN X_PCOUNT X_PBOOT X_PNS X_PTOKEN X_PHASH X_PINODE X_PKIND
    xpan_safe_path "$X_STATE/parent.context" && [ -f "$X_STATE/parent.context" ] || return 1
    [ "${XPAN_TEST_MODE:-}" = 1 ] || {
        X_PMODE=$(xpan_bounded stat -c '%a:%u' "$X_STATE/parent.context") || return 1
        [ "$X_PMODE" = 600:0 ] || return 1
    }
    X_PSEEN='|'; X_PCOUNT=0
    X_PBOOT=; X_PNS=; X_PTOKEN=; X_PHASH=; X_PINODE=; X_PKIND=
    while IFS= read -r X_PLINE || [ -n "$X_PLINE" ]; do
        [ "${#X_PLINE}" -le 1024 ] || return 1
        case "$X_PLINE" in *=*) ;; *) return 1 ;; esac
        X_PKEY=${X_PLINE%%=*}; X_PVAL=${X_PLINE#*=}
        case "$X_PSEEN" in *"|$X_PKEY|"*) return 1 ;; esac
        X_PSEEN=$X_PSEEN$X_PKEY'|'; X_PCOUNT=$((X_PCOUNT+1))
        case "$X_PKEY" in
            parent_version) [ "$X_PVAL" = 1 ] || return 1 ;;
            boot_id) X_PBOOT=$X_PVAL; xpan_uuid "$X_PVAL" || return 1 ;;
            namespace) X_PNS=$X_PVAL ;;
            token) X_PTOKEN=$X_PVAL; xpan_uuid "$X_PVAL" || return 1 ;;
            parent_mount_sha256) X_PHASH=$X_PVAL; xpan_hash_valid "$X_PVAL" || return 1 ;;
            parent_inode) X_PINODE=$X_PVAL; printf '%s\n' "$X_PVAL" | grep -Eq '^[0-9]+:[0-9]+$' || return 1 ;;
            parent_kind) X_PKIND=$X_PVAL; case "$X_PVAL" in normal|ksu) ;; *) return 1 ;; esac ;;
            *) return 1 ;;
        esac
    done < "$X_STATE/parent.context"
    [ "$X_PCOUNT" = 7 ] || return 1
    if [ "$X_PBOOT" != "$X_BOOT_ID" ] || [ "$X_PNS" != "$X_NAMESPACE" ]; then
        [ "${1:-}" = prepare ] && xpan_parent_approval || return 1
        X_PARENT_RECOVERING=1
    fi
    if [ "$X_PTOKEN" != "$X_TOKEN" ]; then
        [ "${1:-}" = prepare ] && xpan_parent_approval || return 1
        [ "$X_PBOOT" = "$X_BOOT_ID" ] && [ "$X_PNS" = "$X_NAMESPACE" ] &&
            [ "$X_PHASH" = "$X_PARENT_HASH" ] && [ "$X_PINODE" = "$X_PARENT_INODE" ] &&
            [ "$X_PKIND" = "$X_PARENT_KIND" ] || return 1
        X_PARENT_RECOVERING=1
    fi
    if [ "${X_PARENT_RECOVERING:-0}" != 1 ]; then
        [ -z "$X_PARENT_HASH" ] || { [ "$X_PARENT_HASH" = "$X_PHASH" ] && [ "$X_PARENT_INODE" = "$X_PINODE" ]; } || return 1
    else
        return 0
    fi
    if [ -n "$X_PARENT_SEALED_HASH" ]; then
        [ "$X_PARENT_SEALED_HASH" = "$X_PHASH" ] && [ "$X_PARENT_SEALED_INODE" = "$X_PINODE" ] &&
            [ "$X_PARENT_SEALED_KIND" = "$X_PKIND" ] || return 1
    fi
    X_PARENT_HASH=$X_PHASH; X_PARENT_INODE=$X_PINODE; X_PARENT_KIND=$X_PKIND
    X_PARENT_V2=1; X_PARENT_INSTANCE=1
    xpan_parent_mounts
}
xpan_parent_start() {
    xpan_parent_observe "$X_INFO" "$X_STOCK" "$X_PARENT_POINT" "$X_PARTITION_POINT" || return 1
    if [ -e "$X_STATE/parent.context" ] || [ -L "$X_STATE/parent.context" ]; then
        xpan_parent_pin
        return $?
    fi
    if [ "$XP_PARENT_KIND" = normal ]; then
        [ -z "$X_PARENT_HASH" ] || { [ "$X_PARENT_HASH" = "$XP_PARENT_HASH" ] && [ "$X_PARENT_INODE" = "$XP_PARENT_INODE" ]; } || return 1
        [ -z "$X_PARENT_SEALED_HASH" ] || { [ "$X_PARENT_SEALED_HASH" = "$XP_PARENT_HASH" ] &&
            [ "$X_PARENT_SEALED_INODE" = "$XP_PARENT_INODE" ] && [ "$X_PARENT_SEALED_KIND" = normal ]; } || return 1
        X_PARENT_HASH=$XP_PARENT_HASH; X_PARENT_INODE=$XP_PARENT_INODE; X_PARENT_KIND=$XP_PARENT_KIND
        X_PARENT_INSTANCE=0; X_PARENT_V2=0
        return 0
    fi
    return 1
}
xpan_parent_approval() {
    local X_AKEY X_AVAL X_ALINE X_ASEEN X_ACOUNT X_ABOOT X_ANS X_ATOKEN X_AHASH X_AINODE
    xpan_safe_path "$X_MOD/payload/parent.approval" && [ -f "$X_MOD/payload/parent.approval" ] || return 1
    [ "${XPAN_TEST_MODE:-}" = 1 ] || {
        X_APMODE=$(xpan_bounded stat -c '%a:%u' "$X_MOD/payload/parent.approval") || return 1
        [ "$X_APMODE" = 600:0 ] || return 1
    }
    X_ASEEN='|'; X_ACOUNT=0
    X_ABOOT=; X_ANS=; X_ATOKEN=; X_AHASH=; X_AINODE=
    while IFS= read -r X_ALINE || [ -n "$X_ALINE" ]; do
        [ "${#X_ALINE}" -le 1024 ] || return 1
        case "$X_ALINE" in *=*) ;; *) return 1 ;; esac
        X_AKEY=${X_ALINE%%=*}; X_AVAL=${X_ALINE#*=}
        case "$X_ASEEN" in *"|$X_AKEY|"*) return 1 ;; esac
        X_ASEEN=$X_ASEEN$X_AKEY'|'; X_ACOUNT=$((X_ACOUNT+1))
        case "$X_AKEY" in
            parent_approval_version) [ "$X_AVAL" = 1 ] || return 1 ;;
            boot_id) X_ABOOT=$X_AVAL; xpan_uuid "$X_AVAL" || return 1 ;;
            namespace) X_ANS=$X_AVAL ;;
            current_token) X_ATOKEN=$X_AVAL; xpan_uuid "$X_AVAL" || return 1 ;;
            parent_mount_sha256) X_AHASH=$X_AVAL; xpan_hash_valid "$X_AVAL" || return 1 ;;
            parent_inode) X_AINODE=$X_AVAL; printf '%s\n' "$X_AVAL" | grep -Eq '^[0-9]+:[0-9]+$' || return 1 ;;
            *) return 1 ;;
        esac
    done < "$X_MOD/payload/parent.approval"
    [ "$X_ACOUNT" = 6 ] && [ "$X_ABOOT" = "$X_BOOT_ID" ] && [ "$X_ANS" = "$X_NAMESPACE" ] &&
        [ "$X_AHASH" = "$X_PARENT_HASH" ] && [ "$X_AINODE" = "$X_PARENT_INODE" ] || return 1
    xpan_read "$X_LIFE/current.token" && [ "$X_VALUE" = "$X_ATOKEN" ] || return 1
    xpan_read "$X_MOD/.xpan-instance" && [ "$X_VALUE" = "$X_ATOKEN" ] &&
        X_PARENT_APPROVED_TOKEN=$X_ATOKEN
}
xpan_parent_prepare() {
    xpan_parent_observe "$X_INFO" "$X_STOCK" "$X_PARENT_POINT" "$X_PARTITION_POINT" || return 1
    X_PARENT_HASH=$XP_PARENT_HASH; X_PARENT_INODE=$XP_PARENT_INODE; X_PARENT_KIND=$XP_PARENT_KIND
    X_PARENT_INSTANCE=0; X_PARENT_V2=1
    if [ -e "$X_STATE/parent.context" ] || [ -L "$X_STATE/parent.context" ]; then
        xpan_read "$X_MOD/.xpan-instance" && xpan_uuid "$X_VALUE" || return 1
        X_TOKEN=$X_VALUE
        xpan_read "$X_LIFE/current.token" && [ "$X_VALUE" = "$X_TOKEN" ] || return 1
        xpan_parent_pin prepare || return 1
        if [ "${X_PARENT_RECOVERING:-0}" = 1 ]; then
            xpan_qualify && xpan_parent_mounts || return 1
            for X_RECOVERY_ROLE in unit feature vendor; do
                xpan_role "$X_RECOVERY_ROLE"
                xpan_mount_line && [ -z "$X_MLINE" ] || return 1
                xpan_hash "$X_TARGET" && [ "$X_HASH" = "$X_ORIG" ] || return 1
                if [ -e "$X_STATE/$X_RECOVERY_ROLE.mount" ] || [ -L "$X_STATE/$X_RECOVERY_ROLE.mount" ]; then
                    xpan_journal "$X_RECOVERY_ROLE" && [ "$X_JBOOT" != "$X_BOOT_ID" ] || return 1
                fi
            done
            xpan_parent_mounts || return 1
            for X_OLD_ROLE in unit feature vendor; do
                if [ -e "$X_STATE/$X_OLD_ROLE.mount" ] || [ -L "$X_STATE/$X_OLD_ROLE.mount" ]; then
                    xpan_role "$X_OLD_ROLE"
                    xpan_journal "$X_OLD_ROLE" && [ "$X_JBOOT" != "$X_BOOT_ID" ] || return 1
                    xpan_mount_line && [ -z "$X_MLINE" ] || return 1
                    xpan_hash "$X_TARGET" && [ "$X_HASH" = "$X_ORIG" ] || return 1
                    xpan_parent_mounts && rm -f "$X_J" || return 1
                fi
            done
        fi
        X_PARENT_PREVIOUS=$(xpan_bounded sha256sum "$X_STATE/parent.context") || return 1
        X_PARENT_PREVIOUS=${X_PARENT_PREVIOUS%% *}
    elif [ "$X_PARENT_KIND" = ksu ]; then
        xpan_parent_approval || return 1
    fi
    for X_PR in unit feature vendor; do
        [ ! -e "$X_STATE/$X_PR.mount" ] && [ ! -L "$X_STATE/$X_PR.mount" ] || return 1
    done
    xpan_parent_mounts
}
xpan_parent_commit() {
    xpan_parent_mounts || return 1
    if [ -n "$X_PARENT_PREVIOUS" ]; then
        X_PNOW=$(xpan_bounded sha256sum "$X_STATE/parent.context") || return 1
        [ "${X_PNOW%% *}" = "$X_PARENT_PREVIOUS" ] || return 1
    else
        [ ! -e "$X_STATE/parent.context" ] && [ ! -L "$X_STATE/parent.context" ] || return 1
    fi
    xpan_write "$X_STATE/parent.context" "parent_version=1
boot_id=$X_BOOT_ID
namespace=$X_NAMESPACE
token=$X_TOKEN
parent_mount_sha256=$X_PARENT_HASH
parent_inode=$X_PARENT_INODE
parent_kind=$X_PARENT_KIND" || return 1
    X_PARENT_RECOVERING=0
    xpan_parent_pin
}
xpan_parent_phase() {
    xpan_parent_mounts || return 1
    xpan_write "$X_MOD/payload/parent.inputs" "parent_version=1
boot_id=$X_BOOT_ID
namespace=$X_NAMESPACE
parent_mount_sha256=$X_PARENT_HASH
parent_inode=$X_PARENT_INODE
parent_kind=$X_PARENT_KIND" || return 1
}
xpan_current() {
    local X_R
    xpan_parent_mounts || return 1
    for X_R in unit feature vendor; do
        xpan_role "$X_R"
        xpan_mount_line || return 1
        if [ -n "$X_MLINE" ]; then
            [ "$1" != originals ] && xpan_owned "$X_R" &&
                [ "$X_JTOKEN" = "$X_TOKEN" ] || return 1
            xpan_hash "$X_TARGET" && [ "$X_HASH" = "$X_OUT" ] || return 1
        else
            # Unmounted output is not stock, even if it matches the payload.
            xpan_hash "$X_TARGET" && [ "$X_HASH" = "$X_ORIG" ] || return 1
            [ ! -e "$X_STATE/$X_R.mount" ] && [ ! -L "$X_STATE/$X_R.mount" ] || return 1
        fi
    done
    xpan_parent_mounts
}
xpan_capture_mount() {
    xpan_parent_mounts || return 1
    xpan_mount_line && [ -n "$X_MLINE" ] || return 1
    xpan_inode "$X_SOURCE" || return 1
    X_SOURCE_INODE=$X_INODE
    xpan_inode "$X_TARGET" && [ "$X_INODE" = "$X_SOURCE_INODE" ] || return 1
    # Verify bind root from the filesystem containing the source; just its name
    # in `mount` output is not evidence of a successful bind.
    X_ROOT=$(xpan_bounded awk -v s="$X_SOURCE" '
        $5=="/" || s==$5 || index(s,$5 "/")==1 {
            if (length($5)>best) { best=length($5); point=$5; root=$4; dev=$3 }
        }
        END { if (!best) exit 1; suffix=substr(s,length(point)+1)
            if (point=="/") suffix="/" substr(s,2)
            if (root=="/") root=""; print dev " " root suffix }
    ' "$X_INFO" 2>/dev/null) || return 1
    X_TARGET_ROOT=$(printf '%s\n' "$X_MLINE" | awk '{print $3 " " $4}') || return 1
    [ "$X_TARGET_ROOT" = "$X_ROOT" ] || return 1
    X_MEMORY_ROLE=$X_R
    X_MEMORY_BOOT=$X_BOOT_ID
    X_MEMORY_NS=$X_NAMESPACE
    X_MEMORY_HASH=$X_MHASH
    X_MEMORY_INODE=$X_SOURCE_INODE
    X_MEMORY_PARENT_HASH=$X_PARENT_HASH
    X_MEMORY_PARENT_INODE=$X_PARENT_INODE
    X_JTEXT="ownership_version=1
boot_id=$X_BOOT_ID
namespace=$X_NAMESPACE
target=$X_TARGET
mount_sha256=$X_MHASH
inode=$X_SOURCE_INODE
token=$X_TOKEN"
    if [ "$X_PARENT_V2" = 1 ]; then
        X_JTEXT="ownership_version=2
boot_id=$X_BOOT_ID
namespace=$X_NAMESPACE
target=$X_TARGET
mount_sha256=$X_MHASH
inode=$X_SOURCE_INODE
token=$X_TOKEN
parent_mount_sha256=$X_PARENT_HASH
parent_inode=$X_PARENT_INODE"
    fi
    xpan_parent_mounts && xpan_write "$X_STATE/$X_R.mount" "$X_JTEXT" && xpan_parent_mounts
}
xpan_mount_all() {
    X_NEW=
    for X_R in unit feature vendor; do
        xpan_role "$X_R"
        xpan_mount_line || { xpan_rollback; return 1; }
        if [ -n "$X_MLINE" ]; then
            xpan_owned "$X_R" && [ "$X_JTOKEN" = "$X_TOKEN" ] || { xpan_rollback; return 1; }
            continue
        fi
        # Recheck immediately before every bind, not only at initial preflight.
        xpan_markers && xpan_tokens && xpan_current mounted && xpan_qualify || { xpan_rollback; return 1; }
        xpan_role "$X_R"
        xpan_source_access && xpan_worker_guard && xpan_parent_mounts || { xpan_rollback; return 1; }
        X_MRC=0
        xpan_bounded mount --bind "$X_SOURCE" "$X_TARGET" || X_MRC=1
        # Some mount implementations can return failure after attaching a bind.
        # Capture only the newly observed exact source/root/inode, then roll back.
        if xpan_capture_mount; then
            X_NEW="$X_R $X_NEW"
            X_MEMORY_ROLE=
        else
            xpan_log "cannot prove new mount ownership: $X_R; refuse blind unmount"
            xpan_rollback
            return 1
        fi
        [ "$X_MRC" = 0 ] || { xpan_rollback; return 1; }
        xpan_hash "$X_TARGET" && [ "$X_HASH" = "$X_OUT" ] || { xpan_rollback; return 1; }
    done
}
xpan_publish() {
    # Caller still holds both locks; repeat eligibility and all three actual bytes.
    xpan_parent_mounts && xpan_markers && xpan_tokens && xpan_qualify && xpan_receipt && xpan_apk &&
        xpan_current mounted || return 1
    for X_R in unit feature vendor; do
        xpan_role "$X_R"
        xpan_owned "$X_R" && [ "$X_JTOKEN" = "$X_TOKEN" ] && xpan_source_access || return 1
    done
    xpan_worker_guard && xpan_parent_mounts || return $?
    xpan_prop debug.xpan.profile "$X_ID" && xpan_prop debug.xpan.token "$X_TOKEN" &&
        xpan_prop debug.xpan.active 1 || return 1
    X_A=$(xpan_bounded getprop debug.xpan.active 2>/dev/null) || return 1
    X_B=$(xpan_bounded getprop debug.xpan.profile 2>/dev/null) || return 1
    X_T=$(xpan_bounded getprop debug.xpan.token 2>/dev/null) || return 1
    [ "$X_A" = 1 ] && [ "$X_B" = "$X_ID" ] && [ "$X_T" = "$X_TOKEN" ] && xpan_parent_mounts
}
xpan_tick() {
    xpan_worker_current || return $?
    if ! xpan_lock; then
        if [ -z "$X_WORKER_TOKEN" ]; then xpan_inactive; fi
        xpan_log 'lock busy; no mount mutation'
        return 1
    fi
    xpan_worker_current || { X_TICK_RC=$?; xpan_unlock; return "$X_TICK_RC"; }
    if [ -n "$X_WORKER_TOKEN" ]; then
        X_TOKEN=$X_WORKER_TOKEN
    else
        if ! { xpan_read "$X_MOD/.xpan-instance" && xpan_uuid "$X_VALUE"; }; then
            # A direct maintenance tick may retract only journal-proven old mounts.
            xpan_inactive
            if xpan_read "$X_LIFE/current.token" && xpan_uuid "$X_VALUE"; then X_TOKEN=$X_VALUE; fi
            xpan_parent_start && xpan_cleanup
            xpan_unlock
            return 1
        fi
        X_TOKEN=$X_VALUE
        # Explicit ticks may retract proven old binds after cancellation, never publish them.
    fi
    if ! xpan_parent_start; then
        xpan_inactive
        xpan_unlock
        xpan_log 'parent context unavailable or changed; existing journals/mounts retained'
        return 1
    fi
    # Validate first without oscillating the live token during healthy monitoring.
    X_READY=$(xpan_bounded getprop sys.boot_completed 2>/dev/null) || X_READY=
    if [ "$X_READY" != 1 ] || ! { xpan_markers && xpan_tokens && xpan_qualify && xpan_receipt && xpan_apk; }; then
        xpan_inactive
        xpan_cleanup
        xpan_unlock
        return 1
    fi
    if ! xpan_current mounted; then
        xpan_inactive
        xpan_cleanup
        xpan_unlock
        return 1
    fi
    # Already healthy mounts need no active=0 flicker, no duplicate bind mounts.
    X_COMPLETE=1
    for X_R in unit feature vendor; do xpan_role "$X_R"; xpan_owned "$X_R" || X_COMPLETE=0; done
    if [ "$X_COMPLETE" != 1 ]; then
        xpan_inactive || { xpan_unlock; return 1; }
        xpan_mount_all || { xpan_inactive; xpan_unlock; return 1; }
    fi
    if ! xpan_publish; then
        xpan_inactive
        # A transaction failure only retracts this invocation's new binds. Any
        # preexisting owned or foreign bind is preserved inactive for next tick.
        xpan_rollback
        xpan_unlock
        return 1
    fi
    X_NEW=
    xpan_unlock
    return 0
}
xpan_prepare() {
    xpan_parent_prepare && xpan_markers && xpan_qualify && xpan_current originals || {
        xpan_log 'install requires exact unmounted originals and actual qualified SDK; orig-only update refused'
        return 1
    }
    xpan_hash "$X_MOD/payload/XpanHook.apk" || return 1
    X_APK_HASH=$X_HASH
    # Only owned payload modes and labels are changed, never stock targets.
    chmod 0755 "$X_MOD" "$X_MOD/payload" "$X_MOD/payload/profiles" "$X_PROFILE" || return 1
    for X_R in unit feature vendor; do
        xpan_role "$X_R"
        xpan_context "$X_TARGET" || return 1
        X_LABEL=$X_CONTEXT
        chmod 0644 "$X_SOURCE" || return 1
        xpan_bounded chcon "$X_LABEL" "$X_SOURCE" || return 1
        xpan_source_access || return 1
    done
    xpan_safe_path "$X_MOD/payload/qualified-config" || return 1
    mkdir -p "$X_MOD/payload/qualified-config" || return 1
    for X_R in unit feature vendor; do
        xpan_role "$X_R"
        X_DEST=$X_MOD/payload/qualified-config/$X_NAME
        xpan_safe_path "$X_DEST" || return 1
        [ ! -e "$X_DEST" ] || [ -f "$X_DEST" ] || return 1
        X_COPY=$X_DEST.tmp.$$
        [ ! -e "$X_COPY" ] && [ ! -L "$X_COPY" ] || return 1
        (set -C; xpan_bounded cat "$X_SOURCE" > "$X_COPY") || { rm -f "$X_COPY"; return 1; }
        chmod 0600 "$X_COPY" && mv -f "$X_COPY" "$X_DEST" || { rm -f "$X_COPY"; return 1; }
        xpan_hash "$X_DEST" && [ "$X_HASH" = "$X_OUT" ] || return 1
    done
    xpan_write "$X_MOD/payload/lifecycle.inputs" "qualification_version=1
status=qualified
model=PJX110
camera_version=$X_CAMERA
build_fingerprint=$X_ROM
apk_package=com.xpanport.hook
apk_sha256=$X_APK_HASH
original_unit_sha256=$X_O_UNIT
original_feature_sha256=$X_O_FEATURE
original_vendor_sha256=$X_O_VENDOR
output_unit_sha256=$X_Q_UNIT
output_feature_sha256=$X_Q_FEATURE
output_vendor_sha256=$X_Q_VENDOR" || return 1
    xpan_receipt && xpan_qualify && xpan_current originals && xpan_parent_phase
}
# OPCameraPro completion helpers: top-level so Android sh never nests definitions.
xpan_op_backup() {
    # $1 target file, $2 relative backup name; exact copy, never overwrites.
    [ -f "$1" ] && [ ! -L "$1" ] || return 1
    xpan_hash "$1" || return 1
    X_OB_H=$X_HASH
    X_OB_D=$X_OC_BACK
    case "$2" in
        */*) X_OB_D=$X_OC_BACK/${2%/*} ;;
    esac
    mkdir -p "$X_OB_D" 2>/dev/null || return 1
    [ -e "$X_OC_BACK/$2" ] && return 0
    X_OB_T=$X_OB_D/.${2##*/}.$$
    (set -C; xpan_bounded cat "$1" > "$X_OB_T") || { rm -f "$X_OB_T"; return 1; }
    chmod 0644 "$X_OB_T" 2>/dev/null
    mv -f "$X_OB_T" "$X_OC_BACK/$2" 2>/dev/null || { rm -f "$X_OB_T"; return 1; }
    xpan_hash "$X_OC_BACK/$2" && [ "$X_HASH" = "$X_OB_H" ]
}
xpan_op_put() {
    # $1 source, $2 target, $3 expected hash after the atomic write.
    [ -f "$1" ] && [ ! -L "$1" ] || return 1
    X_OP_TMP=$2.xpan-compat.$$
    if ! { xpan_bounded cat "$1" > "$X_OP_TMP" 2>/dev/null &&
            chmod 0644 "$X_OP_TMP" 2>/dev/null &&
            mv -f "$X_OP_TMP" "$2" 2>/dev/null; }; then
        rm -f "$X_OP_TMP" 2>/dev/null
        return 1
    fi
    xpan_hash "$2" && [ "$X_HASH" = "$3" ]
}
# OPCameraPro's attached module also carries C16-era filter and tone assets.
# Mounted over this C17 ROM they shadow the C17 engine: selecting any filter
# renders a black preview. Renaming them (every file kept, fully reversible)
# lets the stock C17 assets show through on the next boot. Allowlist only:
# directories meishe_lut, filters_lut, basictone and files named
# gamma_*_conf.json. config/, is_mount, the SDK jars and every other file are
# never moved. The renames are armed only while the attached module still ships
# its known C16 completion, so a future OP update to C17 assets stays untouched.
xpan_op_c16_pin() {
    case "$1" in
        unit) printf '%s' "${XPAN_TEST_OP_C16_UNIT:-37a13d408f7ee95b8e954c2ef11c44a0aee433da45958cc7ddb80c688e0692c6}" ;;
        adapter) printf '%s' "${XPAN_TEST_OP_C16_ADAPTER:-ac0b595faae8d3f684aa457f223fbfb0f167f0d0e0cc461ef08dbbe52d6e61cf}" ;;
        *) return 1 ;;
    esac
}
xpan_op_is_c16_completion() {
    X_OF_U=$X_OC_FW/com.oplus.camera.unit.sdk.jar
    X_OF_A=$X_OC_FW/com.oplus.camera.unit.sdk.adapter.jar
    X_OF_UP=$(xpan_op_c16_pin unit) || return 1
    X_OF_AP=$(xpan_op_c16_pin adapter) || return 1
    if [ -f "$X_OF_U" ] && [ ! -L "$X_OF_U" ] && xpan_hash "$X_OF_U" &&
        [ "$X_HASH" = "$X_OF_UP" ]; then return 0; fi
    if [ -f "$X_OF_A" ] && [ ! -L "$X_OF_A" ] && xpan_hash "$X_OF_A" &&
        [ "$X_HASH" = "$X_OF_AP" ]; then return 0; fi
    return 1
}
xpan_op_hide_c16_filters() {
    X_OF_CAM=$X_OC_DIR/system/odm/etc/camera
    [ -d "$X_OF_CAM" ] && [ ! -L "$X_OF_CAM" ] || return 0
    X_OF_ARM=0
    xpan_op_is_c16_completion && X_OF_ARM=1
    X_OF_N=0
    X_OF_BAD=0
    for X_OF_NAME in meishe_lut filters_lut basictone; do
        X_OF_ONE=$X_OF_CAM/$X_OF_NAME
        X_OF_OLD=$X_OF_ONE.xpan-c16-hidden
        if [ -e "$X_OF_OLD" ] || [ -L "$X_OF_OLD" ]; then
            X_OF_N=$((X_OF_N + 1))
        elif [ "$X_OF_ARM" = 1 ] && [ -d "$X_OF_ONE" ] && [ ! -L "$X_OF_ONE" ]; then
            if mv "$X_OF_ONE" "$X_OF_OLD" 2>/dev/null; then
                X_OF_N=$((X_OF_N + 1))
                X_OC_CHANGED=1
            else
                X_OF_BAD=$((X_OF_BAD + 1))
            fi
        fi
    done
    for X_OF_OLD in "$X_OF_CAM"/gamma_*_conf.json.xpan-c16-hidden; do
        [ -e "$X_OF_OLD" ] || [ -L "$X_OF_OLD" ] || continue
        X_OF_N=$((X_OF_N + 1))
    done
    for X_OF_ONE in "$X_OF_CAM"/gamma_*_conf.json; do
        [ -f "$X_OF_ONE" ] && [ ! -L "$X_OF_ONE" ] || continue
        case "${X_OF_ONE##*/}" in gamma_*_conf.json) ;; *) continue ;; esac
        X_OF_OLD=$X_OF_ONE.xpan-c16-hidden
        [ -e "$X_OF_OLD" ] || [ -L "$X_OF_OLD" ] && continue
        if [ "$X_OF_ARM" = 1 ]; then
            if mv "$X_OF_ONE" "$X_OF_OLD" 2>/dev/null; then
                X_OF_N=$((X_OF_N + 1))
                X_OC_CHANGED=1
            else
                X_OF_BAD=$((X_OF_BAD + 1))
            fi
        fi
    done
    if [ "$X_OF_N" != 0 ]; then
        X_OC_SUM="$X_OC_SUM c16-filter-assets=hidden($X_OF_N)"
        [ "$X_OF_BAD" = 0 ] && [ "$X_OC_CHANGED" = 1 ] &&
            xpan_log 'op-compat: C16 filter/tone assets hidden; stock C17 assets apply after reboot'
    fi
    [ "$X_OF_BAD" = 0 ] || X_OC_SUM="$X_OC_SUM c16-filter-assets=hide-failed($X_OF_BAD)"
    return 0
}
# Mirror OPCameraPro's attached-module completion to this phone's C17 originals.
# Gate: only a C17 ROM. No attached module, unknown identity, unknown hashes,
# symlinks and failed copies stay untouched. Every replaced file is backed up
# under $X_STATE/op-compat-backup. KernelSU mounts module files early, so a
# changed file takes effect on the next boot; the log states that.
xpan_op_compat() {
    X_OC_DIR=${XPAN_TEST_OP_DIR:-/data/adb/modules/OPCameraPro}
    X_OC_FW=$X_OC_DIR/system/product/framework
    X_OC_SRC=$X_MOD/payload/op-compat
    X_OC_MAN=$X_OC_SRC/MANIFEST
    X_OC_STATE=${X_STATE:-/data/adb/xpan-runtime}
    X_OC_BACK=$X_OC_STATE/op-compat-backup
    X_OC_SUM=
    X_OC_CHANGED=0
    if [ "${XPAN_TEST_MODE:-}" = 1 ] && [ -n "${XPAN_TEST_ROM_RELEASE:-}" ]; then
        X_OC_REL=$XPAN_TEST_ROM_RELEASE
    else
        X_OC_REL=$(xpan_bounded getprop ro.build.version.release 2>/dev/null) || X_OC_REL=
    fi
    case "$X_OC_REL" in
        17|17.*) ;;
        *) xpan_log "op-compat: rom release '$X_OC_REL' is not C17; completion untouched"; return 0 ;;
    esac
    [ -f "$X_OC_DIR/module.prop" ] && [ ! -L "$X_OC_DIR/module.prop" ] || {
        xpan_log 'op-compat: no OPCameraPro attached module; nothing to do'
        return 0
    }
    X_OC_ID=$(xpan_bounded sed -n 's/^id=//p' "$X_OC_DIR/module.prop" 2>/dev/null | head -n 1) || X_OC_ID=
    [ "$X_OC_ID" = OPCameraPro ] || {
        xpan_log 'op-compat: attached module identity mismatch; untouched'
        return 0
    }
    [ -d "$X_OC_FW" ] || {
        xpan_log 'op-compat: attached module has no framework directory; untouched'
        return 0
    }
    # C16 filter LUTs must not shadow the C17 engine before any file work.
    xpan_op_hide_c16_filters
    # Bundled stock C17 files always win for their exact names.
    if [ -f "$X_OC_MAN" ] && [ ! -L "$X_OC_MAN" ]; then
        while IFS="$(printf '\t')" read -r X_OC_NAME X_OC_PIN; do
            [ -n "$X_OC_NAME" ] || continue
            case "$X_OC_NAME" in */*|*..*) X_OC_SUM="$X_OC_SUM bundled-unsafe"; continue ;; esac
            [ -n "$X_OC_PIN" ] || { X_OC_SUM="$X_OC_SUM $X_OC_NAME=manifest-invalid"; continue; }
            X_OC_T=$X_OC_FW/$X_OC_NAME
            X_OC_S=$X_OC_SRC/framework/$X_OC_NAME
            if [ ! -f "$X_OC_T" ] || [ -L "$X_OC_T" ]; then
                X_OC_SUM="$X_OC_SUM $X_OC_NAME=absent"; continue
            fi
            if ! xpan_hash "$X_OC_T"; then
                X_OC_SUM="$X_OC_SUM $X_OC_NAME=unreadable"; continue
            fi
            if [ "$X_HASH" = "$X_OC_PIN" ]; then
                X_OC_SUM="$X_OC_SUM $X_OC_NAME=current"; continue
            fi
            if [ ! -f "$X_OC_S" ] || [ -L "$X_OC_S" ] || ! xpan_hash "$X_OC_S" ||
                [ "$X_HASH" != "$X_OC_PIN" ]; then
                X_OC_SUM="$X_OC_SUM $X_OC_NAME=source-invalid"; continue
            fi
            if ! xpan_op_backup "$X_OC_T" "$X_OC_NAME"; then
                X_OC_SUM="$X_OC_SUM $X_OC_NAME=backup-failed"; continue
            fi
            if ! xpan_op_put "$X_OC_S" "$X_OC_T" "$X_OC_PIN"; then
                X_OC_SUM="$X_OC_SUM $X_OC_NAME=replace-failed"; continue
            fi
            X_OC_CHANGED=1
            X_OC_SUM="$X_OC_SUM $X_OC_NAME=replaced"
        done < "$X_OC_MAN"
    else
        xpan_log 'op-compat: bundled C17 manifest missing; only mirror sync remains'
    fi
    # Every other completion file that mirrors a device path is compared with
    # the live C17 stock: equal stays, different is backed up and synced.
    for X_OC_TREE in product/framework odm/etc/camera; do
        X_OC_BASE=$X_OC_DIR/system/$X_OC_TREE
        [ -d "$X_OC_BASE" ] || continue
        X_OC_LIST=$X_OC_STATE/op-compat.list
        xpan_bounded find "$X_OC_BASE" -type f 2>/dev/null > "$X_OC_LIST" || continue
        while IFS= read -r X_OC_T; do
            [ -n "$X_OC_T" ] || continue
            X_OC_REL=${X_OC_T#"$X_OC_DIR/system"}
            case "$X_OC_REL" in /product/*|/odm/*) ;; *) continue ;; esac
            X_OC_STOCK=$X_OC_REL
            if [ "${XPAN_TEST_MODE:-}" = 1 ] && [ -n "${XPAN_TEST_STOCK_ROOT:-}" ]; then
                X_OC_STOCK=$XPAN_TEST_STOCK_ROOT$X_OC_REL
            fi
            [ -f "$X_OC_STOCK" ] && [ ! -L "$X_OC_STOCK" ] || continue
            xpan_hash "$X_OC_T" && X_OC_OH=$X_HASH || continue
            xpan_hash "$X_OC_STOCK" && X_OC_SH=$X_HASH || continue
            [ "$X_OC_OH" = "$X_OC_SH" ] && continue
            X_OC_SUM="$X_OC_SUM ${X_OC_REL#/}=mirror-diff"
            if ! xpan_op_backup "$X_OC_T" "mirror${X_OC_REL}"; then
                X_OC_SUM="$X_OC_SUM backup-failed"; continue
            fi
            if ! xpan_op_put "$X_OC_STOCK" "$X_OC_T" "$X_OC_SH"; then
                X_OC_SUM="$X_OC_SUM sync-failed"; continue
            fi
            X_OC_CHANGED=1
            X_OC_SUM="$X_OC_SUM synced"
        done < "$X_OC_LIST"
        rm -f "$X_OC_LIST" 2>/dev/null
    done
    xpan_write "$X_OC_STATE/op.compat" "compat_version=2
boot_id=${X_BOOT_ID:-unknown}
rom_release=$X_OC_REL
detail=$X_OC_SUM
changed=$X_OC_CHANGED" 2>/dev/null || true
    if [ "$X_OC_CHANGED" = 1 ]; then
        xpan_log "op-compat: C17 completion written:$X_OC_SUM; reboot makes it effective"
    else
        xpan_log "op-compat: no change:$X_OC_SUM"
    fi
    return 0
}

xpan_install() {
    xpan_lock || { xpan_log 'install lock busy'; return 1; }
    # A rejected orig-only update must NOT dismantle already existing mounts.
    xpan_prepare || { xpan_unlock; return 1; }
    xpan_inactive || { xpan_unlock; return 1; }
    xpan_safe_path "$X_MOD/lifecycle.sh" && [ -f "$X_MOD/lifecycle.sh" ] || { xpan_unlock; return 1; }
    xpan_life_unlock
    X_INSTALL_RC=0
    if [ "${XPAN_TEST_MODE:-}" = 1 ]; then
        sh "$X_MOD/lifecycle.sh" install "$X_MOD" "$XPAN_TEST_ROOT/uuid.source" || X_INSTALL_RC=1
    else
        sh "$X_MOD/lifecycle.sh" install "$X_MOD" || X_INSTALL_RC=1
    fi
    xpan_life_lock || { xpan_unlock; return 1; }
    [ "$X_INSTALL_RC" = 0 ] && xpan_markers && xpan_tokens && xpan_qualify &&
        xpan_receipt && xpan_current originals && xpan_apk && xpan_parent_commit || { xpan_unlock; return 1; }
    xpan_safe_path "$X_MOD/payload/parent.inputs" && rm -f "$X_MOD/payload/parent.inputs" || { xpan_unlock; return 1; }
    xpan_unlock
    xpan_op_compat || true
    xpan_log 'strict APK phase complete; configs remain unmounted/inactive until qualified service tick; select LSPosed manually'
}
xpan_main() {
    [ "$#" = 2 ] || return 1
    X_AUTHORIZED=0
    if ! xpan_init "$1" "$2"; then
        case "$1:$X_AUTHORIZED" in boot:1|tick:1|stop:1) xpan_inactive ;; esac
        return 1
    fi
    case "$1" in
        install) xpan_install ;;
        tick) xpan_tick ;;
        uninstall)
            xpan_lock || return 1
            # An old uninstall invocation must not revoke a new/staged instance.
            # Removal needs no available profiles/PM/APK attestation, only exact
            # lifecycle token plus independent mount ownership journal evidence.
            if [ -e "$X_STAGED" ] || [ -L "$X_STAGED" ] ||
                ! xpan_read "$X_MOD/.xpan-instance" || ! xpan_uuid "$X_VALUE"; then
                xpan_unlock
                return 1
            fi
            X_UNINSTALL_TOKEN=$X_VALUE
            xpan_read "$X_LIFE/current.token" && [ "$X_VALUE" = "$X_UNINSTALL_TOKEN" ] || {
                xpan_unlock
                return 1
            }
            X_TOKEN=$X_UNINSTALL_TOKEN
            xpan_parent_start || { xpan_inactive; xpan_unlock; return 1; }
            X_STOP_RC=0
            xpan_inactive || X_STOP_RC=1
            xpan_cleanup || X_STOP_RC=1
            xpan_unlock
            return "$X_STOP_RC"
            ;;
        boot|stop)
            xpan_inactive
            xpan_lock || return 1
            if xpan_read "$X_MOD/.xpan-instance" && xpan_uuid "$X_VALUE"; then
                X_TOKEN=$X_VALUE
                xpan_parent_start || { xpan_unlock; return 1; }
            fi
            X_STOP_RC=0
            xpan_cleanup || X_STOP_RC=1
            xpan_unlock
            return "$X_STOP_RC"
            ;;
        service)
            # Bind once under both locks; a surviving old process never adopts a
            # replacement token or clears its readiness/journals.
            xpan_lock || return 1
            xpan_worker_bind || { xpan_unlock; return 1; }
            X_TOKEN=$X_WORKER_TOKEN
            xpan_parent_start || { xpan_inactive; xpan_unlock; return 1; }
            X_PARENT_SEALED_HASH=$X_PARENT_HASH
            X_PARENT_SEALED_INODE=$X_PARENT_INODE
            X_PARENT_SEALED_KIND=$X_PARENT_KIND
            xpan_unlock
            # Long-lived functions survive removal of MODPATH and can clean only
            # this bound instance while its lifecycle authority remains current.
            xpan_op_compat || true
            while :; do
                X_SERVICE_RC=0
                xpan_tick || X_SERVICE_RC=$?
                [ "$X_SERVICE_RC" != 2 ] || return 0
                if [ ! -d "$X_MOD" ] || [ -e "$X_MOD/remove" ]; then
                    [ -e "$X_STATE/unit.mount" ] || [ -e "$X_STATE/feature.mount" ] ||
                        [ -e "$X_STATE/vendor.mount" ] || return 0
                fi
                sleep "$X_DELAY" || return 1
            done
            ;;
        *) return 1 ;;
    esac
}
xpan_main "$@"
