#!/system/bin/sh
# Stages the data the camera and gallery read from their own private files.
#
# The camera-side payload is NOT here any more. Since 485 it ships as a module filesystem layer
# (system/odm/etc/camera/...), mounted by magic mount before any app starts — no script, no unlock,
# no window in which the camera can see a half-built tree. What is left here is what genuinely
# belongs to an app's own data directory: the master-colour tables, the imported filters, the
# gallery's watermark styles and its feature switch, and the profile.
#
# Everything here is additive: files are written into the apps' data directories, never over /odm.
# Each block is skipped when its payload is absent, so a trimmed build simply does less instead of
# failing halfway.
#
# No restorecon on anything under /data/user/0/<pkg>: running it there replaces the app's SELinux
# categories with the ones in file_contexts, which have none, and the app is then denied the files
# this script just wrote. New files inherit the parent directory's context (categories included), so
# chown and chmod are all that is needed.
#
# Kill switch: touch /data/adb/omnicam_off, then reboot.

MODDIR=${0%/*}
APP=/data/user/0/com.oplus.camera
GALLERY=/data/user/0/com.coloros.gallery3d

case "${1:-}" in
--wait)
    # Detached pass, started below when the camera's data directory is not up yet.
    #
    # /data/user/0/<pkg> only appears once the user unlocks: this device is file-based encrypted
    # (ro.crypto.type=file) and KernelSU runs service.sh at late_start, which on a locked boot is
    # well before that. 481 and every version before it waited 120 seconds, gave up, and exited —
    # silently, because there was nothing to log to. The camera-side data no longer depends on this
    # script, but the master-colour tables, the gallery watermark styles and the gallery's feature
    # switch still do, so keep trying.
    N=0
    while [ ! -d "$APP" ] && [ "$N" -lt 720 ]; do
        sleep 10
        N=$((N + 1))
    done
    [ -d "$APP" ] || exit 0
    ;;
esac

for OFF in /data/adb/omnicam_off /data/adb/omnicam_nostack_off /data/adb/omnicam_fullport_off; do
    [ -f "$OFF" ] && exit 0
done

PAYLOAD=$MODDIR/payload
GR_AVAILABLE='0.6(14),1(23),2(47),3(70),6(139),10(230)'
GR_MARKED='1.2173913(28),1.7391304(40)'

# ---------------------------------------------------------------------------------------------
# The Meishe renderer: the one system file this build substitutes, and the reason it has to.
#
# POP (Polaroid) and GR's four film styles are not BasicTone passes. They are Meishe *renderers*
# that live in /odm/lib64/libmsnativefilter.so. libAlgoInterface.so opens that file by absolute path
# (the string "/odm/lib64/libmsnativefilter.so" is in it) and resolves the renderer classes with
# dlsym, so a renderer the file does not contain cannot be created — there is nothing to fall back
# to, and the frame comes out empty: a black viewfinder, and a green still.
#
# This firmware's file is 7,282,176 bytes / 2,723 exports and is missing exactly the renderers the
# two broken features need:
#
#   CNvPolaroid           0 symbols   POP's Polaroid renderer
#   CNvFilterCCD          0           CCD
#   CNvPortra400          0           Portra 400
#   CNvShapeMask          0           the mask layer GR's looks use
#   CNvCompoundLutTiled   0           the tiled compound-LUT path
#   CNvInflation          0
#
# The GR style LUTs on this device are ".MS-LUT" **version 2** (gr.posi / gr.nega / gr.bw /
# gr.hi.bw.rgba.bin), a shape only the newer compound-LUT renderer reads. So one cause explains
# both reports: POP renders nothing at all, and GR is fine on 标准 (no filter) and black on the
# other four.
#
# Why substituting it is not "replacing a system library" in the sense this project forbids:
#   * libAlgoInterface.so, libBasicTonePhoto*.so, libBasicTonePhotoX9/X10.so and every ODM config
#     stay byte-for-byte as the firmware shipped them;
#   * libmsnativefilter.so is a GPU filter renderer, reached through dlopen/dlsym, so a symbol the
#     substitute lacks fails that one filter instead of failing the load;
#   * every one of the 16 Meishe symbols libAlgoInterface.so currently resolves is still exported by
#     the substitute (checked by name before this was shipped), and it adds three more:
#     MsRenderHardwareBuffer, MsSetFacePoints, MsSetStarFilterAngles. Nothing is lost.
#
# Where the file comes from (487): it is part of the mount layer, at
# system/odm/lib64/libmsnativefilter.so, so magic mount puts it in place before any app starts —
# the same mechanism that already carries /odm/etc/camera and that 485/486 proved works. 486 tried to
# bind it from service.sh alone and the 486 report says that never happened: the settings page still
# read the stock 7282176-byte file. A script binding into /odm/lib64 depends on which mount namespace
# KernelSU hands service.sh, and nothing in the report could tell that apart from a failed mount
# because bind.log was not part of the export. Both are fixed: the layer is the primary path, and
# the bind below is kept as the fallback for a phone without a mount metamodule.
#
# Escape hatch: press this module's action button in KernelSU (it moves the file out of the layer
# and back), or `touch /data/adb/omnicam_msfilter_off`, then reboot. Nothing else depends on it.
MSFILTER_OFF=/data/adb/omnicam_msfilter_off
MSFILTER_SRC=$MODDIR/system/odm/lib64/libmsnativefilter.so
MSFILTER_DST=/odm/lib64/libmsnativefilter.so
MSFILTER_BYTES=10957976
MSFILTER_LOG=$MODDIR/bind.log

msfilter_log() {
    echo "msfilter: $1" >> "$MSFILTER_LOG"
    # A second copy the settings app can read without root, so a report always carries it.
    echo "msfilter: $1" >> /data/local/tmp/omnicam-bind.log 2>/dev/null
}

msfilter_bind() {
    [ -f "$MSFILTER_OFF" ] && { msfilter_log "off ($MSFILTER_OFF), the layer copy is not used"; return 0; }
    [ -f "$MSFILTER_DST" ] || { msfilter_log "$MSFILTER_DST absent, nothing to cover"; return 0; }
    # Already the substitute: the layer mounted it (the normal case since 487), or a soft reboot kept
    # the bind from the last run. Either way there is nothing left to do.
    if [ "$(wc -c < "$MSFILTER_DST")" = "$MSFILTER_BYTES" ]; then
        msfilter_log "in place, $(wc -c < "$MSFILTER_DST") bytes, CNvPolaroid $(grep -qa CNvPolaroid "$MSFILTER_DST" && echo present || echo ABSENT)"
        return 0
    fi
    # A firmware that already ships the Polaroid renderer needs no substitute, and gets none.
    if grep -qa CNvPolaroid "$MSFILTER_DST"; then
        msfilter_log "stock already has CNvPolaroid, left alone"
        return 0
    fi
    if [ ! -f "$MSFILTER_SRC" ]; then
        msfilter_log "stock renderer ($(wc -c < "$MSFILTER_DST") bytes) and no layer copy at $MSFILTER_SRC — the mount layer did not carry it"
        return 0
    fi
    chown root:root "$MSFILTER_SRC" 2>/dev/null
    chmod 0644 "$MSFILTER_SRC"
    # Same SELinux label as the file it covers; the explicit context is the fallback.
    chcon --reference="$MSFILTER_DST" "$MSFILTER_SRC" 2>/dev/null \
        || chcon u:object_r:same_process_hal_file:s0 "$MSFILTER_SRC" 2>/dev/null
    # The mount has to land in init's namespace or the camera never sees it.
    if [ "$(readlink /proc/self/ns/mnt)" != "$(readlink /proc/1/ns/mnt)" ] && command -v nsenter > /dev/null 2>&1; then
        nsenter -t 1 -m -- mount --bind "$MSFILTER_SRC" "$MSFILTER_DST" >> "$MSFILTER_LOG" 2>&1
        MSFILTER_RESULT=$?
    else
        mount --bind "$MSFILTER_SRC" "$MSFILTER_DST" >> "$MSFILTER_LOG" 2>&1
        MSFILTER_RESULT=$?
    fi
    if [ "$MSFILTER_RESULT" = "0" ]; then
        if grep -qa CNvPolaroid "$MSFILTER_DST"; then
            msfilter_log "bound in place, $(wc -c < "$MSFILTER_DST") bytes, CNvPolaroid present"
        else
            msfilter_log "bound but CNvPolaroid is still absent — wrong payload"
        fi
    else
        msfilter_log "BIND FAILED ($MSFILTER_RESULT); the mount layer is the only path left, check that a metamodule is installed"
    fi
}

# Runs before the app-data wait: /odm/lib64 does not depend on the unlock, and the camera must not
# start on the stock renderer.
: > /data/local/tmp/omnicam-bind.log 2>/dev/null
chmod 0644 /data/local/tmp/omnicam-bind.log 2>/dev/null
msfilter_bind

# A bounded wait: a boot must not stall forever if the camera is not installed.
wait_for() {
    DIR=$1
    LIMIT=$2
    N=0
    while [ ! -d "$DIR" ] && [ "$N" -lt "$LIMIT" ]; do
        sleep 1
        N=$((N + 1))
    done
    [ -d "$DIR" ]
}

if ! wait_for "$APP" 60; then
    # Not installed at all, or still locked. Do not give up: hand the whole staging run to a detached
    # copy of this script that keeps waiting for the unlock. Nothing here blocks the boot.
    if command -v setsid > /dev/null 2>&1; then
        setsid sh "$MODDIR/service.sh" --wait > /dev/null 2>&1 &
    else
        (sh "$MODDIR/service.sh" --wait > /dev/null 2>&1) &
    fi
    exit 0
fi

APP_UID=$(stat -c %u "$APP")
PREF_DIR=$APP/shared_prefs
PREF_FILE=$PREF_DIR/override_config_data.xml

mkdir -p "$PREF_DIR"
chown "$APP_UID:$APP_UID" "$PREF_DIR"
chmod 0770 "$PREF_DIR"

if [ ! -f "$PREF_FILE" ]; then
    printf '%s\n' '<?xml version="1.0" encoding="utf-8" standalone="yes" ?>' '<map>' '</map>' > "$PREF_FILE"
fi

set_config() {
    KEY=$1
    VALUE=$2
    if grep -q "name=\"$KEY\"" "$PREF_FILE"; then
        sed -i "s#<string name=\"$KEY\">[^<]*</string>#<string name=\"$KEY\">$VALUE</string>#" "$PREF_FILE"
    else
        sed -i "/<\/map>/i\    <string name=\"$KEY\">$VALUE</string>" "$PREF_FILE"
    fi
}

# GR mode is enabled through the app's own feature switch. The HDR capture list names only modes that
# have a decision tree on this device: retroCamera is left out (its HDR support is unverified), xpan is
# not listed because its HDR support is unverified even though the mode itself is now reachable.
set_config com.oplus.gr.mode.support 1
# POP: the mode row in mode_data.db is written only when this flag is on (ModeDataSQLiteOpenHelper
# i()/j()), and the module stages the setting_Retro basictone it renders with.
set_config com.oplus.feature.retro.camera.support 1
set_config com.oplus.camera.mode.data.db.version 102
set_config com.oplus.available.gr.mode.zoomvalues "$GR_AVAILABLE"
set_config com.oplus.gr.mode.marked.zoomvalues "$GR_MARKED"
set_config com.oplus.camera.capture.hdr.cap.mode.value 'common,portrait,professional,night,highPixel,underWater,telephoto,gr'
set_config com.oplus.camera.preview.hdr.cap.mode.value 'common,professional,night,highPixel,underWater,telephoto,gr'
# XPAN: the firmware ships the mode, the decision-tree nodes and the crop parameters, but the support
# flag in oplus_camera_config is 0. Overriding the VendorTag here leaves the config file untouched.
set_config com.oplus.feature.xpan.mode.support 1

chown "$APP_UID:$APP_UID" "$PREF_FILE"
chmod 0660 "$PREF_FILE"

# The config profile (this device's own camera_unit_config with the modes its firmware ships but
# never lists) is NOT staged here. It is copied out of the module APK by the camera process itself
# (ConfigProfile.ensure), because only the app can write its own files with the app's SELinux
# categories: a root shell writing into /data/user/0/<pkg>/files produces files the app is denied,
# and before the first unlock it cannot write there at all. Nothing is mounted either way — the
# native path hooks serve the copy to the camera process only, so /odm stays untouched.

# Master colour (大师色彩): the engine data, scene profiles and look tables. Baked preview tables
# (files/jiege/mastercolor/live) come from the camera and are rebuilt when the module changes; the
# selected look survives the refresh.
MASTER_COLOR=$APP/files/jiege/mastercolor
if [ -d "$PAYLOAD/master_color" ]; then
    MASTER_COLOR_STAMP=$(grep '^versionCode=' "$MODDIR/module.prop")
    if [ "$(cat "$MASTER_COLOR/stamp" 2>/dev/null)" != "$MASTER_COLOR_STAMP" ] \
        || ! cmp -s "$PAYLOAD/master_color/hncs_engine.bin" "$MASTER_COLOR/hncs_engine.bin" \
        || ! cmp -s "$PAYLOAD/master_color/scene.bin" "$MASTER_COLOR/scene.bin" \
        || ! diff -rq "$PAYLOAD/master_color/looks" "$MASTER_COLOR/looks" >/dev/null 2>&1; then
        LOOK=$(cat "$MASTER_COLOR/look" 2>/dev/null)
        rm -rf "$MASTER_COLOR"
        mkdir -p "$MASTER_COLOR"
        cp "$PAYLOAD/master_color/hncs_engine.bin" "$MASTER_COLOR/hncs_engine.bin"
        cp "$PAYLOAD/master_color/scene.bin" "$MASTER_COLOR/scene.bin"
        cp -r "$PAYLOAD/master_color/looks" "$MASTER_COLOR/looks"
        echo "$MASTER_COLOR_STAMP" > "$MASTER_COLOR/stamp"
        [ -n "$LOOK" ] && echo "$LOOK" > "$MASTER_COLOR/look"
    fi
    mkdir -p "$MASTER_COLOR/live"
    chmod 0771 "$MASTER_COLOR" "$MASTER_COLOR/live" 2>/dev/null || true
fi

# GR watermark fonts: the styles name their fonts by URL, which the camera cannot load. Fonts the
# gallery already downloaded are copied where the GR hook looks for them.
GALLERY_FONTS=$GALLERY/files/watermark_master/materia
mkdir -p "$APP/files/jiege/fonts"
for FONT in GilroySemiBold.otf FZLTZCHK.TTF; do
    if [ -f "$GALLERY_FONTS/$FONT" ] && ! cmp -s "$GALLERY_FONTS/$FONT" "$APP/files/jiege/fonts/$FONT"; then
        cp "$GALLERY_FONTS/$FONT" "$APP/files/jiege/fonts/$FONT"
    fi
done
chown -R "$APP_UID:$APP_UID" "$APP/files/jiege" 2>/dev/null || true
chmod 0771 "$APP/files/jiege" 2>/dev/null || true

# The camera-side basictone copy is gone in 485, and this block cleans it up.
#
# 484 kept a 42 MB copy of the payload under the camera's private files because the merged tree was
# a module mount that could vanish. The copy never appeared on this device: this script runs before
# the user unlocks, /data/user/0/com.oplus.camera does not exist yet, and the detached --wait copy
# dies with its parent. 485 mounts the payload as a module filesystem layer instead, which is in
# place before any app starts, so there is nothing to copy — and a leftover copy from 484 must not
# be left to shadow it, because the camera would then read a stale layer.
BASICTONE_DST=$APP/files/jiege/basictone
if [ -d "$BASICTONE_DST" ]; then
    rm -rf "$BASICTONE_DST"
    echo "removed the 484 basictone copy: the module layer is the only source now" >> "$MODDIR/bind.log"
fi

# The same leftover, one directory over, and it is why one POP filter renders black (488).
#
# pop_path_remap.cpp serves reads of /odm/etc/camera/meishe_lut/<name> from
# files/pop_port/meishe_lut/<name> **when that file exists**, and only falls through to the layer
# when it does not. 484 created that copy; 485 stopped creating it but never removed it, so on a
# phone that has been upgraded from 484 the camera still reads a partial, months-old set of LUTs for
# the filters that happen to be in it, and the layer for the rest. That is exactly the 487 report:
# POP renders every filter except 正片胶片浓郁, which is the one whose LUT is stale or missing there.
#
# With the copy gone the remap finds nothing, falls through, and every POP LUT comes from the single
# source the layer carries. Removing it is safe: nothing writes to it any more.
POP_LUT_DST=$APP/files/pop_port
if [ -d "$POP_LUT_DST" ]; then
    rm -rf "$POP_LUT_DST"
    echo "removed the 484 pop_port copy: the layer is the only POP LUT source now" >> "$MODDIR/bind.log"
fi
echo "layer: lmt_gr=$(ls /odm/etc/camera/basictone/lmt_gr 2>/dev/null | wc -l) pop=$(ls /odm/etc/camera/basictone/pop_port 2>/dev/null | wc -l) retro=$(ls /odm/etc/camera/basictone/setting_Retro 2>/dev/null | wc -l) wm=$(ls /odm/etc/camera/basictone/mode_limit_watermark 2>/dev/null | wc -l) ms=$(ls /odm/etc/camera/meishe_lut 2>/dev/null | wc -l)" >> "$MODDIR/bind.log"

wait_for "$GALLERY" 120 || exit 0
GALLERY_UID=$(stat -c %u "$GALLERY")
GALLERY_PREF=$GALLERY/shared_prefs/business_featureSwitch_config.xml
N=0
while [ ! -f "$GALLERY_PREF" ] && [ "$N" -lt 120 ]; do
    sleep 1
    N=$((N + 1))
done
if [ -f "$GALLERY_PREF" ]; then
    if grep -q 'name="business_featureSwitch_is_camera_gr_supported"' "$GALLERY_PREF"; then
        sed -i 's/name="business_featureSwitch_is_camera_gr_supported" value="false"/name="business_featureSwitch_is_camera_gr_supported" value="true"/' "$GALLERY_PREF"
    else
        sed -i '/<\/map>/i\    <boolean name="business_featureSwitch_is_camera_gr_supported" value="true" />' "$GALLERY_PREF"
    fi
    chown "$GALLERY_UID:$GALLERY_UID" "$GALLERY_PREF"
    chmod 0660 "$GALLERY_PREF"
fi

# Gallery-side GR watermark styles.
if [ -d "$PAYLOAD/watermark_master_styles" ]; then
    STYLE_OUT=$GALLERY/files/ricoh_gr_styles
    mkdir -p "$STYLE_OUT"
    cp -f "$PAYLOAD/watermark_master_styles/"*.json "$STYLE_OUT/" 2>/dev/null || true
    if [ -d "$PAYLOAD/watermark_drawables" ]; then
        cp -f "$PAYLOAD/watermark_drawables/"*.webp "$STYLE_OUT/" 2>/dev/null || true
    fi
    chown -R "$GALLERY_UID:$GALLERY_UID" "$STYLE_OUT"
    chmod 0644 "$STYLE_OUT"/* 2>/dev/null || true
fi

# Camera unit SDK with the palette classes (CombineLut, PaletteColorTable) for the gallery's palette
# editor; the gallery hook loads this copy in place of the system one. Dex must be read-only.
IPU_JAR=$PAYLOAD/ipu/com.oplus.camera.unit.sdk.jar
if [ -f "$IPU_JAR" ]; then
    IPU_OUT=$GALLERY/files/jiege_ipu
    mkdir -p "$IPU_OUT"
    cmp -s "$IPU_JAR" "$IPU_OUT/com.oplus.camera.unit.sdk.jar" || cp -f "$IPU_JAR" "$IPU_OUT/"
    chown "$GALLERY_UID:$GALLERY_UID" "$IPU_OUT"
    chmod 0711 "$IPU_OUT"
    chown 0:0 "$IPU_OUT/com.oplus.camera.unit.sdk.jar"
    chmod 0444 "$IPU_OUT/com.oplus.camera.unit.sdk.jar"
    chcon "$(ls -dZ "$IPU_OUT" | awk '{print $1}')" "$IPU_OUT/com.oplus.camera.unit.sdk.jar" 2>/dev/null || true
fi
