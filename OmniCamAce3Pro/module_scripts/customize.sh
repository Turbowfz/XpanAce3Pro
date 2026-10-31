# Hooks app: install the single Muse APK this module ships while the module is flashed, so LSPosed
# points at it before the reboot that activates the module. A failed install (e.g. a different
# signing key) is left as it is and logged to install.log; the installed app is never uninstalled.

# One build for every device: the payload carries the data layer (LUTs, basictone, GR LMT, watermark
# artwork) plus, since 486, the Meishe GPU renderer that POP and GR's film styles live in. The
# algorithm stack (libAlgoInterface, libBasicTonePhoto*) and every ODM config are never substituted.
# A device whose camera config the module cannot substitute keeps working, because the features that
# need it turn themselves off at install time.
if [ ! -d "$MODPATH/payload" ]; then
    ui_print "- WARNING: payload missing, only the app-layer features will run"
fi

# KernelSU runs this file directly when the user presses the module card's action button, so it has
# to be executable. Everything else is invoked through sh.
[ -f "$MODPATH/action.sh" ] && chmod 0755 "$MODPATH/action.sh"

PACKAGE=local.jiege.camera
APK=$(ls "$MODPATH"/omnicam-muse*.apk 2>/dev/null | head -n 1)
STAGED=/data/local/tmp/omnicam-muse-module.apk
HAVE=$(dumpsys package "$PACKAGE" 2>/dev/null | sed -n 's/.*versionCode=\([0-9]*\).*/\1/p' | head -n 1)
WANT=$(sed -n 's/^versionCode=//p' "$MODPATH/module.prop")
if [ -n "$APK" ] && cp "$APK" "$STAGED" && chmod 0644 "$STAGED"; then
    RESULT=$(pm install -r -d "$STAGED" 2>&1)
    rm -f "$STAGED"
else
    RESULT="APK missing or not staged"
fi
printf 'installed %s -> %s: %s\n' "${HAVE:-none}" "$WANT" "$RESULT" > "$MODPATH/install.log"
ui_print "- Muse 应用: $RESULT"

# Earlier test builds shipped a separate settings app (com.xpanport.omnicamset). This build is
# single-APK: uninstall the old one if it is still around so it does not linger as a dead icon.
if pm list packages 2>/dev/null | grep -q "^package:com.xpanport.omnicamset$"; then
    pm uninstall --user 0 com.xpanport.omnicamset >> "$MODPATH/install.log" 2>&1 \
        && ui_print "- 已移除旧版独立设置应用"
fi

# Builds up to 430 staged the config profile from this script with root and then ran restorecon on
# it, which drops the app's SELinux categories: the camera could not read the file and every mode
# the profile adds stayed hidden. The profile is now written by the camera process itself, so the
# old copy has to go once — it has the right length, and ConfigProfile would otherwise keep it.
for DIR in /data/user/0/com.oplus.camera/files/jiege/profile /data/data/com.oplus.camera/files/jiege/profile; do
    [ -d "$DIR" ] && rm -rf "$DIR"
done

# 484 kept a copy of the payload under the camera's private files. 485 mounts it as a module layer
# instead, and a leftover copy would shadow the layer with whatever 484 shipped. Remove it here as
# well as in service.sh, because the camera can be opened before service.sh gets a chance.
for DIR in /data/user/0/com.oplus.camera/files/jiege/basictone /data/data/com.oplus.camera/files/jiege/basictone; do
    [ -d "$DIR" ] && rm -rf "$DIR"
done

# Same story for the POP LUTs, and this one is the 488 report: 484 copied them to
# files/pop_port/meishe_lut and the native remap looked there before /odm, so one POP filter
# (the "positive film, rich" one) read a pre-format-fix LUT and rendered black. The remap now reads
# the layer first, and the stale copy is removed at install time so there is nothing left to outrank
# the build.
for DIR in /data/user/0/com.oplus.camera/files/pop_port /data/data/com.oplus.camera/files/pop_port; do
    [ -d "$DIR" ] && rm -rf "$DIR"
done


