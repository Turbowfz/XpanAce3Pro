"""Offline packager tests; python -I tests/test_package_module.py.

Fixtures use a checksum-valid minimal DEX, fake native bytes, and the real receipt
recipes with bounded STOCK/OUTPUTS injection. They never claim those bytes are
qualified firmware or a genuinely signed APK. Positive package tests are unsigned
only; identity tooling is isolated, and signed verification never gets fake green.
No signing, phone, network, git, or persistent package output.
"""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("package_module_tests_target", ROOT / "tools/package_module.py")
pm = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pm
SPEC.loader.exec_module(pm)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def json_write(path, value):
    write(path, pm.encoded(value))


def minimal_dex():
    """Real DEX header/checksums/tables, no executable hook implementation."""
    descriptor = b"Lcom/xpanport/hook/XpanHook;"
    # Header, one string_id, one type_id, one class_def, one string_data.
    string_at = 112 + 4 + 4 + 32
    data = bytearray(string_at + 1 + len(descriptor) + 1)
    data[:8] = b"dex\n035\0"
    struct.pack_into("<III", data, 32, len(data), 112, 0x12345678)
    struct.pack_into("<II", data, 56, 1, 112)
    struct.pack_into("<II", data, 64, 1, 116)
    struct.pack_into("<II", data, 96, 1, 120)
    struct.pack_into("<I", data, 112, string_at)
    struct.pack_into("<I", data, 116, 0)
    struct.pack_into("<I", data, 120, 0)
    data[string_at] = len(descriptor)
    data[string_at + 1:] = descriptor + b"\0"
    data[12:32] = hashlib.sha1(data[32:]).digest()
    struct.pack_into("<I", data, 8, zlib.adler32(data[12:]) & 0xffffffff)
    return bytes(data)


def apk_bytes(dex, entry=b"com.xpanport.hook.XpanHook\n", extra=None):
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("AndroidManifest.xml", b"synthetic binary XML; tool identity is separately isolated")
        archive.writestr("classes.dex", dex)
        archive.writestr("assets/xposed_init", entry)
        for name, value in (extra or {}).items():
            archive.writestr(name, value)
    return target.getvalue()


class PackagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="xpan-package-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(pm, "ROOT", self.root).start()
        # Only this narrow tool boundary is bypassed for synthetic APK identity.
        self.identity = mock.patch.object(pm, "verify_identity", return_value=None).start()
        self.signature = mock.patch.object(pm, "verify_signature", side_effect=pm.PackageError("not a verified signed fixture")).start()
        self.module = self.root / "module"
        self.changelog = b"# Changelog\n\n## v2.1\n- Development.\n\n## v1.3\n- High pixel.\n\n## v1.2\n- Sizes.\n\n## v1.1\n- Fixes.\n\n## v1.0\n- Initial.\n"
        write(self.root / "changelog.md", self.changelog)
        write(self.module / "module.prop", b"id=XpanAce3Pro\nname=Synthetic fixture\nversion=v2.1\nversionCode=21\n")
        self.script = b"#!/system/bin/sh\n# synthetic source; not an installer\nexit 0\n"
        for name in pm.ROOT_SCRIPTS:
            write(self.module / name, self.script)
        self.wrapper = b'''#!/system/bin/sh
MODDIR=${0%/*}
sh "$MODDIR/runtime.sh" uninstall "$MODDIR" ||
    printf '%s\\n' 'runtime teardown incomplete; APK ownership checked separately' >&2
. "$MODDIR/payload/uninstall-apk.sh"
'''
        # Read trusted project source as data; never run the APK ownership helper.
        self.helper = (ROOT / "hook/uninstall.sh").read_bytes()
        write(self.module / "uninstall.sh", self.wrapper)
        write(self.root / "hook/uninstall.sh", self.helper)
        write(self.root / "hook/XpanHook.java", b"package com.xpanport.hook; class XpanHook {}\n")
        write(self.root / "hook/CameraProfile.java", b"package com.xpanport.hook; class CameraProfile {}\n")
        self.manifest = b'''<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
 package="com.xpanport.hook" android:versionCode="21" android:versionName="2.1" />
'''
        write(self.root / "hook/AndroidManifest.xml", self.manifest)
        write(self.root / "hook/assets/xposed_init", b"com.xpanport.hook.XpanHook\n")
        # Synthetic stand-ins for the bundled device-stock C17 completion; the
        # production pins require the exact phone bytes (tools/package_module.py).
        self.op_compat = {
            "payload/op-compat/framework/synthetic-unit.jar": b"synthetic C17 unit fixture\n",
            "payload/op-compat/framework/synthetic-adapter.jar": b"synthetic C17 adapter fixture\n",
        }
        mock.patch.object(pm, "OP_COMPAT", {
            name: (len(data), sha(data)) for name, data in self.op_compat.items()}).start()
        for name, data in self.op_compat.items():
            write(self.root / name, data)
        self.build = self.root / "_tmp/build/hook-synthetic"
        self.dex = minimal_dex()
        self.apk = apk_bytes(self.dex)
        write(self.build / "dex/classes.dex", self.dex)
        write(self.build / "XpanHook-aligned.apk", self.apk)
        self.report = {
            "sources": {f"hook\\{p.name}": sha(p.read_bytes()) for p in (self.root / "hook").glob("*.java")},
            "android_api_sha256": "1" * 64, "xposed_api_sha256": "2" * 64,
            "dex_sha256": sha(self.dex), "defined_classes": pm.bh.dex_classes(self.dex),
            "javac_version": "synthetic fixture, never a production build", "mode": "unsigned",
            "manifest_sha256": sha(self.manifest),
        }
        self.save_report()
        self.profiles = self.root / "_tmp/profile-build"
        self.stock, self.outputs, self.receipts = {}, {}, {}
        self.profile_data = {}
        for platform in ("c16", "c17"):
            self.stock[platform], self.outputs[platform] = {}, {}
            records = {}
            for name in pm.mp.FILES:
                old = f"fake stock bytes {platform}/{name}\n".encode()
                new = old if platform == "c16" and name == pm.mp.FILES[2] else f"fake recipe bytes {platform}/{name}\n".encode()
                self.stock[platform][name] = len(old), sha(old)
                self.outputs[platform][name] = len(new), sha(new)
                path = f"payload/profiles/{platform}/{name}"
                self.profile_data[path] = new
                write(self.profiles / path, new)
                write(self.profiles / "originals" / platform / name, old)
                records[name] = {
                    "original": {"size": len(old), "sha256": sha(old), "path": f"originals/{platform}/{name}"},
                    "output": {"size": len(new), "sha256": sha(new), "path": path},
                    "install_allowed_sha256": sorted({sha(old), sha(new)}),
                    "unrelated_semantics_preserved": True, "double_patch_same_bytes": True, "semantic_changes": [],
                }
            receipt = {
                **pm.mp.TARGETS[platform], "profile_id": platform, "files": records,
                "native_picture_size": [4096, 1512],
                "required_java_getter_overrides": pm.mp.getter_overrides() if platform == "c16" else [],
                "encrypted_vendor_unchanged": platform == "c16", "native_binary_replacements": [],
                "provenance": {"schema_sha256": pm.mp.cp.SCHEMA_SHA256, "hal_tested": False,
                               "unit_pb_shared_original_bytes": True},
            }
            self.receipts[platform] = receipt
            write(self.profiles / f"payload/profiles/{platform}/profile.props", pm.mp.profile_props(platform, receipt))
            json_write(self.profiles / f"profiles/{platform}/receipt.json", pm.mp.asset_receipt(platform, receipt))
        self.profile_manifest = {
            "manifest_version": 1, "recipe_id": pm.RECIPE, "module_version": "2.1", "module_version_code": 21,
            "profiles": self.receipts,
            "sdk_app_evidence": {name: {"size": pin[0], "sha256": pin[1]} for name, pin in pm.mp.EVIDENCE.items()},
            "runtime_policies_not_implemented_by_profiles": {"final_output_width": 8192, "zoom_ratios": [1.0],
                "hasselblad_ui": True, "watermark": True, "xdr_ratio_minimum": 8.0}, "hal_tested": False,
        }
        self.save_manifest()
        # Bounded injection: only pins for synthetic profile bytes, no qualifies,
        # DEX validation, archive validation, signing, or source checks bypassed.
        mock.patch.object(pm.mp, "STOCK", self.stock).start()
        mock.patch.object(pm.mp, "OUTPUTS", self.outputs).start()
        self.output = self.root / "_tmp/package-development" / pm.FILENAME
        self.output.parent.mkdir()

    def save_report(self):
        json_write(self.build / "provenance.json", self.report)

    def save_manifest(self):
        json_write(self.profiles / "manifest.json", self.profile_manifest)

    def pack(self):
        return pm.pack(self.build, self.profiles, self.output, unsigned_development=True)

    def rejected(self, call=None):
        with self.assertRaises((pm.PackageError, zipfile.BadZipFile, KeyError, ValueError)):
            (call or self.pack)()
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.output.parent.glob(".xpan-package-*")))

    def claim_signed_without_signing(self):
        self.report.update(mode="signed", apk_sha256=sha(self.apk), certificate_sha256=pm.CERTIFICATE)
        self.save_report()
        write(self.build / "XpanHook.apk", self.apk)

    def claim_device_test_without_signing(self):
        self.report.update(mode="signed-device-test", apk_sha256=sha(self.apk),
                           certificate_sha256=pm.DEVICE_TEST_CERTIFICATE, device_test_only=True)
        self.save_report()
        write(self.build / "XpanHook.apk", self.apk)

    def test_device_test_requires_explicit_mode_and_independent_verification(self):
        self.claim_device_test_without_signing()
        self.rejected(lambda: pm.pack(self.build, self.profiles, self.output))
        self.signature.assert_not_called()
        self.rejected(lambda: pm.pack(self.build, self.profiles, self.output, signed_device_test=True))
        self.signature.assert_called_once_with(self.build / "XpanHook.apk", pm.DEVICE_TEST_CERTIFICATE)

    def test_device_test_wrong_certificate_or_missing_warning_is_rejected(self):
        self.claim_device_test_without_signing()
        for key, value in (("certificate_sha256", pm.CERTIFICATE), ("device_test_only", False)):
            with self.subTest(key=key):
                original = self.report[key]
                self.report[key] = value
                self.save_report()
                self.rejected(lambda: pm.pack(self.build, self.profiles, self.output, signed_device_test=True))
                self.report[key] = original
        self.signature.assert_not_called()

    def test_device_test_module_retains_identity_but_never_advertises_release_update(self):
        data = b"id=XpanAce3Pro\nversion=v2.1\nversionCode=21\nupdateJson=https://example.invalid/update.json\ndescription=fixture\n"
        marked = pm.device_test_module(data)
        identity = pm.props(marked, "module.prop")
        self.assertEqual(identity["id"], "XpanAce3Pro")
        self.assertEqual(identity["version"], "v2.1")
        self.assertEqual(identity["versionCode"], "21")
        self.assertNotIn("updateJson", identity)
        self.assertIn("仅本机验收", identity["description"])
        self.assertEqual(data.splitlines()[3], b"updateJson=https://example.invalid/update.json")

    def test_development_modes_are_mutually_exclusive(self):
        self.rejected(lambda: pm.pack(self.build, self.profiles, self.output,
                                     unsigned_development=True, signed_device_test=True))
        self.signature.assert_not_called()

    def test_release_cannot_use_a_device_test_marker(self):
        self.claim_signed_without_signing()
        self.report["device_test_only"] = True
        self.save_report()
        self.rejected(lambda: pm.pack(self.build, self.profiles, self.output))
        self.signature.assert_not_called()

    def test_unsigned_bundle_exact_whitelist_permissions_and_noninstallable_provenance(self):
        result = self.pack()
        self.assertEqual(result.name, "XpanAce3Pro-v2.1.zip")
        self.signature.assert_not_called()
        self.identity.assert_called_once_with(self.build / "XpanHook-aligned.apk")
        with zipfile.ZipFile(result) as archive:
            self.assertEqual(set(archive.namelist()), pm.archive_names())
            self.assertEqual(len(archive.namelist()), len(set(archive.namelist())))
            self.assertEqual(archive.read("skip_mount"), b"")
            self.assertEqual(archive.read("changelog.md"), self.changelog)
            self.assertEqual(archive.read(pm.UPDATER), b"#MAGISK\n")
            self.assertEqual(archive.read(pm.BOOTSTRAP), pm.UNSIGNED_INSTALLER)
            for name in pm.SCRIPTS:
                script = archive.read(name)
                self.assertEqual(script, pm.UNSIGNED_SCRIPT)
                self.assertIn(b"NOT INSTALLABLE/UNSIGNED", script)
                self.assertNotIn(b"pm install", script)
                self.assertNotIn(b"runtime.sh", script)
            for info in archive.infolist():
                self.assertEqual(info.create_system, 3)
                self.assertEqual(info.external_attr >> 16, pm.mode_for(info.filename))
                self.assertEqual(stat.S_IFMT(info.external_attr >> 16), stat.S_IFREG)
                self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED)
            provenance = json.loads(archive.read("provenance.json"))
            self.assertEqual(provenance["version"], "2.1")
            self.assertEqual(provenance["version_code"], 21)
            self.assertEqual(provenance["status"], "NOT INSTALLABLE/UNSIGNED")
            self.assertIs(provenance["installable"], False)
            self.assertIsNone(provenance["build"]["certificate_sha256"])
            self.assertEqual(provenance["build"]["apk_sha256"], sha(self.apk))
            self.assertNotIn("apk_sha256", self.report)
            self.assertEqual(provenance["profile_manifest_sha256"], sha((self.profiles / "manifest.json").read_bytes()))
            self.assertEqual(provenance["profile_manifest_sha256"], provenance["latest_profile_manifest_sha256"])
            self.assertNotIn("payload/lifecycle.inputs", archive.namelist())
            self.assertEqual(set(provenance["entries"]), pm.archive_names() - {"provenance.json"})
            self.assertIn(pm.APK_UNINSTALL, archive.namelist())
            self.assertEqual(archive.read("uninstall.sh"), pm.UNSIGNED_SCRIPT)
            self.assertEqual(archive.read(pm.APK_UNINSTALL), pm.UNSIGNED_SCRIPT)
            self.assertEqual(provenance["inputs"]["changelog.md"], {"size": len(self.changelog), "sha256": sha(self.changelog)})
            self.assertEqual(provenance["entries"]["changelog.md"]["unix_mode"], "100644")
            self.assertEqual(provenance["inputs"]["module/uninstall.sh"]["sha256"], sha(self.wrapper))
            self.assertEqual(provenance["inputs"]["hook/uninstall.sh"]["sha256"], sha(self.helper))
            self.assertEqual(provenance["entries"][pm.APK_UNINSTALL]["unix_mode"], "100755")
            self.assertEqual(provenance["entries"]["uninstall.sh"]["unix_mode"], "100755")
            self.assertEqual(provenance["entries"][pm.APK_UNINSTALL]["sha256"], sha(pm.UNSIGNED_SCRIPT))
            for name, pin in provenance["entries"].items():
                self.assertEqual(pin["size"], len(archive.read(name)))
                self.assertEqual(pin["sha256"], sha(archive.read(name)))
            for platform in ("c16", "c17"):
                data = archive.read(f"payload/profiles/{platform}/profile.props")
                self.assertEqual(len(pm.props(data, "profile")), 12)
                self.assertNotIn(b"\r", data)
            for path, data in self.profile_data.items():
                self.assertEqual(archive.read(path), data)
            for name in archive.namelist():
                self.assertFalse(any(x in name.split("/") for x in ("originals", "keys", "inputs", "system")))

    def test_default_requires_signed_even_when_unsigned_apk_exists(self):
        self.rejected(lambda: pm.pack(self.build, self.profiles, self.output))
        self.identity.assert_not_called()
        self.signature.assert_not_called()

    def test_forged_signed_receipt_never_substitutes_for_signature_verification(self):
        self.claim_signed_without_signing()
        self.rejected(lambda: pm.pack(self.build, self.profiles, self.output))
        self.signature.assert_called_once_with(self.build / "XpanHook.apk")

    def test_signed_wrong_certificate_rejected_before_verifier(self):
        self.claim_signed_without_signing()
        self.report["certificate_sha256"] = "0" * 64
        self.save_report()
        self.rejected(lambda: pm.pack(self.build, self.profiles, self.output))
        self.signature.assert_not_called()

    def test_explicit_unsigned_does_not_relabel_signed_or_compile_only_build(self):
        for mode in ("signed", "compile-only", "development", None):
            with self.subTest(mode=mode):
                self.report["mode"] = mode
                self.save_report()
                self.rejected()
        self.report["mode"] = "unsigned"
        self.report["certificate_sha256"] = pm.CERTIFICATE
        self.save_report()
        self.rejected()

    def test_no_overwrite_before_read_or_verification(self):
        write(self.output, b"existing user package")
        with mock.patch.object(pm, "module_entries", side_effect=AssertionError("must not read")):
            with self.assertRaises(pm.PackageError):
                self.pack()
        self.assertEqual(self.output.read_bytes(), b"existing user package")
        self.identity.assert_not_called()

    def test_bundled_op_compat_bytes_must_match_the_stock_pin(self):
        path = self.root / "payload/op-compat/framework/synthetic-unit.jar"
        path.write_bytes(b"relabelled or donor bytes are not accepted\n")
        self.rejected()
        path.write_bytes(self.op_compat["payload/op-compat/framework/synthetic-unit.jar"])
        self.pack()

    def test_output_must_be_exact_version_name_in_new_nonfinal_tmp_directory(self):
        for output in (self.root / pm.FILENAME, self.root / "desktopfinal" / pm.FILENAME,
                       self.root / "_tmp" / pm.FILENAME,
                       self.root / "_tmp" / "desktopfinal" / pm.FILENAME,
                       self.output.with_name("XpanAce3Pro-v2.1-dev.zip"),
                       self.output.with_name("XpanAce3Pro-v2.0.zip")):
            with self.subTest(output=output):
                output.parent.mkdir(exist_ok=True)
                with self.assertRaises(pm.PackageError):
                    pm.pack(self.build, self.profiles, output, True)
                self.assertFalse(output.exists())
        self.identity.assert_not_called()

    def test_missing_input_files_fail_closed(self):
        paths = [self.module / name for name in ("module.prop", *pm.ROOT_SCRIPTS)]
        paths += [self.root / "changelog.md", self.root / "hook/uninstall.sh", self.build / "provenance.json", self.build / "dex/classes.dex",
                  self.build / "XpanHook-aligned.apk", self.profiles / "manifest.json"]
        paths += [self.profiles / path for path in self.profile_data]
        paths += [self.profiles / f"payload/profiles/{p}/profile.props" for p in ("c16", "c17")]
        paths += [self.profiles / f"profiles/{p}/receipt.json" for p in ("c16", "c17")]
        for path in paths:
            with self.subTest(path=path):
                data = path.read_bytes()
                path.unlink()
                try:
                    self.rejected()
                finally:
                    write(path, data)

    def test_changelog_requires_complete_lf_text_and_rejects_changes_during_pack(self):
        for data in (b"", b"# Missing newline", b"# CRLF\r\n", b"\xff\n", b"# Bad\x01\n"):
            with self.subTest(data=data):
                write(self.root / "changelog.md", data)
                self.rejected()
        write(self.root / "changelog.md", self.changelog)
        original = pm.build_input

        def changed(*args, **kwargs):
            value = original(*args, **kwargs)
            write(self.root / "changelog.md", self.changelog + b"- Changed during build.\n")
            return value

        with mock.patch.object(pm, "build_input", side_effect=changed):
            self.rejected()

    def test_forbidden_overlay_keys_obsolete_and_extra_inputs_rejected(self):
        cases = [(self.module, "system/odm/etc/camera/config/old"), (self.module, "system.prop"),
                 (self.module, "crop_watch.sh"), (self.module, "unknown.sh"),
                 (self.build, "keys/private.jks"), (self.build, "secret.pem"),
                 (self.build, "payload/enabler.dex"), (self.build, "payload/oc_1.enc"),
                 (self.profiles, "system/old"), (self.profiles, "inputs/extraction.log"),
                 (self.profiles, "payload/lifecycle.inputs"), (self.profiles, "keys/key.p12"),
                 (self.profiles, "payload/profiles/c17/unexpected")]
        for directory, name in cases:
            with self.subTest(name=name):
                path = directory / name
                write(path, b"untrusted old input")
                try:
                    self.rejected()
                finally:
                    path.unlink()
                    parent = path.parent
                    while parent != directory and not list(parent.iterdir()):
                        parent.rmdir()
                        parent = parent.parent

    def test_current_source_changed_removed_added_or_hash_alias_rejected(self):
        path = self.root / "hook/XpanHook.java"
        original = path.read_bytes()
        write(path, original + b"// changed\n")
        self.rejected()
        write(path, original)
        added = self.root / "hook/Added.java"
        write(added, b"class Added {}\n")
        self.rejected()
        added.unlink()
        saved = self.report["sources"].pop("hook\\CameraProfile.java")
        self.save_report()
        self.rejected()
        self.report["sources"]["hook\\CameraProfile.java"] = saved
        self.report["sources"]["hook/XpanHook.java"] = sha(original)
        self.save_report()
        self.rejected()

    def test_stale_manifest_and_wrong_apk_source_version_rejected(self):
        write(self.root / "hook/AndroidManifest.xml", self.manifest + b"\n")
        self.rejected()
        for before, after in ((b'versionCode="21"', b'versionCode="20"'),
                              (b'versionName="2.1"', b'versionName="2.0"'),
                              (b'versionCode="21"', b'versionCode="13"'),
                              (b'versionName="2.1"', b'versionName="1.3"'),
                              (b'com.xpanport.hook', b'com.other.fake')):
            with self.subTest(after=after):
                changed = self.manifest.replace(before, after)
                write(self.root / "hook/AndroidManifest.xml", changed)
                self.report["manifest_sha256"] = sha(changed)
                self.save_report()
                self.rejected()
        self.identity.assert_not_called()

    def test_wrong_module_identity_version_duplicate_or_crlf_rejected(self):
        path = self.module / "module.prop"
        original = path.read_bytes()
        for data in (original.replace(b"id=XpanAce3Pro", b"id=Other"),
                     original.replace(b"v2.1", b"v2.0"), original.replace(b"versionCode=21", b"versionCode=20"),
                     original.replace(b"v2.1", b"v1.3"), original.replace(b"versionCode=21", b"versionCode=13"),
                     original + b"id=XpanAce3Pro\n", original.replace(b"\n", b"\r\n")):
            with self.subTest(data=data):
                write(path, data)
                self.rejected()
        write(path, original)
        write(self.module / "runtime.sh", self.script.replace(b"\n", b"\r\n"))
        self.rejected()

    def test_wrong_profile_recipe_evidence_version_target_or_props_rejected(self):
        original = copy.deepcopy(self.profile_manifest)
        variants = []
        for key, value in (("module_version", "2.0"), ("module_version_code", 20),
                           ("module_version", "1.3"), ("module_version_code", 13),
                           ("recipe_id", "caller-qualified"), ("hal_tested", True)):
            variant = copy.deepcopy(original)
            variant[key] = value
            variants.append(variant)
        variant = copy.deepcopy(original)
        variant["profiles"]["c17"]["camera_version"] = "7"
        variants.append(variant)
        variant = copy.deepcopy(original)
        variant["profiles"]["c16"]["fingerprint"] = "16"
        variants.append(variant)
        variant = copy.deepcopy(original)
        variant["sdk_app_evidence"]["sdk_jar"]["sha256"] = "0" * 64
        variants.append(variant)
        for variant in variants:
            with self.subTest(variant=variant):
                self.profile_manifest = variant
                self.save_manifest()
                self.rejected()
        self.profile_manifest = original
        self.save_manifest()
        path = self.profiles / "payload/profiles/c16/profile.props"
        original_props = path.read_bytes()
        for data in (original_props + b"qualified=true\n", original_props.replace(b"\n", b"\r\n"),
                     original_props.replace(b"camera_version=6.020.876", b"camera_version=6"),
                     original_props + b"model=PJX110\n"):
            write(path, data)
            self.rejected()

    def test_profile_payload_receipt_pins_not_caller_qualified(self):
        platform, name = "c17", pm.mp.FILES[2]
        path = self.profiles / f"payload/profiles/{platform}/{name}"
        old = path.read_bytes()
        new = b"caller invented byte recipe\n"
        write(path, new)
        self.rejected()
        record = self.profile_manifest["profiles"][platform]["files"][name]
        record["output"].update(size=len(new), sha256=sha(new))
        record["install_allowed_sha256"] = sorted({record["original"]["sha256"], sha(new)})
        self.save_manifest()
        write(self.profiles / f"payload/profiles/{platform}/profile.props", pm.mp.profile_props(platform, self.receipts[platform]))
        json_write(self.profiles / f"profiles/{platform}/receipt.json", pm.mp.asset_receipt(platform, self.receipts[platform]))
        self.rejected()  # Even latest local receipt does not redefine trusted OUTPUTS.
        self.assertNotEqual(new, old)

    def test_old_overlay_vendor_hash_never_qualifies_by_relabel(self):
        record = self.profile_manifest["profiles"]["c17"]["files"][pm.mp.FILES[2]]
        record["output"]["sha256"] = next(iter(pm.mp.cp.C16_OVERLAY_SHA256))
        record["install_allowed_sha256"] = sorted({record["original"]["sha256"], record["output"]["sha256"]})
        self.save_manifest()
        self.rejected()

    def test_stale_profile_manifest_compared_to_current_latest(self):
        copy_dir = self.root / "_tmp/copied-profile"
        shutil.copytree(self.profiles, copy_dir)
        value = copy.deepcopy(self.profile_manifest)
        value["qualification"] = "a newer current manifest"
        json_write(self.profiles / "manifest.json", value)
        self.rejected(lambda: pm.pack(self.build, copy_dir, self.output, True))

    def test_asset_receipt_tamper_and_duplicate_json_rejected(self):
        path = self.profiles / "profiles/c17/receipt.json"
        value = json.loads(path.read_bytes())
        value["sdk_sha256"] = "0" * 64
        json_write(path, value)
        self.rejected()
        for data in (b'{"mode":"unsigned","mode":"signed"}', b'{"mode": NaN}'):
            write(self.build / "provenance.json", data)
            self.rejected()

    def test_new_unsigned_and_xposed_entry_hash_fields_are_locked_when_present(self):
        self.report["unsigned_apk_sha256"] = sha(self.apk)
        self.report["xposed_entry_sha256"] = sha(b"com.xpanport.hook.XpanHook\n")
        self.save_report()
        self.pack()
        self.signature.assert_not_called()
        self.output.unlink()
        for field in ("unsigned_apk_sha256", "xposed_entry_sha256"):
            with self.subTest(field=field):
                original = self.report[field]
                self.report[field] = "0" * 64
                self.save_report()
                self.rejected()
                self.report[field] = original
        self.save_report()
        # Legacy unsigned reports are still safe: current APK SHA is computed,
        # recorded in package provenance and structurally checked again.
        self.report.pop("unsigned_apk_sha256")
        self.report.pop("xposed_entry_sha256")
        self.save_report()
        self.pack()
        with zipfile.ZipFile(self.output) as archive:
            report = json.loads(archive.read("provenance.json"))
            self.assertEqual(report["build"]["apk_sha256"], sha(self.apk))
            self.assertEqual(report["build"]["xposed_entry_sha256"], sha(b"com.xpanport.hook.XpanHook\n"))

    def test_new_hash_fields_cannot_supply_signed_apk_hash_or_fake_certificate(self):
        self.claim_signed_without_signing()
        self.report["unsigned_apk_sha256"] = sha(self.apk)
        self.report["xposed_entry_sha256"] = sha(b"com.xpanport.hook.XpanHook\n")
        self.report.pop("apk_sha256")
        self.save_report()
        self.rejected(lambda: pm.pack(self.build, self.profiles, self.output))
        self.signature.assert_not_called()
        self.report["apk_sha256"] = sha(self.apk)
        self.save_report()
        self.rejected(lambda: pm.pack(self.build, self.profiles, self.output))
        self.signature.assert_called_once_with(self.build / "XpanHook.apk")

    def test_apk_hash_and_dex_and_defined_classes_rechecked(self):
        self.report["apk_sha256"] = "0" * 64
        self.save_report()
        self.rejected()
        self.report["apk_sha256"] = sha(self.apk)
        self.report["dex_sha256"] = "0" * 64
        self.save_report()
        self.rejected()
        self.report["dex_sha256"] = sha(self.dex)
        self.report["defined_classes"] = ["Lcom/forged/Fake;"]
        self.save_report()
        self.rejected()
        self.report["defined_classes"] = pm.bh.dex_classes(self.dex)
        self.save_report()
        write(self.build / "dex/classes.dex", b"old dex")
        self.rejected()

    def test_apk_wrong_entry_multidex_traversal_v1_or_v2_signature_rejected(self):
        variants = [apk_bytes(self.dex, b"com.other.Fake\n"),
                    apk_bytes(self.dex, extra={"classes2.dex": self.dex}),
                    apk_bytes(self.dex, extra={"../escape": b"bad"}),
                    apk_bytes(self.dex, extra={"C:/escape": b"bad"}),
                    apk_bytes(self.dex, extra={"META-INF/FAKE.RSA": b"not a real signature"}),
                    self.apk + b"APK Sig Block 42"]
        for variant in variants:
            with self.subTest(length=len(variant)):
                write(self.build / "XpanHook-aligned.apk", variant)
                self.rejected()

    def test_apk_duplicate_and_corrupt_dex_rejected_without_verifier(self):
        duplicate = io.BytesIO(self.apk)
        with zipfile.ZipFile(duplicate, "a") as archive:
            # Suppress only the deliberate duplicate ZIP warning, not validation.
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                archive.writestr("classes.dex", self.dex)
        write(self.build / "XpanHook-aligned.apk", duplicate.getvalue())
        self.rejected()
        damaged = bytearray(self.dex)
        damaged[-2] ^= 1
        write(self.build / "XpanHook-aligned.apk", apk_bytes(bytes(damaged)))
        self.rejected()
        self.identity.assert_not_called()

    def test_unknown_build_assertions_and_missing_hashes_rejected(self):
        original = copy.deepcopy(self.report)
        for key in ("qualified", "signer_verified", "device_ready"):
            self.report = {**original, key: True}
            self.save_report()
            self.rejected()
        for key in ("android_api_sha256", "xposed_api_sha256", "dex_sha256", "manifest_sha256"):
            self.report = copy.deepcopy(original)
            self.report.pop(key)
            self.save_report()
            self.rejected()

    def test_verifier_detected_actual_apk_identity_failure_leaves_no_package(self):
        self.identity.side_effect = pm.PackageError("actual APK version mismatch")
        self.rejected()

    def test_input_changes_during_identity_validation_are_not_published(self):
        def change(_apk):
            write(self.build / "XpanHook-aligned.apk", self.apk + b"tamper")
        self.identity.side_effect = change
        self.rejected()

    def test_input_changes_before_publication_roll_back_only_new_output(self):
        real_validate = pm.validate_archive
        changed = False
        def validate(path, expected):
            nonlocal changed
            real_validate(path, expected)
            if not changed:
                changed = True
                source = self.root / "hook/XpanHook.java"
                write(source, source.read_bytes() + b"// concurrent change\n")
        with mock.patch.object(pm, "validate_archive", side_effect=validate):
            self.rejected()

    def test_input_changes_after_publication_roll_back_new_output(self):
        real_validate = pm.validate_archive
        def validate(path, expected):
            real_validate(path, expected)
            if path == self.output:
                source = self.root / "hook/XpanHook.java"
                write(source, source.read_bytes() + b"// concurrent change\n")
        with mock.patch.object(pm, "validate_archive", side_effect=validate):
            self.rejected()

    def test_output_race_never_overwrites_an_existing_package(self):
        real_validate = pm.validate_archive
        def validate(path, expected):
            real_validate(path, expected)
            write(self.output, b"racing user's package")
        with mock.patch.object(pm, "validate_archive", side_effect=validate):
            with self.assertRaises(FileExistsError):
                self.pack()
        self.assertEqual(self.output.read_bytes(), b"racing user's package")
        self.assertFalse(list(self.output.parent.glob(".xpan-package-*")))

    def test_unsigned_unaligned_fallback_still_structurally_verified(self):
        (self.build / "XpanHook-aligned.apk").rename(self.build / "XpanHook-unsigned.apk")
        self.pack()
        self.identity.assert_called_once_with(self.build / "XpanHook-unsigned.apk")
        self.signature.assert_not_called()

    def test_module_entries_signed_sources_and_skip_mount_are_not_old_customize(self):
        inputs = pm.Inputs()
        values = pm.module_entries(inputs)
        for name in pm.SCRIPTS:
            expected = self.wrapper if name == "uninstall.sh" else self.helper if name == pm.APK_UNINSTALL else self.script
            self.assertEqual(values[name], expected)
            self.assertEqual(pm.mode_for(name), 0o100755)
        self.assertEqual(sha(values[pm.APK_UNINSTALL]), sha((ROOT / "hook/uninstall.sh").read_bytes()))
        self.assertEqual(inputs.hashes[self.root / "hook/uninstall.sh"], (len(self.helper), sha(self.helper)))
        self.assertEqual(inputs.hashes[self.module / "uninstall.sh"], (len(self.wrapper), sha(self.wrapper)))
        runtime_call = b'sh "$MODDIR/runtime.sh" uninstall "$MODDIR"'
        helper_call = b'. "$MODDIR/payload/uninstall-apk.sh"'
        self.assertLess(values["uninstall.sh"].index(runtime_call), values["uninstall.sh"].index(helper_call))
        self.assertNotIn(b'exec ', values["uninstall.sh"])
        self.assertNotEqual(values["uninstall.sh"], values[pm.APK_UNINSTALL])
        self.assertEqual(values["skip_mount"], b"")
        write(self.module / "skip_mount", b"not an empty marker")
        with self.assertRaises(pm.PackageError):
            pm.module_entries(pm.Inputs())

    def test_missing_root_wrapper_never_falls_back_to_apk_only_uninstall(self):
        (self.module / "uninstall.sh").unlink()
        self.assertTrue((self.root / "hook/uninstall.sh").is_file())
        self.rejected()
        self.identity.assert_not_called()

    def test_uninstall_helper_change_during_packaging_rejected(self):
        def change(_apk):
            path = self.root / "hook/uninstall.sh"
            write(path, self.helper + b"# concurrent helper edit\n")
        self.identity.side_effect = change
        self.rejected()

    def test_latest_uninstall_wrapper_and_helper_dual_phase_source_contract(self):
        # Trusted project scripts are inspected as bytes only; no PM or teardown.
        wrapper_path = ROOT / "module/uninstall.sh"
        self.assertTrue(wrapper_path.is_file(), "runtime agent must supply the root uninstall wrapper")
        wrapper = wrapper_path.read_bytes()
        helper = (ROOT / "hook/uninstall.sh").read_bytes()
        runtime_call = b'sh "$MODDIR/runtime.sh" uninstall "$MODDIR"'
        helper_call = b'. "$MODDIR/payload/uninstall-apk.sh"'
        self.assertLess(wrapper.index(runtime_call), wrapper.index(helper_call))
        self.assertNotIn(b'exec sh "$MODDIR/payload/uninstall-apk.sh"', wrapper)
        self.assertNotIn(b'exec "$MODDIR/payload/uninstall-apk.sh"', wrapper)
        self.assertIn(b'MODDIR=${0%/*}', wrapper)
        self.assertIn(b'[ ! -L "$MODDIR/payload/uninstall-apk.sh" ]', wrapper)
        self.assertIn(b'[ "${0%/*}" = "$ACTIVE" ]', helper)
        self.assertIn(b'SCRIPT_SOURCE', helper)
        self.assertIn(b'$ACTIVE/payload/uninstall-apk.sh', helper)
        self.assertIn(b'cat "$SCRIPT_SOURCE"', helper)
        self.assertNotIn(b'cat "$0"', helper)
        for data in (wrapper, helper):
            self.assertTrue(data.startswith(b"#!/system/bin/sh\n"))
            self.assertNotIn(b"\r", data)

    def test_native_originals_not_packaged_or_read_and_pin_injection_is_bounded(self):
        # Real-world profile builder produces originals; the packager needs only
        # pinned hashes, never copies those raw bytes into the module archive.
        for platform in ("c16", "c17"):
            for name in pm.mp.FILES:
                write(self.profiles / f"originals/{platform}/{name}", b"not needed for packaging")
        self.pack()
        with zipfile.ZipFile(self.output) as archive:
            provenance = json.loads(archive.read("provenance.json"))
            self.assertFalse(any("originals/" in path for path in provenance["inputs"]))
        # Remove bounded OUTPUTS injection: fake payload cannot qualify against
        # the real recipe just by a fake receipt or a caller-qualified label.
        spec = importlib.util.spec_from_file_location("real_profile_pins_for_test", ROOT / "tools/make_profiles.py")
        real = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = real
        spec.loader.exec_module(real)
        with mock.patch.object(pm.mp, "OUTPUTS", real.OUTPUTS):
            with self.assertRaises(pm.PackageError):
                pm.profile_entries(pm.Inputs(), self.profiles)


class ArchiveAndVerifierTests(unittest.TestCase):
    def test_bootstrap_conventional_magisk_loader_and_no_archive_execution(self):
        self.assertTrue(pm.INSTALLER.startswith(b"#!/sbin/sh\n"))
        self.assertIn(b"OUTFD=$2\nZIPFILE=$3", pm.INSTALLER)
        self.assertIn(b". /data/adb/magisk/util_functions.sh", pm.INSTALLER)
        self.assertIn(b"install_module\nexit $?", pm.INSTALLER)
        for obsolete in (b"customize.sh", b"pm ", b"enabler", b"oc_1", b"unzip", b"eval"):
            self.assertNotIn(obsolete, pm.INSTALLER)
        self.assertNotIn(b"\r", pm.INSTALLER)
        self.assertEqual(pm.mode_for(pm.BOOTSTRAP), 0o100755)
        self.assertEqual(pm.mode_for(pm.UPDATER), 0o100644)

    def test_safe_zip_names_reject_traversal_drive_backslash_empty_components(self):
        for name in ("../bad", "/bad", "a/../../bad", "C:/bad", "a\\bad", "a//bad", "a/./b", "a/", "a\x00b"):
            with self.subTest(name=name), self.assertRaises(pm.PackageError):
                pm.safe_name(name)
        self.assertEqual(pm.safe_name("payload/profiles/c17/profile.props"), "payload/profiles/c17/profile.props")

    def test_validator_rejects_bad_names_duplicates_permissions_and_bytes(self):
        expected = {name: b"" for name in pm.archive_names()}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "archive.zip"
            for kind in ("duplicate", "path", "permissions", "extra", "bytes"):
                with self.subTest(kind=kind):
                    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                        for name in sorted(expected):
                            info = zipfile.ZipInfo(name)
                            info.create_system = 3
                            info.external_attr = pm.mode_for(name) << 16
                            info.compress_type = zipfile.ZIP_DEFLATED
                            if kind == "permissions" and name == "service.sh":
                                info.external_attr = 0o100644 << 16
                            archive.writestr(info, b"bad" if kind == "bytes" and name == "skip_mount" else b"")
                        if kind == "duplicate":
                            import warnings
                            with warnings.catch_warnings():
                                warnings.simplefilter("ignore", UserWarning)
                                archive.writestr("module.prop", b"")
                        if kind in ("path", "extra"):
                            archive.writestr("../escape" if kind == "path" else "system/old", b"bad")
                    with self.assertRaises(pm.PackageError):
                        pm.validate_archive(path, expected)

    def test_signature_verification_uses_verify_only_and_cannot_accept_wrong_or_ambiguous_cert(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tools = root / "tools"
            write(tools / "lib/apksigner.jar", b"synthetic tool path, never executed")
            write(root / "java/bin/java.exe", b"synthetic executable path, never executed")
            apk = root / "fixture.apk"
            write(apk, b"not signed")
            with mock.patch.object(pm, "sdk_tools", return_value=tools), mock.patch.dict(os.environ, {"JAVA_HOME": str(root / "java")}), \
                    mock.patch.object(pm, "run_verify") as run:
                for response in ("", "Signer #1 certificate SHA-256 digest: " + "0" * 64,
                                 "Signer #1 certificate SHA-256 digest: " + pm.CERTIFICATE + "\nSigner #2 certificate SHA-256 digest: " + pm.CERTIFICATE):
                    with self.subTest(response=response):
                        run.return_value = response
                        with self.assertRaises(pm.PackageError):
                            pm.verify_signature(apk)
                        command = run.call_args.args[0]
                        self.assertIn("verify", command)
                        self.assertNotIn("sign", command)
                        self.assertNotIn("--ks", command)
                        self.assertEqual(command[-1], apk)

    def test_identity_verifier_rejects_wrong_actual_apk_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = Path(tmp)
            write(tools / "aapt2.exe", b"never executed")
            with mock.patch.object(pm, "sdk_tools", return_value=tools), mock.patch.object(pm, "run_verify") as run:
                for output in ("package: name='com.xpanport.hook' versionCode='20' versionName='2.1'",
                               "package: name='com.xpanport.hook' versionCode='21' versionName='2.0'",
                               "package: name='com.xpanport.hook' versionCode='13' versionName='1.3'",
                               "package: name='com.fake' versionCode='21' versionName='2.1'"):
                    run.return_value = output
                    with self.assertRaises(pm.PackageError):
                        pm.verify_identity(tools / "fake.apk")
                run.return_value = "package: name='com.xpanport.hook' versionCode='21' versionName='2.1' platformBuildVersionName='15'\n"
                pm.verify_identity(tools / "fake.apk")
                self.assertEqual(run.call_args.args[0][1:3], ["dump", "badging"])

    def test_run_verifier_never_changes_cwd_to_input_and_has_timeout_and_check(self):
        result = mock.Mock(stdout="verification diagnostic")
        with mock.patch.object(pm.subprocess, "run", return_value=result) as run:
            self.assertEqual(pm.run_verify(["trusted-java", "verify", "untrusted.apk"]), result.stdout)
            kwargs = run.call_args.kwargs
            self.assertEqual(kwargs["cwd"], pm.ROOT)
            self.assertTrue(kwargs["check"])
            self.assertEqual(kwargs["timeout"], 120)
            self.assertNotIn("shell", kwargs)

    def test_symlink_guard_for_inputs_and_outputs_when_platform_allows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / "original"
            write(original, b"real")
            link = root / "link"
            try:
                link.symlink_to(original)
            except OSError:
                self.skipTest("local Windows permission does not allow symlink fixtures")
            with self.assertRaises(pm.PackageError):
                pm.Inputs().read(link)


if __name__ == "__main__":
    unittest.main(verbosity=2)
