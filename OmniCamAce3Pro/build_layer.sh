#!/bin/sh
# 按 OmniCam-Muse-GT7Pro build-full.yml 的权威配方组装挂载层。
# 差异（相对 fork）：x10 的 SCLut0-8 与本机影调表撞名 → 跳过不盖；setting_Retro 用 C16 本机 setting 树。
set -e
ROOT="C:/Users/User/Desktop/相机模块/OmniCamAce3Pro"
TOOLS="C:/Users/User/Desktop/相机模块/OmniCam参考/OmniCam-Muse-GT7Pro/tools"
M="$ROOT/module"
P="$M/payload"
W="$ROOT/layer_build"
CAM="$M/system/odm/etc/camera"
BT="$CAM/basictone"

rm -rf "$W" "$CAM/meishe_lut" "$BT/lmt_gr" "$BT/pop_port" "$BT/mode_limit_watermark" "$BT/setting_Retro"
mkdir -p "$W" "$BT"

echo "== 1. meishe_lut：payload + lut_extra（positive_* 8 件）=="
cp -a "$P/meishe_lut" "$W/meishe_lut"
cp -a "$M/lut_extra/." "$W/meishe_lut/"
echo "== 2. mslut 修 清透/琥珀 的 LUT 版本形状 =="
python -I "$TOOLS/mslut.py" fix "$W/meishe_lut"
python -I "$TOOLS/mslut.py" info "$W/meishe_lut/qing_tou.bin" "$W/meishe_lut/hu_po.bin" | head -4 || true
echo "== 3. GR 色调表烘焙 64^3 =="
python -I "$TOOLS/gr_lmt.py" "$P/gr_adjustment_lmt" "$W/gr_lmt64" --size 64
ls "$W/gr_lmt64" | head -3; echo "gr_lmt64 数量: $(ls "$W/gr_lmt64" | wc -l)"
echo "== 4. 组装层 =="
cp -a "$W/meishe_lut" "$CAM/meishe_lut"
# x10：跳过与本机 lmt/SCLut0-8 撞名的文件
SKIPPED=""
for f in "$P/x10_basictone/lmt/"*; do
    b=$(basename "$f")
    case "$b" in
        SCLut[0-8]) SKIPPED="$SKIPPED $b"; continue ;;
    esac
    cp -a "$f" "$BT/lmt/"
done
echo "x10 撞名跳过:$SKIPPED"
for f in "$P/x10_basictone/"*.ini; do
    [ -e "$f" ] && cp -a "$f" "$BT/"
done
# lmt_gr：占位 LMTLut0/1（SimTool LMT=0 不用）+ 9 张 64^3 SCLut
mkdir -p "$BT/lmt_gr"
cp -a "$TOOLS/lmt_host/LMTLut0" "$TOOLS/lmt_host/LMTLut1" "$BT/lmt_gr/"
cp -a "$W/gr_lmt64/." "$BT/lmt_gr/"
# POP：PLD 水印资源 + 复古 UI 动画/引导图
mkdir -p "$BT/pop_port"
cp -a "$P/pld_watermark" "$BT/pop_port/pld_watermark"
cp -a "$P/apk_resources" "$BT/pop_port/apk_resources"
# GR 水印样式：两种读法都给（dir/style.json 与 style.json）
mkdir -p "$BT/mode_limit_watermark"
for I in 1 2 3 4 5; do
    ID="gr_style_$I"
    mkdir -p "$BT/mode_limit_watermark/$ID"
    cp -a "$P/watermark_master_styles/$ID.json" "$BT/mode_limit_watermark/$ID/$ID.json"
    cp -a "$P/watermark_master_styles/$ID.json" "$BT/mode_limit_watermark/$ID.json"
    cp -a "$P/watermark_drawables/"*.webp "$BT/mode_limit_watermark/$ID/" 2>/dev/null || true
done
cp -a "$P/watermark_drawables/"*.webp "$BT/mode_limit_watermark/"
# setting_Retro：C16 本机 setting 树（SimTool.ini + Ditherout + FromXml）
rm -rf "$BT/setting_Retro"
cd "$ROOT" && tar -xf layer_build_setting.tar -C "$W" && cd - >/dev/null
cp -a "$W/setting" "$BT/setting_Retro"
echo "== 5. 校验 =="
test -f "$BT/setting_Retro/SimTool.ini" && echo "SimTool.ini OK"
test -f "$BT/pop_port/pld_watermark/pld/pld.json" && echo "pld.json OK"
echo "== 层最终清单 =="
du -sh "$CAM" "$BT"/* | sort -h
find "$CAM" -type f | wc -l