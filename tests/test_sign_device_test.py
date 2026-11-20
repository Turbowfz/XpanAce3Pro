"""Focused offline tests; run with python -I -B tests/test_sign_device_test.py.

All credentials/keystores here are synthetic placeholders. Tool calls are mocked;
these fixtures are never presented as genuinely signed APKs. No real key access,
password attempts, historical script execution, device operations, or network.
"""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sign_device_test_target", ROOT / "tools/sign_device_test.py")
sdt = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sdt
SPEC.loader.exec_module(sdt)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def minimal_dex():
    descriptor = b"Lcom/xpanport/hook/XpanHook;"
    string_at = 112 + 4 + 4 + 32
    data = bytearray(string_at + 1 + len(descriptor) + 1)
    data[:8] = b"dex\n035\0"
    struct.pack_into("<III", data, 32, len(data), 112, 0x12345678)
    struct.pack_into("<II", data, 56, 1, 112)
    struct.pack_into("<II", data, 64, 1, 116)
    struct.pack_into("<II", data, 96, 1, 120)
    struct.pack_into("<I", data, 112, string_at)
    data[string_at] = len(descriptor)
    data[string_at + 1:] = descriptor + b"\0"
    data[12:32] = hashlib.sha1(data[32:]).digest()
    struct.pack_into("<I", data, 8, zlib.adler32(data[12:]) & 0xffffffff)
    return bytes(data)


def apk_bytes(dex, extra=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("AndroidManifest.xml", b"synthetic XML; aapt2 boundary mocked")
        archive.writestr("resources.arsc", b"synthetic aapt2 resource table")
        archive.writestr("classes.dex", dex)
        archive.writestr("assets/xposed_init", b"com.xpanport.hook.XpanHook\n")
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return stream.getvalue()


SCRIPT = '''#!/usr/bin/env bash
# Fixture only; never executed, not the actual signing configuration.
keytool -genkeypair --alias unused-placeholder
"$BT/apksigner.bat" sign --ks out/debug.keystore --ks-pass pass:fixture-store-value --key-pass pass:fixture-key-value \\
 --ks-key-alias fixture-alias --out out/XpanHook.apk out/XpanHook-aligned.apk
"$BT/apksigner.bat" verify --verbose out/XpanHook.apk
'''
VERIFY = "Verifies\nNumber of signers: 1\nSigner #1 certificate SHA-256 digest: " + sdt.CERTIFICATE + "\n"
BADGING = "package: name='com.xpanport.hook' versionCode='21' versionName='2.1' platformBuildVersionCode='35'\nminSdkVersion:'24'\ntargetSdkVersion:'35'\n"


class ConfigTests(unittest.TestCase):
    def test_existing_literal_command_and_redacted_repr(self):
        config = sdt.parse_trusted_signing_config(SCRIPT)
        self.assertEqual(config.alias, "fixture-alias")
        self.assertEqual(config.store_pass, "fixture-store-value")
        self.assertEqual(config.key_pass, "fixture-key-value")
        self.assertNotIn("fixture", repr(config))

    def test_identical_repeated_options_and_commands(self):
        repeated = SCRIPT.replace("--ks-key-alias fixture-alias", "--ks-key-alias fixture-alias --ks-key-alias fixture-alias")
        self.assertEqual(sdt.parse_trusted_signing_config(SCRIPT), sdt.parse_trusted_signing_config(repeated + repeated))

    def test_apksigner_variable_form(self):
        self.assertEqual(sdt.parse_trusted_signing_config(SCRIPT), sdt.parse_trusted_signing_config(SCRIPT.replace("$BT/apksigner.bat", "$APKSIGNER")))

    def test_reject_unsupported_or_conflicting_configuration_without_values(self):
        cases = [
            SCRIPT.replace("--ks-key-alias fixture-alias", "--ks-key-alias fixture-alias --ks-key-alias wrong-placeholder"),
            SCRIPT + SCRIPT.replace("fixture-store-value", "conflicting-placeholder"),
            SCRIPT.replace("--ks-key-alias fixture-alias", ""),
            SCRIPT.replace("out/debug.keystore", "out/new.keystore"),
            SCRIPT.replace("pass:fixture-store-value", "env:UNKNOWN"),
            SCRIPT.replace("pass:fixture-store-value", "pass:$EXPANSION"),
            SCRIPT.replace("pass:fixture-store-value", '"pass:$(touch marker)"'),
            SCRIPT.replace("pass:fixture-store-value", "pass:fixture-store-value;touch"),
            SCRIPT.replace("--ks-pass pass:fixture-store-value", "--ks-pass=pass:fixture-store-value"),
            SCRIPT.replace("--key-pass pass:fixture-key-value", "--key-pass 'unterminated"),
            SCRIPT + '"$APKSIGNER" sign out/unknown.apk\n',
            SCRIPT + '"$APKSIGNER" sign --ks-pass=pass:unknown\n',
            "# no signing command\n",
        ]
        for text in cases:
            with self.subTest(case=cases.index(text)), self.assertRaises(sdt.DeviceTestError) as caught:
                sdt.parse_trusted_signing_config(text)
            self.assertNotIn("fixture-", str(caught.exception))
            self.assertNotIn("conflicting-placeholder", str(caught.exception))

    def test_single_debug_signer_required_not_release(self):
        self.assertEqual(sdt.verified_certificate(VERIFY), sdt.CERTIFICATE)
        for text in (VERIFY.replace("Number of signers: 1", "Number of signers: 2"),
                     VERIFY + "Signer #2 certificate SHA-256 digest: " + sdt.CERTIFICATE + "\n",
                     VERIFY.replace(sdt.CERTIFICATE, "9a6d86924f7071a39558596be0e0cb80176e41188b550a0e88f8f57eeeba2fa8"),
                     VERIFY.replace("Signer #1", "Signer #2"), VERIFY.replace("Number of signers: 1\n", "")):
            with self.subTest(output=text[:25]), self.assertRaises(sdt.DeviceTestError):
                sdt.verified_certificate(text)

    def test_process_failures_never_expose_command_or_configuration(self):
        result = subprocess.CompletedProcess(["secret-alias"], 1, "sensitive stdout secret-password")
        with mock.patch.object(sdt.subprocess, "run", return_value=result), self.assertRaises(sdt.DeviceTestError) as caught:
            sdt.run_tool(["java", "secret-alias"], "Signing")
        self.assertNotIn("secret", str(caught.exception))
        self.assertNotIn("sensitive", str(caught.exception))
        for error in (subprocess.TimeoutExpired(["secret-password"], 120), subprocess.CalledProcessError(1, ["secret-password"], output="secret-alias")):
            with mock.patch.object(sdt.subprocess, "run", side_effect=error), self.assertRaises(sdt.DeviceTestError) as caught:
                sdt.run_tool(["java", "secret-password"], "Signing")
            self.assertNotIn("secret", str(caught.exception))

    def test_subprocess_is_no_shell_no_stdin_and_java_injection_env_removed(self):
        observed = {}
        def subprocess_fixture(command, **kwargs):
            observed.update(kwargs)
            observed["env"] = dict(kwargs["env"])
            return subprocess.CompletedProcess(command, 0, "fixture output")
        env = {"JAVA_TOOL_OPTIONS": "fixture-injection", "_JAVA_OPTIONS": "fixture-injection", "JDK_JAVA_OPTIONS": "fixture-injection", "CLASSPATH": "fixture-injection", "XPAN_DEVICE_TEST_STORE_PASS": "fixture-store-value"}
        with mock.patch.object(sdt.subprocess, "run", side_effect=subprocess_fixture):
            self.assertEqual(sdt.run_tool(["java.exe", "-jar", "apksigner.jar"], "Signing", env), "fixture output")
        self.assertFalse(observed["shell"])
        self.assertEqual(observed["stdin"], subprocess.DEVNULL)
        self.assertEqual(observed["cwd"], sdt.ROOT)
        self.assertEqual(observed["env"], {"XPAN_DEVICE_TEST_STORE_PASS": "fixture-store-value"})
        self.assertEqual(env["JAVA_TOOL_OPTIONS"], "fixture-injection")  # Caller/environment not mutated.


class SigningFlowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="xpan-device-test-tests-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.build = self.root / "_tmp/build/hook-fixture"
        self.script = self.root / "trusted-script/build.sh"
        self.key = self.script.parent / "out/debug.keystore"
        self.sdk = self.root / "sdk"
        self.java = self.root / "jdk/bin/java.exe"
        self.dex = minimal_dex()
        self.apk = apk_bytes(self.dex)
        self.calls = []
        self.badging, self.verify = BADGING, VERIFY
        write(self.root / "hook/XpanHook.java", b"package com.xpanport.hook; class XpanHook {}\n")
        manifest = b'<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.xpanport.hook" android:versionCode="21" android:versionName="2.1"/>\n'
        write(self.root / "hook/AndroidManifest.xml", manifest)
        write(self.root / "hook/assets/xposed_init", b"com.xpanport.hook.XpanHook\n")
        write(self.build / "dex/classes.dex", self.dex)
        write(self.build / "XpanHook-aligned.apk", self.apk)
        write(self.script, SCRIPT.encode())
        write(self.key, b"synthetic placeholder; never a real keystore")
        for path in (self.java, self.sdk / "build-tools/35.0.0/lib/apksigner.jar", self.sdk / "build-tools/35.0.0/aapt2.exe", self.sdk / "build-tools/35.0.0/zipalign.exe"):
            write(path, b"placeholder executable; never run")
        write(self.sdk / "platforms/android-35/android.jar", b"synthetic Android compile-only API")
        write(self.root / "_tmp/api-82.jar", b"synthetic Xposed compile-only API")
        self.report = {
            "sources": {"hook\\XpanHook.java": sha((self.root / "hook/XpanHook.java").read_bytes())},
            "android_api_sha256": sha((self.sdk / "platforms/android-35/android.jar").read_bytes()),
            "xposed_api_sha256": sha((self.root / "_tmp/api-82.jar").read_bytes()),
            "dex_sha256": sha(self.dex), "defined_classes": ["Lcom/xpanport/hook/XpanHook;"],
            "javac_version": "synthetic fixture", "mode": "unsigned", "manifest_sha256": sha(manifest),
            "xposed_entry_sha256": sha(b"com.xpanport.hook.XpanHook\n"), "unsigned_apk_sha256": sha(self.apk),
        }
        self.save_report()
        for patch in (mock.patch.object(sdt, "ROOT", self.root),
                      mock.patch.object(sdt, "TRUSTED_SCRIPT", self.script),
                      mock.patch.object(sdt, "KEYSTORE", self.key),
                      mock.patch.object(sdt, "run_tool", side_effect=self.tool_fixture),
                      mock.patch.dict(os.environ, {"ANDROID_SDK_ROOT": str(self.sdk), "JAVA_HOME": str(self.java.parents[1]), "XPAN_XPOSED_API": str(self.root / "_tmp/api-82.jar")})):
            patch.start()
            self.addCleanup(patch.stop)

    def save_report(self):
        write(self.build / "provenance.json", (json.dumps(self.report) + "\n").encode())

    def tool_fixture(self, command, step, env=None):
        command = [str(arg) for arg in command]
        self.calls.append((command, dict(env) if env is not None else None))
        if "sign" in command:
            write(Path(command[command.index("--out") + 1]), apk_bytes(self.dex, {"META-INF/FIXTURE.SF": b"synthetic signature only"}))
            return ""
        if "verify" in command:
            return self.verify
        if "badging" in command:
            return self.badging
        if "-c" in command:
            return ""
        raise AssertionError("Unexpected tool in synthetic fixture")

    def sign(self):
        return sdt.sign_device_test(self.build, sdt.ACK)

    def assert_no_sign(self):
        self.assertFalse(any("sign" in call[0] for call in self.calls))

    def test_fresh_output_minimal_provenance_and_no_secrets_or_key_payload(self):
        before = copy.deepcopy(self.report)
        output = self.sign()
        self.assertTrue(output.name.startswith("device-test-"))
        self.assertEqual(output.parent, self.build.parent)
        self.assertEqual({p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()}, {"XpanHook.apk", "dex/classes.dex", "provenance.json"})
        report = json.loads((output / "provenance.json").read_bytes())
        self.assertEqual(set(report) - set(before), {"apk_sha256", "certificate_sha256", "device_test_only"})
        self.assertEqual(report["mode"], "signed-device-test")
        self.assertIs(report["device_test_only"], True)
        self.assertEqual(report["certificate_sha256"], sdt.CERTIFICATE)
        for name in before.keys() - {"mode"}:
            self.assertEqual(report[name], before[name])
        self.assertEqual(report["apk_sha256"], sha((output / "XpanHook.apk").read_bytes()))
        self.assertEqual((output / "dex/classes.dex").read_bytes(), self.dex)
        self.assertEqual((self.build / "XpanHook-aligned.apk").read_bytes(), self.apk)
        for path in output.rglob("*"):
            if path.is_file():
                for secret in (b"fixture-alias", b"fixture-store-value", b"fixture-key-value", b"debug.keystore"):
                    self.assertNotIn(secret, path.read_bytes())
        sign_command, env = next(call for call in self.calls if "sign" in call[0])
        self.assertEqual(sign_command[0:4], [str(self.java), "-jar", str(self.sdk / "build-tools/35.0.0/lib/apksigner.jar"), "sign"])
        self.assertEqual(sign_command[sign_command.index("--ks") + 1], str(self.key.absolute()))
        self.assertEqual(sign_command[sign_command.index("--ks-pass") + 1], "env:XPAN_DEVICE_TEST_STORE_PASS")
        self.assertEqual(sign_command[sign_command.index("--key-pass") + 1], "env:XPAN_DEVICE_TEST_KEY_PASS")
        self.assertNotIn("pass:fixture-store-value", sign_command)
        self.assertEqual(env["XPAN_DEVICE_TEST_STORE_PASS"], "fixture-store-value")
        self.assertEqual(env["XPAN_DEVICE_TEST_KEY_PASS"], "fixture-key-value")
        self.assertEqual(sum("sign" in call[0] for call in self.calls), 1)

    def test_cli_success_prints_only_nonsecret_summary(self):
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            sdt.main(["--build-dir", str(self.build), "--device-test-ack", sdt.ACK])
        summary = stdout.getvalue()
        self.assertIn("SIGNED/DEVICE TEST ONLY", summary)
        self.assertIn("separate user confirmation", summary)
        for value in ("fixture-alias", "fixture-store-value", "fixture-key-value", "debug.keystore"):
            self.assertNotIn(value, summary)

    def test_cli_failure_does_not_print_signer_command_or_error_values(self):
        stderr = io.StringIO()
        # main also suppresses unexpected input exceptions, not just tool errors.
        with mock.patch.object(sdt, "sign_device_test", side_effect=OSError("fixture-key-value fixture-alias fixture-store-value")), mock.patch("sys.stderr", stderr), self.assertRaises(SystemExit) as caught:
            sdt.main(["--build-dir", str(self.build), "--device-test-ack", sdt.ACK])
        self.assertEqual(caught.exception.code, 1)
        for value in ("fixture-alias", "fixture-store-value", "fixture-key-value"):
            self.assertNotIn(value, stderr.getvalue())
        self.assertIn("sensitive details suppressed", stderr.getvalue())
        self.assert_no_sign()

    def test_missing_ack_denies_before_read_or_sign(self):
        with self.assertRaises(sdt.DeviceTestError):
            sdt.sign_device_test(self.build, "future-uninstall-not-authorized")
        self.assert_no_sign()

    def test_stale_source_or_added_source_denied(self):
        source = self.root / "hook/XpanHook.java"
        original = source.read_bytes()
        for index, mutate in enumerate((lambda: write(source, b"changed"),
                                        lambda: write(self.root / "hook/New.java", b"added"))):
            write(source, original)
            with self.subTest(case=index), self.assertRaises(sdt.DeviceTestError):
                mutate()
                self.sign()
        self.assert_no_sign()

    def test_missing_existing_key_denied_without_creating_or_guessing(self):
        self.key.unlink()  # Synthetic fixture only.
        with self.assertRaises(sdt.DeviceTestError):
            self.sign()
        self.assertFalse(self.key.exists())
        self.assert_no_sign()
        self.assertEqual(list(self.build.parent.glob("device-test-*")), [])

    def test_unsigned_hash_mismatch_denied(self):
        self.report["unsigned_apk_sha256"] = "0" * 64
        self.save_report()
        with self.assertRaises(sdt.DeviceTestError):
            self.sign()
        self.assert_no_sign()

    def test_wrong_source_manifest_and_entry_pin_denied(self):
        self.report["manifest_sha256"] = "0" * 64
        self.save_report()
        with self.assertRaises(sdt.DeviceTestError):
            self.sign()
        self.assert_no_sign()

    def test_invalid_dex_checksum_denied_even_if_hash_pin_updated(self):
        broken = bytearray(self.dex)
        broken[8] ^= 1
        self.dex = bytes(broken)
        self.apk = apk_bytes(self.dex)
        write(self.build / "dex/classes.dex", self.dex)
        write(self.build / "XpanHook-aligned.apk", self.apk)
        self.report.update(dex_sha256=sha(self.dex), unsigned_apk_sha256=sha(self.apk))
        self.save_report()
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.sign()
        self.assert_no_sign()

    def test_key_or_config_payload_rejected(self):
        for name in ("debug.keystore", "assets/build.sh", "META-INF/EXISTING.RSA", "classes2.dex"):
            with self.subTest(entry=name):
                data = apk_bytes(self.dex, {name: b"placeholder forbidden payload"})
                write(self.build / "XpanHook-aligned.apk", data)
                self.report["unsigned_apk_sha256"] = sha(data)
                self.save_report()
                with self.assertRaises(sdt.DeviceTestError):
                    self.sign()
        self.assert_no_sign()

    def test_apk_crc_corruption_denied(self):
        data = bytearray(self.apk)
        marker = b"synthetic aapt2 resource table"
        data[data.index(marker)] ^= 1
        write(self.build / "XpanHook-aligned.apk", bytes(data))
        self.report["unsigned_apk_sha256"] = sha(data)
        self.save_report()
        with self.assertRaises(zipfile.BadZipFile):
            self.sign()
        self.assert_no_sign()

    def test_wrong_actual_identity_denied_before_sign(self):
        self.badging = BADGING.replace("versionCode='21'", "versionCode='20'")
        with self.assertRaises(sdt.DeviceTestError):
            self.sign()
        self.assert_no_sign()

    def test_release_certificate_or_multiple_signers_denied_and_fresh_output_removed(self):
        for verify in (VERIFY.replace(sdt.CERTIFICATE, "9a6d86924f7071a39558596be0e0cb80176e41188b550a0e88f8f57eeeba2fa8"),
                       VERIFY.replace("Number of signers: 1", "Number of signers: 2")):
            with self.subTest(output=verify[:25]):
                self.verify = verify
                with self.assertRaises(sdt.DeviceTestError):
                    self.sign()
                self.assertEqual(list(self.build.parent.glob("device-test-*")), [])

    def test_changed_source_during_sign_denied_and_output_removed(self):
        original_tool = self.tool_fixture
        def changing_tool(command, step, env=None):
            result = original_tool(command, step, env)
            if "sign" in [str(arg) for arg in command]:
                write(self.root / "hook/XpanHook.java", b"changed while signing")
            return result
        with mock.patch.object(sdt, "run_tool", side_effect=changing_tool), self.assertRaises(sdt.DeviceTestError):
            self.sign()
        self.assertEqual(list(self.build.parent.glob("device-test-*")), [])

    def test_changed_unsigned_bytes_during_sign_denied_and_output_removed(self):
        original_tool = self.tool_fixture
        def changing_tool(command, step, env=None):
            result = original_tool(command, step, env)
            if "sign" in [str(arg) for arg in command]:
                write(self.build / "XpanHook-aligned.apk", self.apk + b"changed")
            return result
        with mock.patch.object(sdt, "run_tool", side_effect=changing_tool), self.assertRaises(sdt.DeviceTestError):
            self.sign()
        self.assertEqual(list(self.build.parent.glob("device-test-*")), [])

    def test_bad_config_denied_without_signing_attempt(self):
        write(self.script, (SCRIPT + SCRIPT.replace("fixture-alias", "contradictory-alias")).encode())
        with self.assertRaises(sdt.DeviceTestError):
            self.sign()
        self.assert_no_sign()

    def test_duplicate_and_unknown_provenance_fields_denied(self):
        for data in (b'{"mode":"unsigned","mode":"unsigned"}',
                     json.dumps(dict(self.report, release_approved=True)).encode(),
                     json.dumps(dict(self.report, mode="signed-device-test")).encode(),
                     json.dumps(dict(self.report, certificate_sha256=sdt.CERTIFICATE)).encode()):
            with self.subTest(case=data[:30]):
                write(self.build / "provenance.json", data)
                with self.assertRaises(sdt.DeviceTestError):
                    self.sign()
        self.assert_no_sign()


if __name__ == "__main__":
    unittest.main()
