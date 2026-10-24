#!/system/bin/sh
# X-Pan 2× 真变焦：配置换带 + 会话重建（root 侧）
# hook（XpanHook.java）识别用户点的档位，写请求文件 /sdcard/Android/data/com.oplus.camera/files/xpan_cropzoom
# 本脚本轮询请求文件，变化时：bind-mount 对应配置 → 走一趟模式往返让会话重建 → 清 busy 标记。
# 支持档位：1（1× 原窗口）/ 2（2× 居中带，实测 FOV 0.55）
MODDIR=${0%/*}
CFG=/odm/etc/camera/config/oplus_camera_config
REQ=/sdcard/Android/data/com.oplus.camera/files/xpan_cropzoom
BUSY=/sdcard/Android/data/com.oplus.camera/files/xpan_cropzoom_busy
cur=""

while true; do
    v=$(cat "$REQ" 2>/dev/null | tr -d ' \r\n')
    case "$v" in
        1|2) ;;
        *) sleep 1; continue ;;
    esac
    if [ "$v" != "$cur" ]; then
        f="$MODDIR/payload/oc_$v.enc"
        if [ -f "$f" ]; then
            touch "$BUSY"
            cp -f "$f" /data/local/tmp/oc_active.enc
            chmod 644 /data/local/tmp/oc_active.enc
            chcon u:object_r:vendor_configs_file:s0 /data/local/tmp/oc_active.enc 2>/dev/null
            mount --bind /data/local/tmp/oc_active.enc "$CFG"
            # 会话重建：模式往返一趟（X-Pan → 照片 → X-Pan），按屏幕方向选导航方式。
            # 竖屏：模式条在底部横排，直接点标签（实测可靠，划动会被吸附弹回）；
            # 横屏：模式列表在右侧竖排，慢速滑动往返（实测可靠）。
            ORIENT=$(dumpsys input 2>/dev/null | grep -m1 SurfaceOrientation | tr -d '\r' | awk '{print $2}')
            case "$ORIENT" in
                1|3)
                    input swipe 2150 900 2150 700 600; sleep 1
                    input swipe 2150 900 2150 700 600; sleep 2
                    input swipe 2150 700 2150 900 600; sleep 1
                    input swipe 2150 700 2150 900 600; sleep 2
                    ;;
                *)
                    input tap 90 2070; sleep 2
                    input tap 1150 2100; sleep 2
                    ;;
            esac
            rm -f "$BUSY"
            log -t XpanCrop "裁剪窗口已切到 ${v}× 并重建会话"
        else
            log -t XpanCrop "缺少配置 $f"
        fi
        cur="$v"
    fi
    sleep 1
done
