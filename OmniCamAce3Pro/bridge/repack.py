# 把 classes2.dex 和新的 xposed_init 并入 Muse APK（保持其余条目原样、原压缩方式）。
# 旧签名条目（META-INF/*.SF|*.RSA|*.EC|MANIFEST.MF）跳过，交给 apksigner 重新签。
import zipfile

ROOT = r"C:/Users/User/Desktop/相机模块/OmniCamAce3Pro"
ORIG = ROOT + r"/module/omnicam-muse.apk"
OUT = ROOT + r"/bridge/out/unsigned.apk"
DEX = ROOT + r"/bridge/out/dexout/classes.dex"

XP = b"local.jiege.hook.ModuleEntry\ncom.xpanport.omni.Bridge\n"

zin = zipfile.ZipFile(ORIG)
zout = zipfile.ZipFile(OUT, "w")
skipped = []
added = []
for it in zin.infolist():
    name = it.filename
    base = name.rsplit("/", 1)[-1].upper() if "/" in name else name.upper()
    if name.upper().startswith("META-INF/") and (
            base.endswith(".SF") or base.endswith(".RSA") or base.endswith(".EC")
            or base == "MANIFEST.MF"):
        skipped.append(name)
        continue
    if name == "classes2.dex":
        # 输入可能已是上一轮注入过的包：旧 dex 丢弃，末尾统一补新的，避免同名双条目
        skipped.append(name)
        continue
    data = zin.read(name)
    if name == "assets/xposed_init":
        data = XP
        added.append(name)
    zi = zipfile.ZipInfo(name, date_time=it.date_time)
    zi.compress_type = it.compress_type
    zi.external_attr = it.external_attr
    zout.writestr(zi, data)

dex = open(DEX, "rb").read()
zi = zipfile.ZipInfo("classes2.dex", date_time=(2026, 10, 31, 0, 0, 0))
zi.compress_type = zipfile.ZIP_DEFLATED
zout.writestr(zi, dex)
added.append("classes2.dex (%d bytes)" % len(dex))
zout.close()

print("skipped signatures:", skipped)
print("added/modified:", added)
print("entries total:", len(zipfile.ZipFile(OUT).namelist()))
