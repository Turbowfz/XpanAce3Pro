#!/usr/bin/env bash
# OmniCam 钩子构建（同 XpanHook 五步流水线）+ 参数化 enabler.dex
set -e
export MSYS_NO_PATHCONV=1
export JAVA_HOME='C:\Program Files\Microsoft\jdk-17.0.20.101-hotspot'
export PATH="/c/Program Files/Microsoft/jdk-17.0.20.101-hotspot/bin:$PATH"
B="C:/Users/User/Desktop/相机模块/OmniCamAce3Pro/hook"
SDK="$LOCALAPPDATA/Android/Sdk"
BT="$SDK/build-tools/35.0.0"
AJ="$SDK/platforms/android-35/android.jar"
cd "$B"
rm -rf out && mkdir -p out

echo "[1/5] javac"
javac -encoding UTF-8 -source 8 -target 8 -bootclasspath "$AJ" -cp "$AJ" \
    -d out/classes $(find src -name "*.java") 2>&1 | grep -viE "警告|warning" || true
[ -f out/classes/com/xpanport/omnicam/hook/ModuleEntry.class ] || { echo "!! javac 失败"; exit 1; }

echo "[2/5] d8（只打包 com/xpanport，API 桩不进包）"
"$BT/d8.bat" --min-api 26 --release --output out $(find out/classes/com -name "*.class") 2>&1 | tail -n 1

echo "[3/5] aapt2 link"
"$BT/aapt2.exe" link -o out/base.apk --manifest AndroidManifest.xml \
    -I "$AJ" --min-sdk-version 26 --target-sdk-version 35

echo "[3.5/5] 组装（dex 进包）"
python - "$B" <<'PYB'
import zipfile, os, sys
b = sys.argv[1]
apk = os.path.join(b, 'out', 'base.apk')
dex = os.path.join(b, 'out', 'classes.dex')
zf = zipfile.ZipFile(apk, 'a', zipfile.ZIP_DEFLATED)
zf.write(dex, 'classes.dex')
zf.close()
print('   classes.dex 已写入 APK')
PYB

echo "[4/5] 对齐 + 签名"
cd out
"$BT/zipalign.exe" -f 4 base.apk omnicam-hook.apk
KS="$B/debug.keystore"
if [ ! -f "$KS" ]; then
  keytool -genkeypair -keystore "$KS" -alias omnicam -storepass omnicam -keypass omnicam \
    -dname "CN=omnicam" -keyalg RSA -validity 10000 >/dev/null 2>&1
fi
"$BT/apksigner.bat" sign --ks "$KS" --ks-pass pass:omnicam --out OmniCamAce3Pro-Hook.apk omnicam-hook.apk
cd ..

echo "[5/5] enabler.dex（参数化：传模块包名+作用域）"
mkdir -p out/enabler_tmp
"$BT/d8.bat" --min-api 26 --release --output out/enabler_tmp $(find out/classes/com/xpanport/setup -name "*.class") 2>&1 | tail -n 1
mv out/enabler_tmp/classes.dex enabler.dex
ls -la enabler.dex out/OmniCamAce3Pro-Hook.apk
