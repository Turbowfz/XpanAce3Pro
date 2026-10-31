#!/bin/sh
# OmniCam+ Bridge：编译我们的双版本钩子 → classes2.dex → 并入 Muse APK → 重签名
# 产物：bridge/out/omnicam-muse-bridge.apk（覆盖到 module/omnicam-muse.apk 用）
# ⚠️ d8.bat / apksigner.bat 走 cmd.exe，中文路径会乱码 → 一律 cd 到目录后用相对路径
set -e
SDK="$LOCALAPPDATA/Android/Sdk"
BT="$SDK/build-tools/35.0.0"
AJ="$SDK/platforms/android-35/android.jar"
ROOT="C:/Users/User/Desktop/相机模块/OmniCamAce3Pro"
SRC="$ROOT/bridge"
STUB="C:/Users/User/Desktop/相机模块/XpanAce3Pro/XpanPort/06_hook模块/XpanHook/src"
KS="C:/Users/User/Desktop/相机模块/OmniCam参考/OmniCam-Muse-GT7Pro/signing/port.p12"
ORIG="$ROOT/module/omnicam-muse.apk"

rm -rf "$SRC/out/classes" "$SRC/out/dexout"
mkdir -p "$SRC/out/classes" "$SRC/out/dexout"

echo "== javac =="
javac -encoding UTF-8 -source 8 -target 8 -nowarn -bootclasspath "$AJ" \
    -d "$SRC/out/classes" \
    "$SRC/src/com/xpanport/omni/"*.java \
    "$STUB/de/robv/android/xposed/"*.java \
    "$STUB/de/robv/android/xposed/callbacks/"*.java

echo "== jar（只装我们自己的类，桩不打进去）=="
JARBIN="$(dirname "$(readlink -f "$(which javac)")")/jar.exe"
"$JARBIN" cf "$SRC/out/bridge-classes.jar" -C "$SRC/out/classes" com

echo "== d8（cd 到 bridge 用相对路径，避开 cmd.exe 中文路径乱码）=="
cd "$SRC"
"$BT/d8.bat" --release --min-api 28 --lib "$AJ" \
    --output out/dexout out/bridge-classes.jar
ls -la out/dexout/classes.dex

echo "== 证书一致性检查（原 APK vs port.p12）=="
"$BT/apksigner.bat" verify --print-certs "$ORIG" 2>/dev/null | grep -iE "SHA-256" | head -2 || true

echo "== 重打包（保留每条目原压缩方式；跳过旧签名）=="
python -I "$SRC/repack.py"

echo "== zipalign =="
cp "$KS" out/port.p12
cd out
"$BT/zipalign.exe" -f -p 4 unsigned.apk aligned.apk

echo "== apksigner =="
rm -f omnicam-muse-bridge.apk
"$BT/apksigner.bat" sign --ks port.p12 --ks-key-alias omnicam \
    --ks-pass pass:omnicam --key-pass pass:omnicam \
    --out omnicam-muse-bridge.apk aligned.apk
"$BT/apksigner.bat" verify --print-certs omnicam-muse-bridge.apk | head -3
SIZE=$(wc -c < omnicam-muse-bridge.apk)
echo "产物大小: $SIZE"
[ "$SIZE" -gt 1000000 ] || { echo "!! 产物异常（<1MB），拒绝交付"; exit 1; }
ls -la omnicam-muse-bridge.apk
