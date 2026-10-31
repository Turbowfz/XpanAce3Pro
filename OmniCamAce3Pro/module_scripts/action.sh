#!/system/bin/sh
# KernelSU 的模块卡片上按「执行」时运行这里。
#
# 目前只有一件事需要人在手机上决定：要不要用模块自带的 Meishe 渲染器
# （/odm/lib64/libmsnativefilter.so）替换原厂那一个。
#
# 为什么需要它：POP（Polaroid）和 GR 的四款胶片滤镜是 Meishe 的 renderer，不在 BasicTone 里。
# 本机原厂的 libmsnativefilter.so 只有 2,723 个导出，**没有** CNvPolaroid / CNvFilterCCD /
# CNvPortra400 / CNvShapeMask / CNvCompoundLutTiled，所以 POP 渲染不出画面（取景黑屏、成片纯绿），
# GR 除「标准」以外的四款也一样。模块带的是含这些 renderer 的版本。
#
# 487 起这个文件是挂载层的一部分（system/odm/lib64/libmsnativefilter.so），由 magic mount 挂上去，
# 所以「关掉」不是写个标记就完事，得把它从层里挪出去 —— 本脚本做的就是这件事：
#
#   开：把 msfilter-off/libmsnativefilter.so 挪回 system/odm/lib64/，删掉标记
#   关：把它挪到 msfilter-off/，并写标记（标记同时让 service.sh 的 bind 兜底不再执行）
#
# 两种状态都要重启手机才生效：库在相机启动前就要挂好。
#
# 手动等价操作：
#   关：mv system/odm/lib64/libmsnativefilter.so msfilter-off/ && touch /data/adb/omnicam_msfilter_off
#   开：mv msfilter-off/libmsnativefilter.so system/odm/lib64/ && rm -f /data/adb/omnicam_msfilter_off
#   然后 reboot

MODDIR=${0%/*}
OFF=/data/adb/omnicam_msfilter_off
LAYER=$MODDIR/system/odm/lib64
PARKED=$MODDIR/msfilter-off
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
    say "模块自带的渲染器已从挂载层移出，重启后相机用回原厂那个。"
    say "POP 与 GR 的四款胶片滤镜会重新变成黑屏，其余功能不受影响。"
else
    if [ -f "$PARKED/$LIB" ]; then
        mv -f "$PARKED/$LIB" "$LAYER/$LIB" 2>/dev/null
        say "Meishe 渲染器替换：已开启"
        say "模块自带的渲染器已放回挂载层，重启后 POP 和 GR 的四款胶片滤镜才有画面。"
    else
        say "找不到模块自带的 libmsnativefilter.so —— 这个安装包不完整，请重新刷入模块。"
    fi
    rm -f "$OFF"
fi

# 相机正在跑的时候换库没有意义，下一次启动才会重新 dlopen。
am force-stop com.oplus.camera 2>/dev/null
say "已关闭相机进程；请重启手机让改动生效。"
