#!/system/bin/sh
# The one file this script still has to build at boot, plus a mount report for the next bug report.
#
# What changed in 485: the data layer is no longer bind-mounted here. Everything the camera reads by
# name — meishe_lut, basictone/lmt_gr, basictone/pop_port, basictone/mode_limit_watermark,
# basictone/setting_Retro — now ships inside the module as a plain filesystem layer under
# system/odm/etc/camera/, and the mount is done by KernelSU's magic mount (or by the
# magic_mount_rs metamodule, which is the same mechanism and the one this build is tuned for).
#
# Why the old way had to go: 475-484 composed a merged tree in /data/adb/modules/<id>/merged and
# bind-mounted it over /odm/etc/camera/basictone. That is a mount made by this script, in this
# script's namespace, at post-fs-data time — three things that kept going wrong. 482 reported
# data=true, 483 reported data=false with every data-layer feature off, and 484's app-private copy
# never appeared because service.sh runs before the user unlocks and /data/user/0/com.oplus.camera
# does not exist yet. A module filesystem layer has none of those moving parts: magic mount runs
# before any app starts, it is the same code path that mounts the fonts and thermal modules on this
# device, and the files are already in the right place when the camera opens.
#
# Rule for this build is unchanged: nothing in /odm is replaced. The layer only adds files the
# firmware does not have (lmt_gr, pop_port, mode_limit_watermark, setting_Retro) and, for the two
# directories the payload genuinely extends (meishe_lut, basictone/lmt), the module's copies shadow
# the stock files of the same name and leave every other stock file in place.
#
# Kill switch: touch /data/adb/omnicam_off, then reboot.

MODDIR=${0%/*}
LOG=$MODDIR/bind.log
set -u

log() {
    echo "$1" >> "$LOG"
}

: > "$LOG"
log "begin $(date)"

for OFF in /data/adb/omnicam_off /data/adb/omnicam_nostack_off /data/adb/omnicam_fullport_off; do
    if [ -f "$OFF" ]; then
        log "kill switch $OFF present, nothing mounted"
        exit 0
    fi
done

# Mount report. This is the first thing to read in a bug report: it says whether the module layer is
# actually in the camera's view of /odm, and it names the two mounts that are expected to be there.
log "meishe_lut=$(ls /odm/etc/camera/meishe_lut 2>/dev/null | wc -l) files"
log "basictone=$(ls /odm/etc/camera/basictone 2>/dev/null | wc -l) entries"
for NAME in lmt_gr pop_port mode_limit_watermark setting_Retro; do
    if [ -d "/odm/etc/camera/basictone/$NAME" ]; then
        log "layer $NAME: present, $(ls "/odm/etc/camera/basictone/$NAME" 2>/dev/null | wc -l) entries"
    else
        log "layer $NAME: ABSENT — the module's system/odm layer is not mounted"
    fi
done
if [ -d /odm/etc/camera/basictone/lmt_gr ]; then
    log "lmt_gr SCLut0=$(wc -c < /odm/etc/camera/basictone/lmt_gr/SCLut0 2>/dev/null || echo 0) bytes"
fi
if [ -d /data/adb/magic_mount ]; then
    log "metamodule magic_mount_rs: installed, config=$(cat /data/adb/magic_mount/config.toml 2>/dev/null | tr '\n' ' ')"
else
    log "metamodule magic_mount_rs: not installed, KernelSU's own magic mount is doing the work"
fi

# Pro video ISZ (2x main, 6x tele): stock zeroes the ISO gate of rows 25-40, which DoIzoomStable() can
# never pass; -1 means "no ISO check". Patched from the device's own file every boot and only mounted
# when exactly 16 rows matched, so a different config layout is left alone.
# Touch $MODDIR/disable_pro_video_isz to skip.
HWCFG=/odm/etc/camera/CameraHWConfiguration.config
HWOUT=$MODDIR/merged/CameraHWConfiguration.config
if [ -f "$MODDIR/disable_pro_video_isz" ]; then
    log "pro video isz disabled"
elif [ -f "$HWCFG" ]; then
    mkdir -p "$MODDIR/merged"
    awk -F';' -v OFS=';' '
        /^\[/ { section = $0 }
        section == "[InsensorZoom]" && match($0, /^[ \t]*iZoomSettins\[[0-9]+\]/) {
            row = $0; sub(/^[ \t]*iZoomSettins\[/, "", row); sub(/\].*/, "", row)
            if (row + 0 >= 25 && row + 0 <= 40 && NF == 14 && $12 + 0 == 0 && $13 + 0 == 0 && $12 ~ /^[ \t]*0[ \t]*$/ && $13 ~ /^[ \t]*0[ \t]*$/) {
                $12 = "     -1"; $13 = "     -1"; patched++
            }
        }
        { print }
        END { print patched + 0 > "/dev/stderr" }
    ' "$HWCFG" > "$HWOUT" 2> "$MODDIR/merged/isz_count"
    COUNT=$(cat "$MODDIR/merged/isz_count" 2>/dev/null)
    if [ "$COUNT" = "16" ]; then
        chown root:root "$HWOUT"
        chmod 0644 "$HWOUT"
        chcon u:object_r:vendor_configs_file:s0 "$HWOUT" 2>/dev/null || true
        if mount --bind "$HWOUT" "$HWCFG" >> "$LOG" 2>&1; then
            log "pro video isz ok $HWCFG"
        else
            log "pro video isz FAIL $HWCFG"
        fi
    else
        log "pro video isz skipped, patched rows=$COUNT (expected 16)"
    fi
fi
log "end"
