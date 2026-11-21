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
xpan_sdk() {
    X_EFFECTIVE_SDK=; X_EFFECTIVE_ADAPTER=
    xpan_framework_hash "$X_SDK" || return 1
    X_EFFECTIVE_SDK=$X_HASH
    xpan_framework_hash "$X_ADAPTER" || return 1
    X_EFFECTIVE_ADAPTER=$X_HASH
    [ "$X_EFFECTIVE_SDK" = "$X_SDK_HASH" ] && [ "$X_EFFECTIVE_ADAPTER" = "$X_ADAPTER_HASH" ]
}
xpan_install_sdk() {
    X_INSTALL_REBOOT=0
    xpan_sdk && return 0
    [ "$X_ID" = c17 ] || { xpan_log '当前相机 SDK 与本机配置不匹配'; return 1; }
    case "$X_EFFECTIVE_SDK:$X_EFFECTIVE_ADAPTER" in
        "$X_SDK_HASH:$X_ADAPTER_HASH"|"$X_SDK_HASH:ac0b595faae8d3f684aa457f223fbfb0f167f0d0e0cc461ef08dbbe52d6e61cf"|"37a13d408f7ee95b8e954c2ef11c44a0aee433da45958cc7ddb80c688e0692c6:$X_ADAPTER_HASH"|"37a13d408f7ee95b8e954c2ef11c44a0aee433da45958cc7ddb80c688e0692c6:ac0b595faae8d3f684aa457f223fbfb0f167f0d0e0cc461ef08dbbe52d6e61cf") ;;
        *) xpan_log '当前相机 SDK 不是本机 C17 或已知的 OPCameraPro C16 补全'; return 1 ;;
    esac
    # 仅确认同名 C17 替换源已就位时允许待重启安装，不放宽运行期 SDK 契约。
    local X_PENDING_DIR X_PENDING_NAME X_PENDING_PIN X_PENDING_LIVE
    X_PENDING_DIR=${XPAN_TEST_OP_DIR:-/data/adb/modules/OPCameraPro}
    xpan_safe_path "$X_PENDING_DIR" && [ -f "$X_PENDING_DIR/module.prop" ] &&
        [ ! -L "$X_PENDING_DIR/module.prop" ] && grep -qx 'id=OPCameraPro' "$X_PENDING_DIR/module.prop" || {
        xpan_log '旧 SDK 仍在生效，未找到已更新的 OPCameraPro 补全'; return 1;
    }
    for X_PENDING_NAME in com.oplus.camera.unit.sdk.jar com.oplus.camera.unit.sdk.adapter.jar; do
        case "$X_PENDING_NAME" in
            com.oplus.camera.unit.sdk.jar) X_PENDING_PIN=$X_SDK_HASH; X_PENDING_LIVE=$X_EFFECTIVE_SDK ;;
            *) X_PENDING_PIN=$X_ADAPTER_HASH; X_PENDING_LIVE=$X_EFFECTIVE_ADAPTER ;;
        esac
        [ "$X_PENDING_LIVE" != "$X_PENDING_PIN" ] || continue
        xpan_hash "$X_MOD/payload/op-compat/framework/$X_PENDING_NAME" && [ "$X_HASH" = "$X_PENDING_PIN" ] &&
            xpan_hash "$X_PENDING_DIR/system/product/framework/$X_PENDING_NAME" && [ "$X_HASH" = "$X_PENDING_PIN" ] || {
            xpan_log "旧 SDK 仍在生效，C17 补全尚未成功更新：$X_PENDING_NAME"; return 1;
        }
    done
    X_INSTALL_REBOOT=1
    xpan_log 'OPCameraPro 的 C16 SDK 仍由本次启动挂载；补全已更新，重启手机后生效'
}
xpan_reboot_ready() {
    [ -e "$X_MOD/.xpan-reboot-required" ] || [ -L "$X_MOD/.xpan-reboot-required" ] || return 0
    xpan_read "$X_MOD/.xpan-reboot-required" && xpan_uuid "$X_VALUE" || return 1
    [ "$X_VALUE" != "$X_BOOT_ID" ] || return 1
    # 记录的启动已经是过去式＝真实重启发生过；立即消费标记。
    # 不消费的话，软重启（system_server 重启、boot_id 不变）会让本检查
    # 永远拒绝激活，hook 资格被拒，相机点 XPAN 因 BaseMode 空引用崩溃。
    rm -f "$X_MOD/.xpan-reboot-required" || return 1
    return 0
}
xpan_compat_settled() {
    # op.compat 是“本启动内补全已改过”的持久事实：即使待重启标记写入失败，
    # 也必须继续拒绝激活，直到真重启（boot_id 变化）后重新核对。
    # 这是本模块自己写的 5 行状态文件，直接读，避免每 tick 额外起超时进程。
    [ -f "$X_STATE/op.compat" ] && [ ! -L "$X_STATE/op.compat" ] || return 0
    X_CS_BOOT=; X_CS_CHANGED=
    while IFS='=' read -r X_CS_KEY X_CS_VAL; do
        case "$X_CS_KEY" in
            boot_id) X_CS_BOOT=$X_CS_VAL ;;
            changed) X_CS_CHANGED=$X_CS_VAL ;;
        esac
    done < "$X_STATE/op.compat"
    [ "$X_CS_BOOT" = "$X_BOOT_ID" ] || return 0
    [ "$X_CS_CHANGED" != 1 ]
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
    if [ "${1:-}" = install ]; then
        xpan_install_sdk || return 1
    else
        xpan_sdk || return 1
    fi
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
    # xpan_owned 的挂载行、父层与 journal 检查已在此全部成立且期间无任何变更，
    # 只补齐剩余的哈希与 inode 对账，复用上方一次读取结果，避免重复解析 mountinfo。
    [ "$X_MHASH" = "$X_JHASH" ] && xpan_inode "$X_TARGET" &&
        [ "$X_INODE" = "$X_JINODE" ] || { xpan_log "foreign mount retained: $1"; return 1; }
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
    local X_PCUR_HASH X_PCUR_INODE X_PCUR_KIND
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
    if [ "$X_PBOOT" != "$X_BOOT_ID" ]; then
        xpan_parent_refresh "$X_PBOOT" || return 1
        return 0
    fi
    [ "$X_PNS" = "$X_NAMESPACE" ] && [ "$X_PTOKEN" = "$X_TOKEN" ] || return 1
    # 同一次启动内父层也可能变化：KernelSU 元模块（如 mountify）可能晚于
    # post-fs-data 才挂上 OPCameraPro 的 /odm/etc overlay，父层从 normal 变为
    # ksu。boot/namespace/token 仍一致时自动刷新上下文，而不是永久拒绝。
    # service 刚启动时 X_PARENT_HASH 还是空的，此时用 parent_start 刚刚观测到的
    # XP_PARENT_* 作为“现状”比对，否则旧记录会被无条件接受、随后在
    # xpan_parent_mounts 里被拒，表现为 service 启动即退出（active 永远为 0）。
    if [ -n "$XP_PARENT_HASH" ]; then
        # parent_start 每次都先观测；现状优先于本进程缓存，否则常驻 worker
        # 会把“缓存==旧记录”当成没变化，随后在 parent_mounts 里失败。
        X_PCUR_HASH=$XP_PARENT_HASH
        X_PCUR_INODE=$XP_PARENT_INODE
        X_PCUR_KIND=$XP_PARENT_KIND
    else
        X_PCUR_HASH=$X_PARENT_HASH
        X_PCUR_INODE=$X_PARENT_INODE
        X_PCUR_KIND=$X_PARENT_KIND
    fi
    if [ -n "$X_PCUR_HASH" ] && { [ "$X_PCUR_HASH" != "$X_PHASH" ] ||
        [ "$X_PCUR_INODE" != "$X_PINODE" ] || [ "$X_PCUR_KIND" != "$X_PKIND" ]; }; then
        # 若本实例已有自有挂载，xpan_parent_refresh 内部会拒绝，不会乱动。
        xpan_parent_refresh "$X_BOOT_ID" || return 1
        return 0
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
xpan_parent_refresh() {
    local X_REFRESH_ROLE X_REFRESH_OLD
    X_REFRESH_OLD=$1
    [ "$X_LOCKED:$X_LIFE_LOCKED" = 1:1 ] && xpan_tokens || return 1
    # 跨启动只清理已失效且无当前挂载的记录，绝不借旧记录卸载本次启动的文件。
    xpan_parent_observe "$X_INFO" "$X_STOCK" "$X_PARENT_POINT" "$X_PARTITION_POINT" || return 1
    X_PARENT_HASH=$XP_PARENT_HASH; X_PARENT_INODE=$XP_PARENT_INODE; X_PARENT_KIND=$XP_PARENT_KIND
    X_PARENT_V2=1; X_PARENT_INSTANCE=0
    xpan_inactive || return 1
    for X_REFRESH_ROLE in unit feature vendor; do
        xpan_role "$X_REFRESH_ROLE"
        xpan_mount_line && [ -z "$X_MLINE" ] || return 1
        if [ -e "$X_STATE/$X_REFRESH_ROLE.mount" ] || [ -L "$X_STATE/$X_REFRESH_ROLE.mount" ]; then
            xpan_journal "$X_REFRESH_ROLE" && [ "$X_JBOOT" = "$X_REFRESH_OLD" ] &&
                [ "$X_JBOOT" != "$X_BOOT_ID" ] && [ "$X_JTOKEN" = "$X_TOKEN" ] || return 1
        fi
    done
    xpan_parent_mounts || return 1
    for X_REFRESH_ROLE in unit feature vendor; do
        xpan_role "$X_REFRESH_ROLE"
        xpan_mount_line && [ -z "$X_MLINE" ] || return 1
        [ ! -e "$X_STATE/$X_REFRESH_ROLE.mount" ] || rm -f "$X_STATE/$X_REFRESH_ROLE.mount" || return 1
    done
    X_PARENT_PREVIOUS=$(xpan_bounded sha256sum "$X_STATE/parent.context") || return 1
    X_PARENT_PREVIOUS=${X_PARENT_PREVIOUS%% *}
    # 刷新是显式重新验证：递归 pin 必须认可新父层，所以先更新 seal；
    # commit 失败时恢复原 seal，仍按旧值 fail-closed。
    X_SEAL_OLD_HASH=$X_PARENT_SEALED_HASH
    X_SEAL_OLD_INODE=$X_PARENT_SEALED_INODE
    X_SEAL_OLD_KIND=$X_PARENT_SEALED_KIND
    X_PARENT_SEALED_HASH=$XP_PARENT_HASH
    X_PARENT_SEALED_INODE=$XP_PARENT_INODE
    X_PARENT_SEALED_KIND=$XP_PARENT_KIND
    if ! xpan_parent_commit; then
        X_PARENT_SEALED_HASH=$X_SEAL_OLD_HASH
        X_PARENT_SEALED_INODE=$X_SEAL_OLD_INODE
        X_PARENT_SEALED_KIND=$X_SEAL_OLD_KIND
        return 1
    fi
    xpan_log '已自动刷新本次启动的父层上下文，运行前仍检查真实配置与 SDK'
}
xpan_parent_prepare() {
    xpan_parent_observe "$X_INFO" "$X_STOCK" "$X_PARENT_POINT" "$X_PARTITION_POINT" || return 1
    X_PARENT_HASH=$XP_PARENT_HASH; X_PARENT_INODE=$XP_PARENT_INODE; X_PARENT_KIND=$XP_PARENT_KIND
    X_PARENT_INSTANCE=0; X_PARENT_V2=1; X_PARENT_RECOVERING=0; X_PARENT_PREVIOUS=
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
    if [ "$X_READY" != 1 ] || ! { xpan_markers && xpan_tokens && xpan_reboot_ready && xpan_compat_settled && xpan_qualify && xpan_receipt && xpan_apk; }; then
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
xpan_install_cleanup() {
    local X_MIGRATE_ROLE X_MIGRATE_PATH X_MIGRATE_TOKENS
    X_MIGRATE_TOKENS='|'
    xpan_log '开始清理旧版本残留挂载与失效状态'
    for X_MIGRATE_PATH in "$X_LIFE/current.token" "$X_ACTIVE/.xpan-instance" "$X_MOD/.xpan-instance"; do
        if xpan_read "$X_MIGRATE_PATH" && xpan_uuid "$X_VALUE"; then
            X_MIGRATE_TOKENS=$X_MIGRATE_TOKENS$X_VALUE'|'
        fi
    done
    xpan_parent_observe "$X_INFO" "$X_STOCK" "$X_PARENT_POINT" "$X_PARTITION_POINT" || {
        xpan_log '无法读取当前挂载父层，未撤销任何旧挂载'; return 1;
    }
    X_PARENT_HASH=$XP_PARENT_HASH; X_PARENT_INODE=$XP_PARENT_INODE; X_PARENT_KIND=$XP_PARENT_KIND
    X_PARENT_INSTANCE=1; X_PARENT_V2=1
    xpan_inactive || { xpan_log '无法清除旧实例的启用状态'; return 1; }
    # 先用旧记录撤销自己的挂载，再清失效状态；保留新模块文件和 OPC 备份。
    for X_MIGRATE_ROLE in vendor feature unit; do
        xpan_role "$X_MIGRATE_ROLE"
        xpan_mount_line || { xpan_log "挂载被其他模块覆盖，保留原记录：$X_MIGRATE_ROLE"; return 1; }
        if [ -n "$X_MLINE" ]; then
            xpan_journal "$X_MIGRATE_ROLE" || { xpan_log "无法证明旧挂载归属：$X_MIGRATE_ROLE"; return 1; }
            case "$X_MIGRATE_TOKENS" in *"|$X_JTOKEN|"*) ;; *) xpan_log "非旧实例的挂载已保留：$X_MIGRATE_ROLE"; return 1 ;; esac
            # xpan_unmount_role 复用上方一次读取并完整复核所有权；不再重复预检。
            xpan_unmount_role "$X_MIGRATE_ROLE" || {
                xpan_log "旧挂载与所有权记录不一致，未盲目卸载：$X_MIGRATE_ROLE"; return 1;
            }
        else
            X_MIGRATE_PATH=$X_STATE/$X_MIGRATE_ROLE.mount
            xpan_safe_path "$X_MIGRATE_PATH" || return 1
            [ ! -e "$X_MIGRATE_PATH" ] || { [ -f "$X_MIGRATE_PATH" ] && rm -f "$X_MIGRATE_PATH"; } || return 1
        fi
    done
    for X_MIGRATE_PATH in "$X_STATE/parent.context" "$X_MOD/payload/parent.approval" \
        "$X_MOD/payload/parent.inputs" "$X_MOD/.xpan-apk-identity" "$X_MOD/.xpan-reboot-required"; do
        xpan_safe_path "$X_MIGRATE_PATH" || return 1
        [ ! -e "$X_MIGRATE_PATH" ] || { [ -f "$X_MIGRATE_PATH" ] && rm -f "$X_MIGRATE_PATH"; } || return 1
    done
    X_PARENT_HASH=; X_PARENT_INODE=; X_PARENT_KIND=; X_PARENT_INSTANCE=0
    xpan_log '已自动清理旧 Xpan 挂载与失效状态，保留新模块和兼容备份'
}
xpan_prepare() {
    xpan_parent_prepare || { xpan_log '无法自动刷新安装父层，当前挂载尚未清理'; return 1; }
    xpan_markers || { xpan_log '安装目录不可用，或已有另一份待更新模块'; return 1; }
    xpan_qualify install || { xpan_log '本机相机配置或实际 SDK 不符合原有支持契约'; return 1; }
    xpan_current originals || { xpan_log '当前相机配置不是未挂载的原厂文件，其他模块的挂载未清理'; return 1; }
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
    xpan_receipt && xpan_qualify install && xpan_current originals && xpan_parent_phase
}
xpan_op_size() {
    # 只读尺寸预检，前提与 xpan_hash 一致（常规文件、非空、安全路径）；失败不留值。
    xpan_safe_path "$1" && [ -f "$1" ] && [ -s "$1" ] || return 1
    X_OC_SIZE=$(xpan_bounded stat -Lc '%s' "$1" 2>/dev/null) || return 1
    case "$X_OC_SIZE" in ''|*[!0-9]*) return 1 ;; esac
    [ "$X_OC_SIZE" != 0 ]
}
xpan_op_differs() {
    # 只读“字节必然不同”预检：两侧尺寸可读且不同 → 返回 0（可免哈希判定不同）；
    # 否则返回 1（仍需哈希判定）。写前校验与回读校验绝不走此预检。
    xpan_op_size "$1" || return 1
    X_OC_SIZE_A=$X_OC_SIZE
    xpan_op_size "$2" || return 1
    [ "$X_OC_SIZE_A" != "$X_OC_SIZE" ]
}
# OPCameraPro 兼容 helper 保持顶层定义；只改附属模块，来源不足不是安装门禁。
xpan_op_backup() {
    # 首份备份不覆盖；OP 更新后的不同旧件另存哈希后缀。
    local name dest oldhash tmp
    xpan_hash "$1" || return 1
    oldhash=$X_HASH; name=$2
    case "$name" in ''|/*|*..*|*' '*|*'\\'*|*'
'*) return 1 ;; esac
    dest=$X_OC_BACK/$name
    xpan_safe_path "$dest" || return 1
    if [ -e "$dest" ]; then
        # 只读比较先比文件大小：尺寸必然不同时免哈希即可判定，节省重复整读。
        if ! xpan_op_differs "$1" "$dest"; then
            xpan_hash "$dest" || return 1
            [ "$X_HASH" != "$oldhash" ] || return 0
        fi
        dest=$dest.$oldhash
        xpan_safe_path "$dest" || return 1
        if [ -e "$dest" ]; then
            if xpan_op_differs "$1" "$dest"; then return 1; fi
            xpan_hash "$dest" && [ "$X_HASH" = "$oldhash" ]
            return $?
        fi
    fi
    mkdir -p "${dest%/*}" 2>/dev/null || return 1
    tmp=$dest.xpan-compat.$$
    xpan_safe_path "$tmp" && [ ! -e "$tmp" ] || return 1
    (set -C; xpan_bounded cat "$1" > "$tmp") || { rm -f "$tmp"; return 1; }
    if ! { chmod 0644 "$tmp" && xpan_hash "$tmp" && [ "$X_HASH" = "$oldhash" ] && mv -n "$tmp" "$dest"; }; then
        rm -f "$tmp"; return 1
    fi
    rm -f "$tmp"
    xpan_hash "$dest" && [ "$X_HASH" = "$oldhash" ]
}
xpan_op_put() {
    # 先校验临时副本再原子替换；回读失败用原文件硬链接回滚。
    local tmp keep oldhash
    X_OP_WRITTEN=0
    xpan_hash_valid "$3" && xpan_hash "$1" && [ "$X_HASH" = "$3" ] && xpan_safe_path "$2" || return 1
    [ ! -e "$2" ] || [ -f "$2" ] || return 1
    tmp=$2.xpan-compat.$$; keep=$2.xpan-compat-old.$$
    xpan_safe_path "$tmp" && xpan_safe_path "$keep" && [ ! -e "$tmp" ] && [ ! -e "$keep" ] || return 1
    (set -C; xpan_bounded cat "$1" > "$tmp") || { rm -f "$tmp"; return 1; }
    if ! { chmod 0644 "$tmp" && xpan_hash "$tmp" && [ "$X_HASH" = "$3" ]; }; then rm -f "$tmp"; return 1; fi
    oldhash=
    if [ -e "$2" ]; then
        xpan_hash "$2" && oldhash=$X_HASH && ln "$2" "$keep" 2>/dev/null || { rm -f "$tmp"; return 1; }
    fi
    if ! { xpan_safe_path "$2" && mv -f "$tmp" "$2" 2>/dev/null; }; then rm -f "$tmp" "$keep"; return 1; fi
    X_OP_WRITTEN=1
    if xpan_hash "$2" && [ "$X_HASH" = "$3" ]; then rm -f "$keep"; return 0; fi
    if [ -n "$oldhash" ]; then
        mv -f "$keep" "$2" 2>/dev/null && X_OP_WRITTEN=0
    else
        rm -f "$2" 2>/dev/null && X_OP_WRITTEN=0
    fi
    return 1
}
# C16 JAR 指纹只作日志观察，不能代表 native/素材已升级，也不激活整目录隐藏。
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
    # 成功哈希暂存到本次调用局部变量，供同一次 op_compat 的 MANIFEST 预检复用；
    # 期间无任何写入，写后回读校验仍走 xpan_hash 全量哈希。
    if [ -f "$X_OF_U" ] && [ ! -L "$X_OF_U" ] && xpan_hash "$X_OF_U"; then
        X_OC_H16_U=$X_HASH
        [ "$X_HASH" = "$X_OF_UP" ] && return 0
    fi
    if [ -f "$X_OF_A" ] && [ ! -L "$X_OF_A" ] && xpan_hash "$X_OF_A"; then
        X_OC_H16_A=$X_HASH
        [ "$X_HASH" = "$X_OF_AP" ] && return 0
    fi
    return 1
}
xpan_op_mounts() {
    # 一次读取既有 mountinfo；仅提取来源，不扩展父层授权协议。
    [ -f "${X_INFO:-/proc/self/mountinfo}" ] && [ ! -L "${X_INFO:-/proc/self/mountinfo}" ] || return 1
    LC_ALL=C xpan_bounded awk -v BINMODE=3 '
        function path(p) { return p ~ /^\// && p !~ /[[:cntrl:][:space:]:,]/ &&
            p !~ /\/\// && p !~ /(^|\/)\.\.?($|\/)/ && (p=="/" || p !~ /\/$/) }
        { bytes+=length($0)+1
          if(bytes>1048576 || NR>8192 || length($0)>4096 || NF<10) exit 1
          sep=7; while(sep<=NF && $sep!="-") sep++
          if(NF!=sep+3 || $1 !~ /^[1-9][0-9]*$/ || seen[$1]++ ||
             !path($4) || !path($5)) exit 1
          print $1 "\t" $4 "\t" $5 "\t" $6 "\t" $(sep+1) "\t" $(sep+2) "\t" $(sep+3)
        }
    ' "${X_INFO:-/proc/self/mountinfo}"
}
xpan_op_source_safe() {
    xpan_safe_path "$1" || return 1
    case "$1" in
        "$X_OC_DIR"|"$X_OC_DIR"/*|"$X_MOD"|"$X_MOD"/*|/data|/data/*|/mnt/vendor|/mnt/vendor/*|*/data/adb/modules/*|*/data/adb/modules_update/*) return 1 ;;
    esac
    if [ "${XPAN_TEST_MODE:-}" = 1 ] && [ -n "${XPAN_TEST_ROOT:-}" ]; then
        case "$1" in "$XPAN_TEST_ROOT/data"|"$XPAN_TEST_ROOT/data"/*|"$XPAN_TEST_ROOT/mnt/vendor"|"$XPAN_TEST_ROOT/mnt/vendor"/*) return 1 ;; esac
    fi
    return 0
}
xpan_op_clean_origin() {
    # 最深挂载点优先，同点取当前层；有效 overlay 和模块 bind 不是原厂。
    [ -n "$X_OC_MOUNTS" ] || return 1
    printf '%s\n' "$X_OC_MOUNTS" | LC_ALL=C xpan_bounded awk -F '\t' -v p="$1" '
        function under(p,b) { return p==b || b=="/" || index(p,b "/")==1 }
        under(p,$3) && (length($3)>depth || (length($3)==depth && $1+0>id)) {
            depth=length($3); id=$1+0; root=$2; opts=$4; fs=$5; source=$6; super=$7
        }
        END { if(!depth || root!="/" || fs !~ /^(erofs|ext4)$/ || source !~ /^\/dev\/block\// ||
            opts !~ /(^|,)ro(,|$)/ || super !~ /(^|,)ro(,|$)/) exit 1 }
    '
}
xpan_op_stock_roots() {
    # 挂载候选解析：最深挂载点优先，同点取当前层；语义与 v2.0 逐文件版本一致。
    printf '%s\n' "$X_OC_MOUNTS" | LC_ALL=C xpan_bounded awk -F '\t' -v p="$1" '
        function under(p,b) { return p==b || b=="/" || index(p,b "/")==1 }
        function path(p) { return p ~ /^\// && p !~ /[[:cntrl:][:space:],\\]/ &&
            p !~ /\/\// && p !~ /(^|\/)\.\.?($|\/)/ && (p=="/" || p !~ /\/$/) }
        under(p,$3) { if(length($3)>depth) { depth=length($3); n=0 }
            if(length($3)==depth) rows[++n]=$0 }
        END { for(pass=1;pass<=n;pass++) {
            best=0; for(i=1;i<=n;i++) if(!(i in done)) {
                split(rows[i],a,"\t"); if(a[1]+0>best) { best=a[1]+0; chosen=i }
            }
            done[chosen]=1; split(rows[chosen],a,"\t"); point=a[3]
            if(a[5]!="overlay") { print point "\t" p "\t1"; continue }
            if(a[2]!="/") continue
            count=split(a[7],opts,","); lower=""; fields=0
            for(i=1;i<=count;i++) if(index(opts[i],"lowerdir=")==1) { lower=substr(opts[i],10); fields++ }
            if(fields!=1) continue
            count=split(lower,dirs,":")
            for(i=1;i<=count;i++) if(path(dirs[i]) && dirs[i]!=point)
                print dirs[i] "\t" dirs[i] substr(p,length(point)+1) "\t" (count==1 ? 1 : 0)
        } }
    '
}
xpan_op_stock_memo() {
    # 目录级缓存：同一父目录只解析一次挂载候选，并为每行预注校验结论，行内字段：
    # base、候选目录（挂载点或 lowerdir 加本目录相对后缀）、complete、base 校验
    # (ok/no)、候选目录原厂判定 (ok/no)、候选目录下是否还有更深挂载点 (y/n)。
    # 候选目录下仍有挂载点时该行回退文件级原厂判定，保证与逐文件解析语义一致。
    X_OC_STOCK_DIR=$1
    X_OC_STOCK_ROWS=$(xpan_op_stock_roots "$1") || X_OC_STOCK_ROWS=
    case "$1" in
        /product/framework) X_OC_STOCK_ROWS="$X_OC_STOCK_ROWS
/my_product/product_overlay/framework	/my_product/product_overlay/framework	0" ;;
        /product/lib64) X_OC_STOCK_ROWS="$X_OC_STOCK_ROWS
/my_product/lib64	/my_product/lib64	0" ;;
    esac
    X_OC_STOCK_DEEP=$(printf '%s\n' "$X_OC_MOUNTS" | LC_ALL=C xpan_bounded awk -F '\t' -v p="$1" '
        index($3, p "/")==1 { print $3 }
    ') || X_OC_STOCK_DEEP=
    X_OC_ANNOTATED=
    while IFS="$(printf '\t')" read -r X_OC_MB X_OC_MF X_OC_MC; do
        [ -n "$X_OC_MB" ] && [ -n "$X_OC_MF" ] || continue
        X_OC_BV=
        case "$X_OC_STOCK_BASES" in
            *" "$X_OC_MB":ok "*) X_OC_BV=ok ;;
            *" "$X_OC_MB":no "*) X_OC_BV=no ;;
        esac
        if [ -z "$X_OC_BV" ]; then
            if xpan_op_source_safe "$X_OC_PREFIX$X_OC_MB" && [ -d "$X_OC_PREFIX$X_OC_MB" ] &&
                xpan_op_clean_origin "$X_OC_MB"; then
                X_OC_BV=ok
            else
                X_OC_BV=no
            fi
            X_OC_STOCK_BASES="$X_OC_STOCK_BASES$X_OC_MB:$X_OC_BV "
        fi
        if xpan_op_clean_origin "$X_OC_MF"; then X_OC_DF=ok; else X_OC_DF=no; fi
        X_OC_DY=n
        if printf '%s\n' "$X_OC_MOUNTS" | LC_ALL=C xpan_bounded awk -F '\t' -v p="$X_OC_MF" '
            index($3, p "/")==1 { found=1 }
            END { if (found) exit 0; exit 1 }
        '; then
            X_OC_DY=y
        fi
        X_OC_ANNOTATED="$X_OC_ANNOTATED$X_OC_MB	$X_OC_MF	$X_OC_MC	$X_OC_BV	$X_OC_DF	$X_OC_DY
"
    done <<XPAN_OP_STOCK_MEMO
$X_OC_STOCK_ROWS
XPAN_OP_STOCK_MEMO
    [ -z "$X_OC_ANNOTATED" ] || X_OC_STOCK_ROWS=$X_OC_ANNOTATED
}
xpan_op_stock() {
    # 返回 X_OS_FILE / X_OS_STATUS(found、absent、unavailable)，拒绝 OP 自比较。
    # 目录级缓存：同一父目录只解析一次挂载候选与原厂判定；文件级仅做存在性与
    # 安全复核。文件相对路径本身是挂载点或含 shell 展开敏感字符时回退逐文件解析，
    # 保证与逐文件解析语义完全一致。
    local rel prefix base file complete physical known dir leaf rows transform
    rel=$1
    X_OS_FILE=; X_OS_STATUS=unavailable
    case "$rel" in /odm/*|/product/*) ;; *) return 1 ;; esac
    if [ "${XPAN_TEST_MODE:-}" = 1 ] && [ -n "${XPAN_TEST_STOCK_ROOT:-}" ]; then
        physical=$XPAN_TEST_STOCK_ROOT$rel
        xpan_op_source_safe "$XPAN_TEST_STOCK_ROOT" && [ -d "$XPAN_TEST_STOCK_ROOT" ] &&
            xpan_op_source_safe "$physical" || return 1
        if [ -f "$physical" ]; then X_OS_FILE=$physical; X_OS_STATUS=found; return 0; fi
        [ -e "$physical" ] || X_OS_STATUS=absent
        return 1
    fi
    prefix=
    if [ "${XPAN_TEST_MODE:-}" = 1 ]; then
        [ -n "${XPAN_TEST_ROOT:-}" ] || return 1
        prefix=$XPAN_TEST_ROOT
    fi
    X_OC_PREFIX=$prefix
    dir=${rel%/*}
    leaf=${rel#"$dir"/}
    transform=1
    case "$rel" in
        *'$'*|*'`'*|*'\'*|*[[:cntrl:]]*) transform=0 ;;
    esac
    if [ "$transform" = 1 ]; then
        if [ "$X_OC_STOCK_DIR" != "$dir" ]; then xpan_op_stock_memo "$dir"; fi
        while IFS= read -r X_OC_DEEP_POINT; do
            [ "$X_OC_DEEP_POINT" = "$rel" ] && { transform=0; break; }
        done <<XPAN_OP_DEEP
$X_OC_STOCK_DEEP
XPAN_OP_DEEP
    fi
    if [ "$transform" = 1 ]; then
        rows=$X_OC_STOCK_ROWS
    else
        rows=$(xpan_op_stock_roots "$rel") || rows=
        case "$rel" in
            /product/framework/*) rows="$rows
/my_product/product_overlay/framework	/my_product/product_overlay/framework/${rel#/product/framework/}	0" ;;
            /product/lib64/*) rows="$rows
/my_product/lib64	/my_product/lib64/${rel#/product/lib64/}	0" ;;
        esac
    fi
    known=0
    while IFS="$(printf '\t')" read -r base file complete bv fclean deepflag; do
        [ -n "$base" ] && [ -n "$file" ] || continue
        [ "$transform" = 1 ] && file=$file/$leaf
        if [ -n "$bv" ]; then
            [ "$bv" = ok ] || continue
        else
            xpan_op_source_safe "$prefix$base" && [ -d "$prefix$base" ] &&
                xpan_op_clean_origin "$base" || continue
        fi
        xpan_op_source_safe "$prefix$file" || continue
        if [ -n "$fclean" ]; then
            if [ "$deepflag" = y ]; then
                xpan_op_clean_origin "$file" || continue
            else
                [ "$fclean" = ok ] || continue
            fi
        else
            xpan_op_clean_origin "$file" || continue
        fi
        physical=$prefix$file
        if [ -f "$physical" ]; then X_OS_FILE=$physical; X_OS_STATUS=found; return 0; fi
        [ ! -e "$physical" ] && [ ! -L "$physical" ] || return 1
        [ "$complete" != 1 ] || known=1
    done <<XPAN_OP_STOCK
$rows
XPAN_OP_STOCK
    [ "$known" != 1 ] || X_OS_STATUS=absent
    return 1
}
xpan_op_asset() {
    # $1 原路径，$2 v2.0 隐藏旧件（可为空），$3 设备相对路径。
    local target legacy rel oldhash current stockhash source
    target=$1; legacy=$2; rel=$3
    xpan_safe_path "$target" || { X_OC_SUM="$X_OC_SUM ${rel#/}=protected"; return 0; }
    current=; oldhash=
    if [ -e "$target" ]; then
        xpan_hash "$target" && current=$X_HASH || { X_OC_SUM="$X_OC_SUM ${rel#/}=unreadable"; return 0; }
    fi
    if [ -n "$legacy" ] && { [ -e "$legacy" ] || [ -L "$legacy" ]; }; then
        # 只读冲突比较先比大小：目标已哈希成功且两侧尺寸必然不同时，可直接判定
        # hidden-conflict，免去 legacy 整文件哈希；写前校验仍全量哈希。
        if [ -n "$current" ] && xpan_op_differs "$target" "$legacy"; then
            X_OC_SUM="$X_OC_SUM ${rel#/}=hidden-conflict"; return 0
        fi
        xpan_hash "$legacy" && oldhash=$X_HASH || { X_OC_SUM="$X_OC_SUM ${rel#/}=hidden-protected"; return 0; }
    fi
    xpan_op_stock "$rel" || true
    source=$X_OS_FILE; stockhash=
    if [ -n "$source" ]; then
        xpan_hash "$source" && stockhash=$X_HASH || { X_OC_SUM="$X_OC_SUM ${rel#/}=stock-unreadable"; return 0; }
    fi
    if [ -n "$current" ] && [ "$current" = "$stockhash" ]; then return 0; fi
    if [ -n "$current" ] && [ -n "$oldhash" ] && [ "$current" != "$oldhash" ]; then
        X_OC_SUM="$X_OC_SUM ${rel#/}=hidden-conflict"; return 0
    fi
    if [ -z "$stockhash" ]; then
        if [ "$X_OS_STATUS" != absent ]; then X_OC_SUM="$X_OC_SUM ${rel#/}=stock-pending"; return 0; fi
        [ -z "$current" ] && [ -n "$oldhash" ] || return 0
        source=$legacy; stockhash=$oldhash
    fi
    if [ -n "$current" ] && ! xpan_op_backup "$target" "mirror$rel"; then
        X_OC_SUM="$X_OC_SUM ${rel#/}=backup-failed"; return 0
    fi
    xpan_safe_path "${target%/*}" && mkdir -p "${target%/*}" 2>/dev/null || return 0
    if xpan_op_put "$source" "$target" "$stockhash"; then
        X_OC_CHANGED=1
        if [ "$source" = "$legacy" ]; then
            X_OC_SUM="$X_OC_SUM ${rel#/}=extra-restored"
        else
            X_OC_SUM="$X_OC_SUM ${rel#/}=mirror-diff,synced"
        fi
    else
        [ "${X_OP_WRITTEN:-0}" != 1 ] || X_OC_CHANGED=1
        X_OC_SUM="$X_OC_SUM ${rel#/}=sync-failed"
    fi
}
xpan_op_hide_c16_filters() {
    # 不再整目录隐藏，也不由 C16 JAR 激活；同名原厂优先，独有增量保留。
    local cam name dir hidden list file target rel legacy
    cam=$X_OC_DIR/system/odm/etc/camera
    xpan_safe_path "$cam" && [ -d "$cam" ] || return 0
    for name in meishe_lut filters_lut basictone; do
        dir=$cam/$name; hidden=$dir.xpan-c16-hidden
        for file in "$dir" "$hidden"; do
            xpan_safe_path "$file" && [ -d "$file" ] || continue
            list=$X_OC_STATE/op-compat.list.$$
            xpan_safe_path "$list" && [ ! -e "$list" ] || continue
            (set -C; xpan_bounded find "$file" -type f > "$list" 2>/dev/null) || { rm -f "$list"; continue; }
            while IFS= read -r target; do
                legacy=
                case "$target" in
                    "$hidden"/*)
                        legacy=$target; target=$dir/${target#"$hidden"/}
                        [ ! -e "$target" ] && [ ! -L "$target" ] || continue ;;
                    "$dir"/*) legacy=$hidden/${target#"$dir"/} ;;
                    *) continue ;;
                esac
                rel=${target#"$X_OC_DIR/system"}
                xpan_op_asset "$target" "$legacy" "$rel"
            done < "$list"
            rm -f "$list"
        done
    done
    for file in "$cam"/gamma_*_conf.json "$cam"/gamma_*_conf.json.xpan-c16-hidden; do
        [ -f "$file" ] && [ ! -L "$file" ] || continue
        target=$file; legacy=$file.xpan-c16-hidden
        case "$file" in *.xpan-c16-hidden)
            target=${file%.xpan-c16-hidden}; legacy=$file
            [ ! -e "$target" ] && [ ! -L "$target" ] || continue ;;
        esac
        rel=${target#"$X_OC_DIR/system"}
        xpan_op_asset "$target" "$legacy" "$rel"
    done
}
xpan_op_native() {
    # 双 Meishe 无净来源时只改名模块副本；BasicTone 仅有原厂对应才同步。
    local rel target hidden source stockhash current
    for rel in /odm/lib64/libmsnativefilter.so /product/lib64/libMsEffectSdk.so /odm/lib64/libBasicTonePhoto.so; do
        target=$X_OC_DIR/system$rel; hidden=$target.xpan-c16-hidden
        xpan_safe_path "$target" && xpan_safe_path "$hidden" || {
            X_OC_SUM="$X_OC_SUM ${rel#/}=protected"; continue;
        }
        [ -e "$target" ] || [ -e "$hidden" ] || continue
        current=
        if [ -e "$target" ]; then
            xpan_hash "$target" && current=$X_HASH || { X_OC_SUM="$X_OC_SUM ${rel#/}=unreadable"; continue; }
        fi
        xpan_op_stock "$rel" || true
        source=$X_OS_FILE; stockhash=
        if [ -n "$source" ]; then xpan_hash "$source" && stockhash=$X_HASH || stockhash=; fi
        if [ -n "$stockhash" ]; then
            if [ "$current" = "$stockhash" ]; then X_OC_SUM="$X_OC_SUM ${rel#/}=current"; continue; fi
            if [ -n "$current" ]; then
                xpan_op_backup "$target" "mirror$rel" || { X_OC_SUM="$X_OC_SUM ${rel#/}=backup-failed"; continue; }
            else
                xpan_op_backup "$hidden" "mirror$rel" || { X_OC_SUM="$X_OC_SUM ${rel#/}=backup-failed"; continue; }
            fi
            if xpan_op_put "$source" "$target" "$stockhash"; then
                X_OC_CHANGED=1; X_OC_SUM="$X_OC_SUM ${rel#/}=synced"
            else
                [ "${X_OP_WRITTEN:-0}" != 1 ] || X_OC_CHANGED=1
                X_OC_SUM="$X_OC_SUM ${rel#/}=sync-failed"
            fi
            continue
        fi
        X_OC_SUM="$X_OC_SUM ${rel#/}=stock-pending"
        [ "$rel" != /odm/lib64/libBasicTonePhoto.so ] || continue
        [ -n "$current" ] || { X_OC_SUM="$X_OC_SUM native-hidden=current"; continue; }
        if [ -e "$hidden" ]; then X_OC_SUM="$X_OC_SUM ${rel#/}=hidden-conflict"; continue; fi
        xpan_op_backup "$target" "mirror$rel" || { X_OC_SUM="$X_OC_SUM ${rel#/}=backup-failed"; continue; }
        if mv -n "$target" "$hidden" 2>/dev/null && [ ! -e "$target" ] && [ -f "$hidden" ]; then
            X_OC_CHANGED=1; X_OC_SUM="$X_OC_SUM ${rel#/}=native-hidden"
        else
            X_OC_SUM="$X_OC_SUM ${rel#/}=hide-failed"
        fi
    done
}
# 仅 C17 + exact OPCameraPro 身份；未知原厂来源只记录，不作为安装门禁。
xpan_op_compat() {
    X_OC_DIR=/data/adb/modules/OPCameraPro
    if [ "${XPAN_TEST_MODE:-}" = 1 ] && [ -n "${XPAN_TEST_OP_DIR:-}" ]; then X_OC_DIR=$XPAN_TEST_OP_DIR; fi
    X_OC_FW=$X_OC_DIR/system/product/framework
    X_OC_SRC=$X_MOD/payload/op-compat
    X_OC_MAN=$X_OC_SRC/MANIFEST
    X_OC_STATE=${X_STATE:-/data/adb/xpan-runtime}
    X_OC_BACK=$X_OC_STATE/op-compat-backup
    X_OC_SUM=; X_OC_CHANGED=0; X_OC_MOUNTS=
    X_OC_STOCK_DIR=; X_OC_STOCK_ROWS=; X_OC_STOCK_DEEP=; X_OC_STOCK_BASES=
    X_OC_H16_U=; X_OC_H16_A=
    if [ "${XPAN_TEST_MODE:-}" = 1 ] && [ -n "${XPAN_TEST_ROM_RELEASE:-}" ]; then
        X_OC_ROM_RELEASE=$XPAN_TEST_ROM_RELEASE
    else
        X_OC_ROM_RELEASE=$(xpan_bounded getprop ro.build.version.release 2>/dev/null) || X_OC_ROM_RELEASE=
    fi
    case "$X_OC_ROM_RELEASE" in
        17|17.*) ;;
        *) xpan_log "op-compat: rom release '$X_OC_ROM_RELEASE' is not C17; completion untouched"; return 0 ;;
    esac
    xpan_safe_path "$X_OC_DIR/module.prop" && [ -f "$X_OC_DIR/module.prop" ] || {
        xpan_log 'op-compat: no OPCameraPro attached module; nothing to do'; return 0;
    }
    [ "$(xpan_bounded grep -c '^id=' "$X_OC_DIR/module.prop")" = 1 ] &&
        xpan_bounded grep -qx 'id=OPCameraPro' "$X_OC_DIR/module.prop" || {
        xpan_log 'op-compat: attached module identity mismatch; untouched'; return 0;
    }
    xpan_safe_path "$X_OC_STATE" && mkdir -p "$X_OC_STATE" 2>/dev/null || {
        xpan_log 'op-compat: state unavailable; completion untouched'; return 0;
    }
    X_OC_MOUNTS=$(xpan_op_mounts) || X_OC_MOUNTS=
    xpan_log 'op-compat: 开始核对 OPCameraPro 素材与 C17 补全'
    xpan_op_is_c16_completion && X_OC_SUM=' c16-sdk=present'
    # 两个随包 C17 JAR 保持逐文件校验、备份，不增加 native SHA 门槛。
    if xpan_safe_path "$X_OC_MAN" && [ -f "$X_OC_MAN" ]; then
        while IFS="$(printf '\t')" read -r X_OC_NAME X_OC_PIN; do
            [ -n "$X_OC_NAME" ] || continue
            case "$X_OC_NAME" in com.oplus.camera.unit.sdk.jar|com.oplus.camera.unit.sdk.adapter.jar) ;;
                *) X_OC_SUM="$X_OC_SUM bundled-unsafe"; continue ;;
            esac
            xpan_hash_valid "$X_OC_PIN" || { X_OC_SUM="$X_OC_SUM $X_OC_NAME=manifest-invalid"; continue; }
            X_OC_T=$X_OC_FW/$X_OC_NAME; X_OC_S=$X_OC_SRC/framework/$X_OC_NAME
            if ! xpan_safe_path "$X_OC_T" || [ ! -f "$X_OC_T" ]; then
                X_OC_SUM="$X_OC_SUM $X_OC_NAME=absent"; continue
            fi
            # 预检哈希复用 is_c16_completion 的同文件结果；不一致时才重新哈希。
            case "$X_OC_NAME" in
                com.oplus.camera.unit.sdk.jar) X_OC_TH=$X_OC_H16_U ;;
                *) X_OC_TH=$X_OC_H16_A ;;
            esac
            if [ -n "$X_OC_TH" ] && xpan_hash_valid "$X_OC_TH"; then
                X_HASH=$X_OC_TH
            elif ! xpan_hash "$X_OC_T"; then
                X_OC_SUM="$X_OC_SUM $X_OC_NAME=unreadable"; continue
            fi
            if [ "$X_HASH" = "$X_OC_PIN" ]; then X_OC_SUM="$X_OC_SUM $X_OC_NAME=current"; continue; fi
            if ! xpan_hash "$X_OC_S" || [ "$X_HASH" != "$X_OC_PIN" ]; then
                X_OC_SUM="$X_OC_SUM $X_OC_NAME=source-invalid"; continue
            fi
            if ! xpan_op_backup "$X_OC_T" "$X_OC_NAME"; then X_OC_SUM="$X_OC_SUM $X_OC_NAME=backup-failed"; continue; fi
            if ! xpan_op_put "$X_OC_S" "$X_OC_T" "$X_OC_PIN"; then
                [ "${X_OP_WRITTEN:-0}" != 1 ] || X_OC_CHANGED=1
                X_OC_SUM="$X_OC_SUM $X_OC_NAME=replace-failed"; continue
            fi
            X_OC_CHANGED=1; X_OC_SUM="$X_OC_SUM $X_OC_NAME=replaced"
        done < "$X_OC_MAN"
    else
        xpan_log 'op-compat: bundled C17 manifest missing; stock sync only'
    fi
    # 已是 C17 JAR 也处理遗留 native 和 v2.0 隐藏素材。
    xpan_op_native
    xpan_op_hide_c16_filters
    xpan_write "$X_OC_STATE/op.compat" "compat_version=3
boot_id=${X_BOOT_ID:-unknown}
rom_release=$X_OC_ROM_RELEASE
detail=$X_OC_SUM
changed=$X_OC_CHANGED" 2>/dev/null || true
    if [ "$X_OC_CHANGED" = 1 ]; then
        xpan_log "op-compat: C17 completion written:$X_OC_SUM; reboot makes it effective; filters not device-verified"
    else
        xpan_log "op-compat: no change:$X_OC_SUM"
    fi
    return 0
}

xpan_install() {
    local X_COMPAT_REBOOT X_PREVIOUS_REBOOT X_REBOOT_PATH
    xpan_lock || { xpan_log '安装锁被旧任务占用，未并发修改模块'; return 1; }
    X_PREVIOUS_REBOOT=0
    for X_REBOOT_PATH in "$X_MOD/.xpan-reboot-required" "$X_ACTIVE/.xpan-reboot-required"; do
        if xpan_read "$X_REBOOT_PATH" && xpan_uuid "$X_VALUE" && [ "$X_VALUE" = "$X_BOOT_ID" ]; then
            X_PREVIOUS_REBOOT=1
        fi
    done
    xpan_markers && xpan_install_cleanup || { xpan_unlock; return 1; }
    xpan_op_compat || true
    X_COMPAT_REBOOT=${X_OC_CHANGED:-0}
    # 补全是否待重启是本次启动的事实；后续安装失败也不能丢失这个标记。
    if [ "$X_COMPAT_REBOOT" = 1 ] || [ "$X_PREVIOUS_REBOOT" = 1 ]; then
        xpan_write "$X_MOD/.xpan-reboot-required" "$X_BOOT_ID" || { xpan_unlock; return 1; }
    fi
    xpan_prepare || { xpan_unlock; return 1; }
    xpan_safe_path "$X_MOD/lifecycle.sh" && [ -f "$X_MOD/lifecycle.sh" ] || { xpan_unlock; return 1; }
    xpan_life_unlock
    X_INSTALL_RC=0
    xpan_log '开始安装配套 Hook APK'
    if [ "${XPAN_TEST_MODE:-}" = 1 ]; then
        sh "$X_MOD/lifecycle.sh" install "$X_MOD" "$XPAN_TEST_ROOT/uuid.source" || X_INSTALL_RC=1
    else
        sh "$X_MOD/lifecycle.sh" install "$X_MOD" || X_INSTALL_RC=1
    fi
    xpan_life_lock || { xpan_log 'APK 阶段后无法重新取得安装锁'; xpan_unlock; return 1; }
    if [ "$X_INSTALL_RC" != 0 ]; then
        xpan_unlock
        return 1
    fi
    xpan_markers && xpan_tokens && xpan_qualify install && xpan_receipt && xpan_current originals &&
        xpan_apk && xpan_parent_commit || { xpan_log 'APK 已安装但安装状态登记失败，未启用挂载'; xpan_unlock; return 1; }
    xpan_log 'Hook APK 与实例身份登记完成'
    if [ "$X_INSTALL_REBOOT" = 1 ] || [ "$X_COMPAT_REBOOT" = 1 ] || [ "$X_PREVIOUS_REBOOT" = 1 ]; then
        xpan_write "$X_MOD/.xpan-reboot-required" "$X_BOOT_ID" || { xpan_unlock; return 1; }
        xpan_log '补全已更新，重启手机后生效；新 APK 与实例已登记，当前保持未启用'
    fi
    xpan_safe_path "$X_MOD/payload/parent.inputs" && rm -f "$X_MOD/payload/parent.inputs" || { xpan_unlock; return 1; }
    xpan_unlock
    xpan_log '安装迁移完成；配置由服务按真实 SDK 检查后启用，LSPosed 作用域保持用户设置'
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
            # OP 素材核对在真机可达数十秒（逐文件探测原厂来源）；放在激活之前
            # 会阻塞监护循环，导致 active 长期为 0、hook 资格被拒、XPAN 崩溃。
            # 生产环境放到激活之后异步执行；测试保持同步语义以便断言。
            xpan_parent_start || { xpan_inactive; xpan_unlock; return 1; }
            X_PARENT_SEALED_HASH=$X_PARENT_HASH
            X_PARENT_SEALED_INODE=$X_PARENT_INODE
            X_PARENT_SEALED_KIND=$X_PARENT_KIND
            xpan_unlock
            # 慢探测（真机逐文件核对可达数十秒）不持锁；提交改动前重新取锁并
            # 复核实例 token，避免旧后台任务在新安装后改到新实例。
            xpan_op_compat_service() {
                xpan_op_compat || true
                if [ "${X_OC_CHANGED:-0}" = 1 ]; then
                    xpan_lock || return 1
                    xpan_worker_current || { xpan_unlock; return 1; }
                    if ! xpan_write "$X_MOD/.xpan-reboot-required" "$X_BOOT_ID"; then
                        # 标记写不进去也有兜底：op.compat 的 changed/boot_id 会让
                        # xpan_compat_settled 继续拒绝激活，直到真正重启。
                        xpan_log '无法写入待重启标记；op.compat 将阻止激活'
                    fi
                    xpan_inactive
                    X_OC_RC=$?
                    xpan_unlock
                    xpan_log '补全已更新，重启手机后生效'
                    return "$X_OC_RC"
                fi
            }
            if [ "${XPAN_TEST_MODE:-}" = 1 ]; then
                xpan_op_compat_service
            else
                xpan_op_compat_service >>"$X_STATE/op-compat.log" 2>&1 &
            fi
            # Long-lived functions survive removal of MODPATH and can clean only
            # this bound instance while its lifecycle authority remains current.
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
