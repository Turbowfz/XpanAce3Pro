#!/system/bin/sh
SKIPMOUNT=true
export SKIPMOUNT
MODDIR=${0%/*}
[ -f "$MODDIR/runtime.sh" ] && [ ! -L "$MODDIR/runtime.sh" ] || exit 1
# Poll markers, PM/profile/SDK, payloads, token and mount ownership every 3s.
# No APK installs at boot, no auto-restart, no framework or database mutation.
# A persistent process retains functions after module removal for owned teardown.
exec sh "$MODDIR/runtime.sh" service "$MODDIR"
