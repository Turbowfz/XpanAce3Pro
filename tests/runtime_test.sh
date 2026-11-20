#!/usr/bin/env bash
# Local-only filesystem/mount/PM/property simulation. No Android/adb/real mounts.
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd -- "$HERE/.." && pwd)
[ ! -e /system/bin/pm ] || { printf 'Refuse Android test host\n' >&2; exit 1; }
SUITE=$(mktemp -d "$REPO/_tmp/xpan-runtime-test.XXXXXXXX")
: > "$SUITE/.xpan-runtime-fixture"
trap 'rm -rf -- "$SUITE"' EXIT
# One immutable project-source snapshot for all host fixtures in this run.
mkdir "$SUITE/module"
cp "$REPO/module/"*.sh "$REPO/module/module.prop" "$SUITE/module/"
cp "$REPO/hook/uninstall.sh" "$SUITE/uninstall-apk.sh"
printf 'Runtime source snapshot SHA256=%s\n' "$(/usr/bin/sha256sum "$SUITE/module/runtime.sh" | cut -d' ' -f1)"
export XPAN_TEST_MODE=1
COUNT=0
fail() {
    printf 'FAIL: %s\n' "$*" >&2
    [ ! -f "${XPAN_TEST_ROOT:-}/stdout" ] || printf '%s\n' "$(<"$XPAN_TEST_ROOT/stdout")" >&2
    [ ! -f "${XPAN_TEST_ROOT:-}/commands" ] || printf '%s\n' "$(<"$XPAN_TEST_ROOT/commands")" >&2
    exit 1
}
hash() { local s; s=$(/usr/bin/sha256sum "$1"); printf '%s' "${s%% *}"; }
prop() { [ -f "$XPAN_TEST_ROOT/props/$1" ] && printf '%s' "$(<"$XPAN_TEST_ROOT/props/$1")"; }
receipt() {
    local dst=$1 k v
    {
        printf 'qualification_version=1\nstatus=qualified\n'
        while IFS='=' read -r k v; do
            case "$k" in model|camera_version|build_fingerprint|original_*|output_*) printf '%s=%s\n' "$k" "$v" ;; esac
        done < "$dst/payload/profiles/$PROFILE/profile.props"
        printf 'apk_package=com.xpanport.hook\napk_sha256=%s\n' "$(hash "$dst/payload/XpanHook.apk")"
    } > "$dst/payload/lifecycle.inputs"
}
fixture() {
    COUNT=$((COUNT+1))
    PROFILE=${1:-c17}
    export XPAN_TEST_ROOT=${2:-$SUITE/$COUNT}
    ACTIVE=$XPAN_TEST_ROOT/data/adb/modules/XpanAce3Pro
    STAGED=$XPAN_TEST_ROOT/data/adb/modules_update/XpanAce3Pro
    STATE=$XPAN_TEST_ROOT/data/adb/xpan-runtime
    LIFE=$XPAN_TEST_ROOT/data/adb/xpan-lifecycle
    mkdir -p "$ACTIVE/payload/qualified-config" "$ACTIVE/payload/profiles" "$LIFE" "$STATE" \
        "$XPAN_TEST_ROOT/bin" "$XPAN_TEST_ROOT/stock" "$XPAN_TEST_ROOT/originals" \
        "$XPAN_TEST_ROOT/props" "$XPAN_TEST_ROOT/data/app/fixture/com.xpanport.hook" \
        "$XPAN_TEST_ROOT/product/framework" "$XPAN_TEST_ROOT/my_product/product_overlay/framework" \
        "$XPAN_TEST_ROOT/protected/other-module" "$XPAN_TEST_ROOT/protected/camera" "$XPAN_TEST_ROOT/protected/lsposed"
    : > "$XPAN_TEST_ROOT/.xpan-runtime-fixture"
    : > "$XPAN_TEST_ROOT/.xpan-installer-fixture"
    : > "$XPAN_TEST_ROOT/.xpan-uninstall-fixture"
    : > "$XPAN_TEST_ROOT/commands"
    cp "$SUITE/module/"*.sh "$ACTIVE/"
    cp "$SUITE/module/module.prop" "$ACTIVE/"
    cp "$SUITE/uninstall-apk.sh" "$ACTIVE/payload/uninstall-apk.sh"
    cp -R "$REPO/_tmp/profile-build/payload/profiles/"* "$ACTIVE/payload/profiles/"
    cp "$REPO/_tmp/profile-build/originals/$PROFILE/"* "$XPAN_TEST_ROOT/stock/"
    cp "$XPAN_TEST_ROOT/stock/"* "$XPAN_TEST_ROOT/originals/"
    cp "$ACTIVE/payload/profiles/$PROFILE/"camera* "$ACTIVE/payload/profiles/$PROFILE/oplus_camera_config" "$ACTIVE/payload/qualified-config/"
    printf '12345678-1234-4234-8234-000000000001\n' > "$ACTIVE/.xpan-instance"
    cp "$ACTIVE/.xpan-instance" "$LIFE/current.token"
    printf '12345678-1234-4234-8234-000000000009\n' > "$XPAN_TEST_ROOT/uuid.source"
    printf '12345678-1234-4234-8234-000000000002\n' > "$XPAN_TEST_ROOT/boot-id"
    printf 'mnt:[4026531841]\n' > "$XPAN_TEST_ROOT/ns"
    printf 'fixture signed APK bytes\n' > "$ACTIVE/payload/XpanHook.apk"
    cp "$ACTIVE/payload/XpanHook.apk" "$XPAN_TEST_ROOT/data/app/fixture/com.xpanport.hook/base.apk"
    printf '17\n' > "$XPAN_TEST_ROOT/props/ro.build.version.release"
    unset XPAN_TEST_OP_DIR XPAN_TEST_ROM_RELEASE XPAN_TEST_STOCK_ROOT
    printf 'PJX110\n' > "$XPAN_TEST_ROOT/props/ro.product.model"
    grep '^build_fingerprint=' "$ACTIVE/payload/profiles/$PROFILE/profile.props" | cut -d= -f2- > "$XPAN_TEST_ROOT/props/ro.build.fingerprint"
    grep '^camera_version=' "$ACTIVE/payload/profiles/$PROFILE/profile.props" | cut -d= -f2- > "$XPAN_TEST_ROOT/camera-version"
    printf '1\n' > "$XPAN_TEST_ROOT/props/sys.boot_completed"
    printf '/product/framework\n' > "$XPAN_TEST_ROOT/sdk-parent"
    printf '%s-sdk\n' "$PROFILE" > "$XPAN_TEST_ROOT/product/framework/com.oplus.camera.unit.sdk.jar"
    printf '%s-adapter\n' "$PROFILE" > "$XPAN_TEST_ROOT/product/framework/com.oplus.camera.unit.sdk.adapter.jar"
    cp "$XPAN_TEST_ROOT/product/framework/"* "$XPAN_TEST_ROOT/my_product/product_overlay/framework/"
    printf '1 0 0:1 / / rw - mock mock rw\n' > "$XPAN_TEST_ROOT/mountinfo"
    printf '100\n' > "$XPAN_TEST_ROOT/next-id"
    printf 'must-survive\n' > "$XPAN_TEST_ROOT/protected/other-module/mount"
    printf 'must-survive\n' > "$XPAN_TEST_ROOT/protected/camera/oc_active.enc"
    printf 'must-survive\n' > "$XPAN_TEST_ROOT/protected/camera/preferences.xml"
    printf 'must-survive\n' > "$XPAN_TEST_ROOT/protected/lsposed/modules_config.db"
    printf 'must-survive\n' > "$XPAN_TEST_ROOT/protected/photo.jpg"
    receipt "$ACTIVE"
    cat > "$XPAN_TEST_ROOT/bin/mock" <<'MOCK'
#!/bin/sh
R=$XPAN_TEST_ROOT
CMD=${0##*/}
printf '%s %s\n' "$CMD" "$*" >> "$R/commands"
mount_count() {
    /usr/bin/awk -v t="$R/stock/" '$5==t "camera_unit_config" ||
        $5==t "camera_unit_feature_config.protobuf" || $5==t "oplus_camera_config" {n++}
        END {print n+0}' "$R/mountinfo"
}
parent_drift() {
    /usr/bin/awk -v p="$R" '$5==p {$1=91} {print}' "$R/mountinfo" > "$R/mountinfo.new"
    mv "$R/mountinfo.new" "$R/mountinfo"
}
case "$CMD" in
    readlink) [ "$1" = "$R/ns" ] || exit 85; cat "$R/ns" ;;
    ls)
        if [ "$1" = -ld ]; then exec /usr/bin/ls "$@"; fi
        [ "$1" = -Zd ] || exit 85
        [ ! -f "$R/context-missing" ] || { printf '? %s\n' "$2"; exit 0; }
        case "$2" in
            */payload/profiles/*/camera*|*/payload/profiles/*/oplus_camera_config)
                if [ -f "$R/context-mismatch" ]; then printf 'u:object_r:adb_data_file:s0 %s\n' "$2"; exit 0; fi ;;
        esac
        printf 'u:object_r:vendor_configs_file:s0 %s\n' "$2"
        ;;
    stat)
        if [ "$1:$2" = '-Lc:%a' ]; then
            shift 2
            for F in "$@"; do
                [ ! -f "$R/mode-fail" ] || { printf '600\n'; continue; }
                if [ -d "$F" ]; then printf '755\n'; else printf '644\n'; fi
            done
        else exec /usr/bin/stat "$@"; fi
        ;;
    chcon)
        [ "$1" = u:object_r:vendor_configs_file:s0 ] || exit 85
        case "$2" in "$R"/data/adb/modules*/XpanAce3Pro/payload/profiles/*/camera*|"$R"/data/adb/modules*/XpanAce3Pro/payload/profiles/*/oplus_camera_config) ;;
            *) printf 'chcon outside payload\n' >> "$R/forbidden"; exit 85 ;;
        esac
        ;;
    id) printf '0\n' ;;
    getprop)
        [ ! -f "$R/getprop-fail" ] || exit 1
        [ ! -f "$R/props/$1" ] || cat "$R/props/$1"
        ;;
    setprop)
        [ ! -f "$R/setprop-fail.$1" ] || exit 1
        printf '%s\n' "$2" > "$R/props/$1"
        if [ "$1:$2" = debug.xpan.active:1 ]; then
            [ "$(mount_count)" = 3 ] || exit 87
            [ -s "$R/props/debug.xpan.profile" ] && [ -s "$R/props/debug.xpan.token" ] || exit 87
            [ ! -f "$R/parent-drift.publish" ] || parent_drift
        fi
        ;;
    sha256sum)
        [ ! -f "$R/hash-fail" ] || exit 1
        case "${1:-}" in
            *com.oplus.camera.unit.sdk.jar|*com.oplus.camera.unit.sdk.adapter.jar|*com.oplus.camera.unit.sdk.jar.xpan-compat.*|*com.oplus.camera.unit.sdk.adapter.jar.xpan-compat.*)
                case "$(cat "$1")" in
                    c16-sdk) H=37a13d408f7ee95b8e954c2ef11c44a0aee433da45958cc7ddb80c688e0692c6 ;;
                    c16-adapter) H=ac0b595faae8d3f684aa457f223fbfb0f167f0d0e0cc461ef08dbbe52d6e61cf ;;
                    c17-sdk) H=d2b20133252414e26ac7c68255c76fa61cf41826c3b63d423a14c1187af42899 ;;
                    c17-adapter) H=7418d59955746d8aca56438a5e0e26e1f08ff6ae4d0cacc22057d0041b12aac4 ;;
                    *) exec /usr/bin/sha256sum "$@" ;;
                esac
                printf '%s  %s\n' "$H" "$1" ;;
            *) exec /usr/bin/sha256sum "$@" ;;
        esac
        ;;
    dumpsys)
        [ ! -f "$R/pm-fail" ] || exit 1
        case "$*" in
            'package com.oplus.camera')
                printf '  Package [com.oplus.camera] (abc):\n    versionName='; cat "$R/camera-version"
                [ ! -f "$R/duplicate-camera" ] || printf '    versionName=7.013.51\n'
                [ ! -f "$R/no-libs" ] || exit 0
                P=$(cat "$R/sdk-parent")
                L="$P/com.oplus.camera.unit.sdk.jar, $P/com.oplus.camera.unit.sdk.adapter.jar"
                if [ -f "$R/duplicate-libs" ]; then L="$L, $P/com.oplus.camera.unit.sdk.jar"; fi
                if [ -f "$R/mixed-libs" ]; then L='/product/framework/com.oplus.camera.unit.sdk.jar, /my_product/product_overlay/framework/com.oplus.camera.unit.sdk.adapter.jar'; fi
                if [ -f "$R/unit-only-libs" ]; then L="$P/com.oplus.camera.unit.sdk.jar"; fi
                if [ -f "$R/multiline-libs" ]; then
                    printf '    usesLibraryFiles:\n      /system/framework/org.apache.http.legacy.jar\n'
                    printf '%s\n' "$L" | /usr/bin/awk -F ', ' '{for(i=1;i<=NF;i++) print "      " $i}'
                    printf '      libcdsprpc.so\n      libOpenCL.so\n    timeStamp=2009-01-01 08:00:00\n'
                else
                    printf '    usesLibraryFiles=[%s]\n' "$L"
                fi
                if [ -f "$R/duplicate-lib-field" ]; then printf '    usesLibraryFiles=[%s]\n' "$L"; fi
                ;;
            'package com.xpanport.hook')
                printf '  Package [com.xpanport.hook] (abc):\n'
                printf '    codePath=/data/app/fixture/com.xpanport.hook\n'
                printf '    firstInstallTime=2026-11-14 01:00:00\n'
                printf '    lastUpdateTime=2026-11-14 01:00:00\n'
                ;;
            *) exit 85 ;;
        esac
        ;;
    pm)
        [ ! -f "$R/pm-fail" ] || exit 1
        case "$*" in
            'path android') printf 'package:/system/framework/framework-res.apk\n' ;;
            'list users') printf 'Users:\n\tUserInfo{0:Owner:13} running\n' ;;
            'list packages --user 0 com.xpanport.hook') printf 'package:com.xpanport.hook\n' ;;
            'path --user 0 com.xpanport.hook') printf 'package:/data/app/fixture/com.xpanport.hook/base.apk\n' ;;
            'install -r '*)
                [ -d "$R/data/adb/xpan-lifecycle/lock" ] || exit 88
                M=${3%/payload/XpanHook.apk}
                [ "$(cat "$M/.xpan-instance")" = "$(cat "$R/uuid.source")" ] || exit 88
                cp "$3" "$R/data/app/fixture/com.xpanport.hook/base.apk"
                [ ! -f "$R/parent-drift.pm" ] || parent_drift
                [ ! -f "$R/install-fail" ] || { printf 'Failure [UPDATE_INCOMPATIBLE]\n'; exit 1; }
                printf 'Success\n'
                ;;
            *) printf 'forbidden PM %s\n' "$*" >> "$R/forbidden"; exit 85 ;;
        esac
        ;;
    mount)
        [ "$1" = --bind ] || exit 85
        S=$2; T=$3; N=${T##*/}
        grep -Fq " $T " "$R/mountinfo" && exit 86
        if [ -f "$R/mount-fail.$N" ]; then exit 1; fi
        ID=$(cat "$R/next-id"); printf '%s\n' "$((ID+1))" > "$R/next-id"
        SOURCE_ROOT=$(/usr/bin/awk -v s="$S" '
            $5=="/" || s==$5 || index(s,$5 "/")==1 {
                if (length($5)>best) {best=length($5); point=$5; root=$4; dev=$3}
            }
            END {if (!best) exit 1; suffix=substr(s,length(point)+1)
                if (point=="/") suffix="/" substr(s,2)
                if (root=="/") root=""; print dev " " root suffix}' "$R/mountinfo") || exit 85
        PARENT=$(/usr/bin/awk -v t="$T" '
            $5=="/" || t==$5 || index(t,$5 "/")==1 {
                if (length($5)>best) {best=length($5); id=$1}
            }
            END {if (!best) exit 1; print id}' "$R/mountinfo") || exit 85
        mv "$T" "$R/originals/live.$N"
        ln "$S" "$T"
        printf '%s %s %s %s rw - mock mock rw\n' "$ID" "$PARENT" "$SOURCE_ROOT" "$T" >> "$R/mountinfo"
        case "$N" in camera_unit_config) ROLE=unit ;; camera_unit_feature_config.protobuf) ROLE=feature ;; oplus_camera_config) ROLE=vendor ;; *) exit 85 ;; esac
        if [ -f "$R/journal-block.$N" ]; then mkdir "$R/data/adb/xpan-runtime/$ROLE.mount"; fi
        if [ -f "$R/ota-during-mount.$N" ]; then printf 'OTA/changed\n' > "$R/props/ro.build.fingerprint"; fi
        if [ -f "$R/parent-drift.mount.$N" ]; then parent_drift; fi
        if [ -f "$R/mount-error-after.$N" ]; then exit 1; fi
        ;;
    umount)
        T=$1; N=${T##*/}
        [ ! -f "$R/umount-fail.$N" ] || exit 1
        [ -f "$R/originals/live.$N" ] || exit 89
        rm "$T"; mv "$R/originals/live.$N" "$T"
        /usr/bin/awk -v t="$T" '$5!=t' "$R/mountinfo" > "$R/mountinfo.new"
        mv "$R/mountinfo.new" "$R/mountinfo"
        ;;
    *) printf 'forbidden %s %s\n' "$CMD" "$*" >> "$R/forbidden"; exit 85 ;;
esac
MOCK
    for cmd in readlink ls stat chcon id getprop setprop sha256sum pm dumpsys mount umount adb am sqlite3 reboot app_process; do
        cp "$XPAN_TEST_ROOT/bin/mock" "$XPAN_TEST_ROOT/bin/$cmd"
    done
    chmod +x "$XPAN_TEST_ROOT/bin/"*
    export PATH=$XPAN_TEST_ROOT/bin:/usr/bin:/bin
    "$XPAN_TEST_ROOT/bin/dumpsys" package com.xpanport.hook | grep -E '^    (codePath|firstInstallTime|lastUpdateTime)=' | cut -c 5- | /usr/bin/sha256sum | cut -d' ' -f1 > "$ACTIVE/.xpan-apk-identity"
    : > "$XPAN_TEST_ROOT/commands"
}
run() {
    RC=0
    /usr/bin/sh "$SUITE/module/runtime.sh" "${1:-tick}" "$ACTIVE" > "$XPAN_TEST_ROOT/stdout" 2>&1 || RC=$?
}
run_customize() {
    RC=0
    MODPATH=${1:-$ACTIVE} BOOTMODE=true /usr/bin/sh "$SUITE/module/customize.sh" > "$XPAN_TEST_ROOT/stdout" 2>&1 || RC=$?
}
expect() { [ "$RC" = "$1" ] || fail "expected rc=$1 got $RC"; }
mount_count() {
    /usr/bin/awk -v t="$XPAN_TEST_ROOT/stock/" '$5==t "camera_unit_config" ||
        $5==t "camera_unit_feature_config.protobuf" || $5==t "oplus_camera_config" {n++}
        END {print n+0}' "$XPAN_TEST_ROOT/mountinfo"
}
no_mounts() { [ "$(mount_count)" = 0 ] || fail 'unexpected mounted files'; }
no_mutation() { ! grep -Eq '^(mount|umount) ' "$XPAN_TEST_ROOT/commands" || fail 'unexpected mount mutation'; }
no_pm_install() { ! grep -q '^pm install ' "$XPAN_TEST_ROOT/commands" || fail 'unexpected install'; }
off() { [ "$(prop debug.xpan.active)" != 1 ] || fail 'unexpected active'; }
on() {
    [ "$(prop debug.xpan.active)" = 1 ] && [ "$(prop debug.xpan.profile)" = "$PROFILE" ] &&
        [ "$(prop debug.xpan.token)" = "$(<"$ACTIVE/.xpan-instance")" ] || fail 'bad ready tuple'
    [ "$(mount_count)" = 3 ] || fail 'not three mounts'
    for role in unit feature vendor; do [ -f "$STATE/$role.mount" ] || fail "missing journal $role"; done
}
ok() {
    [ ! -e "$XPAN_TEST_ROOT/forbidden" ] || fail 'forbidden command executed'
    for f in other-module/mount camera/oc_active.enc camera/preferences.xml lsposed/modules_config.db photo.jpg; do
        grep -qx must-survive "$XPAN_TEST_ROOT/protected/$f" || fail "protected changed $f"
    done
    [ ! -e "$STATE/lock" ] && [ ! -e "$LIFE/lock" ] || fail 'lock leaked'
    printf 'PASS: %s\n' "$1"
}
generic_parent() {
    printf '90 1 0:136 / %s rw,relatime shared:76 - overlay KSU\\040 ro,seclabel,lowerdir=/mnt/vendor/u3ve5haz7/odm/etc:/odm/etc\n' "$XPAN_TEST_ROOT" >> "$XPAN_TEST_ROOT/mountinfo"
}
parent_observation() {
    /usr/bin/sh -c '
        . "$1" || exit 1
        xpan_parent_observe "$2/mountinfo" "$2/stock" "$2" / || exit 1
        printf "parent_mount_sha256=%s\nparent_inode=%s\n" "$XP_PARENT_HASH" "$XP_PARENT_INODE"
    ' sh "$ACTIVE/parent-context.sh" "$XPAN_TEST_ROOT" > "$XPAN_TEST_ROOT/parent.observed" || fail 'cannot observe fixture parent'
}
field() { /usr/bin/awk -v k="$2" 'index($0,k "=")==1 {print substr($0,length(k)+2)}' "$1"; }
keys() {
    /usr/bin/awk -F= -v keys="$2" '
        BEGIN {n=split(keys,a," "); for (i=1;i<=n;i++) allowed[a[i]]=1}
        NF!=2 || !($1 in allowed) || seen[$1]++ {bad=1}
        END {if (bad || NR!=n) exit 1}' "$1" || fail "unexpected record keys: $1"
}
parent_protocol() {
    local role name row
    keys "$STATE/parent.context" 'parent_version boot_id namespace token parent_mount_sha256 parent_inode parent_kind'
    [ "$(field "$STATE/parent.context" parent_version)" = 1 ] &&
        [ "$(field "$STATE/parent.context" parent_kind)" = ksu ] &&
        [ "$(field "$STATE/parent.context" boot_id)" = "$(<"$XPAN_TEST_ROOT/boot-id")" ] &&
        [ "$(field "$STATE/parent.context" namespace)" = "$(<"$XPAN_TEST_ROOT/ns")" ] &&
        [ "$(field "$STATE/parent.context" token)" = "$(<"$XPAN_TEST_ROOT/uuid.source")" ] &&
        [ "$(field "$STATE/parent.context" parent_mount_sha256)" = "$(field "$XPAN_TEST_ROOT/parent.observed" parent_mount_sha256)" ] &&
        [ "$(field "$STATE/parent.context" parent_inode)" = "$(/usr/bin/stat -Lc '%d:%i' "$XPAN_TEST_ROOT/stock")" ] || fail 'bad parent pin binding'
    for role in unit feature vendor; do
        keys "$STATE/$role.mount" 'ownership_version boot_id namespace target mount_sha256 inode token parent_mount_sha256 parent_inode'
        [ "$(field "$STATE/$role.mount" ownership_version)" = 2 ] &&
            [ "$(field "$STATE/$role.mount" token)" = "$(<"$ACTIVE/.xpan-instance")" ] &&
            [ "$(field "$STATE/$role.mount" parent_mount_sha256)" = "$(field "$STATE/parent.context" parent_mount_sha256)" ] &&
            [ "$(field "$STATE/$role.mount" parent_inode)" = "$(field "$STATE/parent.context" parent_inode)" ] || fail "bad v2 journal parent binding: $role"
        case "$role" in unit) name=camera_unit_config ;; feature) name=camera_unit_feature_config.protobuf ;; vendor) name=oplus_camera_config ;; esac
        row=$(/usr/bin/awk -v t="$XPAN_TEST_ROOT/stock/$name" '$5==t {print $2 " " $3 " " $4}' "$XPAN_TEST_ROOT/mountinfo")
        [ "$row" = "90 0:136 /data/adb/modules/XpanAce3Pro/payload/profiles/$PROFILE/$name" ] || fail "bad nearest ancestor bind: $role"
        [ "$(field "$STATE/$role.mount" inode)" = "$(/usr/bin/stat -Lc '%d:%i' "$ACTIVE/payload/profiles/$PROFILE/$name")" ] || fail "bad bind inode: $role"
    done
}
journals_save() { cp "$STATE/"*.mount "$XPAN_TEST_ROOT/originals/"; }
journals_retained() {
    local role
    for role in unit feature vendor; do
        cmp -s "$STATE/$role.mount" "$XPAN_TEST_ROOT/originals/$role.mount" || fail "journal changed: $role"
    done
}
parent_preserved() {
    [ "$(/usr/bin/awk -v p="$XPAN_TEST_ROOT" '$5==p {print}' "$XPAN_TEST_ROOT/mountinfo")" = "$PARENT_ROW" ] || fail 'parent overlay changed'
    ! grep -Fqx "umount $XPAN_TEST_ROOT" "$XPAN_TEST_ROOT/commands" || fail 'unmounted parent overlay'
}
foreign() {
    local name=$1 src=$2
    cp "$src" "$XPAN_TEST_ROOT/foreign.$name"
    mv "$XPAN_TEST_ROOT/stock/$name" "$XPAN_TEST_ROOT/originals/live.$name"
    ln "$XPAN_TEST_ROOT/foreign.$name" "$XPAN_TEST_ROOT/stock/$name"
    printf '900 1 0:900 %s %s rw - mock foreign rw\n' "$XPAN_TEST_ROOT/foreign.$name" "$XPAN_TEST_ROOT/stock/$name" >> "$XPAN_TEST_ROOT/mountinfo"
}
op_fixture() {
    export XPAN_TEST_OP_DIR=$XPAN_TEST_ROOT/data/adb/modules/OPCameraPro
    export XPAN_TEST_STOCK_ROOT=$XPAN_TEST_ROOT
    mkdir -p "$XPAN_TEST_OP_DIR/system/product/framework" "$ACTIVE/payload/op-compat/framework"
    printf 'id=OPCameraPro\nversion=fixture\n' > "$XPAN_TEST_OP_DIR/module.prop"
    printf 'c16-sdk\n' > "$XPAN_TEST_OP_DIR/system/product/framework/com.oplus.camera.unit.sdk.jar"
    printf 'c16-adapter\n' > "$XPAN_TEST_OP_DIR/system/product/framework/com.oplus.camera.unit.sdk.adapter.jar"
    printf 'c17-sdk\n' > "$ACTIVE/payload/op-compat/framework/com.oplus.camera.unit.sdk.jar"
    printf 'c17-adapter\n' > "$ACTIVE/payload/op-compat/framework/com.oplus.camera.unit.sdk.adapter.jar"
    printf 'com.oplus.camera.unit.sdk.jar\td2b20133252414e26ac7c68255c76fa61cf41826c3b63d423a14c1187af42899\ncom.oplus.camera.unit.sdk.adapter.jar\t7418d59955746d8aca56438a5e0e26e1f08ff6ae4d0cacc22057d0041b12aac4\n' > "$ACTIVE/payload/op-compat/MANIFEST"
    printf 'c16-sdk\n' > "$XPAN_TEST_ROOT/product/framework/com.oplus.camera.unit.sdk.jar"
    printf 'c16-adapter\n' > "$XPAN_TEST_ROOT/product/framework/com.oplus.camera.unit.sdk.adapter.jar"
}
worker_script() {
    # Source trusted project functions into this local fixture only, without its CLI.
    /usr/bin/grep -v '^xpan_main "\$@"$' "$SUITE/module/runtime.sh" > "$XPAN_TEST_ROOT/worker.sh"
    printf '\nxpan_init service "$XPAN_TEST_ROOT/data/adb/modules/XpanAce3Pro" || exit 1\nxpan_lock || exit 1\nxpan_worker_bind || exit 1\nxpan_unlock\n' >> "$XPAN_TEST_ROOT/worker.sh"
    printf '%s\n' "$1" >> "$XPAN_TEST_ROOT/worker.sh"
    RC=0
    /usr/bin/sh "$XPAN_TEST_ROOT/worker.sh" > "$XPAN_TEST_ROOT/stdout" 2>&1 || RC=$?
}
worker_scenarios() {
    fixture
    run; expect 0; on
    : > "$XPAN_TEST_ROOT/commands"
    worker_script ': > "$XPAN_TEST_ROOT/commands"
xpan_tick || exit 1
if grep -q "^setprop debug.xpan.active 0" "$XPAN_TEST_ROOT/commands"; then exit 87; fi
mkdir "$X_LIFE/lock" || exit 1
: > "$XPAN_TEST_ROOT/commands"
xpan_tick; r=$?
rmdir "$X_LIFE/lock"
[ "$r" = 1 ] || exit 87
[ ! -s "$XPAN_TEST_ROOT/commands" ] || { grep -Eq "^(setprop|mount|umount) " "$XPAN_TEST_ROOT/commands" && exit 87; }
exit 0'
    expect 0; on; no_mutation
    ok 'bound service healthy/lock busy does not flicker readiness or steal lock'
    fixture
    run; expect 0; on
    worker_script 'cp "$XPAN_TEST_ROOT/uuid.source" "$X_LIFE/current.token"
cp "$XPAN_TEST_ROOT/uuid.source" "$X_MOD/.xpan-instance"
printf "1\\n" > "$XPAN_TEST_ROOT/props/debug.xpan.active"
cp "$XPAN_TEST_ROOT/uuid.source" "$XPAN_TEST_ROOT/props/debug.xpan.token"
: > "$XPAN_TEST_ROOT/commands"
xpan_tick; r=$?
[ "$r" = 2 ] || exit 87
xpan_inactive; [ "$?" = 1 ] || exit 87
exit 0'
    expect 0; on; no_mutation
    ! grep -q '^setprop ' "$XPAN_TEST_ROOT/commands" || fail 'superseded worker changed readiness'
    ok 'superseded bound service cannot adopt or clear replacement state'
    for fault in OTA remove; do
        fixture
        run; expect 0; on
        if [ "$fault" = OTA ]; then ACTION='printf "OTA/new\\n" > "$XPAN_TEST_ROOT/props/ro.build.fingerprint"'; else ACTION='rm -rf "$X_MOD"'; fi
        worker_script "$ACTION
xpan_tick; r=\$?
[ \"\$r\" = 1 ] || exit 87
exit 0"
        expect 0; off; no_mounts
        ok "same bound service $fault retracts only its own instance"
    done
    fixture
    printf 'invalid\n' > "$XPAN_TEST_ROOT/ns"
    printf '1\n' > "$XPAN_TEST_ROOT/props/debug.xpan.active"
    cp "$ACTIVE/.xpan-instance" "$XPAN_TEST_ROOT/props/debug.xpan.token"
    run service; expect 1
    [ "$(prop debug.xpan.active)" = 1 ] || fail 'unbound failed service cleared another readiness'
    no_mutation
    ! grep -q '^setprop ' "$XPAN_TEST_ROOT/commands" || fail 'unbound failed service changed properties'
    ok 'service initialization failure has no unproved readiness authority'
}
parent_scenarios() {
    fixture
    generic_parent
    BEFORE=$(hash "$XPAN_TEST_ROOT/mountinfo")
    run; expect 1; off; no_mounts; no_mutation; no_pm_install
    [ ! -e "$STATE/parent.context" ] || fail 'generic first tick auto-adopted parent'
    [ "$(hash "$XPAN_TEST_ROOT/mountinfo")" = "$BEFORE" ] || fail 'unpinned parent changed'
    ok 'generic initial tick without parent pin never auto-adopts'

    fixture
    generic_parent
    rm "$ACTIVE/.xpan-instance" "$ACTIVE/.xpan-apk-identity" "$LIFE/current.token"
    run install; expect 0; off; no_mounts; no_mutation
    [ -f "$STATE/parent.context" ] || fail 'fresh KSU install did not register parent'
    [ "$(<"$ACTIVE/.xpan-instance")" = "$(<"$XPAN_TEST_ROOT/uuid.source")" ] || fail 'fresh KSU install did not generate instance'
    [ ! -e "$ACTIVE/payload/parent.approval" ] || fail 'fresh KSU install introduced manual approval'
    ok 'normal fresh KSU install observes parent automatically without existing token or approval'

    fixture
    generic_parent
    parent_observation
    PARENT_ROW=$(/usr/bin/awk -v p="$XPAN_TEST_ROOT" '$5==p {print}' "$XPAN_TEST_ROOT/mountinfo")
    run install; expect 0; off; no_mounts; no_mutation
    grep -q '^pm install -r ' "$XPAN_TEST_ROOT/commands" || fail 'generic install skipped strict APK lifecycle'
    [ "$(<"$ACTIVE/.xpan-instance")" = "$(<"$XPAN_TEST_ROOT/uuid.source")" ] || fail 'parent did not bind a new instance'
    : > "$XPAN_TEST_ROOT/commands"
    run; expect 0; on; no_pm_install
    parent_protocol
    BEFORE=$(hash "$XPAN_TEST_ROOT/mountinfo")
    PIN_BEFORE=$(hash "$STATE/parent.context")
    journals_save
    [ ! -e "$ACTIVE/payload/parent.approval" ] || fail 'manual parent approval unexpectedly created'
    : > "$XPAN_TEST_ROOT/commands"
    run; expect 0; on; no_mutation; no_pm_install
    ! grep -q '^setprop debug.xpan.active 0' "$XPAN_TEST_ROOT/commands" || fail 'generic healthy tick flickered readiness'
    [ "$(hash "$XPAN_TEST_ROOT/mountinfo")" = "$BEFORE" ] || fail 'generic healthy tick changed mounts'
    journals_retained
    run stop; expect 0; off; no_mounts
    parent_preserved
    [ "$(hash "$STATE/parent.context")" = "$PIN_BEFORE" ] || fail 'stop replaced parent pin'
    for role in unit feature vendor; do [ ! -e "$STATE/$role.mount" ] || fail 'stop retained owned v2 journal'; done
    ok 'automatic KSU install binds new parent, v2 tick is healthy, stop preserves parent'

    for fault in exact-config duplicate deeper writable unknown; do
        fixture
        generic_parent
        parent_observation
        case "$fault" in
            exact-config) printf '99 90 0:99 / %s rw - mock foreign rw\n' "$XPAN_TEST_ROOT/stock" >> "$XPAN_TEST_ROOT/mountinfo" ;;
            duplicate) generic_parent ;;
            deeper) printf '99 90 0:99 / %s rw - mock foreign rw\n' "$XPAN_TEST_ROOT/stock/unknown" >> "$XPAN_TEST_ROOT/mountinfo" ;;
            writable) /usr/bin/awk '{sub(/ro,seclabel,lowerdir=/,"rw,seclabel,lowerdir="); print}' "$XPAN_TEST_ROOT/mountinfo" > "$XPAN_TEST_ROOT/mountinfo.new"; mv "$XPAN_TEST_ROOT/mountinfo.new" "$XPAN_TEST_ROOT/mountinfo" ;;
            unknown) /usr/bin/awk -v p="$XPAN_TEST_ROOT" '$5==p {$10="Unknown"} {print}' "$XPAN_TEST_ROOT/mountinfo" > "$XPAN_TEST_ROOT/mountinfo.new"; mv "$XPAN_TEST_ROOT/mountinfo.new" "$XPAN_TEST_ROOT/mountinfo" ;;
        esac
        BEFORE=$(hash "$XPAN_TEST_ROOT/mountinfo")
        run install; expect 1; off; no_mutation; no_pm_install
        [ "$(hash "$XPAN_TEST_ROOT/mountinfo")" = "$BEFORE" ] || fail "rejected $fault parent changed"
        [ ! -e "$STATE/parent.context" ] || fail 'invalid parent pin published'
        ok "generic $fault parent rejects installation before PM or bind mutation"
    done

    for mode in boot service install; do
        fixture
        generic_parent
        run install; expect 0
        run; expect 0; on
        for name in camera_unit_config camera_unit_feature_config.protobuf oplus_camera_config; do
            "$XPAN_TEST_ROOT/bin/umount" "$XPAN_TEST_ROOT/stock/$name"
        done
        printf '12345678-1234-4234-8234-000000000007\n' > "$XPAN_TEST_ROOT/boot-id"
        printf 'mnt:[4026531999]\n' > "$XPAN_TEST_ROOT/ns"
        /usr/bin/awk -v p="$XPAN_TEST_ROOT" '$5==p {$1=91} {print}' "$XPAN_TEST_ROOT/mountinfo" > "$XPAN_TEST_ROOT/mountinfo.new"
        mv "$XPAN_TEST_ROOT/mountinfo.new" "$XPAN_TEST_ROOT/mountinfo"
        : > "$XPAN_TEST_ROOT/commands"
        case "$mode" in
            boot) run boot; expect 0; off; no_mounts; no_pm_install; run; expect 0; on ;;
            service)
                worker_script 'xpan_lock || exit 1
xpan_parent_start || exit 1
xpan_unlock
xpan_tick || exit 1
exit 0'
                expect 0; on; no_pm_install ;;
            install)
                printf '12345678-1234-4234-8234-000000000008\n' > "$XPAN_TEST_ROOT/uuid.source"
                run install; expect 0; off; no_mounts
                run; expect 0; on ;;
        esac
        [ "$(field "$STATE/parent.context" boot_id)" = "$(<"$XPAN_TEST_ROOT/boot-id")" ] &&
            [ "$(field "$STATE/parent.context" namespace)" = "$(<"$XPAN_TEST_ROOT/ns")" ] || fail 'parent did not refresh current boot/namespace'
        ! grep -q '^umount ' "$XPAN_TEST_ROOT/commands" || fail 'stale boot journal authorized a current unmount'
        ok "KSU cross-boot $mode automatically refreshes parent without manual approval"
    done

    fixture
    generic_parent
    run install; expect 0
    run; expect 0; on
    mkdir -p "$STATE/op-compat-backup" "${STAGED%/*}"
    printf 'old compat backup\n' > "$STATE/op-compat-backup/keep"
    cp -R "$ACTIVE" "$STAGED"
    printf '12345678-1234-4234-8234-000000000008\n' > "$XPAN_TEST_ROOT/uuid.source"
    RC=0
    /usr/bin/sh "$SUITE/module/runtime.sh" install "$STAGED" > "$XPAN_TEST_ROOT/stdout" 2>&1 || RC=$?
    expect 0; off; no_mounts
    [ "$(<"$STAGED/.xpan-instance")" = "$(<"$LIFE/current.token")" ] || fail 'staged token not current'
    [ "$(<"$ACTIVE/.xpan-instance")" != "$(<"$STAGED/.xpan-instance")" ] || fail 'old active instance overwritten'
    grep -qx 'old compat backup' "$STATE/op-compat-backup/keep" || fail 'compat backup lost during migration'
    rm -rf "$ACTIVE"
    mv "$STAGED" "$ACTIVE"
    run; expect 0; on
    ok 'staged replacement automatically cleans only old owned mounts and keeps new bundle/compat backup'

    fixture
    generic_parent
    : > "$XPAN_TEST_ROOT/install-fail"
    run install; expect 1; off; no_mounts
    [ ! -e "$STATE/parent.context" ] || fail 'failed PM transaction published parent'
    rm "$XPAN_TEST_ROOT/install-fail"
    printf '12345678-1234-4234-8234-000000000008\n' > "$XPAN_TEST_ROOT/uuid.source"
    run install; expect 0; off; no_mounts
    run; expect 0; on
    ok 'retry after failed PM token rotation recovers without parent approval deadlock'

    for fault in corrupt symlink missing; do
        fixture
        generic_parent
        parent_observation
        run install; expect 0
        run; expect 0; on
        journals_save
        case "$fault" in
            corrupt) printf 'parent_kind=ksu\n' >> "$STATE/parent.context" ;;
            symlink) mv "$STATE/parent.context" "$XPAN_TEST_ROOT/originals/parent.context"; ln -s "$XPAN_TEST_ROOT/originals/parent.context" "$STATE/parent.context" ;;
            missing) rm "$STATE/parent.context" ;;
        esac
        BEFORE=$(hash "$XPAN_TEST_ROOT/mountinfo")
        : > "$XPAN_TEST_ROOT/commands"
        run; expect 1; off; no_mutation; no_pm_install
        [ "$(hash "$XPAN_TEST_ROOT/mountinfo")" = "$BEFORE" ] || fail "$fault pin caused blind unmount"
        journals_retained
        [ "$fault" != symlink ] || [ -L "$STATE/parent.context" ] || fail 'symlinked pin replaced'
        [ "$fault" != missing ] || [ ! -e "$STATE/parent.context" ] || fail 'missing pin auto-recreated'
        ok "generic $fault parent pin revokes readiness and retains v2 mounts/journals"
    done

    for fault in mount-id stock-inode; do
        fixture
        generic_parent
        parent_observation
        run install; expect 0
        run; expect 0; on
        journals_save
        PIN_BEFORE=$(hash "$STATE/parent.context")
        case "$fault" in
            mount-id) /usr/bin/awk -v p="$XPAN_TEST_ROOT" '$5==p {$1=91} {print}' "$XPAN_TEST_ROOT/mountinfo" > "$XPAN_TEST_ROOT/mountinfo.new"; mv "$XPAN_TEST_ROOT/mountinfo.new" "$XPAN_TEST_ROOT/mountinfo" ;;
            stock-inode) mv "$XPAN_TEST_ROOT/stock" "$XPAN_TEST_ROOT/originals/stock.old"; mkdir "$XPAN_TEST_ROOT/stock"; cp -al "$XPAN_TEST_ROOT/originals/stock.old/." "$XPAN_TEST_ROOT/stock/" ;;
        esac
        BEFORE=$(hash "$XPAN_TEST_ROOT/mountinfo")
        : > "$XPAN_TEST_ROOT/commands"
        run; expect 1; off; no_mutation; no_pm_install
        [ "$(hash "$XPAN_TEST_ROOT/mountinfo")" = "$BEFORE" ] || fail "$fault parent drift caused blind unmount"
        [ "$(hash "$STATE/parent.context")" = "$PIN_BEFORE" ] || fail 'drifted parent pin auto-replaced'
        journals_retained
        ok "generic $fault drift revokes readiness without blind teardown or pin adoption"
    done

    fixture
    generic_parent
    parent_observation
    run install; expect 0
    run; expect 0; on
    for role in unit feature vendor; do
        /usr/bin/awk 'BEGIN {FS=OFS="="} /^ownership_version=/ {$2=1}
            !/^parent_mount_sha256=/ && !/^parent_inode=/ {print}' "$STATE/$role.mount" > "$STATE/$role.mount.new"
        mv "$STATE/$role.mount.new" "$STATE/$role.mount"
        keys "$STATE/$role.mount" 'ownership_version boot_id namespace target mount_sha256 inode token'
    done
    journals_save
    BEFORE=$(hash "$XPAN_TEST_ROOT/mountinfo")
    : > "$XPAN_TEST_ROOT/commands"
    run; expect 1; off; no_mutation; no_pm_install
    [ "$(hash "$XPAN_TEST_ROOT/mountinfo")" = "$BEFORE" ] || fail 'generic v1 same-hash mounts removed'
    journals_retained
    ok 'generic parent never trusts v1 same-mount-hash journals for adoption or teardown'

    fixture
    run; expect 0; on
    [ ! -e "$STATE/parent.context" ] || fail 'normal legacy tick unexpectedly created a parent pin'
    for role in unit feature vendor; do
        keys "$STATE/$role.mount" 'ownership_version boot_id namespace target mount_sha256 inode token'
        [ "$(field "$STATE/$role.mount" ownership_version)" = 1 ] || fail 'legacy journal upgraded without install'
    done
    BEFORE=$(/usr/bin/awk '$5=="/" {print}' "$XPAN_TEST_ROOT/mountinfo")
    run stop; expect 0; off; no_mounts
    [ "$(<"$XPAN_TEST_ROOT/mountinfo")" = "$BEFORE" ] || fail 'legacy normal stop changed root parent'
    ok 'legacy normal no-pin v1 bind and token-owned teardown remain unchanged'

    for fault in mount publish pm; do
        fixture
        generic_parent
        parent_observation
        if [ "$fault" = pm ]; then
            : > "$XPAN_TEST_ROOT/parent-drift.pm"
            run install; expect 1; off; no_mounts; no_mutation
            grep -q '^pm install -r ' "$XPAN_TEST_ROOT/commands" || fail 'PM drift fixture did not enter strict lifecycle'
            [ ! -e "$STATE/parent.context" ] && [ ! -e "$ACTIVE/.xpan-apk-identity" ] || fail 'PM drift published parent/APK identity'
        else
            run install; expect 0
            PIN_BEFORE=$(hash "$STATE/parent.context")
            if [ "$fault" = mount ]; then : > "$XPAN_TEST_ROOT/parent-drift.mount.camera_unit_config"
            else : > "$XPAN_TEST_ROOT/parent-drift.publish"; fi
            : > "$XPAN_TEST_ROOT/commands"
            run; expect 1; off; no_pm_install
            ! grep -q '^umount ' "$XPAN_TEST_ROOT/commands" || fail 'transaction drift blindly unmounted unproven child'
            [ "$(hash "$STATE/parent.context")" = "$PIN_BEFORE" ] || fail 'transaction drift re-pinned parent'
            if [ "$fault" = mount ]; then
                [ "$(mount_count)" = 1 ] || fail 'mount drift continued binding after parent changed'
                [ ! -e "$STATE/unit.mount" ] || fail 'mount drift published unproved journal'
            else
                [ "$(mount_count)" = 3 ] || fail 'publish drift changed unproven mounts'
                for role in unit feature vendor; do
                    keys "$STATE/$role.mount" 'ownership_version boot_id namespace target mount_sha256 inode token parent_mount_sha256 parent_inode'
                done
            fi
        fi
        PARENT_ROW=$(/usr/bin/awk -v p="$XPAN_TEST_ROOT" '$5==p {print}' "$XPAN_TEST_ROOT/mountinfo")
        [ "${PARENT_ROW%% *}" = 91 ] || fail 'parent drift marker did not fire'
        parent_preserved
        ok "generic parent drift during $fault fails inactive and never unmounts foreign parent"
    done
}
for f in runtime customize post-fs-data service uninstall parent-context; do /usr/bin/sh -n "$SUITE/module/$f.sh"; done
if [ "${XPAN_RUNTIME_WORKER_SMOKE:-}" = 1 ]; then
    worker_scenarios
    printf 'Worker scenarios passed.\n'
    exit 0
fi
if [ "${XPAN_RUNTIME_SMOKE:-}" = 1 ]; then
    fixture
    run; expect 0; on
    : > "$XPAN_TEST_ROOT/commands"
    run; expect 0; on; no_mutation
    ! grep -q '^setprop debug.xpan.active 0' "$XPAN_TEST_ROOT/commands" || fail 'healthy tick invalidates process token'
    run uninstall; expect 0; off; no_mounts
    ok 'latest core mount/healthy/no-flicker/token-owned uninstall'
    fixture
    : > "$XPAN_TEST_ROOT/mount-fail.camera_unit_feature_config.protobuf"
    run; expect 1; off; no_mounts
    ok 'latest partial-mount rollback'
    fixture
    : > "$XPAN_TEST_ROOT/context-mismatch"
    run; expect 1; off; no_mounts
    ok 'latest source SELinux mismatch fails closed'
    printf 'Smoke scenarios passed.\n'
    exit 0
fi
if [ "${XPAN_RUNTIME_PARENT_SMOKE:-}" = 1 ]; then
    parent_scenarios
    printf 'All %s parent-context scenarios passed. Host mocks only; no phone or real mounts.\n' "$COUNT"
    exit 0
fi
for p in c16 c17; do
    fixture "$p"
    run; expect 0; on
    BEFORE=$(hash "$XPAN_TEST_ROOT/mountinfo")
    : > "$XPAN_TEST_ROOT/commands"
    run; expect 0; on; no_mutation
    ! grep -q '^setprop debug.xpan.active 0' "$XPAN_TEST_ROOT/commands" || fail 'healthy tick invalidates process token'
    [ "$(hash "$XPAN_TEST_ROOT/mountinfo")" = "$BEFORE" ] || fail 'idempotent changed mounts'
    no_pm_install
    run stop; expect 0; off; no_mounts
    ok "$p exact eligibility, three binds, property order, idempotent, owned stop"
done
for fault in model fingerprint camera duplicate-camera no-libs duplicate-libs mixed-libs duplicate-lib-field sdk adapter payload manifest original current-output hash-fail pm-fail no-token token-mismatch apk-identity apk-bytes receipt receipt-unknown not-booted unknown-lib-parent; do
    fixture
    case "$fault" in
        model) printf 'CPH2667\n' > "$XPAN_TEST_ROOT/props/ro.product.model" ;;
        fingerprint) printf 'OTA/version\n' > "$XPAN_TEST_ROOT/props/ro.build.fingerprint" ;;
        camera) printf '7.013.51.1\n' > "$XPAN_TEST_ROOT/camera-version" ;;
        duplicate-camera|no-libs|duplicate-libs|mixed-libs|duplicate-lib-field|hash-fail|pm-fail) : > "$XPAN_TEST_ROOT/$fault" ;;
        sdk) printf 'donor\n' > "$XPAN_TEST_ROOT/product/framework/com.oplus.camera.unit.sdk.jar" ;;
        adapter) printf 'donor\n' > "$XPAN_TEST_ROOT/product/framework/com.oplus.camera.unit.sdk.adapter.jar" ;;
        payload) printf 'bad\n' >> "$ACTIVE/payload/profiles/c17/camera_unit_config" ;;
        manifest) printf 'model=PJX110\n' >> "$ACTIVE/payload/profiles/c17/profile.props" ;;
        original) printf 'bad\n' >> "$XPAN_TEST_ROOT/stock/camera_unit_config" ;;
        current-output) cp "$ACTIVE/payload/profiles/c17/camera_unit_config" "$XPAN_TEST_ROOT/stock/camera_unit_config" ;;
        no-token) rm "$ACTIVE/.xpan-instance" ;;
        token-mismatch) cp "$XPAN_TEST_ROOT/uuid.source" "$LIFE/current.token" ;;
        apk-identity) printf '%064d\n' 0 > "$ACTIVE/.xpan-apk-identity" ;;
        apk-bytes) printf 'bad\n' >> "$XPAN_TEST_ROOT/data/app/fixture/com.xpanport.hook/base.apk" ;;
        receipt) rm "$ACTIVE/payload/lifecycle.inputs" ;;
        receipt-unknown) printf 'sdk_sha256=unknown\n' >> "$ACTIVE/payload/lifecycle.inputs" ;;
        not-booted) printf '0\n' > "$XPAN_TEST_ROOT/props/sys.boot_completed" ;;
        unknown-lib-parent) printf '/system/framework\n' > "$XPAN_TEST_ROOT/sdk-parent" ;;
    esac
    run; expect 1; off; no_mounts; no_mutation; no_pm_install
    ok "$fault fails closed before mounting"
done
for format in inline-unit multiline-pair multiline-unit; do
    fixture
    case "$format" in
        inline-unit) : > "$XPAN_TEST_ROOT/unit-only-libs" ;;
        multiline-pair) : > "$XPAN_TEST_ROOT/multiline-libs" ;;
        multiline-unit) : > "$XPAN_TEST_ROOT/multiline-libs"; : > "$XPAN_TEST_ROOT/unit-only-libs" ;;
    esac
    run; expect 0; on
    ok "$format actual unit path plus same-parent pinned adapter footprint"
done
fixture
: > "$XPAN_TEST_ROOT/multiline-libs"
: > "$XPAN_TEST_ROOT/unit-only-libs"
printf 'c16-sdk\n' > "$XPAN_TEST_ROOT/product/framework/com.oplus.camera.unit.sdk.jar"
printf 'c16-adapter\n' > "$XPAN_TEST_ROOT/product/framework/com.oplus.camera.unit.sdk.adapter.jar"
run; expect 1; off; no_mutation; no_mounts
ok 'real C17 unit-only format with C16 effective SDK content still rejected'

fixture
printf '/my_product/product_overlay/framework\n' > "$XPAN_TEST_ROOT/sdk-parent"
printf 'donor\n' > "$XPAN_TEST_ROOT/product/framework/com.oplus.camera.unit.sdk.jar"
run; expect 0; on
ok 'actual my_product loaded SDK accepted, unused product file not treated as loaded'

for n in camera_unit_config camera_unit_feature_config.protobuf oplus_camera_config; do
    fixture
    : > "$XPAN_TEST_ROOT/mount-fail.$n"
    run; expect 1; off; no_mounts
    for r in unit feature vendor; do [ ! -e "$STATE/$r.mount" ] || fail 'partial journal retained'; done
    ok "partial mount failure $n rolls back new binds only"
done
fixture
: > "$XPAN_TEST_ROOT/mount-error-after.camera_unit_feature_config.protobuf"
run; expect 1; off; no_mounts
ok 'mount error after attach retracts its proven bind and earlier new bind'
fixture
: > "$XPAN_TEST_ROOT/ota-during-mount.camera_unit_config"
run; expect 1; off; no_mounts
ok 'OTA between binds retracts transaction before active publication'

fixture
: > "$XPAN_TEST_ROOT/journal-block.camera_unit_feature_config.protobuf"
run; expect 1; off; no_mounts
[ -d "$STATE/feature.mount" ] || fail 'occupied untrusted journal path was modified'
ok 'journal publication failure retracts only current proven bind and earlier new bind'

for bytes in original output; do
    fixture
    if [ "$bytes" = original ]; then F=$XPAN_TEST_ROOT/originals/camera_unit_config; else F=$ACTIVE/payload/profiles/c17/camera_unit_config; fi
    foreign camera_unit_config "$F"
    BEFORE=$(hash "$XPAN_TEST_ROOT/mountinfo")
    run; expect 1; off; no_mutation
    [ "$(hash "$XPAN_TEST_ROOT/mountinfo")" = "$BEFORE" ] || fail 'foreign removed'
    ok "foreign same-$bytes hash never qualifies as stock/owned"
done
fixture
printf '99 1 0:99 /overlay %s rw - mock foreign rw\n' "$XPAN_TEST_ROOT/stock" >> "$XPAN_TEST_ROOT/mountinfo"
run; expect 1; off; no_mutation
ok 'foreign config-directory overlay rejected before nested binds'

for fault in disable remove OTA camera sdk token staged payload; do
    fixture
    run; expect 0; on
    : > "$XPAN_TEST_ROOT/commands"
    case "$fault" in
        disable|remove) : > "$ACTIVE/$fault" ;;
        OTA) printf 'OTA/new\n' > "$XPAN_TEST_ROOT/props/ro.build.fingerprint" ;;
        camera) printf '7.013.52\n' > "$XPAN_TEST_ROOT/camera-version" ;;
        sdk) printf 'donor\n' > "$XPAN_TEST_ROOT/product/framework/com.oplus.camera.unit.sdk.jar" ;;
        token) cp "$XPAN_TEST_ROOT/uuid.source" "$LIFE/current.token" ;;
        staged) mkdir -p "$STAGED" ;;
        payload) printf 'modified\n' >> "$ACTIVE/payload/profiles/c17/camera_unit_config" ;;
    esac
    run; expect 1; off; no_mounts
    FIRST=$(grep -m1 '^setprop ' "$XPAN_TEST_ROOT/commands")
    [ "$FIRST" = 'setprop debug.xpan.active 0' ] || fail 'did not revoke active first'
    ok "active $fault revokes properties then only owned mounts"
done
fixture
run; expect 0; on
rm -rf "$ACTIVE"
run; expect 1; off; no_mounts
ok 'missing module directory retracts journal-proven mounts without payload files'

fixture
run; expect 0; on
# Foreign mount above own unit: ambiguous stack must never be unmounted.
printf '901 100 0:999 /foreign %s rw - mock foreign rw\n' "$XPAN_TEST_ROOT/stock/camera_unit_config" >> "$XPAN_TEST_ROOT/mountinfo"
: > "$ACTIVE/disable"
: > "$XPAN_TEST_ROOT/commands"
run; expect 1; off
! grep -Fq "umount $XPAN_TEST_ROOT/stock/camera_unit_config" "$XPAN_TEST_ROOT/commands" || fail 'unmounted covered/foreign unit'
[ -f "$STATE/unit.mount" ] || fail 'lost covered ownership journal'
[ "$(mount_count)" = 2 ] || fail 'did not retract other own binds'
ok 'foreign covering own bind retained, only unobstructed own binds retracted'

fixture
run; expect 0; on
# Keep preexisting unit, model externally removed two of our binds/journals.
"$XPAN_TEST_ROOT/bin/umount" "$XPAN_TEST_ROOT/stock/camera_unit_feature_config.protobuf"
"$XPAN_TEST_ROOT/bin/umount" "$XPAN_TEST_ROOT/stock/oplus_camera_config"
rm "$STATE/feature.mount" "$STATE/vendor.mount"
: > "$XPAN_TEST_ROOT/mount-fail.oplus_camera_config"
: > "$XPAN_TEST_ROOT/commands"
run; expect 1; off
[ "$(mount_count)" = 1 ] || fail 'existing owned bind affected'
[ -f "$STATE/unit.mount" ] || fail 'preexisting journal deleted'
! grep -Fq "umount $XPAN_TEST_ROOT/stock/camera_unit_config" "$XPAN_TEST_ROOT/commands" || fail 'rolled back existing bind'
ok 'partial repair failure preserves preexisting owned bind and rolls back new feature'

for property in debug.xpan.profile debug.xpan.token debug.xpan.active; do
    fixture
    : > "$XPAN_TEST_ROOT/setprop-fail.$property"
    run; expect 1; off; no_mounts
    ok "$property property failure cannot publish ready"
done
fixture
run; expect 0; on
: > "$XPAN_TEST_ROOT/umount-fail.camera_unit_config"
: > "$ACTIVE/disable"
run; expect 1; off
[ -f "$STATE/unit.mount" ] || fail 'lost retry record on unmount failure'
rm "$XPAN_TEST_ROOT/umount-fail.camera_unit_config"
run; expect 1; off; no_mounts
ok 'unmount failure retains ownership for next inactive retry'

fixture
run; expect 0; on
: > "$XPAN_TEST_ROOT/commands"
run_customize; expect 0; off; no_mounts
[ "$(grep -c '^umount ' "$XPAN_TEST_ROOT/commands")" = 3 ] || fail 'installer did not retract three old owned binds'
[ "$(<"$ACTIVE/.xpan-instance")" = "$(<"$XPAN_TEST_ROOT/uuid.source")" ] || fail 'reinstall did not rotate old instance'
run; expect 0; on
ok 'customize automatically retracts only old Xpan owned mounts and installs fresh instance'
fixture
foreign camera_unit_config "$ACTIVE/payload/profiles/c17/camera_unit_config"
run_customize; expect 1; no_mutation; no_pm_install
ok 'customize rejects foreign output rather than relaxing unchanged lifecycle originals'

fixture
op_fixture
run_customize; expect 0; off; no_mounts
[ -s "$ACTIVE/.xpan-reboot-required" ] || fail 'pending SDK install did not register reboot'
grep -Fq '补全已更新，重启手机后生效' "$XPAN_TEST_ROOT/stdout" || fail 'pending install did not explain reboot'
grep -qx c17-sdk "$XPAN_TEST_OP_DIR/system/product/framework/com.oplus.camera.unit.sdk.jar" || fail 'compat helper not called before SDK qualification'
grep -qx c16-sdk "$STATE/op-compat-backup/com.oplus.camera.unit.sdk.jar" || fail 'old OP SDK backup not retained'
[ -s "$ACTIVE/.xpan-apk-identity" ] || fail 'pending install did not verify APK'
run; expect 1; off; no_mounts; no_mutation
printf '12345678-1234-4234-8234-000000000007\n' > "$XPAN_TEST_ROOT/boot-id"
printf 'mnt:[4026531999]\n' > "$XPAN_TEST_ROOT/ns"
cp "$XPAN_TEST_OP_DIR/system/product/framework/"*.jar "$XPAN_TEST_ROOT/product/framework/"
run boot; expect 0; off; no_mounts
run; expect 0; on
ok 'OP C16 mounted SDK installs APK as pending reboot, real C17 SDK required before activation'

for mode in active staged old-boot; do
    fixture
    op_fixture
    run install; expect 0; off; no_mounts
    cp "$XPAN_TEST_OP_DIR/system/product/framework/"*.jar "$XPAN_TEST_ROOT/product/framework/"
    printf '12345678-1234-4234-8234-000000000008\n' > "$XPAN_TEST_ROOT/uuid.source"
    case "$mode" in
        active) TARGET=$ACTIVE ;;
        staged)
            mkdir -p "${STAGED%/*}"
            cp -R "$ACTIVE" "$STAGED"
            rm "$STAGED/.xpan-reboot-required"
            TARGET=$STAGED ;;
        old-boot)
            printf '12345678-1234-4234-8234-000000000007\n' > "$XPAN_TEST_ROOT/boot-id"
            TARGET=$ACTIVE ;;
    esac
    RC=0
    /usr/bin/sh "$SUITE/module/runtime.sh" install "$TARGET" > "$XPAN_TEST_ROOT/stdout" 2>&1 || RC=$?
    expect 0; off; no_mounts
    if [ "$mode" = old-boot ]; then
        [ ! -e "$TARGET/.xpan-reboot-required" ] || fail 'past reboot marker survived successful current SDK install'
    else
        [ "$(<"$TARGET/.xpan-reboot-required")" = "$(<"$XPAN_TEST_ROOT/boot-id")" ] || fail 'same-boot reinstall lost pending state'
    fi
    if [ "$mode" = staged ]; then rm -rf "$ACTIVE"; mv "$STAGED" "$ACTIVE"; fi
    : > "$XPAN_TEST_ROOT/commands"
    run
    if [ "$mode" = old-boot ]; then expect 0; on; else expect 1; off; no_mounts; fi
    ok "$mode reinstall retains only current-boot pending state without reintroducing manual approval"
done

fixture
op_fixture
cp "$XPAN_TEST_OP_DIR/system/product/framework/"*.jar "$XPAN_TEST_ROOT/product/framework/"
SERVICE_CASE=$XPAN_TEST_ROOT/service-case.sh
/usr/bin/grep -v '^xpan_main "\$@"$' "$SUITE/module/runtime.sh" > "$SERVICE_CASE"
printf '\nsleep() { exit 0; }\nxpan_main service "$XPAN_TEST_ROOT/data/adb/modules/XpanAce3Pro"\n' >> "$SERVICE_CASE"
RC=0
/usr/bin/sh "$SERVICE_CASE" > "$XPAN_TEST_ROOT/stdout" 2>&1 || RC=$?
expect 0; off; no_mounts
[ "$(<"$ACTIVE/.xpan-reboot-required")" = "$(<"$XPAN_TEST_ROOT/boot-id")" ] || fail 'service helper changes did not arm pending reboot'
no_pm_install
ok 'service compatibility update under both locks arms reboot marker before any activation'

for fault in no-source unknown-sdk source-corrupt; do
    fixture
    op_fixture
    case "$fault" in
        no-source) rm "$ACTIVE/payload/op-compat/framework/com.oplus.camera.unit.sdk.jar" ;;
        unknown-sdk) printf 'unknown-sdk\n' > "$XPAN_TEST_ROOT/product/framework/com.oplus.camera.unit.sdk.jar" ;;
        source-corrupt) printf 'unapproved C17\n' > "$ACTIVE/payload/op-compat/framework/com.oplus.camera.unit.sdk.adapter.jar" ;;
    esac
    run_customize; expect 1; off; no_mounts; no_pm_install
    [ "$(<"$ACTIVE/.xpan-reboot-required")" = "$(<"$XPAN_TEST_ROOT/boot-id")" ] || fail 'partial compatibility update lost reboot fact after rejected SDK install'
    ok "OP $fault cannot bypass actual SDK contract; partial update keeps pending reboot"
done

# 补全已写入后的安装失败，不得在同一次启动的重试中丢失待重启事实。
fixture
op_fixture
: > "$XPAN_TEST_ROOT/install-fail"
run install; expect 1; off; no_mounts
[ "$(<"$ACTIVE/.xpan-reboot-required")" = "$(<"$XPAN_TEST_ROOT/boot-id")" ] || fail 'PM failure lost compatibility reboot marker'
cp "$XPAN_TEST_OP_DIR/system/product/framework/"*.jar "$XPAN_TEST_ROOT/product/framework/"
rm "$XPAN_TEST_ROOT/install-fail"
printf '12345678-1234-4234-8234-000000000008\n' > "$XPAN_TEST_ROOT/uuid.source"
run install; expect 0; off; no_mounts
[ "$(<"$ACTIVE/.xpan-reboot-required")" = "$(<"$XPAN_TEST_ROOT/boot-id")" ] || fail 'PM retry lost current-boot pending compatibility'
run; expect 1; off; no_mounts
ok 'failed PM after compatibility update retains reboot marker across same-boot retry'

# Joint runtime/lifecycle fixture stays inside the project test root.
fixture
rm "$ACTIVE/payload/lifecycle.inputs"
run_customize; expect 0; off; no_mounts
[ "$(wc -l < "$ACTIVE/payload/lifecycle.inputs")" = 13 ] || fail 'receipt not exactly 13 keys'
[ "$(<"$ACTIVE/.xpan-instance")" = "$(<"$XPAN_TEST_ROOT/uuid.source")" ] || fail 'lifecycle did not rotate fresh token'
grep -q '^pm install -r ' "$XPAN_TEST_ROOT/commands" || fail 'lifecycle not called'
run; expect 0; on
ok 'strict customize producer -> unchanged lifecycle -> qualified runtime end-to-end mock'

fixture
mkdir "$LIFE/lock"
run; expect 1; off; no_mutation
rmdir "$LIFE/lock"
ok 'shared live lifecycle lock not stolen; no mount mutation'
fixture
ln -s "$XPAN_TEST_ROOT/protected/camera/preferences.xml" "$ACTIVE/disable"
run; expect 1; off; no_mutation
ok 'symlink marker is disabled, never followed or deleted'
fixture
/usr/bin/sh "$ACTIVE/post-fs-data.sh" > "$XPAN_TEST_ROOT/stdout" 2>&1 || fail 'post-fs-data failed'
off; no_mutation
ok 'post-fs-data revokes readiness without mounting before PM qualification'

for fault in boot-id namespace state-file; do
    fixture
    printf '1\n' > "$XPAN_TEST_ROOT/props/debug.xpan.active"
    printf 'c17\n' > "$XPAN_TEST_ROOT/props/debug.xpan.profile"
    cp "$ACTIVE/.xpan-instance" "$XPAN_TEST_ROOT/props/debug.xpan.token"
    case "$fault" in
        boot-id) printf 'invalid\n' > "$XPAN_TEST_ROOT/boot-id" ;;
        namespace) printf 'invalid\n' > "$XPAN_TEST_ROOT/ns" ;;
        state-file) rmdir "$STATE"; printf 'occupied state path\n' > "$STATE" ;;
    esac
    run boot; expect 1; off; no_mutation
    [ "$fault" != state-file ] || rm "$STATE"
    ok "early-init $fault revokes readiness without unproven cleanup"
done

for fault in context-missing context-mismatch mode-fail; do
    fixture
    : > "$XPAN_TEST_ROOT/$fault"
    run; expect 1; off; no_mounts
    ok "$fault rejects source access without SELinux bypass"
done
fixture
run; expect 0; on
rm -rf "$ACTIVE/payload/profiles"
run uninstall; expect 0; off; no_mounts
ok 'synchronous uninstall token-owned teardown needs no available profile'
for fault in new-token staged; do
    fixture
    run; expect 0; on
    : > "$XPAN_TEST_ROOT/commands"
    case "$fault" in
        new-token) cp "$XPAN_TEST_ROOT/uuid.source" "$LIFE/current.token" ;;
        staged) mkdir -p "$STAGED" ;;
    esac
    run uninstall; expect 1; on; no_mutation
    ! grep -q '^setprop ' "$XPAN_TEST_ROOT/commands" || fail 'old uninstall changed newer state'
    ok "uninstall $fault protects newer instance and existing mounts"
done
fixture
run; expect 0; on
printf 'mnt:[9999999]\n' > "$XPAN_TEST_ROOT/ns"
: > "$ACTIVE/disable"
: > "$XPAN_TEST_ROOT/commands"
run; expect 1; off; no_mutation
[ -f "$STATE/unit.mount" ] || fail 'cross-namespace journal stolen'
ok 'different namespace cannot unmount or steal another namespace journal'

parent_scenarios
worker_scenarios
printf '\nAll %s local runtime/customize scenarios passed. SDK hashes are explicit mock pins; config/manifest/APK hashes use host SHA256. No phone commands or real mounts.\n' "$COUNT"
