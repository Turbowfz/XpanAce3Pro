#!/usr/bin/env bash
# OmniCamAce3Pro 一键自测（手机回来后执行）
set -e
export MSYS_NO_PATHCONV=1
ADB="C:/Windows/system32/adb.exe"
echo "[1] 模块与 APK 状态"
"$ADB" shell su -c 'ls /data/adb/modules/ | grep -E "OmniCam|Xpan"; pm path com.xpanport.omnicam.set; pm path com.xpanport.omnicam.hook'
echo "[2] 设置应用开关往返（8192→4096→8192）"
"$ADB" shell su -c 'touch /sdcard/DCIM/.xpan_smallpic; am force-stop com.oplus.camera'
"$ADB" shell su -c 'rm -f /sdcard/DCIM/.xpan_smallpic; am force-stop com.oplus.camera'
echo "[3] X-Pan 出片尺寸核对（进 X-Pan 后拍一张）"
read -p "手动：进 X-Pan 拍一张，回车继续"
L=$("$ADB" shell "su -c 'ls -t /sdcard/DCIM/Camera/ | head -1'" | tr -d '\r')
"$ADB" exec-out "su -c 'cat /sdcard/DCIM/Camera/$L'" > /tmp/selftest.heic
python -c "
from pillow_heif import register_heif_opener
register_heif_opener()
from PIL import Image
im = Image.open('/tmp/selftest.heic'); print('出片:', im.size, '比例 %.3f' % (im.width/im.height))
"
echo "[4] 日志"
"$ADB" shell su -c 'logcat -d | grep -a "OmniCam:" | tail -20'
