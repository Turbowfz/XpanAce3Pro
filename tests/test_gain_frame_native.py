"""Compiled local gain-frame prototype tests; no APK, phone, network or publication.

Host mock tests execute the SAME C++ wrapper used for Android compilation.
ASan/UBSan support is probed by behavior, with explicit unavailable evidence.
"""
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "tools/build_gain_frame_prototype.py"
spec = importlib.util.spec_from_file_location("gain_frame_prototype_builder", BUILDER)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
EXPECTED_CASES = {"core_rgb888_full_width_5_roi_padding_guards", "core_y8_odd_width_roi_padding_guards",
    "core_fail_closed_roi_capacity_overflow_alias_budget", "wrapper_rgb888_independent_canvas_cpu_only",
    "wrapper_y8_odd_width_explicit_gpu_sampling", "wrapper_nested_reentry_balanced_borrowed_ownership",
    "wrapper_null_format_layer_roi_fence_dimensions_usage_source_layout", "allocate_error", "allocate_null",
    "allocate_error_object", "allocate_alias", "target_shape", "target_height", "target_layer", "target_format",
    "target_usage", "target_lock", "target_planes0", "target_planes2", "target_planes5", "target_null",
    "target_pixel_stride", "target_row_stride", "target_row_budget", "target_memory_alias",
    "target_unlock_failure", "both_unlock_failures", "source_unlock_failure_discards_completed_target",
    "cleanup_unlock_failure_overrides_allocate_failure", "all_host_tests"}


class CompiledGainFrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        # Snapshot, not a baked-in repository baseline: preserve existing user edits.
        protected = list((ROOT / "hook").rglob("*")) + list((ROOT / "module").rglob("*"))
        protected += [ROOT / name for name in ("tools/build_hook.py", "tools/package_module.py", "README.md",
                                               "changelog.md", "update.json", ".gitignore", ".gitattributes")]
        cls.protected = {str(p): builder.sha256(p) for p in protected if p.is_file()}
        old_outputs = {p.resolve() for p in (ROOT / "_tmp").glob("gain-frame-prototype-*")}
        result = subprocess.run([sys.executable, "-I", str(BUILDER)], cwd=ROOT,
                                capture_output=True, text=True, encoding="utf-8",
                                errors="backslashreplace", timeout=900)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        lines = [line.removeprefix("Prototype output: ") for line in result.stdout.splitlines()
                 if line.startswith("Prototype output: ")]
        if len(lines) != 1:
            raise AssertionError("Builder did not report exactly one fresh output directory")
        cls.output = Path(lines[0]).resolve()
        if cls.output in old_outputs or cls.output.parent != (ROOT / "_tmp").resolve():
            raise AssertionError(f"Builder output was not fresh and isolated: {cls.output}")
        cls.report = json.loads((cls.output / "provenance.json").read_text(encoding="utf-8"))
        print(result.stdout.strip())

    def test_native_host_matrix_and_actual_sanitizer_evidence(self):
        host = self.report["host"]
        self.assertEqual(set(host["normal"]["cases"]), EXPECTED_CASES)
        self.assertGreater(host["normal"]["checks"], 5000)
        self.assertEqual(host["normal"]["compile"]["returncode"], 0)
        self.assertEqual(host["normal"]["run"]["returncode"], 0)
        for kind, evidence in host["sanitizers"].items():
            with self.subTest(sanitizer=kind):
                self.assertTrue(evidence["probes"])
                for probe in evidence["probes"]:
                    self.assertTrue(Path(probe["compile"]["log"]).is_file())
                if evidence["available"]:
                    self.assertEqual(evidence["status"], "passed")
                    violation = evidence["probes"][-1]
                    self.assertTrue(violation["violation_confirmed"])
                    self.assertNotEqual(violation["run"]["returncode"], 0)
                    self.assertEqual(set(evidence["suite"]["cases"]), EXPECTED_CASES)
                    self.assertEqual(evidence["suite"]["checks"], host["normal"]["checks"])
                else:
                    self.assertEqual(evidence["status"], "unavailable")
                    self.assertIn("reason", evidence)
                    self.assertNotIn("suite", evidence)

    def test_real_android_public_header_elf_exports_and_provenance(self):
        report = self.report
        self.assertTrue(report["ok"])
        self.assertEqual(report["markers"], builder.MARKERS)
        self.assertFalse(report["in_apk"])
        self.assertFalse(report["native_gainmap_generator"])
        android = report["android"]
        lib = Path(android["library"])
        self.assertEqual(lib.parent, self.output)
        self.assertEqual(lib.name, builder.LIB_NAME)
        self.assertEqual(builder.sha256(lib), android["library_sha256"])
        self.assertFalse(android["runtime_tested_on_device"])
        self.assertEqual(android["elf"]["exports"], [builder.EXPORT])
        self.assertEqual(android["elf"]["machine_number"], 183)
        self.assertNotIn("libc++_shared.so", android["elf"]["dt_needed"])
        self.assertEqual({n for n in android["elf"]["imports"] if n.startswith("AHardwareBuffer_")}, builder.PUBLIC_AHB)
        self.assertTrue(any(p.endswith("android\\hardware_buffer.h") or p.endswith("android/hardware_buffer.h")
                            for p in android["header_sha256"]))
        self.assertFalse(any(Path(p).name == "mock_ahardware_buffer.h" for p in android["header_sha256"]))
        for collection in (report["source_sha256"], android["tools"], android["header_sha256"]):
            for path, digest in collection.items():
                self.assertEqual(builder.sha256(path), digest, path)
        flags = android["compile"][0]["command"]
        self.assertIn("--target=aarch64-linux-android29", flags)
        self.assertNotIn("-DXPAN_GAINFRAME_HOST_TEST=1", flags)
        self.assertIn("-static-libstdc++", android["link"]["command"])
        self.assertTrue(Path(android["inspection"]["log"]).is_file())

    def test_no_changes_to_production_paths_or_release_files(self):
        for path, digest in self.protected.items():
            self.assertEqual(builder.sha256(path), digest, path)
        self.assertEqual(self.output.parent, (ROOT / "_tmp").resolve())
        self.assertFalse((self.output / "failure.json").exists())
        self.assertFalse(list(self.output.glob("*.apk")))
        self.assertFalse(list(self.output.glob("*.zip")))
        # Explicit geometry is required; no automatic production integration exists.
        for name in ("tools/build_hook.py", "tools/package_module.py", "hook/XpanHook.java", "hook/XpanGainMapFrame.java"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn(builder.LIB_NAME, text)
            self.assertNotIn(builder.EXPORT, text)
        self.assertEqual(self.report["bounds"]["padding"], "untouched")
        self.assertIn("no JNI", self.report["caller_gap"])


class FailClosedVerifierTests(unittest.TestCase):
    def setUp(self):
        self.binary = bytearray(64)
        self.binary[:7] = b"\x7fELF\x02\x01\x01"
        struct.pack_into("<HH", self.binary, 16, 3, 183)
        self.text = "Machine: AArch64\nSymbol table '.dynsym' contains 9 entries:\n"
        self.text += "0: 0 0 NOTYPE LOCAL DEFAULT UND \n"
        for i, name in enumerate(sorted(builder.PUBLIC_AHB), 1):
            self.text += f"{i}: 0 0 FUNC GLOBAL DEFAULT UND {name}\n"
        self.text += "7: 4000 80 FUNC GLOBAL DEFAULT 13 " + builder.EXPORT + "\n"
        self.text += "8: 0 0 FUNC GLOBAL DEFAULT UND memcpy@LIBC\n"
        for lib in ("libandroid.so", "liblog.so", "libc.so"):
            self.text += f"0x1 (NEEDED) Shared library: [{lib}]\n"
        self.text += "0xe (SONAME) Library soname: [" + builder.LIB_NAME + "]\n"

    def test_header_and_readelf_evidence_reject_malformed_outputs(self):
        bad_magic = bytearray(self.binary); bad_magic[0] = 0
        wrong_machine = bytearray(self.binary); struct.pack_into("<H", wrong_machine, 18, 62)
        wrong_type = bytearray(self.binary); struct.pack_into("<H", wrong_type, 16, 2)
        variants = [(self.binary[:10], self.text), (bad_magic,self.text), (wrong_machine,self.text), (wrong_type,self.text),
            (self.binary, ""), (self.binary, self.text.replace("Machine: AArch64", "Machine: x86_64")),
            (self.binary, self.text.replace(builder.EXPORT, "not_the_export")),
            (self.binary, self.text.replace("DEFAULT 13 " + builder.EXPORT, "DEFAULT UND " + builder.EXPORT)),
            (self.binary, self.text + "9: 4001 80 FUNC GLOBAL DEFAULT 13 hidden_helper\n"),
            (self.binary, self.text.replace("[liblog.so]", "[libvendor.so]")),
            (self.binary, self.text + "0x1 (NEEDED) Shared library: [libc++_shared.so]\n"),
            (self.binary, self.text.replace("AHardwareBuffer_lockPlanes", "AHardwareBuffer_privateLock")),
            (self.binary, self.text.replace("Library soname: [" + builder.LIB_NAME + "]", "Library soname: [different.so]"))]
        for binary, text in variants:
            with self.subTest(binary_size=len(binary), text=text):
                with self.assertRaises(ValueError):
                    builder.validate_elf_evidence(binary, text)

    def test_missing_explicit_tools_do_not_fall_back_or_download(self):
        missing = ROOT / "_tmp/nonexistent-gain-frame-toolchain"
        with self.assertRaises(ValueError):
            builder.find_ndk(missing)
        with self.assertRaises(ValueError):
            builder.find_host_compiler(missing)

    def test_msvc_setup_quotes_paths_and_pins_exact_toolset(self):
        command = builder.msvc_setup_command(Path("C:/Windows/System32/cmd.exe"),
            Path("D:/VS&Tools/VC/Auxiliary/Build/vcvars64.bat"), "14.29.30133")
        self.assertIn('call "D:/VS&Tools/VC/Auxiliary/Build/vcvars64.bat"', command.replace("\\", "/"))
        self.assertIn("-vcvars_ver=14.29.30133", command)
        self.assertIn("/v:off /s /c", command)
        for path in ("D:/VS%NAME%/vcvars64.bat", "D:/VS!NAME!/vcvars64.bat", 'D:/VS"/vcvars64.bat',
                     "D:/VS^Tools/vcvars64.bat"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                builder.msvc_setup_command(Path("C:/Windows/System32/cmd.exe"), Path(path), "14.29.30133")
        with self.assertRaises(ValueError):
            builder.msvc_setup_command(Path("C:/Windows/System32/cmd.exe"), Path("D:/VS/vcvars64.bat"), "14.29&set")

    def test_incomplete_host_success_text_is_not_accepted(self):
        for text in ("", "PASS all_host_tests\n", "CHECKS 2\nPASS all_host_tests\n", "CHECKS 7124\nPASS some_test\n"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    builder.host_summary(text, {}, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
