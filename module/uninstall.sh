#!/system/bin/sh
SKIPMOUNT=true
export SKIPMOUNT
MODDIR=${0%/*}
[ -d "$MODDIR" ] && [ ! -L "$MODDIR" ] && [ ! -L "${MODDIR%/*}" ] || exit 1
[ -d "$MODDIR/payload" ] && [ ! -L "$MODDIR/payload" ] || exit 1
[ -f "$MODDIR/runtime.sh" ] && [ ! -L "$MODDIR/runtime.sh" ] || exit 1
[ -f "$MODDIR/payload/uninstall-apk.sh" ] && [ ! -L "$MODDIR/payload/uninstall-apk.sh" ] || exit 1
# Synchronous property revocation + journal-only teardown for this token.
# A foreign/blocked mount must not prevent the original APK ownership protocol
# from deciding its own safe removal/deferred retry independently.
sh "$MODDIR/runtime.sh" uninstall "$MODDIR" ||
    printf '%s\n' 'xpan uninstall: runtime teardown refused/incomplete; APK ownership checked separately' >&2
# Source the trusted packaged helper, NOT profile/state/input data. Keeping $0
# at the root uninstall hook is required by the unchanged lifecycle helper.
. "$MODDIR/payload/uninstall-apk.sh"
