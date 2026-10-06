#!/system/bin/sh
# 每次开机都把 LSPosed 里的启用状态写一遍：
# 这个脚本跑在 lspd 启动之前，所以写进去的状态当次开机就生效。
MODDIR=${0%/*}
DEX="$MODDIR/payload/enabler.dex"
[ -f "$DEX" ] || exit 0
CLASSPATH="$DEX" app_process /system/bin com.xpanport.setup.Enabler >/dev/null 2>&1
