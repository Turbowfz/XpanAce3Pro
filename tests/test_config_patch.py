"""Pure offline tests. Run: python -I tests/test_config_patch.py

The embedded descriptor is the exact 1729-byte stock SDK FileDescriptorProto,
not a fabricated target schema. All PB/config payloads below are synthetic;
real C16 originals/overlays are optional read-only evidence checked separately.
"""
import base64
import contextlib
import copy
from decimal import Decimal
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("config_patch", ROOT / "tools/config_patch.py")
cp = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cp
spec.loader.exec_module(cp)

DESCRIPTOR = base64.b64decode(
    "CiBjYW1lcmFfdW5pdF9mZWF0dXJlX2NvbmZpZy5wcm90byKiAQoMRmVhdHVyZVRhYmxlEhAKCHN0cl9wb29sGAEgAygJEjEKFGNhbWVyYV9mZWF0dXJlX3RhYmxlGAIgASgLMhMuQ2FtZXJhRmVhdHVyZVRhYmxlEjwKGmNhbWVyYV9ncm91cF9mZWF0dXJlX3RhYmxlGAMgASgLMhguQ2FtZXJhR3JvdXBGZWF0dXJlVGFibGUSDwoHdmVyc2lvbhgEIAEoCSLcAgoHRmVhdHVyZRIaChJmZWF0dXJlX25hbWVfaW5kZXgYASABKAUSHgoWZmVhdHVyZV9rZXlfbmFtZV9pbmRleBgCIAEoBRIhChlmZWF0dXJlX3ZhbHVlX3JhbmdlX2luZGV4GAMgASgFEiMKG2ZlYXR1cmVfZGVmYXVsdF9WYWx1ZV9pbmRleBgEIAEoBRIYChBlbnRyeV90eXBlX2luZGV4GAUgASgFEiAKGGZlYXR1cmVfdmFsdWVfdHlwZV9pbmRleBgGIAEoBRIWCg5ncm91cF9jb25mbGljdBgHIAEoCBIvCgxjb25mbGljdF9tYXAYCCADKAsyGS5GZWF0dXJlLkNvbmZsaWN0TWFwRW50cnkaSAoQQ29uZmxpY3RNYXBFbnRyeRILCgNrZXkYASABKAUSIwoFdmFsdWUYAiABKAsyFC5Db25mbGljdEZlYXR1cmVMaXN0OgI4ASJCChNDb25mbGljdEZlYXR1cmVMaXN0EisKEWNvbmZsaWN0X2ZlYXR1cmVzGAEgAygLMhAuQ29uZmxpY3RGZWF0dXJlInAKD0NvbmZsaWN0RmVhdHVyZRIaChJmZWF0dXJlX25hbWVfaW5kZXgYASABKAUSGwoTZmVhdHVyZV92YWx1ZV9pbmRleBgCIAEoBRIkChxmZWF0dXJlX2NvbmZsaWN0X3ZhbHVlX2luZGV4GAMgASgFIjgKFkNhbWVyYVR5cGVGZWF0dXJlVGFibGUSHgoMZmVhdHVyZV9saXN0GAEgAygLMgguRmVhdHVyZSJdChpDYW1lcmFUeXBlRmVhdHVyZVRhYmxlTGlzdBI/Ch5jYW1lcmFfdHlwZV9mZWF0dXJlX3RhYmxlX2xpc3QYASADKAsyFy5DYW1lcmFUeXBlRmVhdHVyZVRhYmxlIr8BChBNb2RlRmVhdHVyZVRhYmxlElIKGmNhbWVyYV90eXBlX2ZlYXR1cmVfdGFibGVzGAEgAygLMi4uTW9kZUZlYXR1cmVUYWJsZS5DYW1lcmFUeXBlRmVhdHVyZVRhYmxlc0VudHJ5GlcKHENhbWVyYVR5cGVGZWF0dXJlVGFibGVzRW50cnkSCwoDa2V5GAEgASgFEiYKBXZhbHVlGAIgASgLMhcuQ2FtZXJhVHlwZUZlYXR1cmVUYWJsZToCOAEizQEKFU1vZGVHcm91cEZlYXR1cmVUYWJsZRJXChpjYW1lcmFfdHlwZV9mZWF0dXJlX3RhYmxlcxgBIAMoCzIzLk1vZGVHcm91cEZlYXR1cmVUYWJsZS5DYW1lcmFUeXBlRmVhdHVyZVRhYmxlc0VudHJ5GlsKHENhbWVyYVR5cGVGZWF0dXJlVGFibGVzRW50cnkSCwoDa2V5GAEgASgFEioKBXZhbHVlGAIgASgLMhsuQ2FtZXJhVHlwZUZlYXR1cmVUYWJsZUxpc3Q6AjgBIqoBChJDYW1lcmFGZWF0dXJlVGFibGUSRwoTbW9kZV9mZWF0dXJlX3RhYmxlcxgBIAMoCzIqLkNhbWVyYUZlYXR1cmVUYWJsZS5Nb2RlRmVhdHVyZVRhYmxlc0VudHJ5GksKFk1vZGVGZWF0dXJlVGFibGVzRW50cnkSCwoDa2V5GAEgASgJEiAKBXZhbHVlGAIgASgLMhEuTW9kZUZlYXR1cmVUYWJsZToCOAEiyQEKF0NhbWVyYUdyb3VwRmVhdHVyZVRhYmxlElcKGW1vZGVfZ3JvdXBfZmVhdHVyZV90YWJsZXMYASADKAsyNC5DYW1lcmFHcm91cEZlYXR1cmVUYWJsZS5Nb2RlR3JvdXBGZWF0dXJlVGFibGVzRW50cnkaVQobTW9kZUdyb3VwRmVhdHVyZVRhYmxlc0VudHJ5EgsKA2tleRgBIAEoCRIlCgV2YWx1ZRgCIAEoCzIWLk1vZGVHcm91cEZlYXR1cmVUYWJsZToCOAFCNwoeY29tLm9wbHVzLm9jcy5jYW1lcmEuY29uZmlndXJlQhVQcm90b2J1ZkZlYXR1cmVDb25maWdiBnByb3RvMw=="
)


def unit_json():
    return {
        "mode_type_list": {"rear_main": ["video_mode", "photo_mode"], "rear_wide": ["photo_mode"]},
        "universal_mode_type_list": {"rear_main": ["photo_mode"], "front_main": ["photo_mode"]},
        "mode_operation_mode": {"photo_mode": "8001", "xpan_mode": "80AC"},
        "vendor_tag_type_values": {cp.PICTURE: "8192x3724", "unrelated": "0"},
        "unknown": [{"nested": [1, 1, None, {"future": True}]}],
    }


def vendor_json():
    return {"file_version": "1", "future": {"keep": [3, 2, 1]}, "file_data": [
        {"VendorTag": cp.SUPPORT, "Type": "Byte", "Count": "1", "Value": "0", "unknown": {"list": [1, 2]}},
        {"VendorTag": cp.PICTURE, "Type": "Int32", "Count": "2", "Value": "8192x3724", "future": "retain"},
        {"VendorTag": "unrelated", "Type": "Byte", "Count": "1", "Value": "1"},
    ]}


def encoded(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")


def vi(number, value):
    return cp.encode_varint(number << 3) + cp.encode_varint(value)


def ld(number, payload):
    return cp.length_field(number, payload)


# Noncanonical varint, fixed64, fixed32, opaque bytes, and unknown group.
UNKNOWN = vi(99, 1)[:-1] + b"\x81\x00" + cp.encode_varint(100 << 3 | 1) + b"12345678" + cp.encode_varint(101 << 3 | 5) + b"abcd" + ld(102, b"\x00\xffopaque") + cp.encode_varint(103 << 3 | 3) + vi(1, 7) + cp.encode_varint(103 << 3 | 4)


def mode_entry(name, body):
    return ld(1, ld(1, name.encode()) + ld(2, body) + UNKNOWN)


def native_pb(photo=True, camera=1, feature_index=2, large=0, xpan=False):
    pool = ["common", "rear_main", "com.oplus.camera.feature.zoom", "pref_zoom", "[0~100]", "0", "String", "front_main"]
    feature = b"".join(vi(i, value) for i, value in enumerate([feature_index, 3, 4, 5, 6, 6], 1)) + UNKNOWN
    conflict = vi(1, 2) + vi(2, 5) + vi(3, 5) + UNKNOWN
    feature += ld(8, vi(1, 5) + ld(2, ld(1, conflict)))
    camera_table = ld(1, feature) + UNKNOWN
    body = ld(1, vi(1, camera) + ld(2, camera_table) + UNKNOWN) + UNKNOWN
    if large:
        body += ld(110, b"q" * large)
    modes = mode_entry("video_mode", body)
    if photo:
        modes += mode_entry("photo_mode", body)
    if xpan:
        modes += mode_entry("xpan_mode", body)
    modes += mode_entry("common", body) + UNKNOWN
    return b"".join(ld(1, x.encode()) for x in pool) + ld(2, modes) + ld(4, b"fixture-1") + UNKNOWN


class JsonTests(unittest.TestCase):
    def test_unit_only_rear_main_and_all_other_fields_preserved(self):
        original = unit_json()
        output = cp.patch_unit_json(encoded(original))
        expected = copy.deepcopy(original)
        for section in ("mode_type_list", "universal_mode_type_list"):
            expected[section]["rear_main"].append("xpan_mode")
        self.assertEqual(cp.JsonDocument(output).obj, expected)
        self.assertEqual(cp.patch_unit_json(output), output)
        self.assertEqual(original, unit_json())

    def test_noop_is_exact_original_bytes(self):
        obj = unit_json()
        for section in ("mode_type_list", "universal_mode_type_list"):
            obj[section]["rear_main"].append("xpan_mode")
        data = b" \r\n" + encoded(obj) + b"\r\n"
        self.assertEqual(cp.patch_unit_json(data), data)

    def test_universal_section_not_invented(self):
        obj = unit_json()
        del obj["universal_mode_type_list"]
        new = cp.JsonDocument(cp.patch_unit_json(encoded(obj))).obj
        self.assertNotIn("universal_mode_type_list", new)
        self.assertEqual(new["mode_operation_mode"], obj["mode_operation_mode"])

    def test_native_xpan_operation_required(self):
        for value in (None, {}, {"xpan_mode": ""}, {"xpan_mode": 32940}):
            obj = unit_json()
            obj["mode_operation_mode"] = value
            with self.assertRaises(cp.ConfigError) as error:
                cp.patch_unit_json(encoded(obj))
            self.assertEqual(error.exception.code, "E_NATIVE_MODE")

    def test_duplicate_xpan_rejected_without_deduping_other_modes(self):
        obj = unit_json()
        obj["mode_type_list"]["rear_main"] += ["xpan_mode", "xpan_mode"]
        with self.assertRaises(cp.ConfigError):
            cp.patch_unit_json(encoded(obj))
        obj["mode_type_list"]["rear_main"] = ["photo_mode", "photo_mode"]
        new = cp.JsonDocument(cp.patch_unit_json(encoded(obj))).obj
        self.assertEqual(new["mode_type_list"]["rear_main"], ["photo_mode", "photo_mode", "xpan_mode"])

    def test_lexical_unknown_numbers_strings_and_crlf_preserved(self):
        data = encoded(unit_json()).replace(b'"unknown": [', b'"extra_number": 1.0000000000000000000009, "unknown": [').replace(b"\n", b"\r\n")
        output = cp.patch_unit_json(data)
        self.assertIn(b"1.0000000000000000000009", output)
        self.assertIn(b"\r\n", output)
        self.assertEqual(cp.JsonDocument(output).obj["extra_number"], Decimal("1.0000000000000000000009"))

    def test_duplicate_keys_and_junk_and_nonfinite_rejected(self):
        for data in (b'{"a":1,"a":2}', b'{} garbage', b'{"x":NaN}', b'{"x":Infinity}', b'[1,]', b'{"a":1,}', b'\xff', b'{}\0'):
            with self.subTest(data=data), self.assertRaises(cp.ConfigError):
                cp.JsonDocument(data)

    def test_depth_and_size_limits(self):
        with self.assertRaises(cp.ConfigError):
            cp.JsonDocument(b"[" * 66 + b"0" + b"]" * 66)
        with self.assertRaises(cp.ConfigError):
            cp.JsonDocument(b" " * (cp.MAX_PAYLOAD + 1))

    def test_vendor_support_changes_value_only(self):
        obj = vendor_json()
        data = encoded(obj)
        output = cp.patch_vendor_json(data)
        self.assertEqual(output, data.replace(b'"Value": "0"', b'"Value": "1"', 1))
        new = cp.JsonDocument(output).obj
        self.assertEqual(new["file_data"][0]["unknown"], obj["file_data"][0]["unknown"])
        self.assertEqual(cp.patch_vendor_json(output), output)

    def test_vendor_no_donor_record_or_metadata_guess(self):
        for mutation in ("absent", "type", "duplicate"):
            obj = vendor_json()
            if mutation == "absent":
                obj["file_data"].pop(0)
            elif mutation == "type":
                obj["file_data"][0]["Type"] = "Int32"
            else:
                obj["file_data"].append(copy.deepcopy(obj["file_data"][0]))
            with self.subTest(mutation=mutation), self.assertRaises(cp.ConfigError):
                cp.patch_vendor_json(encoded(obj))

    def test_unrelated_duplicate_vendor_records_are_retained(self):
        obj = vendor_json()
        duplicate = dict(obj["file_data"][-1], Value="0", extra=[1, 2])
        obj["file_data"].append(duplicate)
        output = cp.patch_vendor_json(encoded(obj), c16_crop=True)
        new = cp.JsonDocument(output).obj
        self.assertEqual(new["file_data"][2:4], obj["file_data"][2:4])
        self.assertEqual(cp.patch_vendor_json(output, c16_crop=True), output)
        _, tags = cp.vendor_records(new)
        self.assertEqual(len(tags["unrelated"]), 2)

    def test_c16_crop_only_main_and_unknown_records_retained(self):
        obj = vendor_json()
        data = encoded(obj)
        output = cp.patch_vendor_json(data, c16_crop=True)
        new = cp.JsonDocument(output).obj
        self.assertEqual(new["file_data"][1]["future"], "retain")
        self.assertEqual(new["file_data"][1]["Value"], "4096x1512")
        self.assertEqual(new["file_data"][3:], list(cp.C16_MAIN_CROPS))
        self.assertEqual(new["future"], obj["future"])
        self.assertFalse(any("wide" in x["VendorTag"] or "tele" in x["VendorTag"] for x in new["file_data"]))
        self.assertEqual(cp.patch_vendor_json(output, c16_crop=True), output)

    def test_conflicting_crop_geometry_refused(self):
        obj = vendor_json()
        crop = dict(cp.C16_MAIN_CROPS[0], Value="1,2,3,4")
        obj["file_data"].append(crop)
        with self.assertRaises(cp.ConfigError):
            cp.patch_vendor_json(encoded(obj), c16_crop=True)

    def test_c16_unit_crop_opt_in(self):
        data = encoded(unit_json())
        self.assertEqual(cp.JsonDocument(cp.patch_unit_json(data)).obj["vendor_tag_type_values"][cp.PICTURE], "8192x3724")
        output = cp.patch_unit_json(data, c16_crop=True)
        self.assertEqual(cp.JsonDocument(output).obj["vendor_tag_type_values"][cp.PICTURE], "4096x1512")
        self.assertEqual(cp.patch_unit_json(output, c16_crop=True), output)

    def test_c16_padding_read_only_exact_suffix(self):
        cp.JsonDocument(b'{}\n' + b'\x07' * 7 + b'\x00' * 13, allow_c16_padding=True)
        for data in (b'{}' + b'\x07' * 6, b'{}\0', b'{}\x07junk'):
            with self.assertRaises(cp.ConfigError):
                cp.JsonDocument(data, allow_c16_padding=True)

    def test_plain_vendor_analysis_keeps_duplicates_and_proves_proposal(self):
        obj = vendor_json()
        obj["file_data"].append(dict(obj["file_data"][-1], Value="0"))
        report = cp.analyze({"platform": "C17"}, {"oplus_camera_config.json": encoded(obj)}, None)
        item = report["files"]["oplus_camera_config.json"]
        self.assertEqual(item["duplicate_vendor_tags"], {"unrelated": 2})
        proposal = item["main_only_plaintext_proposal"]
        self.assertTrue(proposal["double_patch_same_bytes"])
        self.assertEqual(len(proposal["semantic_delta"]), 1)
        self.assertEqual(len(obj["file_data"]), 4)

    def test_semantic_diff_retains_array_order_absence_null_and_duplicates(self):
        delta = cp.semantic_diff({"a": [1, 2, 1]}, {"a": [2, 1, 1], "b": None})
        self.assertEqual(len(delta), 3)
        self.assertEqual(delta[-1]["kind"], "add")
        self.assertNotIn("old", delta[-1])


class WireTests(unittest.TestCase):
    def test_raw_roundtrip_all_wire_types_unknown_groups_and_noncanonical(self):
        data = vi(1, 300) + ld(2, b"not-a-message\x00") + UNKNOWN
        self.assertEqual(cp.serialize_wire(cp.parse_wire(data)), data)

    def test_varint_roundtrip_and_negative_refused(self):
        for value in (0, 127, 128, 16383, 16384, 2**64 - 1):
            data = cp.encode_varint(value)
            self.assertEqual(cp.read_varint(data, 0), (value, len(data)))
        with self.assertRaises(cp.ConfigError):
            cp.encode_varint(-1)

    def test_bad_wire_is_bounded_and_rejected(self):
        bad = [b"\0", b"\x08\x80", b"\x08" + b"\xff" * 10, b"\x12\x7fabc", b"\x09a", b"\x0da", b"\x0e", b"\x0c", b"\x0b", b"\x0b\x14", b"\x08\x01\0"]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(cp.ConfigError):
                cp.parse_wire(data)

    def test_group_depth_and_field_limit(self):
        with self.assertRaises(cp.ConfigError):
            cp.parse_wire(b"\x0b" * 66 + b"\x0c" * 66)
        with mock.patch.object(cp, "MAX_FIELDS", 3):
            with self.assertRaises(cp.ConfigError):
                cp.parse_wire(b"\x08\0" * 4)

    def test_length_prefix_boundary_change_retains_key_bytes(self):
        field = cp.parse_wire(b"\x92\x00\x01q")[0]
        changed = field.with_payload(b"q" * 128)
        self.assertTrue(changed.startswith(b"\x92\x00\x80\x01"))
        self.assertEqual(cp.parse_wire(changed)[0].value, b"q" * 128)


class IndexedTests(unittest.TestCase):
    def test_exact_descriptor_and_smali_verification(self):
        self.assertEqual(len(DESCRIPTOR), 1729)
        self.assertEqual(cp.verify_schema(DESCRIPTOR), cp.SCHEMA_SHA256)
        smali = ('const-string v0, ' + json.dumps(DESCRIPTOR.decode("latin-1")) + '\n').encode()
        self.assertEqual(cp.verify_schema(smali, "smali"), cp.SCHEMA_SHA256)

    def test_unknown_or_duplicate_schema_refused(self):
        for descriptor, form in ((DESCRIPTOR + b"\0", "descriptor"), (b"", "descriptor"), (b"arbitrary", "smali"), (DESCRIPTOR, "proto")):
            with self.subTest(form=form), self.assertRaises(cp.ConfigError):
                cp.patch_indexed_pb(native_pb(), descriptor, form)

    def test_clone_keeps_pool_indices_all_unknown_fields_and_other_modes(self):
        data = native_pb()
        before = cp.inspect_indexed_pb(data, DESCRIPTOR)
        output = cp.patch_indexed_pb(data, DESCRIPTOR)
        after = cp.inspect_indexed_pb(output, DESCRIPTOR)
        self.assertEqual(after["str_pool"], before["str_pool"])
        old_modes = before["camera_feature_table"]["modes"]
        new_modes = after["camera_feature_table"]["modes"]
        self.assertEqual([x["key"] for x in new_modes], ["video_mode", "photo_mode", "xpan_mode", "common"])
        photo = next(x for x in old_modes if x["key"] == "photo_mode")
        xpan = next(x for x in new_modes if x["key"] == "xpan_mode")
        self.assertEqual(xpan, dict(photo, key="xpan_mode"))
        after["camera_feature_table"]["modes"] = [x for x in new_modes if x["key"] != "xpan_mode"]
        self.assertEqual(after, before)
        old_top = cp.parse_wire(data)
        new_top = cp.parse_wire(output)
        self.assertEqual([x.raw for x in old_top if x.number != 2], [x.raw for x in new_top if x.number != 2])
        self.assertEqual(cp.patch_indexed_pb(output, DESCRIPTOR), output)

    def test_native_photo_and_native_main_required(self):
        for data in (native_pb(photo=False), native_pb(camera=7)):
            with self.assertRaises(cp.ConfigError) as error:
                cp.patch_indexed_pb(data, DESCRIPTOR)
            self.assertEqual(error.exception.code, "E_NATIVE_PHOTO")

    def test_out_of_range_index_refused(self):
        for data in (native_pb(camera=8), native_pb(feature_index=500)):
            with self.assertRaises(cp.ConfigError) as error:
                cp.patch_indexed_pb(data, DESCRIPTOR)
            self.assertEqual(error.exception.code, "E_INDEX")

    def test_duplicate_mode_and_top_singular_refused(self):
        data = native_pb()
        top = cp.parse_wire(data)
        outer = next(x for x in top if x.number == 2)
        duplicate = cp.parse_wire(outer.value)[0].raw
        bad = b"".join(x.with_payload(outer.value + duplicate) if x is outer else x.raw for x in top)
        for payload in (bad, data + outer.raw):
            with self.assertRaises(cp.ConfigError):
                cp.patch_indexed_pb(payload, DESCRIPTOR)

    def test_known_wrong_wire_type_refused(self):
        with self.assertRaises(cp.ConfigError):
            cp.inspect_indexed_pb(vi(1, 1), DESCRIPTOR)

    def test_existing_exact_clone_is_noop_conflict_refused(self):
        data = native_pb(xpan=True)
        self.assertEqual(cp.patch_indexed_pb(data, DESCRIPTOR), data)
        data = cp.patch_indexed_pb(native_pb(), DESCRIPTOR)
        # Native clone unknown field is semantically relevant, not discardable.
        top = cp.parse_wire(data)
        outer = next(x for x in top if x.number == 2)
        parts = cp.parse_wire(outer.value)
        changed = []
        for field in parts:
            if field.number == 1 and b"xpan_mode" in field.value:
                changed.append(field.with_payload(field.value + vi(111, 1)))
            else:
                changed.append(field.raw)
        bad = b"".join(x.with_payload(b"".join(changed)) if x is outer else x.raw for x in top)
        with self.assertRaises(cp.ConfigError) as error:
            cp.patch_indexed_pb(bad, DESCRIPTOR)
        self.assertEqual(error.exception.code, "E_CONFLICT")

    def test_large_ancestor_varint_boundaries_supported(self):
        for large in (80, 16320):
            data = native_pb(large=large)
            output = cp.patch_indexed_pb(data, DESCRIPTOR)
            self.assertEqual(cp.patch_indexed_pb(output, DESCRIPTOR), output)
            self.assertIn(b"q" * large, output)

    def test_pb_payload_limit(self):
        with self.assertRaises(cp.ConfigError):
            cp.parse_wire(b"x" * (cp.MAX_PAYLOAD + 1))


class ManifestAndOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def fixture_manifest(self, platform="C17", name="camera_unit_config", data=None, form="json"):
        data = encoded(unit_json()) if data is None else data
        (self.base / "input").write_bytes(data)
        manifest = {"manifest_version": 1, "platform": platform, "source_kind": "stock-original", "original_validated": True, "validation": {"method": "synthetic unit-test attestation", "reference": "not real stock"}, "files": {name: {"path": "input", "size": len(data), "sha256": cp.sha256(data), "format": form}}}
        path = self.base / "manifest.json"
        path.write_bytes(encoded(manifest))
        return path, manifest

    def test_hash_size_source_kind_and_validation_required(self):
        for key, value in (("source_kind", "patched-overlay"), ("original_validated", False), ("validation", {}), ("manifest_version", True)):
            path, obj = self.fixture_manifest()
            obj[key] = value
            path.write_bytes(encoded(obj))
            with self.subTest(key=key), self.assertRaises(cp.ConfigError):
                cp.load_manifest(path)
        path, obj = self.fixture_manifest()
        (self.base / "input").write_bytes(b"tamper")
        with self.assertRaises(cp.ConfigError) as error:
            cp.load_manifest(path)
        self.assertEqual(error.exception.code, "E_HASH")

    def test_c16_hash_pin_rejects_synthetic_overlay_even_with_own_hash(self):
        path, _ = self.fixture_manifest(platform="C16")
        with self.assertRaises(cp.ConfigError) as error:
            cp.load_manifest(path)
        self.assertEqual(error.exception.code, "E_ORIGINAL")

    def test_known_c16_stock_or_overlay_cannot_be_relabelled_c17(self):
        path, _ = self.fixture_manifest()
        real_hash = cp.sha256
        for known_hash in (cp.C16_STOCK_SHA256["camera_unit_config"], next(iter(cp.C16_OVERLAY_SHA256))):
            data = (self.base / "input").read_bytes()
            with mock.patch.object(cp, "sha256", side_effect=lambda value: known_hash if value == data else real_hash(value)):
                obj = json.loads(path.read_bytes())
                obj["files"]["camera_unit_config"]["sha256"] = known_hash
                path.write_bytes(encoded(obj))
                with self.assertRaises(cp.ConfigError) as error:
                    cp.load_manifest(path)
                self.assertEqual(error.exception.code, "E_ORIGINAL")

    def test_path_escape_and_absolute_refused(self):
        for relative in ("../input", str(self.base / "input")):
            path, obj = self.fixture_manifest()
            obj["files"]["camera_unit_config"]["path"] = relative
            path.write_bytes(encoded(obj))
            with self.assertRaises(cp.ConfigError):
                cp.load_manifest(path)

    def test_c17_pb_and_security_not_supported(self):
        for name, data, form in (("camera_unit_feature_config.protobuf", native_pb(), "indexed-pb"), ("oplus_camera_config", b"x", "c16-envelope"), ("libOplusSecurity.so", b"x", "library")):
            path, _ = self.fixture_manifest(name=name, data=data, form=form)
            with self.subTest(name=name), self.assertRaises(cp.ConfigError):
                cp.load_manifest(path)

    def test_json_unit_provenance_and_c17_crop_refusal(self):
        path, _ = self.fixture_manifest()
        obj, payloads, _, schema, _ = cp.load_manifest(path)
        outputs, proofs = cp.make_patch(obj, payloads, schema, "patch-json")
        self.assertIn("camera_unit_config", outputs)
        proof = proofs["camera_unit_config"]
        self.assertEqual(proof["old_sha256"], cp.sha256(payloads["camera_unit_config"]))
        self.assertEqual(proof["new_sha256"], cp.sha256(outputs["camera_unit_config"]))
        self.assertTrue(proof["unrelated_semantics_preserved"])
        self.assertTrue(proof["double_patch_same_bytes"])
        with self.assertRaises(cp.ConfigError):
            cp.make_patch(obj, payloads, schema, "patch-json", c16_crop=True)

    def test_publication_new_directory_complete_and_nooverwrite(self):
        destination = self.base / "output"
        results = Path(cp.publish_bundle(destination, {"unit.json": b"{}"}, {"files": {"proof": True}}))
        self.assertEqual((results / "unit.json").read_bytes(), b"{}")
        self.assertEqual(json.loads((results / "provenance.json").read_bytes()), {"files": {"proof": True}})
        with self.assertRaises(cp.ConfigError):
            cp.publish_bundle(destination, {"unit.json": b"tamper"}, {})
        self.assertEqual((results / "unit.json").read_bytes(), b"{}")

    def test_publication_failure_rolls_back_entire_output(self):
        for operation in ("rename", "fsync"):
            destination = self.base / ("failure-" + operation)
            with mock.patch.object(cp.os, operation, side_effect=OSError("injected failure")):
                with self.assertRaises(OSError):
                    cp.publish_bundle(destination, {"one": b"1", "two": b"2"}, {})
            self.assertFalse(destination.exists())
            self.assertFalse(list(self.base.glob(".xpan-offline-*")))

    def test_invalid_bundle_not_written(self):
        destination = self.base / "output"
        with self.assertRaises(cp.ConfigError):
            cp.publish_bundle(destination, {"../escape": b"x"}, {})
        self.assertFalse(destination.exists())

    def test_unsupported_cli_never_creates_output(self):
        path, _ = self.fixture_manifest(platform="C16")
        destination = self.base / "output"
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            result = cp.main(["patch-pb", "--manifest", str(path), "--out-dir", str(destination)])
        self.assertEqual(result, 2)
        self.assertEqual(json.loads(errors.getvalue())["status"], "error")
        self.assertFalse(destination.exists())

    def test_analyze_readonly_and_cli_patch(self):
        path, _ = self.fixture_manifest()
        destination = self.base / "output"
        with contextlib.redirect_stdout(io.StringIO()) as messages:
            self.assertEqual(cp.main(["analyze", "--manifest", str(path)]), 0)
        self.assertEqual(json.loads(messages.getvalue())["status"], "analysis-only")
        self.assertFalse(destination.exists())
        with contextlib.redirect_stdout(io.StringIO()) as messages:
            self.assertEqual(cp.main(["patch-json", "--manifest", str(path), "--out-dir", str(destination)]), 0)
        result = json.loads(messages.getvalue())
        self.assertTrue(result["not_a_complete_config_set"])
        self.assertTrue((destination / "results/provenance.json").is_file())


class EnvelopeTests(unittest.TestCase):
    def test_c17_or_wrong_stock_library_cannot_use_c16_offset(self):
        for platform in ("C16", "C17"):
            with self.assertRaises(cp.ConfigError) as error:
                cp.decrypt_c16_evidence(b"fake", b"wrong library", platform)
            self.assertEqual(error.exception.code, "E_SECURITY_PROFILE")

    def test_encrypted_output_always_explicitly_unsupported(self):
        with self.assertRaises(cp.ConfigError) as error:
            cp.encrypt_envelope(b"original", b"changed")
        self.assertEqual(error.exception.code, "E_UNVERIFIED_ENVELOPE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
