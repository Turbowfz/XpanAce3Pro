#!/usr/bin/env python3
"""Package verified v2.1 inputs offline; never sign, install, or publish.

Run with python -I. --output must be a NEW _tmp/.../XpanAce3Pro-v2.1.zip
with an existing parent. Signed is the default; --unsigned-development accepts
only an unsigned build and emits a deliberately NOT INSTALLABLE/UNSIGNED bundle.
--signed-device-test accepts only the existing pinned debug certificate; it is
installable for explicitly authorized local tests, never a release replacement.
No scripts from inputs or reference archives are executed.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CERTIFICATE = "9a6d86924f7071a39558596be0e0cb80176e41188b550a0e88f8f57eeeba2fa8"
DEVICE_TEST_CERTIFICATE = "916348b3987d0f557f6aa6ae480822c18a91aeba2b9e46cbf4af30b295e43f9c"
FILENAME = "XpanAce3Pro-v2.1.zip"
RECIPE = "PJX110-Xpan-v2-native-crop-v1"
ROOT_SCRIPTS = ("customize.sh", "runtime.sh", "lifecycle.sh", "post-fs-data.sh", "service.sh", "uninstall.sh", "parent-context.sh")
APK_UNINSTALL = "payload/uninstall-apk.sh"
SCRIPTS = (*ROOT_SCRIPTS, APK_UNINSTALL)
BOOTSTRAP = "META-INF/com/google/android/update-binary"
UPDATER = "META-INF/com/google/android/updater-script"
LIMIT = 4 * 1024 * 1024
APK_LIMIT = 32 * 1024 * 1024
OP_LIMIT = 16 * 1024 * 1024
# Bundled device-stock C17 completion for OPCameraPro's attached module.
# 一加13 补全（3873cc99/5ddf19ea）曾实测：XPAN 可进但拍照卡死，已回退。
# Path -> (exact size, exact SHA-256 of this phone's stock original).
OP_COMPAT = {
    "payload/op-compat/framework/com.oplus.camera.unit.sdk.jar":
        (5377971, "d2b20133252414e26ac7c68255c76fa61cf41826c3b63d423a14c1187af42899"),
    "payload/op-compat/framework/com.oplus.camera.unit.sdk.adapter.jar":
        (2948231, "7418d59955746d8aca56438a5e0e26e1f08ff6ae4d0cacc22057d0041b12aac4"),
}


def load(name, path):
    # Trusted repository siblings only, not cwd or directories containing APKs.
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bh = load("_xpan_package_build_hook", Path(__file__).with_name("build_hook.py"))
mp = load("_xpan_package_make_profiles", Path(__file__).with_name("make_profiles.py"))

# Same conventional contract inspected in the existing v1.3 ZIP, not its scripts.
# KernelSU managers recognize #MAGISK and use their own module loader; the Magisk
# recovery entry delegates to the installed manager's util_functions/install_module.
INSTALLER = b"""#!/sbin/sh
umask 022
OUTFD=$2
ZIPFILE=$3
ui_print() { printf '%s\\n' "$1"; }
[ -f /data/adb/magisk/util_functions.sh ] || {
    ui_print 'Magisk v20.4+ module installer is required'
    exit 1
}
. /data/adb/magisk/util_functions.sh
[ "${MAGISK_VER_CODE:-0}" -ge 20400 ] || {
    ui_print 'Magisk v20.4+ module installer is required'
    exit 1
}
install_module
exit $?
"""
UNSIGNED_SCRIPT = b"""#!/system/bin/sh
printf '%s\\n' 'NOT INSTALLABLE/UNSIGNED: development bundle; no installation or PM actions allowed' >&2
exit 1
"""
UNSIGNED_INSTALLER = UNSIGNED_SCRIPT.replace(b"#!/system/bin/sh", b"#!/sbin/sh", 1)


class PackageError(ValueError):
    pass


def fail(message):
    raise PackageError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def valid_hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def regular(path, directory=False):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        fail(f"Symlink input/output is forbidden: {path}")
    if not (path.is_dir() if directory else path.is_file()):
        fail(f"Required {'directory' if directory else 'file'} is missing: {path}")
    return path


class Inputs:
    """Byte snapshots, checked again immediately before and after publication."""
    def __init__(self):
        self.hashes = {}

    def read(self, path, limit=LIMIT):
        path = regular(path)
        if path.stat().st_size > limit:
            fail(f"Input exceeds bounded size: {path}")
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            fail(f"Input grew beyond bounded size: {path}")
        pin = (len(data), digest(data))
        if path in self.hashes and self.hashes[path] != pin:
            fail(f"Input changed while packaging: {path}")
        self.hashes[path] = pin
        return data

    def recheck(self):
        for path, pin in list(self.hashes.items()):
            if (len(data := self.read(path, max(LIMIT, pin[0]))), digest(data)) != pin:
                fail(f"Input changed while packaging: {path}")


def json_object(data, label):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                fail(f"Duplicate JSON key in {label}: {key}")
            result[key] = value
        return result
    try:
        result = json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                            parse_constant=lambda value: fail(f"Non-finite JSON in {label}"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        fail(f"Invalid JSON in {label}: {exc}")
    if not isinstance(result, dict):
        fail(f"Expected a JSON object in {label}")
    return result


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def lf_text(data, label):
    if not data or not data.endswith(b"\n") or b"\r" in data:
        fail(f"LF-only text with a final newline required: {label}")
    if any(byte < 32 and byte not in (9, 10) for byte in data) or b"\x7f" in data:
        fail(f"Control character in {label}")
    try:
        return data.decode("utf-8")
    except UnicodeError:
        fail(f"UTF-8 text required: {label}")


def props(data, label):
    result = {}
    for line in lf_text(data, label).splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key) or key in result or not value:
            fail(f"Malformed or duplicate property in {label}")
        result[key] = value
    return result


def safe_name(name):
    if (not isinstance(name, str) or not name or name.startswith("/") or "\\" in name
            or ":" in name or any(ord(c) < 32 or ord(c) == 127 for c in name)
            or any(part in ("", ".", "..") for part in name.split("/"))):
        fail(f"Unsafe archive/input path: {name!r}")
    return name


def tree_files(directory):
    directory = regular(directory, directory=True)
    found = set()
    for path in directory.rglob("*"):
        relative = path.relative_to(directory).as_posix()
        safe_name(relative)
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            fail(f"Non-regular input: {path}")
        parts = [part.lower() for part in relative.split("/")]
        if (any(part in {"keys", "system", "vendor", "odm", "product", "system_ext", "overlay"} for part in parts)
                or path.suffix.lower() in {".key", ".pem", ".jks", ".keystore", ".p12", ".pfx"}
                or path.name.lower() in {"system.prop", "crop_watch.sh", "enabler.dex", "oc_1.enc"}):
            fail(f"Forbidden legacy overlay/key input: {path}")
        if path.is_file():
            found.add(relative)
        if len(found) > 4096:
            fail("Too many input files")
    return found


def archive_names():
    return {
        "module.prop", *SCRIPTS, "skip_mount", "payload/XpanHook.apk",
        "manifest.json", "provenance.json", "changelog.md", BOOTSTRAP, UPDATER,
        *OP_COMPAT, "payload/op-compat/MANIFEST",
        *(f"payload/profiles/{platform}/{name}" for platform in ("c16", "c17") for name in (*mp.FILES, "profile.props")),
        *(f"profiles/{platform}/receipt.json" for platform in ("c16", "c17")),
    }


def mode_for(name):
    return stat.S_IFREG | (0o755 if name in SCRIPTS or name == BOOTSTRAP else 0o644)


def module_entries(inputs):
    required = {"module.prop", *ROOT_SCRIPTS}
    found = tree_files(ROOT / "module")
    if found - required - {"skip_mount"} or not required.issubset(found):
        fail("module/ must contain only the complete approved root files (and optional skip_mount)")
    result = {name: inputs.read(ROOT / "module" / name) for name in required}
    identity = props(result["module.prop"], "module.prop")
    if (identity.get("id"), identity.get("version"), identity.get("versionCode")) not in {
            ("XpanAce3Pro", "v2.1", "21"), ("XpanAce3Pro", "2.1", "21")}:
        fail("Wrong module id/version: XpanAce3Pro v2.1/21 is required")
    # Root wrapper first revokes this instance's runtime/mount eligibility;
    # the unchanged Hook helper is phase two, never the root uninstall entry.
    result[APK_UNINSTALL] = inputs.read(ROOT / "hook/uninstall.sh")
    for name in SCRIPTS:
        lf_text(result[name], name)
        if not result[name].startswith(b"#!/system/bin/sh\n"):
            fail(f"Expected Android sh shebang: {name}")
    if "skip_mount" in found and inputs.read(ROOT / "module/skip_mount") != b"":
        fail("skip_mount must be an empty marker")
    result["skip_mount"] = b""
    return result


def op_compat_entries(inputs):
    """Bundled C17 completion must be this phone's exact stock bytes; no relabel."""
    result = {}
    manifest = []
    for name, (size, pin) in OP_COMPAT.items():
        data = inputs.read(ROOT / name, OP_LIMIT)
        if len(data) != size or digest(data) != pin:
            fail("Bundled C17 completion is not the approved stock original: " + name)
        result[name] = data
        manifest.append(name.rsplit("/", 1)[-1] + "\t" + pin)
    result["payload/op-compat/MANIFEST"] = ("\n".join(sorted(manifest)) + "\n").encode("utf-8")
    return result


def source_set():
    paths = sorted((ROOT / "hook").glob("*.java"))
    if not paths or ROOT / "hook/XpanHook.java" not in paths:
        fail("Current canonical hook sources are missing")
    return {path.relative_to(ROOT).as_posix(): path for path in paths}


def source_hashes(inputs):
    return {name: digest(inputs.read(path)) for name, path in source_set().items()}


def normalized_sources(value):
    if not isinstance(value, dict):
        fail("Build source hashes are missing")
    result = {}
    for name, pin in value.items():
        name = safe_name(name.replace("\\", "/"))
        if not re.fullmatch(r"hook/[^/]+\.java", name) or name in result or not valid_hash(pin):
            fail("Invalid/duplicate canonical build source hash")
        result[name] = pin
    return result


def current_manifest(inputs):
    data = inputs.read(ROOT / "hook/AndroidManifest.xml")
    lf_text(data, "hook/AndroidManifest.xml")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        fail(f"Invalid source Android manifest: {exc}")
    android = "{http://schemas.android.com/apk/res/android}"
    identity = (root.attrib.get("package"), root.attrib.get(android + "versionCode"), root.attrib.get(android + "versionName"))
    if root.tag != "manifest" or identity != ("com.xpanport.hook", "21", "2.1"):
        fail("Wrong current APK package/version: com.xpanport.hook 2.1/21 is required")
    return data


def sdk_tools():
    sdk = os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME")
    if not sdk:
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            fail("Set ANDROID_SDK_ROOT for offline APK verification")
        sdk = str(Path(local) / "Android/Sdk")
    return regular(Path(sdk) / "build-tools/35.0.0", directory=True)


def run_verify(command):
    # APK paths are arguments, never a cwd or executable. No sign/PM/network path.
    try:
        result = subprocess.run([str(arg) for arg in command], cwd=ROOT, check=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="strict", timeout=120)
    except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
        fail(f"Offline APK verification failed: {exc}")
    return result.stdout


def verify_signature(apk, expected_certificate=CERTIFICATE):
    tools = sdk_tools()
    home = os.environ.get("JAVA_HOME")
    java = regular(Path(home) / "bin/java.exe" if home else shutil.which("java") or "missing-java")
    signer = regular(tools / "lib/apksigner.jar")
    output = run_verify([java, "-jar", signer, "verify", "--verbose", "--print-certs", apk])
    certificates = re.findall(r"Signer #\d+ certificate SHA-256 digest: ([0-9a-fA-F]+)", output)
    if expected_certificate not in (CERTIFICATE, DEVICE_TEST_CERTIFICATE):
        fail("Unapproved signing certificate")
    if len(certificates) != 1 or certificates[0].lower() != expected_certificate:
        fail("APK signing certificate mismatch; the explicitly selected pinned certificate is required")
    return expected_certificate


def verify_identity(apk):
    aapt = regular(sdk_tools() / "aapt2.exe")
    output = run_verify([aapt, "dump", "badging", apk])
    identity = "package: name='com.xpanport.hook' versionCode='21' versionName='2.1'"
    if not any(line == identity or line.startswith(identity + " ") for line in output.splitlines()):
        fail("Actual APK package/version differs from the canonical 2.1/21 manifest")


def inspect_apk(data, entry, unsigned):
    if unsigned and b"APK Sig Block 42" in data:
        fail("Unsigned provenance cannot describe an APK signing block")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        items = archive.infolist()
        if len(items) > 4096 or sum(item.file_size for item in items) > 128 * 1024 * 1024:
            fail("APK exceeds bounded ZIP limits")
        for item in items:
            safe_name(item.filename.rstrip("/") if item.is_dir() else item.filename)
            kind = stat.S_IFMT(item.external_attr >> 16)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or item.flag_bits & 1:
                fail("APK contains non-regular/encrypted entries")
            if item.file_size > APK_LIMIT or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                fail("APK entry exceeds limits or uses unsupported compression")
            if unsigned and re.fullmatch(r"META-INF/[^/]+\.(RSA|DSA|EC|SF)", item.filename, re.IGNORECASE):
                fail("Unsigned provenance cannot describe a v1 signed APK")
        if archive.read("assets/xposed_init") != entry:
            fail("APK Xposed entry differs from the current source asset")
        dex_hash = digest(archive.read("classes.dex"))
    defined = bh.verify_apk(io.BytesIO(data))
    return dex_hash, defined


def build_input(inputs, build_dir, unsigned, device_test=False):
    build_dir = regular(build_dir, directory=True)
    tree_files(build_dir)  # Reject old overlays/keys, never recursively package it.
    report = json_object(inputs.read(build_dir / "provenance.json"), "build provenance")
    allowed = {"sources", "android_api_sha256", "xposed_api_sha256", "dex_sha256", "defined_classes",
               "javac_version", "mode", "manifest_sha256", "apk_sha256", "certificate_sha256",
               "xposed_entry_sha256", "unsigned_apk_sha256", "device_test_only"}
    if set(report) - allowed:
        fail("Unknown build provenance fields; caller-qualified assertions are not accepted")
    if unsigned and device_test:
        fail("Unsigned development and signed device-test modes are mutually exclusive")
    expected_mode = "signed-device-test" if device_test else "unsigned" if unsigned else "signed"
    if report.get("mode") != expected_mode:
        fail(f"Build mode must be {expected_mode}; development/test signing requires its explicit option")
    if device_test:
        if report.get("device_test_only") is not True:
            fail("Device-test provenance must explicitly restrict use to local testing")
    elif "device_test_only" in report:
        fail("Device-test provenance must not be relabelled as a release or unsigned development build")
    expected_certificate = DEVICE_TEST_CERTIFICATE if device_test else CERTIFICATE
    sources = source_hashes(inputs)
    if normalized_sources(report.get("sources")) != sources:
        fail("Build source hashes are stale or incomplete; rebuild the current canonical sources")
    manifest = current_manifest(inputs)
    if report.get("manifest_sha256") != digest(manifest):
        fail("Build Android manifest hash is stale")
    for key in ("dex_sha256", "android_api_sha256", "xposed_api_sha256"):
        if not valid_hash(report.get(key)):
            fail(f"Missing/invalid build hash: {key}")
    if unsigned:
        if report.get("certificate_sha256") is not None or (build_dir / "XpanHook.apk").exists():
            fail("Unsigned development must not reuse a signed build or certificate claim")
        apk = build_dir / "XpanHook-aligned.apk"
        if not apk.exists():
            apk = build_dir / "XpanHook-unsigned.apk"
    else:
        if report.get("certificate_sha256") != expected_certificate or not valid_hash(report.get("apk_sha256")):
            fail("Signed provenance must pin the selected certificate and exact APK hash")
        apk = build_dir / "XpanHook.apk"
    data = inputs.read(apk, APK_LIMIT)
    apk_hash = digest(data)  # build_hook's unsigned reports may omit this field.
    if "apk_sha256" in report and report["apk_sha256"] != apk_hash:
        fail("APK hash does not match build provenance")
    if unsigned and "unsigned_apk_sha256" in report and report["unsigned_apk_sha256"] != apk_hash:
        fail("Unsigned APK hash does not match build provenance")
    entry = inputs.read(ROOT / "hook/assets/xposed_init")
    lf_text(entry, "hook/assets/xposed_init")
    if "xposed_entry_sha256" in report and report["xposed_entry_sha256"] != digest(entry):
        fail("Build Xposed entry hash is stale")
    dex_hash, defined = inspect_apk(data, entry, unsigned)
    if dex_hash != report["dex_sha256"] or defined != report.get("defined_classes"):
        fail("APK DEX/hash/class definitions differ from build provenance")
    dex_file = build_dir / "dex/classes.dex"
    if digest(inputs.read(dex_file, APK_LIMIT)) != dex_hash:
        fail("Build DEX differs from the APK DEX")
    verify_identity(regular(apk))
    if unsigned:
        certificate = None
    elif device_test:
        certificate = verify_signature(regular(apk), expected_certificate)
    else:
        certificate = verify_signature(regular(apk))
    inputs.recheck()  # Verifier and hash inspection must have observed the same APK.
    summary = {key: report[key] for key in ("android_api_sha256", "xposed_api_sha256", "dex_sha256", "defined_classes")}
    summary.update(mode=expected_mode, sources=sources, manifest_sha256=digest(manifest),
                   xposed_entry_sha256=digest(entry), apk_sha256=apk_hash, certificate_sha256=certificate)
    return data, summary


def profile_entries(inputs, profiles_dir):
    profiles_dir = regular(profiles_dir, directory=True)
    required = {"manifest.json", *(f"payload/profiles/{p}/{n}" for p in ("c16", "c17") for n in (*mp.FILES, "profile.props")),
                *(f"profiles/{p}/receipt.json" for p in ("c16", "c17"))}
    optional = {f"originals/{p}/{n}" for p in ("c16", "c17") for n in mp.FILES}
    found = tree_files(profiles_dir)
    if not required.issubset(found) or found - required - optional:
        fail("Incomplete or non-whitelisted profile build files")
    data = inputs.read(profiles_dir / "manifest.json")
    latest = inputs.read(ROOT / "_tmp/profile-build/manifest.json")
    if data != latest:
        fail("Profile manifest is stale: it differs from current _tmp/profile-build/manifest.json")
    manifest = json_object(data, "profile manifest")
    if (manifest.get("manifest_version"), manifest.get("recipe_id"), manifest.get("module_version"),
            manifest.get("module_version_code")) != (1, RECIPE, "2.1", 21):
        fail("Wrong profile recipe/module version")
    if manifest.get("hal_tested") is not False or set(manifest.get("profiles", {})) != {"c16", "c17"}:
        fail("Both exact, non-HAL-claimed C16/C17 profiles are required")
    evidence = {name: {"size": pin[0], "sha256": pin[1]} for name, pin in mp.EVIDENCE.items()}
    if manifest.get("sdk_app_evidence") != evidence:
        fail("Profile SDK/app evidence differs from the investigated recipe")
    policies = {"final_output_width": 8192, "zoom_ratios": [1.0], "hasselblad_ui": True,
                "watermark": True, "xdr_ratio_minimum": 8.0}
    if manifest.get("runtime_policies_not_implemented_by_profiles") != policies:
        fail("Profile/runtime policy provenance is missing or changed")
    entries = {"manifest.json": data}
    for platform in ("c16", "c17"):
        receipt = manifest["profiles"][platform]
        if not isinstance(receipt, dict) or any(receipt.get(k) != v for k, v in mp.TARGETS[platform].items()):
            fail(f"Wrong exact profile target: {platform}")
        if receipt.get("profile_id") != platform or set(receipt.get("files", {})) != set(mp.FILES):
            fail(f"Incomplete profile receipt: {platform}")
        getters = mp.getter_overrides() if platform == "c16" else []
        if (receipt.get("native_picture_size") != [4096, 1512]
                or receipt.get("required_java_getter_overrides") != getters
                or receipt.get("encrypted_vendor_unchanged") is not (platform == "c16")
                or receipt.get("native_binary_replacements") != []):
            fail(f"Wrong native/getter recipe: {platform}")
        provenance = receipt.get("provenance", {})
        if (provenance.get("schema_sha256") != mp.cp.SCHEMA_SHA256
                or provenance.get("hal_tested") is not False
                or provenance.get("unit_pb_shared_original_bytes") is not True):
            fail(f"Missing exact schema/profile provenance: {platform}")
        for name in mp.FILES:
            record = receipt["files"][name]
            if not isinstance(record, dict):
                fail("Malformed profile file record")
            for kind, pin in (("original", mp.STOCK[platform][name]), ("output", mp.OUTPUTS[platform][name])):
                expected_path = f"{'originals' if kind == 'original' else 'payload/profiles'}/{platform}/{name}"
                expected = {"size": pin[0], "sha256": pin[1], "path": expected_path}
                if record.get(kind) != expected:
                    fail(f"Profile {kind} does not match locked recipe pins: {platform}/{name}")
            allowed = sorted({mp.STOCK[platform][name][1], mp.OUTPUTS[platform][name][1]})
            if (record.get("install_allowed_sha256") != allowed
                    or record.get("unrelated_semantics_preserved") is not True
                    or record.get("double_patch_same_bytes") is not True
                    or not isinstance(record.get("semantic_changes"), list)):
                fail("Invalid profile preservation/allowed-hash receipt")
            path = record["output"]["path"]
            payload = inputs.read(profiles_dir / path)
            if (len(payload), digest(payload)) != mp.OUTPUTS[platform][name]:
                fail(f"Profile payload differs from the pinned recipe: {platform}/{name}")
            entries[path] = payload
        path = f"payload/profiles/{platform}/profile.props"
        actual = inputs.read(profiles_dir / path)
        parsed = props(actual, path)
        expected = mp.profile_props(platform, receipt)
        if len(parsed) != 12 or actual != expected:
            fail(f"profile.props must be the exact 12-key LF recipe: {platform}")
        entries[path] = actual
        path = f"profiles/{platform}/receipt.json"
        asset = inputs.read(profiles_dir / path)
        if json_object(asset, path) != mp.asset_receipt(platform, receipt):
            fail(f"Profile asset receipt does not match the pinned manifest: {platform}")
        entries[path] = asset
    # Do NOT generate lifecycle.inputs: runtime alone attests the observed device.
    return entries, digest(data)


def output_path(output):
    output = Path(output).absolute()
    if output.name != FILENAME:
        fail(f"Use the single version filename {FILENAME}, without development suffixes")
    if output.exists() or output.is_symlink():
        fail("Output already exists; no package will be overwritten")
    regular(output.parent, directory=True)
    temporary = regular(ROOT / "_tmp", directory=True)
    try:
        relative = output.relative_to(temporary)
    except ValueError:
        fail("Packaging outputs (including unsigned development) must stay under repository _tmp")
    if len(relative.parts) < 2 or any("final" in part.lower() or part.lower() == "releases" for part in relative.parts[:-1]):
        fail("Use a separate _tmp development/package directory, never a final/release directory")
    return output


def validate_archive(path, expected):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        for name in names:
            safe_name(name)
        if len(names) != len(set(names)) or set(names) != archive_names() or set(expected) != archive_names():
            fail("Package contains duplicate, missing, or non-whitelisted ZIP entries")
        for item in archive.infolist():
            if (item.create_system != 3 or item.external_attr >> 16 != mode_for(item.filename)
                    or item.flag_bits & 1 or item.is_dir() or item.compress_type != zipfile.ZIP_DEFLATED):
                fail(f"Invalid package ZIP permissions/type/compression: {item.filename}")
            data = expected[item.filename]
            if item.file_size != len(data) or archive.read(item) != data:
                fail(f"Package bytes/hash mismatch: {item.filename}")
        if archive.testzip() is not None or archive.read("skip_mount") != b"":
            fail("Package checksum/skip_mount validation failed")


def device_test_module(data):
    lines = lf_text(data, "module.prop").splitlines()
    result = []
    for line in lines:
        if line.startswith("updateJson="):
            continue  # Do not advertise release updates for an incompatible local test certificate.
        if line.startswith("description="):
            line = "description=仅本机验收，使用既有调试签名，勿发布。 " + line.partition("=")[2]
        result.append(line)
    if not any(line.startswith("description=") for line in result):
        result.append("description=仅本机验收，使用既有调试签名，勿发布。")
    return ("\n".join(result) + "\n").encode("utf-8")


def pack(build_dir, profiles_dir, output, unsigned_development=False, signed_device_test=False):
    if unsigned_development and signed_device_test:
        fail("Unsigned development and signed device-test modes are mutually exclusive")
    output = output_path(output)  # No input read or verifier run before no-overwrite check.
    inputs = Inputs()
    entries = module_entries(inputs)
    changelog = inputs.read(ROOT / "changelog.md")
    lf_text(changelog, "changelog.md")
    entries["changelog.md"] = changelog
    apk, build = build_input(inputs, build_dir, unsigned_development, signed_device_test)
    profiles, manifest_hash = profile_entries(inputs, profiles_dir)
    entries.update(profiles)
    entries.update(op_compat_entries(inputs))
    entries["payload/XpanHook.apk"] = apk
    if signed_device_test:
        entries["module.prop"] = device_test_module(entries["module.prop"])
    entries[BOOTSTRAP] = UNSIGNED_INSTALLER if unsigned_development else INSTALLER
    entries[UPDATER] = b"#MAGISK\n"
    if unsigned_development:
        # Even managers that bypass update-binary, and manual extraction, cannot
        # run source install/service/uninstall scripts from this development ZIP.
        entries.update({name: UNSIGNED_SCRIPT for name in SCRIPTS})
    generator_hashes = {f"tools/{name}": digest(inputs.read(Path(__file__).with_name(name)))
                        for name in ("package_module.py", "build_hook.py", "make_profiles.py", "config_patch.py")}
    provenance = {
        "package_manifest_version": 1, "module_id": "XpanAce3Pro", "version": "2.1", "version_code": 21,
        "status": "NOT INSTALLABLE/UNSIGNED" if unsigned_development else "SIGNED/DEVICE TEST ONLY" if signed_device_test else "SIGNED/CERTIFICATE VERIFIED",
        "installable": not unsigned_development,
        "device_test_only": signed_device_test,
        "release_certificate_preserved": not unsigned_development and not signed_device_test,
        "unsigned_scripts_replaced_by_abort_stubs": unsigned_development,
        "build": build, "profile_manifest_sha256": manifest_hash,
        "latest_profile_manifest_sha256": manifest_hash, "generator_sources": generator_hashes,
        "inputs": {path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name:
                   {"size": pin[0], "sha256": pin[1]} for path, pin in inputs.hashes.items()},
        "entries": {name: {"size": len(data), "sha256": digest(data), "unix_mode": f"{mode_for(name):06o}"}
                    for name, data in sorted(entries.items())},
        "lifecycle_inputs": "not bundled; runtime must qualify the actual device before producing its 13-key receipt",
        "hal_tested": False,
    }
    entries["provenance.json"] = encoded(provenance)
    reserved = False
    descriptor, stage_name = tempfile.mkstemp(prefix=".xpan-package-", suffix=".zip", dir=output.parent)
    stage = Path(stage_name)
    os.close(descriptor)
    try:
        with zipfile.ZipFile(stage, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in sorted(entries.items()):
                info = zipfile.ZipInfo(safe_name(name), date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = mode_for(name) << 16
                archive.writestr(info, data)
        validate_archive(stage, entries)
        # APK structural validation is repeated on the exact bytes being shipped.
        inspect_apk(entries["payload/XpanHook.apk"], inputs.read(ROOT / "hook/assets/xposed_init"), unsigned_development)
        if source_hashes(inputs) != build["sources"]:
            fail("Current hook source set changed while packaging")
        inputs.recheck()
        with output.open("xb") as destination:  # O_EXCL on Windows and POSIX, never replace.
            reserved = True
            with stage.open("rb") as source:
                shutil.copyfileobj(source, destination)
            destination.flush()
            os.fsync(destination.fileno())
        validate_archive(output, entries)
        if source_hashes(inputs) != build["sources"]:
            fail("Current hook source set changed during publication")
        inputs.recheck()
        return output
    except Exception:
        if reserved:
            output.unlink()
        raise
    finally:
        stage.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", required=True, help="build_hook output containing provenance and APK/DEX")
    parser.add_argument("--profiles-dir", required=True, help="make_profiles output; compared to current _tmp/profile-build")
    parser.add_argument("--output", required=True, help=f"NEW _tmp/<directory>/{FILENAME}")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--unsigned-development", action="store_true", help="NOT INSTALLABLE/UNSIGNED bundle, only under _tmp")
    modes.add_argument("--signed-device-test", action="store_true", help="Use the existing pinned debug certificate only for explicitly authorized local device tests")
    args = parser.parse_args(argv)
    try:
        result = pack(args.build_dir, args.profiles_dir, args.output, args.unsigned_development, args.signed_device_test)
    except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile, mp.cp.ConfigError) as exc:
        print(f"Package rejected: {exc}", file=sys.stderr)
        return 2
    status = "NOT INSTALLABLE/UNSIGNED" if args.unsigned_development else "SIGNED/DEVICE TEST ONLY" if args.signed_device_test else "SIGNED/CERTIFICATE VERIFIED"
    print(f"{status}: {result}")
    print("No signing, device access, installation, or publication performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
