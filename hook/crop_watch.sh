#!/system/bin/sh
# X-Pan 2× 真放大：配置换带 + 会话重建（root 侧）
# hook（XpanHook.java）识别用户点的档位，写请求文件 /sdcard/Android/data/com.oplus.camera/files/xpan_cropzoom
# 本脚本轮询请求文件，变化时：bind-mount 对应配置 → 走一趟模式往返让会话重建 → 清 busy 标记。
# 支持档位：1（1× 原窗口）/ 2（2× 居中带）/ 25、3（倍率扫描测试档）
MODDIR=${0%/*}
CFG=/odm/etc/camera/config/oplus_camera_config
REQ=/sdcard/Android/data/com.oplus.camera/files/xpan_cropzoom
BUSY=/sdcard/Android/data/com.oplus.camera/files/xpan_cropzoom_busy
cur=""

while true; do
    v=$(cat "$REQ" 2>/dev/null | tr -d ' \r\n')
    case "$v" in
        1|2|25|3) ;;
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
            # 会话重建：模式条往返（XPAN → 照片 → XPAN），实测这条手势可靠
            input swipe 2150 900 2150 700 600; sleep 1
            input swipe 2150 900 2150 700 600; sleep 2
            input swipe 2150 700 2150 900 600; sleep 1
            input swipe 2150 700 2150 900 600; sleep 2
            rm -f "$BUSY"
            log -t XpanCrop "裁剪窗口已切到 ${v}× 并重建会话"
        else
            log -t XpanCrop "缺少配置 $f"
        fi
        cur="$v"
    fi
    sleep 1
done
