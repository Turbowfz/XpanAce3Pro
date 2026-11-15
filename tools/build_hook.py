"""Build the canonical hook without compiling the historical src tree."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hook"


def require_file(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"Required file is missing: {path}")
    return path


def run(argv, capture=False):
    result = subprocess.run(
        [str(arg) for arg in argv], check=True, text=True, encoding="utf-8", errors="backslashreplace",
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )
    return result.stdout or ""


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dex_classes(data):
    if len(data) < 112 or not data.startswith(b"dex\n"):
        raise ValueError("Invalid DEX header")
    file_size, header_size, endian = struct.unpack_from("<III", data, 32)
    if file_size != len(data) or header_size != 112 or endian != 0x12345678:
        raise ValueError("Invalid DEX size or byte order")
    if hashlib.sha1(data[32:]).digest() != data[12:32]:
        raise ValueError("Invalid DEX signature")
    import zlib
    if zlib.adler32(data[12:]) & 0xffffffff != struct.unpack_from("<I", data, 8)[0]:
        raise ValueError("Invalid DEX checksum")

    def table(at, width):
        count, offset = struct.unpack_from("<II", data, at)
        if offset + count * width > len(data):
            raise ValueError("DEX table extends beyond file")
        return count, offset

    strings_count, strings_offset = table(56, 4)
    types_count, types_offset = table(64, 4)
    classes_count, classes_offset = table(96, 32)

    def string(index):
        if index >= strings_count:
            raise ValueError("Invalid DEX string index")
        offset = struct.unpack_from("<I", data, strings_offset + index * 4)[0]
        for _ in range(5):
            if offset >= len(data):
                raise ValueError("Truncated DEX string")
            byte = data[offset]
            offset += 1
            if not byte & 0x80:
                break
        else:
            raise ValueError("Invalid DEX string length")
        end = data.index(b"\0", offset)
        return data[offset:end].decode("utf-8", errors="strict")

    defined = []
    for index in range(classes_count):
        type_index = struct.unpack_from("<I", data, classes_offset + index * 32)[0]
        if type_index >= types_count:
            raise ValueError("Invalid DEX class index")
        string_index = struct.unpack_from("<I", data, types_offset + type_index * 4)[0]
        defined.append(string(string_index))
    if "Lcom/xpanport/hook/XpanHook;" not in defined:
        raise ValueError("Canonical hook class is missing from DEX")
    unexpected = [name for name in defined if not name.startswith("Lcom/xpanport/hook/")]
    if unexpected:
        raise ValueError(f"Unexpected defined classes (API stubs are forbidden): {unexpected}")
    if len(defined) != len(set(defined)):
        raise ValueError("Duplicate DEX class definitions")
    return defined


def verify_apk(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate APK ZIP entries")
        for name in names:
            if name.startswith("/") or ".." in name.split("/") or "\\" in name:
                raise ValueError(f"Invalid APK entry: {name}")
        if archive.testzip() is not None:
            raise ValueError("APK ZIP checksum failed")
        required = {"AndroidManifest.xml", "classes.dex", "assets/xposed_init"}
        if not required.issubset(names):
            raise ValueError("APK required entry is missing")
        dex_names = [name for name in names if re.fullmatch(r"classes\d*\.dex", name)]
        if dex_names != ["classes.dex"]:
            raise ValueError(f"Unexpected DEX payloads: {dex_names}")
        if archive.read("assets/xposed_init").strip() != b"com.xpanport.hook.XpanHook":
            raise ValueError("Unexpected Xposed entry point")
        return dex_classes(archive.read("classes.dex"))


def signer_certificate(java, signer_jar, apk):
    output = run([java, "-jar", signer_jar, "verify", "--verbose", "--print-certs", apk], capture=True)
    print(output, end="")
    certificates = re.findall(r"Signer #\d+ certificate SHA-256 digest: ([0-9a-fA-F]+)", output)
    if len(certificates) != 1:
        raise ValueError("Expected one APK signing certificate")
    return certificates[0].lower()


def build(args):
    java_home = os.environ.get("JAVA_HOME")
    java = require_file(Path(java_home) / "bin/java.exe") if java_home else require_file(shutil.which("java"))
    javac = require_file(java.parent / "javac.exe")
    sdk = Path(os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME") or
               str(Path(os.environ["LOCALAPPDATA"]) / "Android/Sdk"))
    tools = sdk / "build-tools/35.0.0"
    android_jar = require_file(sdk / "platforms/android-35/android.jar")
    xposed_jar = require_file(os.environ.get("XPAN_XPOSED_API", ROOT / "_tmp/api-82.jar"))
    d8_jar = require_file(tools / "lib/d8.jar")
    sources = sorted(HOOK.glob("*.java"))
    source_hashes = {str(path.relative_to(ROOT)): sha256(path) for path in sources}
    manifest = require_file(HOOK / "AndroidManifest.xml")
    entry = require_file(HOOK / "assets/xposed_init")
    manifest_hash, entry_hash = sha256(manifest), sha256(entry)
    if HOOK / "XpanHook.java" not in sources:
        raise ValueError("Canonical hook source is missing")
    with zipfile.ZipFile(xposed_jar) as archive:
        if "de/robv/android/xposed/IXposedHookLoadPackage.class" not in archive.namelist():
            raise ValueError("Not a compile-only Xposed API JAR")

    parent = ROOT / "_tmp/build"
    parent.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="hook-", dir=parent))
    classes = output / "classes"
    dex_output = output / "dex"
    classes.mkdir()
    dex_output.mkdir()
    print(f"Build output: {output}", flush=True)
    run([javac, "-encoding", "UTF-8", "-source", "8", "-target", "8",
         "-bootclasspath", android_jar, "-classpath", os.pathsep.join(map(str, [android_jar, xposed_jar])),
         "-d", classes, *sources])
    class_files = sorted(classes.rglob("*.class"))
    if not class_files or any(not path.relative_to(classes).as_posix().startswith("com/xpanport/hook/")
                              for path in class_files):
        raise ValueError("Unexpected compiled class output")
    compiled_jar = output / "hook-classes.jar"
    with zipfile.ZipFile(compiled_jar, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in class_files:
            archive.write(path, path.relative_to(classes).as_posix())
    run([java, "-cp", d8_jar, "com.android.tools.r8.D8", "--min-api", "24",
         "--lib", android_jar, "--classpath", xposed_jar,
         "--output", dex_output, compiled_jar])
    dex = require_file(dex_output / "classes.dex")
    if list(dex_output.glob("*.dex")) != [dex]:
        raise ValueError("Unexpected additional DEX files")
    defined = dex_classes(dex.read_bytes())
    report = {
        "sources": source_hashes,
        "android_api_sha256": sha256(android_jar),
        "xposed_api_sha256": sha256(xposed_jar),
        "dex_sha256": sha256(dex),
        "defined_classes": defined,
        "javac_version": run([javac, "-version"], capture=True).strip(),
        "mode": "compile-only" if args.compile_only else "unsigned" if args.unsigned else "signed",
        "manifest_sha256": manifest_hash,
        "xposed_entry_sha256": entry_hash,
    }
    if not args.compile_only:
        aapt = require_file(tools / "aapt2.exe")
        zipalign = require_file(tools / "zipalign.exe")
        manifest = require_file(HOOK / "AndroidManifest.xml")
        entry = require_file(HOOK / "assets/xposed_init")
        base = output / "base.apk"
        run([aapt, "link", "-o", base, "--manifest", manifest,
             "-I", android_jar, "--min-sdk-version", "24", "--target-sdk-version", "35"])
        unsigned = output / "XpanHook-unsigned.apk"
        with zipfile.ZipFile(base) as source, zipfile.ZipFile(unsigned, "w", zipfile.ZIP_DEFLATED) as target:
            for item in source.infolist():
                target.writestr(item, source.read(item.filename))
            target.write(dex, "classes.dex")
            target.write(entry, "assets/xposed_init")
        verify_apk(unsigned)
        aligned = output / "XpanHook-aligned.apk"
        run([zipalign, "-f", "4", unsigned, aligned])
        run([zipalign, "-c", "4", aligned])
        report["unsigned_apk_sha256"] = sha256(aligned)
        if not args.unsigned:
            signer = require_file(tools / "lib/apksigner.jar")
            for name in ("XPAN_KEYSTORE", "XPAN_KEY_ALIAS", "XPAN_STORE_PASS"):
                if not os.environ.get(name):
                    raise ValueError(f"Set {name} for signing; no key will be created")
            key = require_file(os.environ["XPAN_KEYSTORE"])
            expected = os.environ.get("XPAN_EXPECTED_CERT_SHA256", "").lower()
            release = Path(os.environ.get("XPAN_REFERENCE_MODULE", ROOT / "XpanAce3Pro-v2.0.zip"))
            if not release.is_file() and not os.environ.get("XPAN_REFERENCE_MODULE"):
                release = ROOT / "XpanAce3Pro-v1.3.zip"
            if release.is_file():
                with zipfile.ZipFile(release) as archive:
                    if archive.namelist().count("payload/XpanHook.apk") != 1:
                        raise ValueError("Ambiguous release APK")
                    reference = output / "release-reference.apk"
                    reference.write_bytes(archive.read("payload/XpanHook.apk"))
                release_certificate = signer_certificate(java, signer, reference)
                if expected and expected != release_certificate:
                    raise ValueError("Expected certificate differs from current release")
                expected = release_certificate
            if not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise ValueError("Set XPAN_EXPECTED_CERT_SHA256 when no verified release exists")
            signed = output / "XpanHook.apk"
            command = [java, "-jar", signer, "sign", "--ks", key,
                       "--ks-key-alias", os.environ["XPAN_KEY_ALIAS"],
                       "--ks-pass", "env:XPAN_STORE_PASS"]
            if os.environ.get("XPAN_KEY_PASS"):
                command += ["--key-pass", "env:XPAN_KEY_PASS"]
            run(command + ["--out", signed, aligned])
            certificate = signer_certificate(java, signer, signed)
            if certificate != expected:
                raise ValueError("Signing certificate mismatch; this APK must not be installed")
            verify_apk(signed)
            run([zipalign, "-c", "4", signed])
            badging = run([aapt, "dump", "badging", signed], capture=True)
            xml = ET.parse(manifest).getroot()
            namespace = "{http://schemas.android.com/apk/res/android}"
            identity = f"package: name='{xml.attrib['package']}' versionCode='{xml.attrib[namespace + 'versionCode']}' versionName='{xml.attrib[namespace + 'versionName']}'"
            if identity not in badging:
                raise ValueError("APK identity differs from source manifest")
            report["apk_sha256"] = sha256(signed)
            report["certificate_sha256"] = certificate
    if sorted(HOOK.glob("*.java")) != sources or any(
            sha256(path) != source_hashes[str(path.relative_to(ROOT))] for path in sources):
        raise ValueError("Hook sources changed during build; discard this output and rebuild")
    if sha256(manifest) != manifest_hash or sha256(entry) != entry_hash:
        raise ValueError("Manifest/Xposed entry changed during build; discard this output and rebuild")
    (output / "provenance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Verified {len(defined)} hook classes; output: {output}")
    if args.compile_only or args.unsigned:
        print("Not signed, not packaged as a module, and not installed.")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--compile-only", action="store_true", help="Compile and validate DEX only")
    modes.add_argument("--unsigned", action="store_true", help="Also assemble and align an unsigned APK")
    args = parser.parse_args()
    try:
        build(args)
    except (ValueError, OSError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        parser.exit(1, f"Build failed: {error}\n")


if __name__ == "__main__":
    main()
