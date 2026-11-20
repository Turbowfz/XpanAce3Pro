#!/usr/bin/env python3
"""Sign a current unsigned 2.1 build with the existing debug key, for local tests only.

Run with python -I. Never execute the historical build script, create a key,
install/uninstall an app, package a module, or replace a release. Passwords are
parsed as literal data and passed only in the signing child's environment.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TRUSTED_SCRIPT = ROOT.parent / "XpanPort/06_hook模块/XpanHook/build.sh"
KEYSTORE = TRUSTED_SCRIPT.parent / "out/debug.keystore"
CERTIFICATE = "916348b3987d0f557f6aa6ae480822c18a91aeba2b9e46cbf4af30b295e43f9c"
ACK = "existing-debug-key-local-test-only"
MODE = "signed-device-test"
LIMIT = 32 * 1024 * 1024
BUILD_FIELDS = {
    "sources", "android_api_sha256", "xposed_api_sha256", "dex_sha256",
    "defined_classes", "javac_version", "mode", "manifest_sha256",
    "xposed_entry_sha256", "unsigned_apk_sha256", "apk_sha256", "certificate_sha256",
}


class DeviceTestError(ValueError):
    """Safe, fixed messages only: never include command/configuration values."""


def fail(message):
    raise DeviceTestError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def regular(path, directory=False):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            attrs = part.lstat()
            if part.is_symlink() or getattr(attrs, "st_file_attributes", 0) & 0x400:
                fail("Symlink/reparse-point inputs and outputs are forbidden")
    if not (path.is_dir() if directory else path.is_file()):
        fail("A required local file or directory is missing")
    return path


class Inputs:
    """Pin bytes in memory and recheck all files before/after signing."""
    def __init__(self):
        self.pins = {}

    def read(self, path, limit=LIMIT):
        path = regular(path)
        if path.stat().st_size > limit:
            fail("A local input exceeds the size limit")
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            fail("A local input exceeds the size limit")
        pin = (len(data), digest(data))
        if path in self.pins and self.pins[path] != pin:
            fail("An input changed during signing; discard and rebuild")
        self.pins[path] = pin
        return data

    def recheck(self):
        for path in list(self.pins):
            self.read(path)


@dataclass(frozen=True, repr=False)
class SigningConfig:
    alias: str = field(repr=False)
    store_pass: str = field(repr=False)
    key_pass: str = field(repr=False)

    def __repr__(self):
        return "<existing debug signing configuration: redacted>"


def parse_trusted_signing_config(text):
    """Recognize only the trusted script's simple literal apksigner sign commands.

    No shell evaluation, execution, variable expansion, password guesses, or
    fallback configurations. Repeated options/commands must agree exactly.
    """
    commands = []
    logical = text.replace("\\\r\n", " ").replace("\\\n", " ")
    for line in logical.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not re.search(r"--(?:ks(?:-key-alias|-pass)?|key-pass)(?:\s|=|$)", line) and not re.search(r"(?:apksigner\.bat|APKSIGNER)[\"'}]*\s+sign\b", line):
            continue
        try:
            tokens = shlex.split(line, comments=True, posix=True)
        except ValueError:
            fail("Trusted signing configuration is not a simple literal command")
        if len(tokens) < 2 or tokens[0] not in (
                "$BT/apksigner.bat", "$APKSIGNER", "${APKSIGNER}") or tokens[1] != "sign":
            fail("Trusted signing configuration contains an unrecognized command")
        options, positional = {}, []
        index = 2
        while index < len(tokens):
            name = tokens[index]
            if name.startswith("--"):
                if name not in {"--ks", "--ks-pass", "--key-pass", "--ks-key-alias", "--out"} or index + 1 >= len(tokens):
                    fail("Trusted signing configuration has unsupported options")
                value = tokens[index + 1]
                if not value or any(char in value for char in "$`\\\x00\r\n;&|<>"):
                    fail("Trusted signing configuration must use non-executable literals")
                if name in options and options[name] != value:
                    fail("Trusted signing configuration has contradictory repeated options")
                options[name] = value
                index += 2
            else:
                positional.append(name)
                index += 1
        required = {"--ks", "--ks-pass", "--key-pass", "--ks-key-alias", "--out"}
        if set(options) != required or options["--ks"] != "out/debug.keystore" or options["--out"] != "out/XpanHook.apk" or positional != ["out/XpanHook-aligned.apk"]:
            fail("Trusted signing configuration does not match the existing debug-key command")
        if not re.fullmatch(r"[A-Za-z0-9_.@+-]+", options["--ks-key-alias"]):
            fail("Trusted signing alias is not a supported literal")
        if any(not options[name].startswith("pass:") or len(options[name]) <= 5 for name in ("--ks-pass", "--key-pass")):
            fail("Trusted signing passwords must be existing nonempty pass literals")
        config = SigningConfig(options["--ks-key-alias"], options["--ks-pass"][5:], options["--key-pass"][5:])
        if commands and config != commands[0]:
            fail("Trusted signing configuration has contradictory signing commands")
        commands.append(config)
    if not commands:
        fail("No unique supported signing configuration was found in the trusted script")
    return commands[0]


def json_report(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                fail("Unsigned provenance contains duplicate JSON keys")
            result[key] = value
        return result
    try:
        report = json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                            parse_constant=lambda _: fail("Unsigned provenance contains non-finite JSON"))
    except (UnicodeError, json.JSONDecodeError):
        fail("Unsigned provenance is not valid UTF-8 JSON")
    if not isinstance(report, dict) or set(report) - BUILD_FIELDS or report.get("mode") != "unsigned":
        fail("Only a canonical unsigned build provenance is accepted")
    if report.get("certificate_sha256") is not None:
        fail("Unsigned provenance must not claim a signing certificate")
    for name in ("android_api_sha256", "xposed_api_sha256", "dex_sha256", "manifest_sha256", "xposed_entry_sha256", "unsigned_apk_sha256"):
        if not isinstance(report.get(name), str) or not re.fullmatch(r"[0-9a-f]{64}", report[name]):
            fail("Unsigned provenance is missing a required valid hash")
    return report


def current_sources(inputs, report):
    paths = sorted((ROOT / "hook").glob("*.java"))
    if ROOT / "hook/XpanHook.java" not in paths:
        fail("Current canonical hook sources are missing")
    actual = {path.relative_to(ROOT).as_posix(): digest(inputs.read(path)) for path in paths}
    claimed = report.get("sources")
    if not isinstance(claimed, dict):
        fail("Unsigned provenance source pins are missing")
    normalized = {}
    for name, pin in claimed.items():
        if not isinstance(name, str):
            fail("Unsigned provenance has invalid source pins")
        name = name.replace("\\", "/")
        if not re.fullmatch(r"hook/[^/]+\.java", name) or name in normalized:
            fail("Unsigned provenance has invalid or duplicate source pins")
        normalized[name] = pin
    if actual != normalized:
        fail("Unsigned source pins are stale or incomplete; rebuild current sources")
    manifest = inputs.read(ROOT / "hook/AndroidManifest.xml")
    try:
        xml = ET.fromstring(manifest)
    except ET.ParseError:
        fail("Current source manifest is invalid")
    android = "{http://schemas.android.com/apk/res/android}"
    identity = (xml.attrib.get("package"), xml.attrib.get(android + "versionCode"), xml.attrib.get(android + "versionName"))
    if xml.tag != "manifest" or identity != ("com.xpanport.hook", "21", "2.1"):
        fail("Current source manifest must identify com.xpanport.hook 21/2.1")
    entry = inputs.read(ROOT / "hook/assets/xposed_init")
    if digest(manifest) != report["manifest_sha256"] or digest(entry) != report["xposed_entry_sha256"]:
        fail("Unsigned manifest or Xposed entry pins are stale")
    return entry


def run_tool(command, step, env=None):
    # Capture everything: apksigner failures can include alias/configuration data.
    # Never stringify exceptions or echo argv/stdout/stderr. No shell/bat execution.
    child_env = dict(os.environ) if env is None else dict(env)
    for name in ("JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS", "CLASSPATH"):
        child_env.pop(name, None)
    try:
        result = subprocess.run([str(arg) for arg in command], cwd=ROOT, env=child_env,
                                shell=False, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError):
        fail(step + " could not complete; command details suppressed")
    finally:
        child_env.clear()
    if result.returncode != 0:
        fail(step + " failed; command output suppressed; no retry attempted")
    return result.stdout


def inspect_apk(data, entry, bh, unsigned):
    if unsigned and b"APK Sig Block 42" in data:
        fail("Unsigned APK must not contain an APK signing block")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        items = archive.infolist()
        if len(items) > 4096 or sum(item.file_size for item in items) > 128 * 1024 * 1024:
            fail("APK exceeds ZIP size limits")
        payload = {}
        for item in items:
            name = item.filename
            if not name or name.startswith("/") or "\\" in name or any(part in ("", ".", "..") for part in name.split("/")):
                fail("APK has unsafe ZIP entry names")
            if name in payload or item.flag_bits & 1 or stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG):
                fail("APK has duplicate, encrypted or non-regular ZIP entries")
            if item.file_size > LIMIT or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                fail("APK has unsupported or oversized ZIP entries")
            signature = re.fullmatch(r"META-INF/(?:MANIFEST\.MF|[^/]+\.(?:SF|RSA|DSA|EC))", name, re.IGNORECASE)
            if name not in {"AndroidManifest.xml", "resources.arsc", "classes.dex", "assets/xposed_init"} and not (signature and not unsigned):
                fail("APK contains an unexpected payload; key/configuration files are forbidden")
            payload[name] = archive.read(item)
        if payload.get("assets/xposed_init") != entry:
            fail("APK Xposed entry differs from the current source asset")
    defined = bh.verify_apk(io.BytesIO(data))  # ZIP CRC, DEX SHA-1/Adler32, class set.
    return payload, defined


def verify_identity(apk, aapt):
    output = run_tool([aapt, "dump", "badging", apk], "APK identity verification")
    identity = "package: name='com.xpanport.hook' versionCode='21' versionName='2.1'"
    if not any(line == identity or line.startswith(identity + " ") for line in output.splitlines()):
        fail("Actual APK identity is not com.xpanport.hook 21/2.1")
    if "minSdkVersion:'24'" not in output.splitlines() or "targetSdkVersion:'35'" not in output.splitlines():
        fail("Actual APK SDK versions differ from the canonical SDK35 build")


def verified_certificate(output):
    counts = re.findall(r"^Number of signers: ([0-9]+)\s*$", output, re.MULTILINE)
    certificates = re.findall(r"^Signer #([0-9]+) certificate SHA-256 digest: ([0-9a-fA-F]{64})\s*$", output, re.MULTILINE)
    if counts != ["1"] or len(certificates) != 1 or certificates[0][0] != "1" or certificates[0][1].lower() != CERTIFICATE:
        fail("APK must have exactly one signer with the pinned existing DEBUG certificate; not release")
    return certificates[0][1].lower()


def sign_device_test(build_dir, device_test_ack):
    if device_test_ack != ACK:
        fail("Explicit existing-debug-key local-test acknowledgment is required")
    inputs = Inputs()
    parent = regular(ROOT / "_tmp/build", directory=True)
    build_dir = regular(build_dir, directory=True)
    if build_dir.parent != parent or not build_dir.name.startswith("hook-"):
        fail("Unsigned input must be an existing repository _tmp/build/hook-* directory")
    if (build_dir / "XpanHook.apk").exists():
        fail("Unsigned input must not reuse a signed build directory")
    report = json_report(inputs.read(build_dir / "provenance.json"))
    entry = current_sources(inputs, report)
    helper = regular(Path(__file__).with_name("build_hook.py"))
    inputs.read(helper)
    inputs.read(Path(__file__))
    spec = importlib.util.spec_from_file_location("_xpan_device_test_build_hook", helper)
    bh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bh)  # Trusted sibling only; never load/run old build.sh.
    apk = regular(build_dir / "XpanHook-aligned.apk")
    unsigned_data = inputs.read(apk)
    if digest(unsigned_data) != report["unsigned_apk_sha256"] or ("apk_sha256" in report and report["apk_sha256"] != digest(unsigned_data)):
        fail("Aligned unsigned APK hash differs from original provenance")
    payload, defined = inspect_apk(unsigned_data, entry, bh, unsigned=True)
    dex = inputs.read(build_dir / "dex/classes.dex")
    if payload["classes.dex"] != dex or digest(dex) != report["dex_sha256"] or defined != report.get("defined_classes"):
        fail("Unsigned APK/DEX hash or class set differs from original provenance")
    sdk = Path(os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME") or str(Path(os.environ.get("LOCALAPPDATA", "")) / "Android/Sdk"))
    tools = regular(sdk / "build-tools/35.0.0", directory=True)
    java_home = os.environ.get("JAVA_HOME")
    java = regular(Path(java_home) / "bin/java.exe" if java_home else shutil.which("java") or "missing-java")
    signer = regular(tools / "lib/apksigner.jar")
    aapt, zipalign = regular(tools / "aapt2.exe"), regular(tools / "zipalign.exe")
    for path in (java, signer, aapt, zipalign):
        inputs.read(path)
    if digest(inputs.read(sdk / "platforms/android-35/android.jar")) != report["android_api_sha256"] or digest(inputs.read(Path(os.environ.get("XPAN_XPOSED_API", ROOT / "_tmp/api-82.jar")))) != report["xposed_api_sha256"]:
        fail("Current compile-only API hashes differ from unsigned provenance")
    config = parse_trusted_signing_config(inputs.read(TRUSTED_SCRIPT, limit=1024 * 1024).decode("utf-8"))
    key = regular(KEYSTORE)
    if key != regular(TRUSTED_SCRIPT).parent / "out/debug.keystore":
        fail("Only the trusted script's existing absolute debug.keystore is allowed")
    inputs.read(key)
    verify_identity(apk, aapt)
    run_tool([zipalign, "-c", "4", apk], "Unsigned APK alignment verification")
    inputs.recheck()
    output = Path(tempfile.mkdtemp(prefix="device-test-", dir=parent))
    try:
        signed = output / "XpanHook.apk"
        env = dict(os.environ)
        env["XPAN_DEVICE_TEST_STORE_PASS"] = config.store_pass
        env["XPAN_DEVICE_TEST_KEY_PASS"] = config.key_pass
        try:
            run_tool([java, "-jar", signer, "sign", "--ks", key,
                      "--ks-key-alias", config.alias,
                      "--ks-pass", "env:XPAN_DEVICE_TEST_STORE_PASS",
                      "--key-pass", "env:XPAN_DEVICE_TEST_KEY_PASS",
                      "--v4-signing-enabled", "false", "--out", signed, apk],
                     "Existing debug-key signing", env=env)
        finally:
            env.clear()
        certificate = verified_certificate(run_tool([java, "-jar", signer, "verify", "--verbose", "--print-certs", signed], "Signed APK certificate verification"))
        verify_identity(signed, aapt)
        run_tool([zipalign, "-c", "4", signed], "Signed APK alignment verification")
        signed_data = regular(signed).read_bytes()
        actual_payload, actual_defined = inspect_apk(signed_data, entry, bh, unsigned=False)
        if any(actual_payload.get(name) != data for name, data in payload.items()) or actual_defined != defined:
            fail("Signing changed the original APK payload")
        current_sources(inputs, report)
        inputs.recheck()
        (output / "dex").mkdir()
        (output / "dex/classes.dex").write_bytes(dex)
        final_report = dict(report)
        final_report.update(mode=MODE, device_test_only=True, apk_sha256=digest(signed_data), certificate_sha256=certificate)
        (output / "provenance.json").write_text(json.dumps(final_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if regular(signed).read_bytes() != signed_data or (output / "dex/classes.dex").read_bytes() != dex:
            fail("Fresh output changed before completion")
        current_sources(inputs, report)
        inputs.recheck()
    except BaseException:
        shutil.rmtree(output)  # Only this newly created directory, never inputs/apps.
        raise
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", required=True, type=Path, help="Current unsigned build_hook output under _tmp/build/hook-*")
    parser.add_argument("--device-test-ack", required=True, choices=[ACK], help="Acknowledges existing DEBUG key for local testing only, not future uninstall/cleanup")
    args = parser.parse_args(argv)
    try:
        output = sign_device_test(args.build_dir, args.device_test_ack)
    except DeviceTestError as error:
        parser.exit(1, "Device-test signing failed: " + str(error) + "\n")
    except (OSError, UnicodeError, ValueError, zipfile.BadZipFile, EOFError, IndexError):
        parser.exit(1, "Device-test signing failed: invalid/missing local input or APK; sensitive details suppressed\n")
    report = json.loads((output / "provenance.json").read_text(encoding="utf-8"))
    print("SIGNED/DEVICE TEST ONLY: not a release; no installation, uninstall, or publication performed.")
    print("Changing to a release certificate may require removing the test app; separate user confirmation is required.")
    print("Output: " + str(output))
    print("APK SHA-256: " + report["apk_sha256"])
    print("DEBUG certificate SHA-256: " + report["certificate_sha256"])
    print("Original unsigned APK SHA-256: " + report["unsigned_apk_sha256"])


if __name__ == "__main__":
    main()
