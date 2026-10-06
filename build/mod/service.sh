#!/system/bin/sh
# 兜底：如果 hook APK 因为系统升级等原因丢了，开机补装一次
MODDIR=${0%/*}
APK="$MODDIR/payload/XpanHook.apk"
DEX="$MODDIR/payload/enabler.dex"
[ -f "$APK" ] || exit 0

if ! pm list packages 2>/dev/null | grep -q com.xpanport.hook; then
    pm install -r -d "$APK" >/dev/null 2>&1
    [ -f "$DEX" ] && CLASSPATH="$DEX" app_process /system/bin com.xpanport.setup.Enabler >/dev/null 2>&1
fi
