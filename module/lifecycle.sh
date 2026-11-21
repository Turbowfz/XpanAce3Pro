#!/system/bin/sh
# Standalone APK install phase, called by runtime.sh after device qualification.
# Usage: sh lifecycle.sh install /data/adb/{modules,modules_update}/XpanAce3Pro
# 不挂载配置、不签名、不修改相机数据或 LSPosed；跨签名仅迁移本模块 APK。
# runtime.sh keeps SKIPMOUNT=true, qualifies the exact profile and SDK, then
# stages the verified configs and creates this phase's thirteen-key receipt.
#
# Required caller-attested receipt: MODPATH/payload/lifecycle.inputs, strict
# key=value data (never sourced). It is not a signature or proof of stock origin.
# Exactly these keys: qualification_version=1, status=qualified, model=PJX110,
# camera_version, build_fingerprint, apk_package=com.xpanport.hook, apk_sha256,
# and {original,output}_{unit,feature,vendor}_sha256. Hashes are lowercase SHA256.
# Outputs live in payload/qualified-config/{camera_unit_config,
# camera_unit_feature_config.protobuf,oplus_camera_config}; originals must match
# the current /odm/etc/camera/config files. No static system/ overlay is accepted.
# Missing/changed inputs must abort before PM; this phase never generates them.
#
# Ownership contract shared with hook/uninstall.sh:
# mkdir xpan-lifecycle/lock (never steal); validate all inputs; atomically rotate
# current.token BEFORE pm install; set .xpan-instance; cancel ONLY the old token's
# cleanup job. 精确旧无锁脚本先撤销并等待其运行副本退出，不杀相机或其他进程。
# Keep lock through PM and identity publication. Failure retains the new cancellation token, never
# rolls it back. A token is NOT readiness: .xpan-apk-identity is written only
# after exact PM success and installed-byte/path/user/identity verification.
# That identity certifies the APK phase ONLY, not config/mount/module readiness.

PKG=com.xpanport.hook
BASE=/data/adb
STOCK=/odm/etc/camera/config
LOG=/data/local/tmp/xpan_install.log
COMMAND_SECONDS=15
UUID_SOURCE=/proc/sys/kernel/random/uuid

# Same isolated root convention as uninstall tests, with an additional marker.
# Only the optional single UUID-file argument is accepted for deterministic tests.
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
    [ -f "$XPAN_TEST_ROOT/.xpan-uninstall-fixture" ] &&
        [ -f "$XPAN_TEST_ROOT/.xpan-installer-fixture" ] || exit 1
    BASE=$XPAN_TEST_ROOT/data/adb
    STOCK=$XPAN_TEST_ROOT/stock
    LOG=$XPAN_TEST_ROOT/install.log
    PATH=$XPAN_TEST_ROOT/bin:/usr/bin:/bin
    COMMAND_SECONDS=3
    if [ "$#" = 3 ]; then
        [ "$3" = "$XPAN_TEST_ROOT/uuid.source" ] || exit 1
        [ ! -L "$3" ] || exit 1
        UUID_SOURCE=$3
    fi
else
    [ -z "${XPAN_TEST_ROOT:-}" ] && [ "$#" = 2 ] || exit 1
    PATH=/system/bin:/system/xbin:/data/adb/magisk:/data/adb/ksu/bin
fi
export PATH
umask 077
STATE=$BASE/xpan-lifecycle
LOCK=$STATE/lock
JOB=$BASE/service.d/xpan_cleanup.sh
ACTIVE=$BASE/modules/XpanAce3Pro
STAGED=$BASE/modules_update/XpanAce3Pro

log() { printf '%s\n' "xpan install: $*" >> "$LOG"; }
fail() { log "$*"; printf '%s\n' "xpan install: $*" >&2; exit 1; }
read_file() { [ -f "$1" ] && IFS= read -r VALUE < "$1"; }
valid_token() {
    case "$1" in ''|*[!a-zA-Z0-9_-]*) return 1 ;; esac
    [ "${#1}" -le 80 ]
}
valid_uuid() {
    printf '%s\n' "$1" | grep -Eq '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
}
valid_hash() {
    case "$1" in ''|*[!0-9a-f]*) return 1 ;; esac
    [ "${#1}" = 64 ]
}
bounded() { timeout -s KILL "$COMMAND_SECONDS" "$@"; }
pm_run() { bounded pm "$@"; }
unlock() {
    if [ "${HAVE_LOCK:-0}" = 1 ]; then
        rmdir "$LOCK" 2>/dev/null
        HAVE_LOCK=0
    fi
}
trap 'unlock' EXIT
trap 'unlock; exit 1' HUP INT TERM

# Both input and state destinations must be regular files, never symlinks.
file_hash() {
    [ -f "$1" ] && [ ! -L "$1" ] || return 1
    HASH=$(bounded sha256sum "$1" 2>&1) || { log "hash failed: $HASH"; return 1; }
    HASH=${HASH%% *}
    valid_hash "$HASH"
}
atomic_write() {
    DEST=$1
    [ ! -L "$DEST" ] || return 1
    TEMP=$DEST.tmp.$TOKEN
    [ ! -e "$TEMP" ] && [ ! -L "$TEMP" ] || return 1
    (set -C; printf '%s\n' "$2" > "$TEMP") || return 1
    chmod 0600 "$TEMP" && mv -f "$TEMP" "$DEST" || {
        rm -f "$TEMP"
        return 1
    }
}

load_inputs() {
    INPUT=$MODPATH/payload/lifecycle.inputs
    [ -f "$INPUT" ] && [ ! -L "$INPUT" ] || return 1
    SEEN='|'
    COUNT=0
    while IFS= read -r LINE || [ -n "$LINE" ]; do
        [ "${#LINE}" -le 1024 ] || return 1
        case "$LINE" in *=*) ;; *) return 1 ;; esac
        KEY=${LINE%%=*}
        VAL=${LINE#*=}
        [ -n "$VAL" ] || return 1
        case "$SEEN" in *"|$KEY|"*) return 1 ;; esac
        SEEN=$SEEN$KEY'|'
        COUNT=$((COUNT+1))
        [ "$COUNT" -le 13 ] || return 1
        case "$KEY" in
            qualification_version) [ "$VAL" = 1 ] || return 1 ;;
            status) [ "$VAL" = qualified ] || return 1 ;;
            model) [ "$VAL" = PJX110 ] || return 1 ;;
            camera_version) INPUT_CAMERA=$VAL ;;
            build_fingerprint) INPUT_ROM=$VAL ;;
            apk_package) [ "$VAL" = "$PKG" ] || return 1 ;;
            apk_sha256) APK_HASH=$VAL; valid_hash "$VAL" || return 1 ;;
            original_unit_sha256) ORIGINAL_UNIT=$VAL; valid_hash "$VAL" || return 1 ;;
            original_feature_sha256) ORIGINAL_FEATURE=$VAL; valid_hash "$VAL" || return 1 ;;
            original_vendor_sha256) ORIGINAL_VENDOR=$VAL; valid_hash "$VAL" || return 1 ;;
            output_unit_sha256) OUTPUT_UNIT=$VAL; valid_hash "$VAL" || return 1 ;;
            output_feature_sha256) OUTPUT_FEATURE=$VAL; valid_hash "$VAL" || return 1 ;;
            output_vendor_sha256) OUTPUT_VENDOR=$VAL; valid_hash "$VAL" || return 1 ;;
            *) return 1 ;;
        esac
    done < "$INPUT"
    [ "$COUNT" = 13 ]
}
profile() {
    MODEL=$(bounded getprop ro.product.model 2>&1) || { log "model query failed: $MODEL"; return 1; }
    [ "$MODEL" = PJX110 ] || { log 'unsupported real model'; return 1; }
    ROM=$(bounded getprop ro.build.fingerprint 2>&1) || { log "ROM query failed: $ROM"; return 1; }
    [ -n "$ROM" ] && [ "$ROM" = "$INPUT_ROM" ] || { log 'ROM fingerprint changed/unqualified'; return 1; }
    CAMERA_DUMP=$(bounded dumpsys package com.oplus.camera 2>&1) || {
        log "camera query failed: $CAMERA_DUMP"; return 1;
    }
    CAMERA=$(printf '%s\n' "$CAMERA_DUMP" | awk '
        /^[[:space:]]*Package \[com\.oplus\.camera\]/ { pkg++ }
        /^[[:space:]]*versionName=/ { sub(/^[[:space:]]*versionName=/, ""); print; count++ }
        END { if (pkg != 1 || count != 1) exit 1 }
    ') || { log "camera profile unparseable: $CAMERA_DUMP"; return 1; }
    case "$CAMERA" in 6.020.876|7.013.51) ;; *) log 'unknown exact camera version'; return 1 ;; esac
    [ "$CAMERA" = "$INPUT_CAMERA" ] || { log 'camera profile changed/unqualified'; return 1; }
}
check_files() {
    for ROLE in unit feature vendor; do
        case "$ROLE" in
            unit) NAME=camera_unit_config; ORIGINAL=$ORIGINAL_UNIT; OUTPUT=$OUTPUT_UNIT ;;
            feature) NAME=camera_unit_feature_config.protobuf; ORIGINAL=$ORIGINAL_FEATURE; OUTPUT=$OUTPUT_FEATURE ;;
            vendor) NAME=oplus_camera_config; ORIGINAL=$ORIGINAL_VENDOR; OUTPUT=$OUTPUT_VENDOR ;;
        esac
        file_hash "$STOCK/$NAME" && [ "$HASH" = "$ORIGINAL" ] || {
            log "current original changed/unavailable: $NAME"; return 1;
        }
        [ -s "$CONFIGS/$NAME" ] && file_hash "$CONFIGS/$NAME" && [ "$HASH" = "$OUTPUT" ] || {
            log "qualified output changed/unavailable: $NAME"; return 1;
        }
    done
    [ -s "$APK" ] && file_hash "$APK" && [ "$HASH" = "$APK_HASH" ] || {
        log 'qualified source APK changed/unavailable'; return 1;
    }
}

parent_phase() {
    local PP_INFO PP_PARENT PP_PARTITION PP_BOOT PP_NS PP_LINE PP_KEY PP_VAL PP_SEEN PP_COUNT
    [ -e "$MODPATH/payload/parent.inputs" ] || [ -L "$MODPATH/payload/parent.inputs" ] || {
        [ -z "${PHASE_PARENT_BOUND:-}" ]
        return $?
    }
    [ -f "$MODPATH/payload/parent.inputs" ] && [ ! -L "$MODPATH/payload/parent.inputs" ] &&
        [ -f "$MODPATH/parent-context.sh" ] && [ ! -L "$MODPATH/parent-context.sh" ] || return 1
    if [ "${XPAN_TEST_MODE:-}" != 1 ]; then
        PP_MODE=$(bounded stat -c '%a:%u' "$MODPATH/payload/parent.inputs") || return 1
        [ "$PP_MODE" = 600:0 ] || return 1
    fi
    . "$MODPATH/parent-context.sh"
    PP_INFO=/proc/self/mountinfo; PP_PARENT=/odm/etc; PP_PARTITION=/odm
    PP_BOOT=/proc/sys/kernel/random/boot_id; PP_NS=/proc/self/ns/mnt
    if [ "${XPAN_TEST_MODE:-}" = 1 ]; then
        PP_INFO=$XPAN_TEST_ROOT/mountinfo; PP_PARENT=$XPAN_TEST_ROOT; PP_PARTITION=/
        PP_BOOT=$XPAN_TEST_ROOT/boot-id; PP_NS=$XPAN_TEST_ROOT/ns
    fi
    PP_SEEN='|'; PP_COUNT=0
    PP_EXPECT_BOOT=; PP_EXPECT_NS=; PP_EXPECT_HASH=; PP_EXPECT_INODE=; PP_EXPECT_KIND=
    while IFS= read -r PP_LINE || [ -n "$PP_LINE" ]; do
        [ "${#PP_LINE}" -le 1024 ] || return 1
        case "$PP_LINE" in *=*) ;; *) return 1 ;; esac
        PP_KEY=${PP_LINE%%=*}; PP_VAL=${PP_LINE#*=}
        case "$PP_SEEN" in *"|$PP_KEY|"*) return 1 ;; esac
        PP_SEEN=$PP_SEEN$PP_KEY'|'; PP_COUNT=$((PP_COUNT+1))
        case "$PP_KEY" in
            parent_version) [ "$PP_VAL" = 1 ] || return 1 ;;
            boot_id) PP_EXPECT_BOOT=$PP_VAL; valid_uuid "$PP_VAL" || return 1 ;;
            namespace) PP_EXPECT_NS=$PP_VAL ;;
            parent_mount_sha256) PP_EXPECT_HASH=$PP_VAL; valid_hash "$PP_VAL" || return 1 ;;
            parent_inode) PP_EXPECT_INODE=$PP_VAL; printf '%s\n' "$PP_VAL" | grep -Eq '^[0-9]+:[0-9]+$' || return 1 ;;
            parent_kind) PP_EXPECT_KIND=$PP_VAL; case "$PP_VAL" in normal|ksu) ;; *) return 1 ;; esac ;;
            *) return 1 ;;
        esac
    done < "$MODPATH/payload/parent.inputs"
    [ "$PP_COUNT" = 6 ] || return 1
    PP_NOW_BOOT=$(bounded cat "$PP_BOOT") || return 1
    PP_NOW_NS=$(bounded readlink "$PP_NS") || return 1
    [ "$PP_NOW_BOOT" = "$PP_EXPECT_BOOT" ] && [ "$PP_NOW_NS" = "$PP_EXPECT_NS" ] || return 1
    xpan_parent_observe "$PP_INFO" "$STOCK" "$PP_PARENT" "$PP_PARTITION" || return 1
    [ "$XP_PARENT_HASH" = "$PP_EXPECT_HASH" ] && [ "$XP_PARENT_INODE" = "$PP_EXPECT_INODE" ] &&
        [ "$XP_PARENT_KIND" = "$PP_EXPECT_KIND" ] || return 1
    if [ -z "${PHASE_PARENT_BOUND:-}" ]; then
        PHASE_PARENT_BOUND=$PP_EXPECT_BOOT'|'$PP_EXPECT_NS'|'$PP_EXPECT_HASH'|'$PP_EXPECT_INODE'|'$PP_EXPECT_KIND
    fi
    [ "$PHASE_PARENT_BOUND" = "$PP_EXPECT_BOOT|$PP_EXPECT_NS|$PP_EXPECT_HASH|$PP_EXPECT_INODE|$PP_EXPECT_KIND" ]
}
# No -u queries: packages known only as uninstalled are not installed packages.
# Enumerate all current users, with a hard bound, and require at least one actual
# installation. Never force-enable/restore a user's uninstalled app selection.
package_users() {
    FRAMEWORK=$(pm_run path android 2>&1) || { log "framework query failed: $FRAMEWORK"; return 1; }
    case "$FRAMEWORK" in package:/*) ;; *) log "framework unavailable: $FRAMEWORK"; return 1 ;; esac
    USER_OUT=$(pm_run list users 2>&1) || { log "users query failed: $USER_OUT"; return 1; }
    USERS=$(printf '%s\n' "$USER_OUT" | awk '
        /^Users:$/ { header++; next }
        /UserInfo\{/ {
            s=$0; sub(/^.*UserInfo\{/, "", s); sub(/:.*$/, "", s)
            if (s !~ /^[0-9]+$/ || seen[s]++ || ++n > 64) bad=1; else print s
            next
        }
        /[^[:space:]]/ { bad=1 }
        END { if (header != 1 || bad || !n) exit 1 }
    ') || { log "users unparseable: $USER_OUT"; return 1; }
    [ -n "$USERS" ]
}

# Shared normalization with hook/uninstall.sh and runtime.sh. Legacy global
# firstInstallTime keeps the three-line hash; all supplied user times must agree.
# Per-user v2 chooses owner 0 even if installed=false, else the numeric lowest
# user with an actual time. This is package identity, NOT installed-user proof;
# verify_install separately requires PM presence, exact path and APK bytes.
package_identity() {
    IDENTITY=; ID_LINES=; CODE_PATH=
    DUMP=$(bounded dumpsys package "$PKG" 2>&1) || { log 'identity query failed'; return 1; }
    [ "${#DUMP}" -le 1048576 ] || { log 'identity query exceeds size bound'; return 1; }
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
    ') || { log 'identity unparseable or ambiguous'; return 1; }
    IDENTITY=$(printf '%s\n' "$ID_LINES" | bounded sha256sum) || return 1
    IDENTITY=${IDENTITY%% *}
    valid_hash "$IDENTITY" || return 1
    CODE_PATH=$(printf '%s\n' "$ID_LINES" | awk '/^codePath=/ { sub(/^codePath=/, ""); print }')
    case "$CODE_PATH" in /data/app/*) ;; *) log 'APK codePath is not a data install'; return 1 ;; esac
    case "$CODE_PATH" in *..*|*'
'*) return 1 ;; esac
}
verify_install() {
    package_users && package_identity || return 1
    PRESENT=0
    for USER in $USERS; do
        LIST=$(pm_run list packages --user "$USER" "$PKG" 2>&1) || {
            log "user $USER package query failed: $LIST"; return 1;
        }
        case "$LIST" in
            '') continue ;;
            "package:$PKG") PRESENT=1 ;;
            *) log "user $USER package query unparseable: $LIST"; return 1 ;;
        esac
        APK_PATH=$(pm_run path --user "$USER" "$PKG" 2>&1) || {
            log "user $USER APK path failed: $APK_PATH"; return 1;
        }
        # Split APKs / multiple paths / other packages cannot pass this exact test.
        [ "$APK_PATH" = "package:$CODE_PATH/base.apk" ] || {
            log "user $USER APK path does not match codePath: $APK_PATH"; return 1;
        }
        INSTALLED_APK=$CODE_PATH/base.apk
        if [ "${XPAN_TEST_MODE:-}" = 1 ]; then
            INSTALLED_APK=$XPAN_TEST_ROOT$INSTALLED_APK
        fi
        file_hash "$INSTALLED_APK" && [ "$HASH" = "$APK_HASH" ] || {
            log "user $USER installed APK differs from qualified source"; return 1;
        }
    done
    [ "$PRESENT" = 1 ] || { log 'no actually installed package for any current user'; return 1; }
    # An external PM transaction must not change identity during verification.
    VERIFIED_IDENTITY=$IDENTITY
    package_identity && [ "$IDENTITY" = "$VERIFIED_IDENTITY" ] || {
        log 'installed APK identity changed during verification'; return 1;
    }
}
old_job_owned() {
    [ -f "$JOB" ] && [ ! -L "$JOB" ] || return 1
    [ -n "$OLD_TOKEN" ] && grep -Fqx "XPAN_JOB_TOKEN='$OLD_TOKEN'" "$JOB"
}
legacy_job_present() {
    [ -f "$JOB" ] && [ ! -L "$JOB" ] || return 1
    file_hash "$JOB" || return 1
    [ "$HASH" = 0261785d42aadaf66423999f8478f409202b86046df4848bb03eb45fb0dd1952 ]
}
legacy_job_cancel() {
    local LEGACY_PROC LEGACY_CMD LEGACY_WAIT LEGACY_LIMIT LEGACY_DELAY LEGACY_BUSY
    if legacy_job_present; then
        [ ! -e "$STATE/legacy-cleanup.$TOKEN" ] && [ ! -L "$STATE/legacy-cleanup.$TOKEN" ] || return 1
        mv "$JOB" "$STATE/legacy-cleanup.$TOKEN" && chmod 0600 "$STATE/legacy-cleanup.$TOKEN" || return 1
        log '已自动撤销旧无锁清理脚本，等待正在运行的旧副本结束；不终止相机或其他进程'
    fi
    # 即使上次等待超时且脚本已归档，复装也必须继续等旧副本退出。
    LEGACY_PROC=/proc; LEGACY_LIMIT=900; LEGACY_DELAY=1
    if [ "${XPAN_TEST_MODE:-}" = 1 ]; then
        LEGACY_PROC=$XPAN_TEST_ROOT/proc; LEGACY_LIMIT=3; LEGACY_DELAY=0
    fi
    LEGACY_WAIT=0
    while :; do
        LEGACY_BUSY=0
        for LEGACY_CMD in "$LEGACY_PROC"/[0-9]*/cmdline; do
            [ -f "$LEGACY_CMD" ] || continue
            bounded tr '\000' '\n' < "$LEGACY_CMD" | grep -Fqx "$JOB" && LEGACY_BUSY=1
        done
        [ "$LEGACY_BUSY" = 1 ] || break
        LEGACY_WAIT=$((LEGACY_WAIT+1))
        [ "$LEGACY_WAIT" -lt "$LEGACY_LIMIT" ] || { log '旧无锁清理进程尚未结束，未安装新 APK；旧脚本已归档'; return 1; }
        sleep "$LEGACY_DELAY" || return 1
    done
    log '旧无锁清理任务已结束，可以安全安装新实例'
}
pm_success() {
    [ "$RESULT" = 0 ] && [ "$OUT" = Success ]
}
pm_signature_mismatch() {
    [ "$RESULT" != 0 ] || return 1
    printf '%s\n' "$OUT" | LC_ALL=C awk '
        /^Failure \[INSTALL_FAILED_UPDATE_INCOMPATIBLE(:[^][]*)?\]$/ { mismatch++; next }
        /^Success$/ || /^Failure \[/ { other=1 }
        END { if (mismatch!=1 || other) exit 1 }
    '
}
backup_old_apk() {
    local BACKUP_SOURCE BACKUP_HASH BACKUP_USERS BACKUP_PATH BACKUP_OLD_ID
    package_users && package_identity || return 1
    BACKUP_OLD_ID=$IDENTITY
    BACKUP_SOURCE=$CODE_PATH/base.apk
    [ "${XPAN_TEST_MODE:-}" != 1 ] || BACKUP_SOURCE=$XPAN_TEST_ROOT$BACKUP_SOURCE
    file_hash "$BACKUP_SOURCE" || { log '旧 APK 不可读取，未移除旧应用'; return 1; }
    BACKUP_HASH=$HASH; BACKUP_USERS=
    for USER in $USERS; do
        LIST=$(pm_run list packages --user "$USER" "$PKG" 2>&1) || { log "旧 APK 用户查询失败：$LIST"; return 1; }
        case "$LIST" in '') continue ;; "package:$PKG") ;; *) log "旧 APK 用户查询异常：$LIST"; return 1 ;; esac
        BACKUP_PATH=$(pm_run path --user "$USER" "$PKG" 2>&1) || { log "旧 APK 路径查询失败：$BACKUP_PATH"; return 1; }
        [ "$BACKUP_PATH" = "package:$CODE_PATH/base.apk" ] || { log "旧 APK 不是唯一独立包，未移除：$BACKUP_PATH"; return 1; }
        BACKUP_USERS=$BACKUP_USERS" $USER"
    done
    [ -n "$BACKUP_USERS" ] || { log '未找到实际安装的旧 Hook APK，未自动卸载'; return 1; }
    [ ! -L "$STATE/apk-backup" ] || return 1
    mkdir -p "$STATE/apk-backup" || return 1
    APK_BACKUP=$STATE/apk-backup/$TOKEN
    mkdir "$APK_BACKUP" || return 1
    chmod 0700 "$STATE/apk-backup" "$APK_BACKUP" || return 1
    cp "$BACKUP_SOURCE" "$APK_BACKUP/base.apk" && chmod 0600 "$APK_BACKUP/base.apk" &&
        file_hash "$APK_BACKUP/base.apk" && [ "$HASH" = "$BACKUP_HASH" ] || {
        log "旧 APK 备份失败，未移除旧应用：$APK_BACKUP"; return 1;
    }
    atomic_write "$APK_BACKUP/identity" "$ID_LINES" &&
        atomic_write "$APK_BACKUP/receipt" "apk_package=$PKG
apk_sha256=$BACKUP_HASH
users=$BACKUP_USERS
source=$CODE_PATH/base.apk" || return 1
    package_identity && [ "$IDENTITY" = "$BACKUP_OLD_ID" ] &&
        file_hash "$BACKUP_SOURCE" && [ "$HASH" = "$BACKUP_HASH" ] || {
        log "旧 APK 在备份期间变化，未移除；备份保留：$APK_BACKUP"; return 1;
    }
    log "跨签名旧 Hook APK 已完整备份：$APK_BACKUP/base.apk"
}
install_apk() {
    parent_phase || fail 'parent changed before PM install; cancellation token retained'
    OUT=$(pm_run install -r "$APK" 2>&1)
    RESULT=$?
    log "install rc=$RESULT: $OUT"
    pm_success && return 0
    pm_signature_mismatch || fail "APK 安装失败：$OUT；cancellation token retained，未自动卸载"
    backup_old_apk || fail '跨签名旧 APK 备份未完成，未卸载旧应用；cancellation token retained'
    parent_phase && profile && check_files || fail "跨签名迁移前输入变化，未卸载；备份保留：$APK_BACKUP"
    # -k 会保留旧签名约束，跨签名只能移除这个已备份的独立 Hook 包。
    OUT=$(pm_run uninstall "$PKG" 2>&1)
    RESULT=$?
    log "signature migration uninstall rc=$RESULT: $OUT"
    pm_success || fail "旧 Hook APK 移除失败：$OUT；备份保留：$APK_BACKUP；cancellation token retained"
    parent_phase && profile && check_files || fail "跨签名重装前输入变化；备份保留：$APK_BACKUP"
    OUT=$(pm_run install -r "$APK" 2>&1)
    RESULT=$?
    log "signature migration install rc=$RESULT: $OUT"
    pm_success || fail "新 Hook APK 重装失败：$OUT；备份保留：$APK_BACKUP；cancellation token retained"
    log "已自动迁移跨签名 Hook APK，旧包备份保留：$APK_BACKUP"
}

[ "$#" = 2 ] || { [ "${XPAN_TEST_MODE:-}" = 1 ] && [ "$#" = 3 ]; } || exit 1
[ "$1" = install ] || exit 1
MODPATH=$2
case "$MODPATH" in "$ACTIVE"|"$STAGED") ;; *) exit 1 ;; esac
[ "$(id -u)" = 0 ] || exit 1
[ -d "$MODPATH" ] && [ ! -L "$MODPATH" ] || fail 'invalid module directory'
grep -qx 'id=XpanAce3Pro' "$MODPATH/module.prop" || fail 'not the Xpan module'
[ ! -e "$MODPATH/disable" ] && [ ! -e "$MODPATH/remove" ] || fail 'module disabled/removed; preserve user choice'
# Do not mutate an old active instance while a different staged one is pending.
[ "$MODPATH" != "$ACTIVE" ] || [ ! -e "$STAGED" ] || fail 'staged replacement exists; use its qualified bundle'
APK=$MODPATH/payload/XpanHook.apk
CONFIGS=$MODPATH/payload/qualified-config
[ ! -L "$BASE" ] && [ ! -L "${MODPATH%/*}" ] && [ ! -L "$MODPATH/payload" ] &&
    [ ! -L "$CONFIGS" ] && [ ! -L "$STATE" ] || fail 'symlinked lifecycle/input directory'
mkdir -p "$STATE" || fail 'cannot create lifecycle state'
mkdir "$LOCK" 2>/dev/null || fail 'lifecycle lock busy; no installation attempted'
HAVE_LOCK=1
load_inputs || fail 'config generation not ready: missing/invalid qualified three-output receipt'
parent_phase && profile && check_files && parent_phase || fail 'config generation not ready or ROM/profile/input/parent changed; no installation attempted'
package_users || fail 'PM unavailable; no installation attempted'

OLD_TOKEN=
VALUE=
if [ -e "$STATE/current.token" ]; then
    [ ! -L "$STATE/current.token" ] && read_file "$STATE/current.token" && valid_token "$VALUE" || fail 'invalid current token'
    OLD_TOKEN=$VALUE
elif [ -e "$ACTIVE/.xpan-instance" ]; then
    [ ! -L "$ACTIVE/.xpan-instance" ] && read_file "$ACTIVE/.xpan-instance" && valid_token "$VALUE" || fail 'invalid old instance token'
    OLD_TOKEN=$VALUE
fi
VALUE=
read_file "$UUID_SOURCE" && valid_uuid "$VALUE" || fail 'fresh random UUID unavailable; no date/PID fallback'
TOKEN=$VALUE
[ "$TOKEN" != "$OLD_TOKEN" ] || fail 'UUID reused current token'
VALUE=
if [ -e "$MODPATH/.xpan-instance" ]; then
    [ ! -L "$MODPATH/.xpan-instance" ] && read_file "$MODPATH/.xpan-instance" && valid_token "$VALUE" || fail 'invalid module instance token'
    [ "$TOKEN" != "$VALUE" ] || fail 'UUID reused module token'
fi
# Recheck before rotation; no token is published before valid current outputs.
legacy_job_cancel || fail '旧无锁清理任务迁移未完成，未安装新 APK'
parent_phase && profile && check_files && parent_phase || fail 'inputs or parent changed before token rotation'
atomic_write "$STATE/current.token" "$TOKEN" || fail 'cannot atomically rotate current token'
atomic_write "$MODPATH/.xpan-instance" "$TOKEN" || fail 'cannot publish module token; cancellation token retained'
rm -f "$MODPATH/.xpan-apk-identity" || fail 'cannot clear stale APK identity'
if old_job_owned; then
    rm -f "$JOB" || fail 'cannot cancel owned old cleanup job'
    log 'cancelled matching old cleanup job'
fi

# 不降级、不强制启用、不操作 LSP 数据库；只有真实跨签名错误自动迁移。
install_apk
parent_phase && profile && check_files && verify_install && parent_phase || fail 'post-install verification failed; no APK identity published'
atomic_write "$MODPATH/.xpan-apk-identity" "$IDENTITY" || fail 'cannot publish verified APK identity'
# 登记独立开机清理守护：KSU 不执行 uninstall.sh 时，由 service.d 在模块目录
# 真正缺席后恢复 OP 改动、清相机应用数据并完整卸载 Hook。登记失败不算安装成功。
GUARD=$MODPATH/payload/uninstall-apk.sh
[ -f "$GUARD" ] && [ ! -L "$GUARD" ] || fail 'uninstall guard helper missing; install not accepted'
XPAN_ARM=1 XPAN_ARM_LOCKED=1 sh "$GUARD" >/dev/null 2>&1 || fail 'cannot arm boot uninstall guard'
log 'boot uninstall guard armed (service.d)'
log 'APK phase verified; config/mount integration and LSPosed selection remain separate'
printf '%s\n' 'APK phase verified only; no config mounts or LSPosed enablement performed.'
exit 0
