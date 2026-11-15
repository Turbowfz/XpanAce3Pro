#!/system/bin/sh
SKIPMOUNT=true
export SKIPMOUNT
MODDIR=${0%/*}
# Early boot cannot prove PM/actual SDK eligibility. Revoke stale readiness only;
# runtime may retract exact journal-proven binds, never create an early bind.
[ -f "$MODDIR/runtime.sh" ] && [ ! -L "$MODDIR/runtime.sh" ] || exit 1
sh "$MODDIR/runtime.sh" boot "$MODDIR"
