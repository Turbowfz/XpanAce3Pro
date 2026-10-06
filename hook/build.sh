#!/usr/bin/env bash
# build.sh -- 一键编译打包 XPan hook 模块（Windows Git Bash）
set -e
export MSYS_NO_PATHCONV=1
export JAVA_HOME='C:\Program Files\Microsoft\jdk-17.0.20.101-hotspot'
export PATH="/c/Program Files/Microsoft/jdk-17.0.20.101-hotspot/bin:$PATH"

B="C:/Users/User/Desktop/XpanPort/06_hook模块/XpanHook"
SDK="$LOCALAPPDATA/Android/Sdk"
BT="$SDK/build-tools/35.0.0"
AJAR="$SDK/platforms/android-35/android.jar"

cd "$B"
rm -rf out/classes out/classes.dex out/*.apk out/*.idsig
mkdir -p out/classes

echo "[1/5] javac"
javac -encoding UTF-8 -source 8 -target 8 -bootclasspath "$AJAR" -cp "$AJAR" \
  -d out/classes $(find src -name "*.java") 2>&1 | grep -viE "警告|warning" || true
if [ ! -f out/classes/com/xpanport/hook/XpanHook.class ]; then
  echo "!! javac 失败"; exit 1
fi

echo "[2/5] d8（只打包 com/xpanport，API 桩不进包，否则 LSPosed 拒绝加载）"
"$BT/d8.bat" --min-api 24 --output out $(find out/classes/com -name "*.class") 2>&1 | tail -n 2

echo "[3/5] aapt2 link"
"$BT/aapt2.exe" link -o out/base.apk --manifest AndroidManifest.xml \
  -I "$AJAR" --min-sdk-version 24 --target-sdk-version 35

echo "[4/5] 组装 + 签名"
python - <<'PY'
import zipfile, os
B=r'C:/Users/User/Desktop/XpanPort/06_hook模块/XpanHook'
zin=zipfile.ZipFile(B+'/out/base.apk')
out=B+'/out/XpanHook-unsigned.apk'
with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as zo:
    for it in zin.infolist(): zo.writestr(it, zin.read(it.filename))
    zo.write(B+'/out/classes.dex','classes.dex')
    zo.write(B+'/assets/xposed_init','assets/xposed_init')
print('  unsigned', os.path.getsize(out))
PY
if [ ! -f out/debug.keystore ]; then
  keytool -genkeypair -keystore out/debug.keystore -alias xpan -keyalg RSA -keysize 2048 \
    -validity 10000 -storepass android -keypass android \
    -dname "CN=XpanPort, OU=dev, O=dev, L=dev, S=dev, C=CN" >/dev/null 2>&1
fi
"$BT/zipalign.exe" -f 4 out/XpanHook-unsigned.apk out/XpanHook-aligned.apk
"$BT/apksigner.bat" sign --ks out/debug.keystore --ks-pass pass:android --key-pass pass:android \
  --ks-key-alias xpan --out out/XpanHook.apk out/XpanHook-aligned.apk

echo "[5/5] 校验 dex 里没有 API 桩定义"
python - <<'PY'
import zipfile, struct
d=zipfile.ZipFile(r'C:/Users/User/Desktop/XpanPort/06_hook模块/XpanHook/out/XpanHook.apk').read('classes.dex')
so=struct.unpack_from('<II',d,56)[1]; to=struct.unpack_from('<II',d,64)[1]
cn,co=struct.unpack_from('<II',d,96)
def uleb(p):
    v=0;sh=0
    while True:
        x=d[p];p+=1;v|=(x&0x7f)<<sh
        if not x&0x80:break
        sh+=7
    return v,p
def string(i):
    off=struct.unpack_from('<I',d,so+i*4)[0]
    n,p=uleb(off); e=d.index(b'\x00',p)
    return d[p:e].decode('utf-8','replace')
defined=[string(struct.unpack_from('<I',d,to+struct.unpack_from('<I',d,co+i*32)[0]*4)[0]) for i in range(cn)]
bad=[c for c in defined if c.startswith('Lde/robv')]
print('  定义类:', defined)
print('  结论:', 'OK（无 API 桩）' if not bad else '!! 含桩 %s' % bad)
PY
ls -l out/XpanHook.apk
