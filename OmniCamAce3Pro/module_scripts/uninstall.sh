#!/system/bin/sh
# Runs when the module is removed (KernelSU / Magisk both execute uninstall.sh before deleting the
# module directory). Everything the module staged outside its own directory is removed here, so an
# uninstall leaves nothing behind.
#
# What is NOT touched: the camera's own photos, its settings unrelated to this module, and the
# gallery's own files. mode_data.db is deleted on purpose — the module can leave mode rows in it
# that stock config has no entry for; the camera rebuilds it on the next start.

MODDIR=${0%/*}
APP=/data/user/0/com.oplus.camera
GALLERY=/data/user/0/com.coloros.gallery3d
PREF=$APP/shared_prefs/override_config_data.xml
GALLERY_PREF=$GALLERY/shared_prefs/business_featureSwitch_config.xml
LOG=/data/local/tmp/omnicam-uninstall.log

: > "$LOG"
say() { echo "$1" >> "$LOG"; }

say "begin $(date)"

# 1. Stop both apps so nothing rewrites what is being removed.
am force-stop com.oplus.camera > /dev/null 2>&1
am force-stop com.coloros.gallery3d > /dev/null 2>&1
sleep 1

# 2. Kill switches: this build, plus the ones earlier test builds left behind.
rm -f /data/adb/omnicam_off /data/adb/omnicam_native_off       /data/adb/omnicam_nostack_off /data/adb/omnicam_fullport_off /data/adb/omnicam_fullport_nolib
say "kill switches removed"

# 3. Watermark style folders the XPAN isolation backed up: put back what the camera lost, then drop
#    the backup directory. Only folders that are gone are restored, nothing is overwritten.
if [ -d "$APP/files/jiege/wm_backup" ]; then
    for DIR in "$APP/files/jiege/wm_backup"/*; do
        [ -e "$DIR" ] || continue
        NAME=${DIR##*/}
        if [ ! -e "$APP/files/photo_watermark/$NAME" ]; then
            cp -a "$DIR" "$APP/files/photo_watermark/$NAME" 2>> "$LOG" \
                && say "restored watermark style $NAME"
        fi
    done
fi

# 4. Everything the module staged in the camera's data dir.
for DIR in \
    "$APP/files/jiege" \
    "$APP/files/ricoh_gr" \
    "$APP/files/pop_port" \
    "$APP/files/odm" \
    "$APP/files/mode_limit_watermark"
do
    [ -e "$DIR" ] && rm -rf "$DIR" && say "removed $DIR"
done

# 5. Everything the module staged in the gallery's data dir.
for DIR in \
    "$GALLERY/files/ricoh_gr_styles" \
    "$GALLERY/files/jiege_ipu"
do
    [ -e "$DIR" ] && rm -rf "$DIR" && say "removed $DIR"
done

# 6. The mode database the module forced to rebuild (it can hold rows stock config has no entry for).
rm -f "$APP/databases/mode_data.db" "$APP/databases/mode_data.db-journal" \
      "$APP/databases/mode_data.db-wal" "$APP/databases/mode_data.db-shm" \
    && say "mode_data.db dropped, the camera rebuilds it on next start"

# 7. The preference keys the module wrote. Only these keys are removed; the rest of the camera's
#    settings stay as they are. A key the module only changed falls back to its default.
if [ -f "$PREF" ]; then
    for KEY in \
        com.oplus.gr.mode.support \
        com.oplus.available.gr.mode.zoomvalues \
        com.oplus.gr.mode.marked.zoomvalues \
        com.oplus.camera.capture.hdr.cap.mode.value \
        com.oplus.camera.preview.hdr.cap.mode.value \
        com.oplus.camera.mode.data.db.version \
        com.oplus.feature.retro.camera.support \
        com.oplus.flashlevel.configurable.support \
        com.oplus.feature.tilt.shift.photo.support \
        com.oplus.feature.xpan.mode.support \
        pref_jiege_wm_
    do
        sed -i "/name=\"$KEY/d" "$PREF"
    done
    say "camera preference keys removed"
fi

# 8. The gallery switch that made it accept GR watermarks.
if [ -f "$GALLERY_PREF" ]; then
    sed -i 's/name="business_featureSwitch_is_camera_gr_supported" value="true"/name="business_featureSwitch_is_camera_gr_supported" value="false"/' "$GALLERY_PREF"
    say "gallery gr switch turned off"
fi

# 9. The settings app the module installed. Its data goes with it.
if pm list packages 2>/dev/null | grep -q "^package:local.jiege.camera$"; then
    pm uninstall --user 0 local.jiege.camera >> "$LOG" 2>&1 && say "settings app uninstalled"
fi

say "done"
