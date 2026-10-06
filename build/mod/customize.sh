SKIPMOUNT=false
PROPFILE=false
POSTFSDATA=true
LATESTARTSERVICE=true

ui_print " "
ui_print "*******************************"
ui_print " X-Pan 宽幅模式（Ace 3 Pro）"
ui_print "*******************************"

APK="$MODPATH/payload/XpanHook.apk"
DEX="$MODPATH/payload/enabler.dex"

# 1) 装尺寸修正的 hook APK（ksud 安装时系统是起来的，pm 可用）
if [ -f "$APK" ]; then
    ui_print "- 安装尺寸修正 hook"
    pm install -r -d "$APK" >/dev/null 2>&1 && ui_print "  hook 安装成功" || ui_print "  hook 安装失败（可稍后手动装 payload/XpanHook.apk）"
fi

# 2) 让 LSPosed 自动启用它（设备上没 sqlite3，用 app_process 跑 Java 改库）
if [ -f "$DEX" ]; then
    ui_print "- 启用 LSPosed 作用域"
    CLASSPATH="$DEX" app_process /system/bin com.xpanport.setup.Enabler 2>&1 | while read -r l; do ui_print "  $l"; done
fi

ui_print "- 安装完成，重启后生效"
ui_print " "
