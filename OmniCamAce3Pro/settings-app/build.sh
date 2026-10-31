#!/usr/bin/env bash
# OmniCamAce3Pro 独立设置应用构建（aapt2 → javac → d8 → zipalign → apksigner）
set -e
export MSYS_NO_PATHCONV=1
export JAVA_HOME='C:\Program Files\Microsoft\jdk-17.0.20.101-hotspot'
export PATH="/c/Program Files/Microsoft/jdk-17.0.20.101-hotspot/bin:$PATH"
B="C:/Users/User/Desktop/相机模块/OmniCamAce3Pro/settings-app"
SDK="$LOCALAPPDATA/Android/Sdk"
BT="$SDK/build-tools/35.0.0"
AJ="$SDK/platforms/android-35/android.jar"
cd "$B"
rm -rf out gen && mkdir -p out gen

echo "[1/5] aapt2 link"
"$BT/aapt2.exe" link -o out/base.apk --manifest AndroidManifest.xml \
    -I "$AJ" --min-sdk-version 26 --target-sdk-version 35 \
    -A assets \
    --java gen

echo "[2/5] javac"
javac -encoding UTF-8 -source 8 -target 8 -bootclasspath "$AJ" \
    -classpath "$AJ:out/base.apk" -d out/classes \
    $(find gen src -name "*.java") 2>&1 | grep -viE "警告|warning|已过时|deprecat" || true

echo "[3/5] d8"
"$BT/d8.bat" --min-api 26 --release --output out $(find out/classes -name "*.class") 2>&1 | tail -n 2

echo "[4/5] 组装（dex 进包 → 对齐）"
python - "$B" <<'PYA'
import zipfile, os, sys
b = sys.argv[1]
apk = os.path.join(b, 'out', 'base.apk')
dex = os.path.join(b, 'out', 'classes.dex')
zf = zipfile.ZipFile(apk, 'a', zipfile.ZIP_DEFLATED)
zf.write(dex, 'classes.dex')
zf.close()
print('   classes.dex 已写入 APK')
PYA
cd out && "$BT/zipalign.exe" -f 4 base.apk omnicam-set.apk && cd ..

echo "[5/5] 签名"
KS="$B/debug.keystore"
if [ ! -f "$KS" ]; then
  keytool -genkeypair -keystore "$KS" -alias omnicam -storepass omnicam -keypass omnicam \
    -dname "CN=omnicam" -keyalg RSA -validity 10000 >/dev/null 2>&1
fi
"$BT/apksigner.bat" sign --ks "$KS" --ks-pass pass:omnicam --out out/OmniCamAce3Pro-Settings.apk out/omnicam-set.apk
echo "完成: $B/out/OmniCamAce3Pro-Settings.apk"
