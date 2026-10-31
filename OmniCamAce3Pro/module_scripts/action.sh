#!/system/bin/sh
# KernelSU 的模块卡片上按「执行」时运行这里：开关 Meishe 渲染器替换（POP/GR 胶片滤镜的画面来源）。
#
# 背景：本机原厂 /odm/lib64/libmsnativefilter.so（6218904 字节）没有 CNvPolaroid / CNvFilterCCD /
# CNvPortra400 / CNvShapeMask / CNvCompoundLutTiled 渲染器，POP 和 GR 的四款胶片滤镜渲染不出画面。
# 模块带的替换库（10957976 字节，4097 导出）含这些渲染器。
#
# ⚠️ 与 GT7Pro 版的重要差别：对照过本机原厂库（2391 导出）与替换库的导出表，替换库
# **缺 98 个本机原厂符号**（CNvBlackSoft / NvGenerateHDRPlusToneMapData / CMraContext 等）。
# 注册模型下大概率无碍（缺的只是那几个滤镜），但不排除个别滤镜/功能黑屏。
# 所以 v0.4 起默认**不替换**（库停在 msfilter-off/，行为与原厂完全一致），想试 POP/GR 的
# 再用本开关打开；任何滤镜异常就再按一次关回来，重启即还原。
#
# 状态以「库在哪」为准（挂载层数据源），/data/adb/omnicam_msfilter_off 标记同步维护
# （service.sh 的 bind 兜底读它）。
#
# 两种状态都要重启手机才生效。

MODDIR=${0%/*}
LAYER=$MODDIR/system/odm/lib64
PARKED=$MODDIR/msfilter-off
OFF=/data/adb/omnicam_msfilter_off
LIB=libmsnativefilter.so

say() {
    echo "$1"
    echo "$1" >> "$MODDIR/action.log"
}

mkdir -p "$LAYER" "$PARKED" 2>/dev/null

if [ -f "$LAYER/$LIB" ]; then
    mv -f "$LAYER/$LIB" "$PARKED/$LIB" 2>/dev/null
    : > "$OFF"
    say "Meishe 渲染器替换：已关闭"
    say "替换库已收回 msfilter-off/，重启后相机用回原厂渲染器（6218904 字节）。"
    say "POP 与 GR 的四款胶片滤镜会回到黑屏，其余功能不受影响。"
elif [ -f "$PARKED/$LIB" ]; then
    mv -f "$PARKED/$LIB" "$LAYER/$LIB" 2>/dev/null
    rm -f "$OFF"
    say "Meishe 渲染器替换：已开启"
    say "替换库已放进挂载层，重启后 POP 和 GR 的四款胶片滤镜才有画面。"
    say "提醒：替换库缺 98 个本机原厂导出符号，若个别滤镜异常，再按一次本按钮关闭。"
else
    say "找不到模块自带的 $LIB —— 这个安装包不完整，请重新刷入模块。"
fi

# 相机正在跑的时候换库没有意义，下一次启动才会重新 dlopen。
am force-stop com.oplus.camera 2>/dev/null
say "已关闭相机进程；请重启手机让改动生效。"
