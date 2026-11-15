"""Offline selected-OTA CLI tests: python -I -B tests/test_extract_payload_camera.py.

Trusted sibling sources are imported once; main() uses frozen fake readers only.
Small synthetic inputs live in fresh _tmp directories and are never executed.
No firmware, device, network, decryption, signing, or full-suite operations.
"""
import argparse
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import importlib.util
import inspect
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import warnings
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "extract_payload_camera.py"
spec = importlib.util.spec_from_file_location("tested_local_camera_extract", SOURCE)
e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e)
REAL_READER, REAL_COMPRESSED = e.reader, e.compressed
PROOF_KEYS = ("nid", "inode_offset", "mode", "size", "layout", "data_address_raw", "inline_offset")
CONFIG = "/odm/etc/camera/config/camera_unit_config"
FEATURE = "/odm/etc/camera/config/camera_unit_feature_config.protobuf"
VENDOR = "/odm/etc/camera/config/oplus_camera_config"
APK = "/product/priv-app/OplusCamera/OplusCamera.apk"
SDK = "/product/framework/com.oplus.camera.unit.sdk.jar"
ADAPTER = "/product/framework/com.oplus.camera.unit.sdk.adapter.jar"


def dex(version=b"035"):
    data = bytearray(112)
    data[:8] = b"dex\n" + version + b"\0"
    struct.pack_into("<III", data, 32, len(data), 112, 0x12345678)
    data[12:32] = hashlib.sha1(data[32:]).digest()
    struct.pack_into("<I", data, 8, zlib.adler32(data[12:]) & 0xffffffff)
    return bytes(data)


def archive_bytes(entries):
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Duplicate name:", category=UserWarning)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, data in entries:
                # Preserve on-disk names; Windows ZipInfo otherwise normalizes '\\'.
                info = zipfile.ZipInfo("placeholder")
                info.filename = info.orig_filename = name
                archive.writestr(info, data)
    return output.getvalue()


class ExtractPayloadCameraTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="payload-camera-test-", dir=ROOT / "_tmp")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.inputs = self.work / "inputs"
        self.inputs.mkdir()
        self.inventory = self.inputs / "inventory.json"
        self.payload = self.inputs / "payload.bin"
        self.payload.write_bytes(b"synthetic fixture, not an OTA")
        self.output = self.work / "new" / "output"
        packed = archive_bytes([("classes.dex", dex())])
        self.data = {CONFIG: b'{"mode_operation_mode":{}}', FEATURE: b"\x08\x01",
                     VENDOR: b'{"file_version":1,"file_data":[]}', APK: packed, SDK: packed, ADAPTER: packed}
        self.pins = {path: {"nid": i, "inode_offset": i * 32, "mode": 0o100644,
                           "size": len(data), "layout": 3, "data_address_raw": 1,
                           "inline_offset": i * 32 + 32, "type": "file"}
                     for i, (path, data) in enumerate(self.data.items(), 1)}
        self.write_inventory([{"path": path, "inode": inode} for path, inode in self.pins.items()])
        self.resolved = deepcopy(self.pins)
        self.proofs = {inode["nid"]: {"inode": deepcopy(inode), "raw_inode_hex": bytes(32).hex(),
                       "index_packs": [{"offset": 64, "length": 8, "raw_hex": bytes(8).hex()}],
                       "extents": [{"physical_offset": 4096 * inode["nid"], "physical_bytes": 4096}],
                       "exact_index_verified": True, "candidate_search_used": False}
                       for inode in self.pins.values()}
        self.ranges = SimpleNamespace(close=Mock(side_effect=self.close_ranges))
        self.ranges_ctor = Mock(side_effect=self.make_ranges)
        self.fs = SimpleNamespace(resolve=Mock(side_effect=lambda path: deepcopy(self.resolved[path])))
        self.fs_ctor = Mock(return_value=self.fs)
        self.fs_ctor.path_parts = REAL_READER.TargetedErofs.path_parts
        self.extractor = SimpleNamespace(superblock={"raw_hex": bytes(128).hex()}, metadata_bytes=0,
                                        physical_bytes=0, plan=Mock(side_effect=self.plan),
                                        extract=Mock(side_effect=self.extract))
        self.extractor_ctor = Mock(return_value=self.extractor)
        self.validation = Mock(return_value={"format": "synthetic-static-validation"})

    def write_inventory(self, source):
        self.inventory.write_text(json.dumps(source), encoding="utf-8")

    def make_ranges(self, payload, partition, audit, **limits):
        self.audit = audit
        audit.update(mode="synthetic ranges only", reads=[], operations=[])
        return self.ranges

    def close_ranges(self):
        self.audit["totals"] = {"target_bytes": 0, "encoded_operation_bytes": 0,
                                "decoded_operation_bytes": 0, "resident_cache_bytes": 0}

    def plan(self, inode):
        self.extractor.metadata_bytes += 64
        return deepcopy(self.proofs[inode["nid"]])

    def extract(self, inode):
        proof = self.plan(inode)
        self.extractor.physical_bytes += 4096
        path = next(path for path, expected in self.pins.items() if expected["nid"] == inode["nid"])
        data = self.data[path]
        proof.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), byte_count_verified=True)
        return data, proof

    def args(self, paths=(CONFIG,), file_budget=4096, read_budget=8192, plan=False):
        result = ["--payload", str(self.payload), "--partition", "odm", "--inventory", str(self.inventory),
                  "--output", str(self.output), "--file-budget", str(file_budget), "--read-budget", str(read_budget)]
        for path in paths:
            result.extend(("--path", path))
        return result + (["--plan-only"] if plan else [])

    def run_main(self, argv):
        reader = SimpleNamespace(PayloadRanges=self.ranges_ctor, TargetedErofs=self.fs_ctor)
        compressed = SimpleNamespace(INODE_PROOF_KEYS=PROOF_KEYS, ExactCompressedErofs=self.extractor_ctor,
                                     validate_config=self.validation)
        before = (self.inventory.read_bytes(), self.payload.read_bytes(), deepcopy(self.proofs))
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        with patch.object(e, "reader", reader), patch.object(e, "compressed", compressed), \
                redirect_stdout(self.stdout), redirect_stderr(self.stderr):
            try:
                return e.main(argv)
            finally:
                self.assertEqual((self.inventory.read_bytes(), self.payload.read_bytes(), self.proofs), before)

    def cli_error(self, argv):
        with self.assertRaises(SystemExit) as caught:
            self.run_main(argv)
        self.assertEqual(caught.exception.code, 2)
        self.ranges_ctor.assert_not_called()
        self.assertIn("error:", self.stderr.getvalue())

    def report(self):
        report = json.loads((self.output / "extraction.json").read_text(encoding="utf-8"))
        self.assertIs(report["phone_operations"], False)
        self.assertIs(report["full_image"], False)
        self.assertIs(report["donor_reference_only"], True)
        self.assertEqual(report["source_inventory"], str(self.inventory.resolve()))
        return report

    def archive(self, entries):
        path = self.inputs / "reference.apk"
        path.write_bytes(archive_bytes(entries))
        return path

    def test_inventory_shapes_exact_paths_and_no_source_mutation(self):
        record = {"path": CONFIG, "inode": deepcopy(self.pins[CONFIG])}
        sources = [[record], {"files": [record]}, {"inodes": [record]},
                   {"directories": [{"path": CONFIG.rsplit("/", 1)[0] + "/", "entries": [
                       {"name": "camera_unit_config", "inode": self.pins[CONFIG]}, {"name": "unpinned"}]}]},
                   {"files": [{"path": "/not/pinned"}], "inodes": [record], "unrelated_metadata": True}]
        for source in sources:
            with self.subTest(source=source):
                self.write_inventory(source)
                snapshot, raw = deepcopy(source), self.inventory.read_bytes()
                with patch.object(e.json, "loads", return_value=source):
                    self.assertEqual(e.inventory_paths(self.inventory), {CONFIG: self.pins[CONFIG]})
                self.assertEqual(source, snapshot)
                self.assertEqual(self.inventory.read_bytes(), raw)

    def test_inventory_duplicates_and_traversal_fail(self):
        record = {"path": CONFIG, "inode": self.pins[CONFIG]}
        sources = [[record, record], {"files": [record], "inodes": [record]},
                   {"files": [record], "directories": [{"path": CONFIG.rsplit("/", 1)[0],
                    "entries": [{"name": "camera_unit_config", "inode": self.pins[CONFIG]}]}]}]
        for source in sources:
            self.write_inventory(source)
            with self.assertRaisesRegex(ValueError, "duplicate"):
                e.inventory_paths(self.inventory)
        for path in ("relative/config", "/odm/../config", "/odm/./config", "/odm//config", "/odm\\config", "/odm/config/", "/" + "/".join(["x"] * 33)):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.write_inventory([{"path": path, "inode": self.pins[CONFIG]}])
                e.inventory_paths(self.inventory)

    def test_inventory_bounds_and_unsupported_roots(self):
        for path in (self.inputs, self.inputs / "missing.json"):
            with self.assertRaisesRegex(ValueError, "bounded regular"):
                e.inventory_paths(path)
        self.inventory.write_bytes(b" " * ((1 << 20) + 1))
        with patch.object(Path, "read_text", side_effect=AssertionError("oversized inventory was read")), self.assertRaises(ValueError):
            e.inventory_paths(self.inventory)
        for source in (None, True, 12, "not an inventory"):
            self.write_inventory(source)
            with self.assertRaisesRegex(ValueError, "unsupported inventory root"):
                e.inventory_paths(self.inventory)
        self.inventory.write_text("{broken-json", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            e.inventory_paths(self.inventory)

    def test_budgets_are_explicit_positive_and_at_most_192_mib(self):
        self.assertEqual(e.LIMIT, 192 << 20)
        for value in (1, 4 << 20, 192 << 20):
            self.assertEqual(e.bounded(str(value)), value)
        for value in (0, -1, (192 << 20) + 1):
            with self.assertRaises(argparse.ArgumentTypeError):
                e.bounded(str(value))
        for option in ("--file-budget", "--read-budget"):
            for value in ("0", "-1", str((192 << 20) + 1), "not-a-number"):
                argv = self.args()
                argv[argv.index(option) + 1] = value
                self.cli_error(argv)
            argv = self.args()
            index = argv.index(option)
            del argv[index:index + 2]
            self.cli_error(argv)
        self.assertFalse(self.output.exists())

    def test_cli_requires_unique_pinned_one_to_three_paths(self):
        for paths in ((), (CONFIG, CONFIG), (CONFIG, FEATURE, VENDOR, APK),
                      (CONFIG, "/other/camera_unit_config"), ("/other/camera_unit_config",)):
            with self.subTest(paths=paths):
                self.cli_error(self.args(paths))
        self.write_inventory({"unknown_files_key": [{"path": CONFIG, "inode": self.pins[CONFIG]}]})
        self.cli_error(self.args())
        self.assertFalse(self.output.exists())

    def test_unknown_filenames_including_audit_names_are_rejected(self):
        for name in ("boot.img", "script.py", "read-audit.json", "extraction.json", "OplusCamera.apk.bak", "opluscamera.apk"):
            path = "/odm/" + name
            self.write_inventory([{"path": path, "inode": self.pins[CONFIG]}])
            self.cli_error(self.args((path,)))
        self.assertFalse(self.output.exists())

    def test_existing_output_and_symlink_branch_never_overwrite(self):
        for kind in ("file", "directory", "symlink"):
            self.output = self.work / kind
            if kind == "directory":
                self.output.mkdir()
                (self.output / "keep").write_bytes(b"keep")
            elif kind == "file":
                self.output.write_bytes(b"keep")
            with patch.object(Path, "is_symlink", return_value=kind == "symlink"):
                self.cli_error(self.args())
            if kind != "symlink":
                self.assertEqual((self.output / "keep" if kind == "directory" else self.output).read_bytes(), b"keep")
            else:
                self.assertFalse(self.output.exists())

    def test_plan_only_uses_frozen_metadata_no_extraction_or_validation(self):
        self.assertEqual(self.run_main(self.args((CONFIG, FEATURE, VENDOR), 192 << 20, 192 << 20, True)), 0)
        self.extractor.extract.assert_not_called()
        self.validation.assert_not_called()
        self.assertEqual([call.args[0] for call in self.fs.resolve.call_args_list], [CONFIG, FEATURE, VENDOR])
        report = self.report()
        self.assertEqual(report["status"], "complete")
        self.assertTrue(report["plan_only"])
        self.assertEqual(report["selected_physical_bytes"], 0)
        self.assertEqual({path.name for path in self.output.iterdir()}, {"read-audit.json", "extraction.json"})
        for proof in report["files"]:
            self.assertNotIn("host_output", proof)
            self.assertNotIn("sha256", proof)
            self.assertTrue(proof["inventory_inode_verified"])
        limits = self.ranges_ctor.call_args.kwargs
        self.assertEqual(limits, dict(max_target=192 << 20, max_encoded=192 << 20, max_decoded=192 << 20,
                                      max_operation=8 << 20, cache_bytes=64 << 20))
        self.extractor_ctor.assert_called_once_with(self.fs, max_file_bytes=192 << 20,
            max_physical_bytes=192 << 20, max_metadata_bytes=4 << 20, budget_ceiling=192 << 20)
        self.ranges.close.assert_called_once_with()

    def test_selected_outputs_match_inode_and_exact_hash_proofs(self):
        self.resolved[APK]["nid"] = -1  # Unselected records must not be resolved or decoded.
        self.assertEqual(self.run_main(self.args((CONFIG, FEATURE, VENDOR))), 0)
        report = self.report()
        self.assertEqual(report["status"], "complete")
        self.assertFalse(report["plan_only"])
        self.assertEqual([proof["path"] for proof in report["files"]], [CONFIG, FEATURE, VENDOR])
        for proof in report["files"]:
            path = proof["path"]
            output = Path(proof["host_output"])
            self.assertEqual(output.parent, self.output.resolve())
            self.assertEqual(output.read_bytes(), self.data[path])
            self.assertEqual(proof["sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
            self.assertEqual(proof["bytes"], self.pins[path]["size"])
            self.assertEqual(proof["inode"], self.pins[path])
            self.assertTrue(proof["inventory_inode_verified"])
        audit = json.loads((self.output / "read-audit.json").read_text(encoding="utf-8"))
        self.assertEqual(audit["totals"]["target_bytes"], 0)
        self.assertEqual(len(self.stdout.getvalue().splitlines()), 4)
        self.ranges.close.assert_called_once_with()

    def test_all_three_archive_names_use_real_static_validator(self):
        with patch("subprocess.run", side_effect=AssertionError("no phone or script processes")), \
                patch("os.system", side_effect=AssertionError("no shell execution")):
            self.assertEqual(self.run_main(self.args((APK, SDK, ADAPTER))), 0)
        self.validation.assert_not_called()
        for proof in self.report()["files"]:
            self.assertTrue(proof["container_validation"]["zip_crc_verified"])
            self.assertEqual(proof["container_validation"]["dex_files"], ["classes.dex"])

    def test_every_inode_proof_key_must_match_before_plan_or_decode(self):
        for key in PROOF_KEYS:
            with self.subTest(key=key):
                self.output = self.work / ("mismatch-" + key)
                self.resolved[CONFIG] = deepcopy(self.pins[CONFIG])
                self.resolved[CONFIG][key] += 1
                self.assertEqual(self.run_main(self.args(plan=True)), 2)
                self.assertIn("differs from prior targeted inventory", self.report()["error"])
        self.extractor.plan.assert_not_called()
        self.extractor.extract.assert_not_called()

    def test_missing_or_misspelled_inode_keys_fail_closed(self):
        for key in PROOF_KEYS:
            self.output = self.work / ("missing-" + key)
            expected = deepcopy(self.pins[CONFIG])
            expected["unknown_" + key] = expected.pop(key)
            self.write_inventory([{"path": CONFIG, "inode": expected}])
            self.assertEqual(self.run_main(self.args()), 2)
            self.assertIn("KeyError", self.report()["error"])
            self.assertFalse((self.output / "camera_unit_config").exists())
        self.extractor.extract.assert_not_called()

    def test_reader_metadata_and_decode_failures_write_audits_not_outputs(self):
        for stage in ("reader", "metadata", "decode"):
            self.output = self.work / stage
            target = (self.ranges_ctor if stage == "reader" else
                      self.extractor.plan if stage == "metadata" else self.extractor.extract)
            previous = target.side_effect
            target.side_effect = ValueError(stage + " byte budget exceeded")
            try:
                self.assertEqual(self.run_main(self.args(plan=stage == "metadata")), 2)
            finally:
                target.side_effect = previous
            report = self.report()
            self.assertEqual(report["status"], "failed")
            self.assertIn(stage + " byte budget exceeded", report["error"])
            self.assertEqual(report["files"], [])
            self.assertEqual({path.name for path in self.output.iterdir()}, {"read-audit.json", "extraction.json"})
        self.assertEqual(self.ranges.close.call_count, 2)

    def test_validation_failure_removes_all_created_outputs(self):
        self.validation.side_effect = [{"valid": True}, ValueError("synthetic config validation failure")]
        self.assertEqual(self.run_main(self.args((CONFIG, FEATURE))), 2)
        self.assertTrue(self.report()["failed_outputs_removed"])
        self.assertEqual({path.name for path in self.output.iterdir()}, {"read-audit.json", "extraction.json"})
        self.ranges.close.assert_called_once_with()
        self.output = self.work / "invalid-apk"
        self.data[APK] = archive_bytes([("not-dex.txt", b"data")])
        self.assertEqual(self.run_main(self.args((APK,))), 2)
        self.assertFalse((self.output / "OplusCamera.apk").exists())
        self.assertIn("DEX", self.report()["error"])

    def test_partial_host_write_is_removed(self):
        real_open = Path.open
        destination = self.output / "camera_unit_config"
        class BrokenWriter:
            def __enter__(inner):
                inner.stream = real_open(destination, "xb")
                return inner
            def write(inner, data):
                inner.stream.write(data[:3])
                raise OSError("synthetic short write")
            def __exit__(inner, *exc):
                inner.stream.close()
        def opened(path, mode="r", *args, **kwargs):
            return BrokenWriter() if path == destination and mode == "xb" else real_open(path, mode, *args, **kwargs)
        with patch.object(Path, "open", opened):
            self.assertEqual(self.run_main(self.args()), 2)
        self.assertFalse(destination.exists())
        self.assertIn("synthetic short write", self.report()["error"])
        self.ranges.close.assert_called_once_with()

    def test_raced_existing_target_is_neither_overwritten_nor_deleted(self):
        destination = self.output / "camera_unit_config"
        def raced(inode):
            destination.write_bytes(b"external owner")
            return self.extract(inode)
        self.extractor.extract.side_effect = raced
        self.assertEqual(self.run_main(self.args()), 2)
        self.assertEqual(destination.read_bytes(), b"external owner")
        self.assertIn("FileExistsError", self.report()["error"])

    def test_reports_use_exclusive_creation_and_repeat_run_cannot_overwrite(self):
        for name in ("read-audit.json", "extraction.json"):
            self.output = self.work / ("race-" + name)
            def raced(payload, partition, audit, **limits):
                result = self.make_ranges(payload, partition, audit, **limits)
                (self.output / name).write_bytes(b"external report")
                return result
            self.ranges_ctor.side_effect = raced
            with self.assertRaises(FileExistsError):
                self.run_main(self.args(plan=True))
            self.assertEqual((self.output / name).read_bytes(), b"external report")
        self.ranges_ctor.side_effect = self.make_ranges
        self.output = self.work / "repeat"
        self.assertEqual(self.run_main(self.args()), 0)
        snapshot = {path.name: path.read_bytes() for path in self.output.iterdir()}
        self.ranges_ctor.reset_mock()
        self.cli_error(self.args())
        self.assertEqual({path.name: path.read_bytes() for path in self.output.iterdir()}, snapshot)

    def test_archive_valid_headers_multiple_dex_and_inert_script_data(self):
        entries = [("classes.dex", dex()), ("classes2.dex", dex(b"038")), ("classes10.dex", dex()),
                   ("classes100.dex", dex()), ("json.py", b"raise AssertionError('must never execute')")]
        path = self.archive(entries)
        before = path.read_bytes()
        validation = e.validate_archive(path)
        self.assertEqual(validation["dex_files"], [name for name, _ in entries[:-1]])
        self.assertTrue(validation["zip_crc_verified"])
        self.assertEqual(validation["entry_count"], len(entries))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual({item.name for item in self.inputs.iterdir()}, {"reference.apk", "inventory.json", "payload.bin"})

    def test_archive_duplicates_traversal_crc_and_no_main_dex(self):
        cases = [([("classes.dex", dex()), ("classes.dex", dex())], "duplicate"),
                 ([("resource.txt", b"data")], "DEX"), ([("classes2.dex", dex())], "DEX")]
        for unsafe in ("/escape", "../escape", "assets/../escape", "assets\\escape"):
            cases.append(([("classes.dex", dex()), (unsafe, b"data")], "unsafe"))
        for entries, message in cases:
            with self.subTest(entries=[name for name, _ in entries]), self.assertRaisesRegex(ValueError, message):
                e.validate_archive(self.archive(entries))
        path = self.archive([("classes.dex", dex())])
        raw = bytearray(path.read_bytes())
        name_len, extra_len = struct.unpack_from("<HH", raw, 26)
        raw[30 + name_len + extra_len] ^= 1
        path.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, "CRC"):
            e.validate_archive(path)
        path.write_bytes(b"not a ZIP")
        with self.assertRaises(zipfile.BadZipFile):
            e.validate_archive(path)

    def test_archive_checks_header_size_endian_signature_and_each_secondary_dex(self):
        mutations = [(0, b"bad!"), (4, b"999"), (32, struct.pack("<I", 111)),
                     (36, struct.pack("<I", 96)), (40, struct.pack("<I", 0)), (12, b"X"), (8, b"X")]
        for offset, value in mutations:
            data = bytearray(dex())
            data[offset:offset + len(value)] = value
            with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, "DEX"):
                e.validate_archive(self.archive([("classes.dex", bytes(data))]))
        with self.assertRaisesRegex(ValueError, "DEX"):
            e.validate_archive(self.archive([("classes.dex", b"dex\n035\0")]))
        for name in ("classes2.dex", "classes10.dex", "classes100.dex"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "DEX"):
                e.validate_archive(self.archive([("classes.dex", dex()), (name, b"invalid secondary")]))

    def test_archive_resource_limits_precede_any_entry_read(self):
        item = lambda size=112, flags=0, method=zipfile.ZIP_STORED: SimpleNamespace(
            file_size=size, flag_bits=flags, compress_type=method)
        cases = [(["x"] * 65537, []), (["classes.dex"], [item((192 << 20) + 1)]),
                 (["classes.dex"], [item(180 << 20)] * 3), (["classes.dex"], [item(flags=1)]),
                 (["classes.dex"], [item(method=zipfile.ZIP_BZIP2)])]
        for names, entries in cases:
            fake = Mock()
            fake.__enter__ = Mock(return_value=fake)
            fake.__exit__ = Mock(return_value=False)
            fake.namelist.return_value, fake.infolist.return_value = names, entries
            with patch.object(e.zipfile, "ZipFile", return_value=fake), self.assertRaises(ValueError):
                e.validate_archive(self.inputs / "metadata-only.apk")
            fake.testzip.assert_not_called()
            fake.read.assert_not_called()

    def test_trusted_sibling_imports_and_legacy_four_mib_ceiling_remain(self):
        self.assertEqual(Path(REAL_READER.__file__).resolve(), (SOURCE.parent / "payload_range_reader.py").resolve())
        self.assertEqual(Path(REAL_COMPRESSED.__file__).resolve(), (SOURCE.parent / "erofs_compressed.py").resolve())
        self.assertEqual(REAL_COMPRESSED.INODE_PROOF_KEYS, PROOF_KEYS)
        self.assertEqual(REAL_COMPRESSED.MAX_BUDGET, 4 << 20)
        defaults = inspect.signature(REAL_COMPRESSED.ExactCompressedErofs).parameters
        self.assertEqual(defaults["budget_ceiling"].default, 4 << 20)
        self.assertEqual(defaults["max_physical_bytes"].default, 4 << 20)
        for limits in ({"max_file_bytes": (4 << 20) + 1}, {"max_physical_bytes": (4 << 20) + 1},
                       {"max_metadata_bytes": (4 << 20) + 1}):
            with self.assertRaises(REAL_COMPRESSED.ExtractionError):
                REAL_COMPRESSED.ExactCompressedErofs(object(), **limits)
        argv = ["--adb", "never-run-adb", "--serial", "synthetic", "--device", "/dev/block/dm-0",
                "--inventory", str(self.inventory), "--output", str(self.output), "--budget", str((4 << 20) + 1)]
        with patch.object(REAL_COMPRESSED, "load_module", side_effect=AssertionError("device code must not load")), \
                redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            REAL_COMPRESSED.main(argv)
        self.assertEqual(caught.exception.code, 2)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
