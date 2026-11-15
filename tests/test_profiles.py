"""Offline profile tests; python -I tests/test_profiles.py.

Synthetic parser fixtures never qualify as stock. Optional real-assets tests use
local exact originals read-only; no raw firmware, keys, serials or private receipts
are embedded in tests. No phone access, writes, install or reboot.
"""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


mp = load("make_profiles", ROOT / "tools/make_profiles.py")
fixtures = load("profiles_parser_fixtures", ROOT / "tests/test_config_patch.py")
cp = mp.cp


def encoded(obj):
    return json.dumps(obj, indent=2).encode("utf-8")


class SyntheticTests(unittest.TestCase):
    def test_vendor_minimal_semantics_types_and_idempotence(self):
        before = fixtures.vendor_json()
        raw = encoded(before).replace(b'"future": {', b'"precision": 1.00000000000000000009, "future": {')
        output = mp.patch_vendor(raw)
        expected = copy.deepcopy(cp.JsonDocument(raw).obj)
        expected["file_data"][0]["Value"] = "1"
        expected["file_data"][1]["Value"] = "4096x1512"
        expected["file_data"].extend(copy.deepcopy(mp.CROPS))
        expected["file_data"].extend([
            {"VendorTag": mp.VERSION, "Type": "Int32", "Count": "1", "Value": "3"},
            {"VendorTag": mp.LEGACY, "Type": "Byte", "Count": "1", "Value": "0"},
        ])
        self.assertEqual(cp.JsonDocument(output).obj, expected)
        self.assertIn(b'1.00000000000000000009', output)
        self.assertEqual(mp.patch_vendor(output), output)
        self.assertEqual(before, fixtures.vendor_json())

    def test_wrong_key_duplicate_type_or_crop_refused(self):
        for kind in ("support-key", "version-key", "duplicate", "wrong-type", "conflicting-crop"):
            with self.subTest(kind=kind):
                obj = fixtures.vendor_json()
                if kind == "support-key":
                    obj["file_data"][0]["VendorTag"] = "com.oplus.feature.xpan.mode.support.wrong"
                elif kind == "version-key":
                    obj["file_data"].append({"VendorTag": mp.VERSION, "Type": "Byte", "Count": "1", "Value": "3"})
                elif kind == "duplicate":
                    obj["file_data"].append(copy.deepcopy(obj["file_data"][0]))
                elif kind == "wrong-type":
                    obj["file_data"][1]["Type"] = "Byte"
                else:
                    crop = copy.deepcopy(mp.CROPS[0])
                    crop["Value"] = "0,0,8192,3724"
                    obj["file_data"].append(crop)
                with self.assertRaises(cp.ConfigError):
                    mp.patch_vendor(encoded(obj))

    def test_synthetic_mislabel_and_security_library_never_qualify(self):
        synthetic = {mp.FILES[0]: encoded(fixtures.unit_json()),
                     mp.FILES[1]: fixtures.native_pb(),
                     mp.FILES[2]: encoded(fixtures.vendor_json())}
        for platform in ("c16", "c17", "C17", "PJX110"):
            with self.subTest(platform=platform), self.assertRaises(cp.ConfigError):
                mp.make_profile(platform, synthetic, b"synthetic schema")
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / "libOplusSecurity.so"
            library.write_bytes(b"a security library hash does not qualify a Vendor file")
            with self.assertRaises(cp.ConfigError):
                mp.read_pinned(library, mp.STOCK["c17"][mp.FILES[2]])

    def test_no_trust_on_first_use_hash_or_version_qualification(self):
        receipt = {"profile_id": "c17", **mp.TARGETS["c17"], "files": {}}
        observed = {}
        for name in mp.FILES:
            own = cp.sha256(name.encode())
            receipt["files"][name] = {
                "original": {"size": 3, "sha256": own},
                "output": {"size": 3, "sha256": own},
                "install_allowed_sha256": [own],
            }
            observed[name] = own
        self.assertFalse(mp.qualifies(receipt, receipt["camera_version"], receipt["fingerprint"], observed))
        self.assertFalse(mp.qualifies(receipt, "7.013.51", "17", observed))

    def test_required_c16_getters_are_exact_six_keys(self):
        overrides = mp.getter_overrides()
        actual = {x["key"]: (x["java_type"], x["value"]) for x in overrides}
        expected = {
            mp.SUPPORT: ("boolean", True), mp.VERSION: ("int", 3),
            mp.LEGACY: ("boolean", False), mp.PICTURE: ("size", [4096, 1512]),
            "com.oplus.xpan.crop.region.main.portrait": ("int[]", [780, 0, 1512, 4096]),
            "com.oplus.xpan.crop.region.main.landscape": ("int[]", [0, 780, 4096, 1512]),
        }
        self.assertEqual(actual, expected)
        self.assertEqual(len(overrides), 6)

    def test_existing_output_never_modified(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "existing"
            out.mkdir()
            marker = out / "keep"
            marker.write_bytes(b"caller data")
            with mock.patch.object(mp, "validate_evidence", side_effect=AssertionError("must not read")):
                with self.assertRaises(cp.ConfigError) as exc:
                    mp.build("x", "x", "x", "x", "x", out)
            self.assertEqual(exc.exception.code, "E_OUTPUT")
            self.assertEqual(marker.read_bytes(), b"caller data")
            self.assertEqual(list(out.iterdir()), [marker])


C16 = ROOT.parent / "XpanPort/02_本机提取/odm_config"
C17 = ROOT / "_tmp/inputs/PJX110_17.0.1.201/stock-20261114-05"
DECODED = ROOT / "_tmp/decoded-PJX110_17.0.1.201"
INPUTS = ROOT / "_tmp/inputs/PJX110_17.0.1.201"
REAL_AVAILABLE = all((folder / name).is_file() for folder in (C16, C17) for name in mp.FILES) and all(
    (DECODED / relative).is_file() for relative in mp.DECODED.values()) and (
    INPUTS / "OplusCamera-7.013.51.apk").is_file() and (INPUTS / "com.oplus.camera.unit.sdk.jar").is_file()


@unittest.skipUnless(REAL_AVAILABLE, "exact local originals/app/SDK unavailable; synthetic fixtures cannot substitute")
class RealStockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = mp.validate_evidence(DECODED, INPUTS / "com.oplus.camera.unit.sdk.jar",
                                          INPUTS / "OplusCamera-7.013.51.apk")
        cls.originals = {p: {name: mp.read_pinned(folder / name, mp.STOCK[p][name]) for name in mp.FILES}
                         for p, folder in (("c16", C16), ("c17", C17))}
        cls.results = {p: mp.make_profile(p, files, cls.schema) for p, files in cls.originals.items()}

    def test_shared_schema_native_photo_clone_unrelated_semantics(self):
        for platform, (outputs, receipt) in self.results.items():
            with self.subTest(platform=platform):
                old = cp.inspect_indexed_pb(self.originals[platform][mp.FILES[1]], self.schema, "smali")
                new = cp.inspect_indexed_pb(outputs[mp.FILES[1]], self.schema, "smali")
                self.assertEqual(old["str_pool"], new["str_pool"])
                old_modes = old["camera_feature_table"]["modes"]
                new_modes = new["camera_feature_table"]["modes"]
                clone = next(x for x in new_modes if x["key"] == "xpan_mode")
                donor = copy.deepcopy(next(x for x in old_modes if x["key"] == "photo_mode"))
                donor["key"] = "xpan_mode"
                self.assertEqual(clone, donor)
                new["camera_feature_table"]["modes"] = [x for x in new_modes if x["key"] != "xpan_mode"]
                self.assertEqual(old, new)
                self.assertEqual(len(outputs[mp.FILES[1]]), 40573)
                before = cp.JsonDocument(self.originals[platform][mp.FILES[0]]).obj
                after = cp.JsonDocument(outputs[mp.FILES[0]]).obj
                expected = copy.deepcopy(before)
                expected["vendor_tag_type_values"][mp.PICTURE] = "4096x1512"
                self.assertEqual(after, expected)
                self.assertFalse(receipt["provenance"]["hal_tested"])

    def test_c16_vendor_byte_identical_no_encryption_or_library(self):
        outputs, receipt = self.results["c16"]
        self.assertEqual(outputs[mp.FILES[2]], self.originals["c16"][mp.FILES[2]])
        self.assertEqual(receipt["files"][mp.FILES[2]]["semantic_changes"], [])
        self.assertEqual(receipt["required_java_getter_overrides"], mp.getter_overrides())
        self.assertEqual(receipt["native_binary_replacements"], [])

    def test_c17_vendor_only_approved_fields(self):
        outputs, receipt = self.results["c17"]
        before = cp.JsonDocument(self.originals["c17"][mp.FILES[2]]).obj
        after = cp.JsonDocument(outputs[mp.FILES[2]]).obj
        expected = copy.deepcopy(before)
        for x in expected["file_data"]:
            if x["VendorTag"] == mp.SUPPORT:
                x["Value"] = "1"
            if x["VendorTag"] == mp.PICTURE:
                x["Value"] = "4096x1512"
        expected["file_data"].extend(copy.deepcopy(mp.CROPS))
        expected["file_data"].extend([
            {"VendorTag": mp.VERSION, "Type": "Int32", "Count": "1", "Value": "3"},
            {"VendorTag": mp.LEGACY, "Type": "Byte", "Count": "1", "Value": "0"}])
        self.assertEqual(after, expected)
        self.assertEqual(receipt["required_java_getter_overrides"], [])

    def test_whole_trio_qualification_original_own_not_donor(self):
        for platform, (outputs, receipt) in self.results.items():
            with self.subTest(platform=platform):
                original_hashes = {n: cp.sha256(b) for n, b in self.originals[platform].items()}
                output_hashes = {n: cp.sha256(b) for n, b in outputs.items()}
                args = (receipt, receipt["camera_version"], receipt["fingerprint"])
                self.assertTrue(mp.qualifies(*args, original_hashes))
                self.assertTrue(mp.qualifies(*args, output_hashes))
                for name in mp.FILES:
                    # Unit/PB own outputs intentionally reproduce old observed
                    # bytes; identical content is allowed, irrespective of origin.
                    forbidden = next(h for h in cp.C16_OVERLAY_SHA256
                                     if h not in receipt["files"][name]["install_allowed_sha256"])
                    wrong = {**original_hashes, name: forbidden}
                    self.assertFalse(mp.qualifies(*args, wrong))
                    wrong = dict(original_hashes)
                    del wrong[name]
                    self.assertFalse(mp.qualifies(*args, wrong))
                self.assertFalse(mp.qualifies(receipt, "7.013.51" if platform == "c16" else "6.020.876",
                                              receipt["fingerprint"], original_hashes))
                self.assertFalse(mp.qualifies(receipt, receipt["camera_version"], "synthetic-17", original_hashes))
                self.assertFalse(mp.qualifies(*args, {**original_hashes, "libOplusSecurity.so": cp.C16_LIBRARY_SHA256}))
                forged = copy.deepcopy(receipt)
                forged["files"][mp.FILES[2]]["output"]["sha256"] = cp.C16_LIBRARY_SHA256
                forged["files"][mp.FILES[2]]["install_allowed_sha256"] = sorted({
                    original_hashes[mp.FILES[2]], cp.C16_LIBRARY_SHA256})
                self.assertFalse(mp.qualifies(forged, receipt["camera_version"], receipt["fingerprint"],
                                              {**original_hashes, mp.FILES[2]: cp.C16_LIBRARY_SHA256}))

    def test_relabel_stock_vendor_and_own_output_as_original_refused(self):
        for platform in ("c16", "c17"):
            wrong = dict(self.originals[platform])
            wrong[mp.FILES[2]] = self.originals["c17" if platform == "c16" else "c16"][mp.FILES[2]]
            with self.assertRaises(cp.ConfigError):
                mp.make_profile(platform, wrong, self.schema)
            outputs = self.results[platform][0]
            with self.assertRaises(cp.ConfigError):
                mp.make_profile(platform, outputs, self.schema)

    def test_tampered_schema_and_consumer_refused_before_output(self):
        with self.assertRaises(cp.ConfigError):
            mp.make_profile("c17", self.originals["c17"], self.schema.replace(b"camera_unit_feature_config.proto", b"camera_unit_feature_config.wrong"))
        with tempfile.TemporaryDirectory() as tmp:
            tamper = Path(tmp) / "model.smali"
            raw = (DECODED / mp.DECODED["xpan_model"]).read_bytes()
            tamper.write_bytes(raw.replace(b"0x3", b"0x4"))
            with self.assertRaises(cp.ConfigError):
                mp.read_pinned(tamper, mp.EVIDENCE["xpan_model"])

    def test_publish_contract_no_raw_assets_and_no_overwrite(self):
        profiles = {p: result[0] for p, result in self.results.items()}
        receipts = {p: result[1] for p, result in self.results.items()}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "new"
            mp.publish(out, profiles, self.originals, receipts)
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(set(manifest["profiles"]), {"c16", "c17"})
            self.assertFalse((out / "system").exists())
            for platform in profiles:
                folder = out / "payload/profiles" / platform
                self.assertEqual({p.name for p in folder.iterdir()}, {*mp.FILES, "profile.props"})
                props = dict(line.split("=", 1) for line in (folder / "profile.props").read_text().splitlines())
                self.assertEqual(set(props), {"profile_id", "model", "camera_version", "build_fingerprint",
                    "original_unit_sha256", "output_unit_sha256", "original_feature_sha256", "output_feature_sha256",
                    "original_vendor_sha256", "output_vendor_sha256", "sdk_sha256", "adapter_sha256"})
                self.assertEqual(props["model"], "PJX110")
                assets = out / "profiles" / platform
                self.assertEqual([p.name for p in assets.iterdir()], ["receipt.json"])
                for name in mp.FILES:
                    self.assertEqual((out / "originals" / platform / name).read_bytes(), self.originals[platform][name])
                    self.assertEqual((folder / name).read_bytes(), profiles[platform][name])
            snapshot = {str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()}
            with self.assertRaises(cp.ConfigError):
                mp.publish(out, profiles, self.originals, receipts)
            self.assertEqual(snapshot, {str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()})


if __name__ == "__main__":
    unittest.main()
