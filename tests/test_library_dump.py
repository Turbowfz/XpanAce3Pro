"""Offline library-dump parser tests; python -I tests/test_library_dump.py.

Extract only named functions from trusted runtime source, never its CLI. No PM,
property or mount commands run; synthetic inputs and scripts live under _tmp.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SDK = "/product/framework/com.oplus.camera.unit.sdk.jar"
ADAPTER = "/product/framework/com.oplus.camera.unit.sdk.adapter.jar"
OVERLAY = "/my_product/product_overlay/framework"

# Sanitized excerpt of readonly camera-package.txt lines 468-475. Only the
# library block and a neutral next field remain; no identity or device metadata.
CAPTURE = """    usesLibraryFiles:
      /system/framework/org.apache.http.legacy.jar
      /product/framework/com.oplus.camera.unit.sdk.jar
      /system_ext/framework/androidx.window.extensions.jar
      /system_ext/framework/androidx.window.sidecar.jar
      libcdsprpc.so
      libOpenCL.so
    timeStamp=redacted
"""


def named_function(source, name):
    pattern = rf"^{re.escape(name)}\(\) \{{[^\n]*\n.*?^\}}\n"
    if name == "xpan_bounded":
        pattern = rf"^{re.escape(name)}\(\) \{{[^\n]*\}}\n"
    matches = re.findall(pattern, source, re.MULTILINE | re.DOTALL)
    if len(matches) != 1:
        raise AssertionError(f"expected exactly one trusted function: {name}")
    return matches[0]


def bracket(*paths):
    return "    usesLibraryFiles=[" + ", ".join(paths) + "]\n"


def block(*paths, end="    timeStamp=redacted\n"):
    return "    usesLibraryFiles:\n" + "".join("      " + p + "\n" for p in paths) + end


class LibraryDumpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        shell = shutil.which("sh")
        if not shell or not shutil.which("awk") or not shutil.which("timeout"):
            raise unittest.SkipTest("POSIX sh, awk and timeout are required")
        cls.shell = shell
        source = (ROOT / "module/runtime.sh").read_text(encoding="utf-8")
        cls.parser = named_function(source, "parsecamera_libraryfiles")
        cls.bounded = named_function(source, "xpan_bounded")
        cls.qualify = named_function(source, "xpan_qualify")
        cls.temp = tempfile.TemporaryDirectory(prefix="xpan-library-test.", dir=ROOT / "_tmp")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        cls.runner = cls.work / "parser.sh"
        cls.runner.write_text(cls.bounded + cls.parser + "X_SECONDS=2\nparsecamera_libraryfiles\n",
                              encoding="utf-8", newline="\n")
        cls.env = os.environ.copy()
        for key in ("BASH_ENV", "ENV", "XPAN_TEST_MODE", "XPAN_TEST_ROOT", "TEST_HASH"):
            cls.env.pop(key, None)

    def run_script(self, script, dump):
        # Relative trusted script names avoid Git Bash/Windows path conversion;
        # diagnostic bytes are data on stdin and a file, never shell code.
        fixture = self.work / "input.txt"
        fixture.write_text(dump, encoding="utf-8", newline="\n")
        return subprocess.run([self.shell, "./" + script.name], input=dump, text=True, encoding="utf-8",
                              capture_output=True, cwd=self.work, env=self.env, timeout=6)

    def assert_parses(self, dump, expected):
        result = self.run_script(self.runner, dump)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "".join(p + "\n" for p in expected))
        self.assertEqual(result.stderr, "")

    def assert_rejects(self, dump):
        result = self.run_script(self.runner, dump)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "", "failed parse must not leak partial SDK evidence")

    def test_actual_sanitized_capture_has_only_direct_sdk(self):
        self.assert_parses(CAPTURE, [SDK])

    def test_original_bracket_two_paths_and_whitelisted_overlay(self):
        for parent in ("/product/framework", OVERLAY):
            paths = [parent + "/" + Path(p).name for p in (SDK, ADAPTER)]
            for diagnostic in (bracket(*paths), block(*paths)):
                with self.subTest(parent=parent, diagnostic=diagnostic):
                    self.assert_parses(diagnostic, paths)

    def test_sdk_alone_is_parseable_not_adapter_evidence(self):
        for diagnostic in (bracket(SDK), block(SDK)):
            with self.subTest(diagnostic=diagnostic):
                self.assert_parses(diagnostic, [SDK])

    def test_unrelated_valid_java_and_native_libraries_are_ignored(self):
        paths = ["/system/framework/com.example.unknown.jar", "libfuture_camera.so",
                 SDK, "/vendor/lib64/libfuture-camera.so", ADAPTER]
        for diagnostic in (bracket(*paths), block(*paths)):
            with self.subTest(diagnostic=diagnostic):
                self.assert_parses(diagnostic, [SDK, ADAPTER])

    def test_indentation_ends_block_before_other_library_fields(self):
        diagnostic = block(SDK, end="    usesOptionalLibraries:\n      " + ADAPTER + "\n")
        self.assert_parses(diagnostic, [SDK])
        self.assert_parses(block(SDK, end="  User 0: installed=true\n"), [SDK])

    def test_duplicate_fields_including_malformed_counter_fields_rejected(self):
        malformed = ("    usesLibraryFiles=broken\n", "    usesLibraryFiles :\n",
                     "    usesLibraryFiles=[broken\n", "    usesLibraryFiles\n",
                     "    usesLibraryFiles: []\n")
        for diagnostic in (bracket(SDK) + bracket(SDK), CAPTURE + bracket(SDK),
                           bracket(SDK) + CAPTURE, CAPTURE + CAPTURE):
            with self.subTest(diagnostic=diagnostic):
                self.assert_rejects(diagnostic)
        for field in malformed:
            for diagnostic in (bracket(SDK) + field, field + bracket(SDK)):
                with self.subTest(diagnostic=diagnostic):
                    self.assert_rejects(diagnostic)

    def test_duplicate_rows_and_sdk_identities_rejected(self):
        overlay_sdk = OVERLAY + "/" + Path(SDK).name
        overlay_adapter = OVERLAY + "/" + Path(ADAPTER).name
        for paths in ((SDK, SDK), (SDK, ADAPTER, ADAPTER), (SDK, overlay_sdk),
                      (SDK, ADAPTER, overlay_adapter), (SDK, "libOpenCL.so", "libOpenCL.so")):
            for diagnostic in (bracket(*paths), block(*paths)):
                with self.subTest(diagnostic=diagnostic):
                    self.assert_rejects(diagnostic)

    def test_direct_sdk_required_and_unknown_only_lists_rejected(self):
        for paths in ((), (ADAPTER,), ("/system/framework/com.example.unknown.jar",),
                      ("libOpenCL.so",)):
            for diagnostic in (bracket(*paths), block(*paths)):
                with self.subTest(diagnostic=diagnostic):
                    self.assert_rejects(diagnostic)
        self.assert_rejects("    usesLibraries:\n      " + SDK + "\n")
        self.assert_rejects("    usesLibraryFilesExtra=[" + SDK + "]\n")

    def test_malformed_single_bracket_fields_rejected(self):
        for field in ("usesLibraryFiles=[" + SDK, "usesLibraryFiles=[" + SDK + "] trailing",
                      "usesLibraryFiles=[" + SDK + ",]", "usesLibraryFiles=[," + SDK + "]",
                      "usesLibraryFiles=[" + SDK + "; " + ADAPTER + "]",
                      "usesLibraryFiles=[ " + SDK + "]", "usesLibraryFiles=[" + SDK + " ]",
                      "usesLibraryFiles=[null, " + SDK + "]", "usesLibraryFiles = [" + SDK + "]",
                      "usesLibraryFiles: [" + SDK + "]", "usesLibraryFiles=[[" + SDK + "]]",
                      "usesLibraryFiles=[" + SDK + "\n      " + ADAPTER + "]"):
            with self.subTest(field=field):
                self.assert_rejects("    " + field + "\n")

    def test_multiline_empty_truncated_and_malformed_blocks_rejected(self):
        diagnostics = ("    usesLibraryFiles:\n", block(end=""), block(SDK, end=""),
                       "    usesLibraryFiles:\n    timeStamp=redacted\n",
                       "    usesLibraryFiles:\n\n      " + SDK + "\n    timeStamp=x\n",
                       block(SDK, "not-a-library"), block(SDK, "null"),
                       block(SDK, "[" + ADAPTER + "]"), block(SDK, SDK + ","),
                       block(SDK, end="      timeStamp=redacted\n"),
                       block(SDK, end="      usesOptionalLibraries:\n        libOpenCL.so\n"),
                       block(SDK, end="     " + ADAPTER + "\n    timeStamp=x\n"),
                       block(SDK, end="        " + ADAPTER + "\n    timeStamp=x\n"),
                       block(SDK, end="    " + ADAPTER + "\n"),
                       block(SDK, end="    unexpected diagnostic\n"),
                       "\tusesLibraryFiles:\n\t  " + SDK + "\n    timeStamp=x\n",
                       "    usesLibraryFiles:\n\t" + SDK + "\n    timeStamp=x\n")
        for diagnostic in diagnostics:
            with self.subTest(diagnostic=diagnostic):
                self.assert_rejects(diagnostic)

    def test_unknown_sdk_parents_and_lookalike_filenames_rejected(self):
        wrong_paths = [parent + "/" + Path(SDK).name for parent in
                       ("/system/framework", "/system_ext/framework", "/vendor/framework",
                        "/product/framework/subdir", "/data/local/tmp",
                        "/my_product/product_overlay", "/product//framework",
                        "/product/framework/../framework")]
        wrong_paths += [SDK + ".bak", SDK.replace(".jar", ".so"), SDK + ".evil.jar",
                        SDK.removeprefix("/"), "/product/framework/prefix." + Path(SDK).name,
                        SDK + "$()", "/product/framework/" + Path(SDK).name.replace("sdk", "sdk2")]
        for wrong in wrong_paths:
            for diagnostic in (bracket(SDK, wrong), block(SDK, wrong)):
                with self.subTest(wrong=wrong, diagnostic=diagnostic):
                    self.assert_rejects(diagnostic)
        for parent in ("/system/framework", "/vendor/framework"):
            wrong_adapter = parent + "/" + Path(ADAPTER).name
            self.assert_rejects(bracket(SDK, wrong_adapter))
            self.assert_rejects(block(SDK, wrong_adapter))

    def test_unrelated_malformed_rows_do_not_hide_in_valid_sdk_field(self):
        for wrong in ("unknown-library", "relative/future.jar", "/system/../framework/future.jar",
                      "/system//framework/future.jar", "/system/framework/future.jar trailing",
                      "libFuture.so;exit", "/system/framework/$(exit).jar", "libOpenCL.so\r",
                      "/system/framework/future.jar\x00"):
            for diagnostic in (bracket(SDK, wrong), block(SDK, wrong)):
                with self.subTest(wrong=wrong):
                    self.assert_rejects(diagnostic)

    def test_resource_bounds_reject_even_after_valid_field(self):
        diagnostics = (bracket(SDK) + "x" * 4097 + "\n", bracket(SDK) + "\n" * 8192,
                       bracket(SDK) + ("x" * 4096 + "\n") * 256,
                       block(SDK, *(f"libfuture{i}.so" for i in range(64))))
        for index, diagnostic in enumerate(diagnostics):
            with self.subTest(bound=index):
                self.assert_rejects(diagnostic)

    def test_qualify_uses_only_attested_parent_and_pinned_adapter_footprint(self):
        # Only external dependencies are inert functions. Record the exact paths
        # reaching hash checks without creating fake system/PM/mount executables.
        script = self.work / "qualify.sh"
        qualify = self.qualify.replace("xpan_bounded getprop", "qualification_input getprop")
        qualify = qualify.replace("xpan_bounded dumpsys", "qualification_input dumpsys")
        script.write_text(self.bounded + self.parser + qualify + r"""
qualification_input() {
    case "$1:$2" in
        getprop:ro.product.model) printf 'PJX110\n' ;;
        getprop:ro.build.fingerprint) printf 'fixture-rom\n' ;;
        dumpsys:package)
            printf '  Package [com.oplus.camera] (fixture):\n    versionName=7.013.51\n'
            command awk '{ print }' ./input.txt ;;
        *) return 91 ;;
    esac
}
xpan_load_profile() {
    X_CAMERA=7.013.51; X_ROM=fixture-rom
    X_SDK_HASH=d2b20133252414e26ac7c68255c76fa61cf41826c3b63d423a14c1187af42899
    X_ADAPTER_HASH=7418d59955746d8aca56438a5e0e26e1f08ff6ae4d0cacc22057d0041b12aac4
}
xpan_framework_hash() {
    printf '%s\n' "$1"
    case "$1" in
        */com.oplus.camera.unit.sdk.jar) X_HASH=$X_SDK_HASH ;;
        */com.oplus.camera.unit.sdk.adapter.jar) X_HASH=$X_ADAPTER_HASH ;;
        *) return 92 ;;
    esac
    case "${TEST_HASH:-}:$1" in
        missing-adapter:*/com.oplus.camera.unit.sdk.adapter.jar) return 1 ;;
        wrong-adapter:*/com.oplus.camera.unit.sdk.adapter.jar) X_HASH=wrong ;;
        c16-sdk:*/com.oplus.camera.unit.sdk.jar)
            X_HASH=37a13d408f7ee95b8e954c2ef11c44a0aee433da45958cc7ddb80c688e0692c6 ;;
        c16-adapter:*/com.oplus.camera.unit.sdk.adapter.jar)
            X_HASH=ac0b595faae8d3f684aa457f223fbfb0f167f0d0e0cc461ef08dbbe52d6e61cf ;;
    esac
}
xpan_role() { X_SOURCE=fixture-$1; X_OUT=fixture-hash; }
xpan_hash() { X_HASH=fixture-hash; }
X_SECONDS=2
xpan_qualify
""", encoding="utf-8", newline="\n")
        overlay_sdk = OVERLAY + "/" + Path(SDK).name
        overlay_adapter = OVERLAY + "/" + Path(ADAPTER).name
        for diagnostic, expected in ((CAPTURE, [SDK, ADAPTER]),
                                     (bracket(SDK, ADAPTER), [SDK, ADAPTER]),
                                     (block(SDK, ADAPTER), [SDK, ADAPTER]),
                                     (bracket(overlay_sdk), [overlay_sdk, overlay_adapter]),
                                     (CAPTURE.replace(SDK, overlay_sdk), [overlay_sdk, overlay_adapter])):
            with self.subTest(diagnostic=diagnostic):
                result = self.run_script(script, diagnostic)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "".join(p + "\n" for p in expected))
        for diagnostic in (bracket(ADAPTER), bracket(SDK, overlay_adapter),
                           bracket(SDK, SDK), bracket("/system/framework/" + Path(SDK).name),
                           CAPTURE + "    usesLibraryFiles=[" + SDK + "]\n"):
            with self.subTest(diagnostic=diagnostic):
                result = self.run_script(script, diagnostic)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
        for mismatch in ("missing-adapter", "wrong-adapter", "c16-sdk", "c16-adapter"):
            self.env["TEST_HASH"] = mismatch
            try:
                for diagnostic in (CAPTURE, bracket(SDK, ADAPTER)):
                    with self.subTest(mismatch=mismatch, diagnostic=diagnostic):
                        result = self.run_script(script, diagnostic)
                        self.assertNotEqual(result.returncode, 0)
                        expected = [SDK] if mismatch == "c16-sdk" else [SDK, ADAPTER]
                        self.assertEqual(result.stdout, "".join(p + "\n" for p in expected))
            finally:
                self.env.pop("TEST_HASH", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
