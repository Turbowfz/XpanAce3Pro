#!/system/bin/sh
# Magisk and KernelSU installer: never opt into automatic system/ mounting.
# Output is a short, plain-language progress report; detailed diagnostics stay in
# /data/local/tmp/xpan_install.log instead of scrolling past the user.
SKIPMOUNT=true
export SKIPMOUNT

STEP=0
say() {
    if command -v ui_print >/dev/null 2>&1; then ui_print "$1"; else printf '%s\n' "$1"; fi
}
step() { STEP=$((STEP + 1)); say ""; say "$STEP/5  $1"; }
xpan_abort() {
    say ""
    say "× 安装中止：$1"
    exit 1
}

say "XpanAce3Pro v2.0 安装程序"
say "一加 Ace 3 Pro 哈苏 X-Pan 模块"

step "检查模块文件"
[ -n "${MODPATH:-}" ] && [ -d "$MODPATH" ] && [ ! -L "$MODPATH" ] || xpan_abort "模块目录无效"
[ "${BOOTMODE:-}" = true ] || xpan_abort "请在已开机的系统中安装（不支持 recovery 安装）"
# Refuse old overlays rather than silently letting a root manager mount them.
for XPAN_OVERLAY in system vendor product system_ext odm; do
    [ ! -e "$MODPATH/$XPAN_OVERLAY" ] && [ ! -L "$MODPATH/$XPAN_OVERLAY" ] ||
        xpan_abort "模块内含旧式分区覆盖目录，请使用正式包"
done
[ ! -e "$MODPATH/system.prop" ] && [ ! -L "$MODPATH/system.prop" ] ||
    xpan_abort "模块内含静态属性文件，请使用正式包"
for XPAN_SCRIPT in runtime.sh post-fs-data.sh service.sh lifecycle.sh uninstall.sh parent-context.sh; do
    [ -f "$MODPATH/$XPAN_SCRIPT" ] && [ ! -L "$MODPATH/$XPAN_SCRIPT" ] ||
        xpan_abort "模块脚本缺失或异常"
done
chmod 0755 "$MODPATH/runtime.sh" "$MODPATH/post-fs-data.sh" "$MODPATH/service.sh" \
    "$MODPATH/lifecycle.sh" "$MODPATH/uninstall.sh" || xpan_abort "无法设置脚本权限"
say "  文件完整"

step "检查本机机型与相机版本"
MODEL=$(getprop ro.product.model 2>/dev/null) || MODEL=
[ "$MODEL" = PJX110 ] || xpan_abort "机型不是 PJX110（当前：${MODEL:-未知}）"
CAMERA_VER=$(dumpsys package com.oplus.camera 2>/dev/null |
    sed -n 's/^[[:space:]]*versionName=\([0-9][0-9.]*\).*/\1/p' | head -n 1)
case "$CAMERA_VER" in
    6.020.876|7.013.51) ;;
    *) xpan_abort "相机版本不受支持（当前：${CAMERA_VER:-未读到}）" ;;
esac
say "  机型 PJX110，相机 $CAMERA_VER"

step "校验本机配置与 SDK，并安装配套 Hook APK"
INSTALL_OUT=$MODPATH/.install-step.log
if sh "$MODPATH/runtime.sh" install "$MODPATH" > "$INSTALL_OUT" 2>&1; then
    :
else
    rm -f "$INSTALL_OUT"
    xpan_abort "本机配置或 SDK 校验未通过；详细原因见 /data/local/tmp/xpan_install.log"
fi
rm -f "$INSTALL_OUT"
say "  配置与 SDK 校验通过"

step "核对安装结果"
[ -s "$MODPATH/.xpan-instance" ] || xpan_abort "安装实例编号未生成"
[ -s "$MODPATH/.xpan-apk-identity" ] || xpan_abort "Hook APK 身份未登记"
say "  安装完成，实例已登记"

step "完成"
say ""
say "接下来两步："
say "  1. 打开 LSPosed，勾选 XpanAce3Pro，作用域选“相机”"
say "  2. 重启相机（不是重启手机）"
say ""
say "提示：若检测到 OPCameraPro 附件模块，本模块会自动换用兼容 C17 的补全文件，"
say "      并隐去其 C16 滤镜素材；都需重启手机后生效，原文件会自动备份。"
say "      卸载本模块会清理本模块的 Hook APK。"
