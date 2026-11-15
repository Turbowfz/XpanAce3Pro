"""Offline OPCameraPro attached-module completion tests; no device or module writes.

The production helpers/functions are sourced unchanged from module/runtime.sh.
Only test-mode pins, paths and ROM release are injected through XPAN_TEST_* vars.
"""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "module/runtime.sh"
UNIT = "com.oplus.camera.unit.sdk.jar"
ADAPTER = "com.oplus.camera.unit.sdk.adapter.jar"
C16 = {"unit": b"fake C16 unit adapter bytes\n", "adapter": b"fake C16 adapter bytes\n"}
C17 = {"unit": b"fake C17 unit adapter bytes\n", "adapter": b"fake C17 adapter bytes\n"}


class OpCompatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bash = shutil.which("bash")
        if not cls.bash:
            raise unittest.SkipTest("Bash is required")
        cls.env = os.environ.copy()
        cls.env.pop("BASH_ENV", None)
        cls.env.pop("ENV", None)
        cls.env.update(LC_ALL="C.UTF-8", MSYS2_ARG_CONV_EXCL="*")
        cls.temp_root = ROOT / "_tmp/xpan-runtime-test.opcompat"
        cls.temp_root.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        self.cygpath = shutil.which("cygpath")
        self.work = Path(tempfile.mkdtemp(prefix="op-compat-", dir=self.temp_root))
        self.addCleanup(shutil.rmtree, self.work, True)
        self.mod = self.work / "module"
        self.op = self.work / "OPCameraPro"
        self.fw = self.op / "system/product/framework"
        self.state = self.work / "state"
        self.stock = self.work / "stock"
        self.payload = self.mod / "payload/op-compat"
        for path in (self.mod, self.fw, self.state, self.payload / "framework", self.stock):
            path.mkdir(parents=True, exist_ok=True)
        (self.op / "module.prop").write_text("id=OPCameraPro\nversion=3.1.22\n", encoding="utf-8")
        self.pins = {
            "XPAN_TEST_OP_C16_UNIT": hashlib.sha256(C16["unit"]).hexdigest(),
            "XPAN_TEST_OP_C16_ADAPTER": hashlib.sha256(C16["adapter"]).hexdigest(),
            "XPAN_TEST_OP_C17_UNIT": hashlib.sha256(C17["unit"]).hexdigest(),
            "XPAN_TEST_OP_C17_ADAPTER": hashlib.sha256(C17["adapter"]).hexdigest(),
        }

    def posix(self, path):
        # Git Bash tools cannot open native backslash paths.
        if self.cygpath:
            return subprocess.check_output([self.cygpath, "-au", str(path)],
                                           cwd=ROOT, timeout=20).decode("utf-8").strip()
        return str(Path(path).absolute())

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def install_source(self, unit=True, adapter=True):
        lines = []
        if unit:
            self.write(self.payload / "framework" / UNIT, C17["unit"])
            lines.append(UNIT + "\t" + self.pins["XPAN_TEST_OP_C17_UNIT"])
        if adapter:
            self.write(self.payload / "framework" / ADAPTER, C17["adapter"])
            lines.append(ADAPTER + "\t" + self.pins["XPAN_TEST_OP_C17_ADAPTER"])
        (self.payload / "MANIFEST").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    def install_c16_target(self, unit=True, adapter=True):
        if unit:
            self.write(self.fw / UNIT, C16["unit"])
        if adapter:
            self.write(self.fw / ADAPTER, C16["adapter"])

    def run_compat(self, release="17", use_stock=True):
        harness = self.work / "harness.sh"
        harness.write_text(
            "#!/usr/bin/env bash\nRUNTIME=$1 MOD=$2 STATE=$3 OPDIR=$4 STOCK=$5 REL=$6\nset --\n"
            ". \"$RUNTIME\"\nX_SECONDS=10\nexport X_SECONDS\n"
            "X_MOD=$MOD\nX_STATE=$STATE\nexport X_MOD X_STATE\n"
            "export XPAN_TEST_MODE=1 XPAN_TEST_OP_DIR=$OPDIR XPAN_TEST_ROM_RELEASE=$REL\n"
            "if [ \"$STOCK\" != - ]; then export XPAN_TEST_STOCK_ROOT=$STOCK; fi\n"
            "xpan_op_compat\nprintf 'rc=%s\\n' \"$?\"\n",
            encoding="utf-8", newline="\n")
        env = dict(self.env, **self.pins)
        result = subprocess.run([self.bash, "--noprofile", "--norc", str(harness),
                                 self.posix(RUNTIME), self.posix(self.mod), self.posix(self.state),
                                 self.posix(self.op),
                                 self.posix(self.stock) if use_stock else "-", release],
                                cwd=ROOT, env=env, capture_output=True, encoding="utf-8", timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rc=0", result.stdout)
        return result.stderr

    def digest(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_exact_c16_pair_is_replaced_and_backed_up_once(self):
        self.install_source()
        self.install_c16_target()
        log = self.run_compat()
        self.assertIn(UNIT + "=replaced", log)
        self.assertIn(ADAPTER + "=replaced", log)
        self.assertIn("reboot makes it effective", log)
        self.assertEqual(self.digest(self.fw / UNIT), self.pins["XPAN_TEST_OP_C17_UNIT"])
        self.assertEqual(self.digest(self.fw / ADAPTER), self.pins["XPAN_TEST_OP_C17_ADAPTER"])
        backup = self.state / "op-compat-backup"
        self.assertEqual(self.digest(backup / UNIT), self.pins["XPAN_TEST_OP_C16_UNIT"])
        self.assertEqual(self.digest(backup / ADAPTER), self.pins["XPAN_TEST_OP_C16_ADAPTER"])
        before = {p: self.digest(p) for p in (self.fw / UNIT, self.fw / ADAPTER, backup / UNIT)}
        log = self.run_compat()
        self.assertIn(UNIT + "=current", log)
        self.assertNotIn("reboot", log)
        self.assertEqual(before, {p: self.digest(p) for p in before})

    def test_c16_rom_never_touches_completion(self):
        self.install_source()
        self.install_c16_target()
        log = self.run_compat(release="16")
        self.assertIn("is not C17", log)
        self.assertEqual(self.digest(self.fw / UNIT), self.pins["XPAN_TEST_OP_C16_UNIT"])
        self.assertEqual(self.digest(self.fw / ADAPTER), self.pins["XPAN_TEST_OP_C16_ADAPTER"])
        self.assertFalse((self.state / "op-compat-backup").exists())

    def test_any_non_c17_bytes_for_a_bundled_name_are_replaced(self):
        # OP may ship a third variant; with a verified bundled C17 source and a
        # C17 ROM the approved bytes still win, with the old file backed up.
        self.install_source(adapter=True, unit=False)
        self.write(self.fw / ADAPTER, b"some other vendor adapter bytes\n")
        log = self.run_compat()
        self.assertIn(ADAPTER + "=replaced", log)
        self.assertEqual(self.digest(self.fw / ADAPTER), self.pins["XPAN_TEST_OP_C17_ADAPTER"])
        self.assertEqual((self.state / "op-compat-backup" / ADAPTER).read_bytes(),
                         b"some other vendor adapter bytes\n")

    def test_absent_attached_module_is_untouched(self):
        shutil.rmtree(self.op)
        self.install_source()
        log = self.run_compat()
        self.assertIn("no OPCameraPro attached module", log)
        self.assertFalse((self.fw / UNIT).exists())

    def test_identity_mismatch_is_untouched(self):
        self.install_source()
        self.install_c16_target()
        (self.op / "module.prop").write_text("id=SomethingElse\n", encoding="utf-8")
        log = self.run_compat()
        self.assertIn("identity mismatch", log)
        self.assertEqual(self.digest(self.fw / UNIT), self.pins["XPAN_TEST_OP_C16_UNIT"])

    def test_missing_or_wrong_bundled_source_keeps_original(self):
        self.install_c16_target()
        self.write(self.payload / "framework" / UNIT, b"not the approved C17 bytes\n")
        (self.payload / "MANIFEST").write_text(
            UNIT + "\t" + self.pins["XPAN_TEST_OP_C17_UNIT"] + "\n" +
            ADAPTER + "\t" + self.pins["XPAN_TEST_OP_C17_ADAPTER"] + "\n", encoding="utf-8")
        log = self.run_compat()
        self.assertIn(UNIT + "=source-invalid", log)
        self.assertEqual(self.digest(self.fw / UNIT), self.pins["XPAN_TEST_OP_C16_UNIT"])
        # Mirror sync has no stock counterpart here, so the adapter also stays.
        self.assertIn(ADAPTER + "=source-invalid", log)

    def test_absent_and_symlinked_targets_are_not_created(self):
        self.install_source(unit=True, adapter=True)
        self.install_c16_target(unit=True, adapter=False)
        (self.op / "disable").write_text("other-module-marker\n", encoding="utf-8")
        log = self.run_compat()
        self.assertIn(UNIT + "=replaced", log)
        self.assertIn(ADAPTER + "=absent", log)
        self.assertFalse((self.fw / ADAPTER).exists())
        self.assertEqual((self.op / "disable").read_text(encoding="utf-8"), "other-module-marker\n")

    def test_mirror_sync_uses_live_c17_stock_for_other_completion_files(self):
        camera = self.op / "system/odm/etc/camera"
        self.write(camera / "gamma_photo_hdr_conf.json", b"stale C16 gamma\n")
        self.write(camera / "same.json", b"identical bytes\n")
        self.write(self.stock / "odm/etc/camera/gamma_photo_hdr_conf.json", b"current C17 gamma\n")
        self.write(self.stock / "odm/etc/camera/same.json", b"identical bytes\n")
        log = self.run_compat()
        self.assertIn("odm/etc/camera/gamma_photo_hdr_conf.json=mirror-diff", log)
        self.assertIn("synced", log)
        self.assertEqual((camera / "gamma_photo_hdr_conf.json").read_bytes(), b"current C17 gamma\n")
        self.assertEqual((camera / "same.json").read_bytes(), b"identical bytes\n")
        backup = self.state / "op-compat-backup/mirror/odm/etc/camera/gamma_photo_hdr_conf.json"
        self.assertEqual(backup.read_bytes(), b"stale C16 gamma\n")

    def test_c16_filter_assets_are_hidden_only_on_c17(self):
        camera = self.op / "system/odm/etc/camera"
        self.write(camera / "meishe_lut/800t_sdr.bin", b"c16 filter lut\n")
        self.write(camera / "meishe_lut/portra400_master.bin", b"c16 portra\n")
        self.write(camera / "filters_lut/classic.bin", b"c16 filter pack\n")
        self.write(camera / "basictone/tone.bin", b"c16 tone pack\n")
        self.write(camera / "gamma_photo_hdr_conf.json", b"c16 photo gamma\n")
        self.write(camera / "gamma_preview_sdr_conf.json", b"c16 preview gamma\n")
        self.write(camera / "config/op_only.json", b"op camera config\n")
        self.write(camera / "is_mount", b"")
        self.install_source()
        self.install_c16_target()
        log = self.run_compat(release="17")
        self.assertIn("c16-filter-assets=hidden(5)", log)
        for name in ("meishe_lut", "filters_lut", "basictone"):
            self.assertFalse((camera / name).exists())
            self.assertTrue((camera / (name + ".xpan-c16-hidden")).is_dir())
        for name in ("gamma_photo_hdr_conf.json", "gamma_preview_sdr_conf.json"):
            self.assertFalse((camera / name).exists())
            self.assertEqual((camera / (name + ".xpan-c16-hidden")).read_bytes(),
                             b"c16 " + (b"photo" if "photo" in name else b"preview") + b" gamma\n")
        self.assertEqual((camera / "config/op_only.json").read_bytes(), b"op camera config\n")
        self.assertTrue((camera / "is_mount").is_file())
        # Idempotent: a second run reports hidden and moves nothing else.
        log = self.run_compat(release="17")
        self.assertIn("c16-filter-assets=hidden(5)", log)
        self.assertTrue((camera / "meishe_lut.xpan-c16-hidden").is_dir())
        # On C16 the attached module stays exactly as shipped.
        other = self.work / "OPCameraPro16"
        (other / "system/odm/etc/camera/meishe_lut").mkdir(parents=True)
        (other / "module.prop").write_text("id=OPCameraPro\n", encoding="utf-8")
        (other / "system/odm/etc/camera/meishe_lut/800t_sdr.bin").write_bytes(b"c16 filter lut\n")
        harness = self.work / "harness16.sh"
        harness.write_text(
            "#!/usr/bin/env bash\nRUNTIME=$1 MOD=$2 STATE=$3 OPDIR=$4\nset --\n. \"$RUNTIME\"\n"
            "X_SECONDS=10\nexport X_SECONDS\nX_MOD=$MOD\nX_STATE=$STATE\nexport X_MOD X_STATE\n"
            "export XPAN_TEST_MODE=1 XPAN_TEST_OP_DIR=$OPDIR XPAN_TEST_ROM_RELEASE=16\n"
            "xpan_op_compat\nprintf 'rc=%s\\n' \"$?\"\n",
            encoding="utf-8", newline="\n")
        result = subprocess.run([self.bash, "--noprofile", "--norc", str(harness),
                                 self.posix(RUNTIME), self.posix(self.mod), self.posix(self.state),
                                 self.posix(other)],
                                cwd=ROOT, env=dict(self.env, **self.pins),
                                capture_output=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((other / "system/odm/etc/camera/meishe_lut/800t_sdr.bin").is_file())
        self.assertFalse((other / "system/odm/etc/camera/meishe_lut.xpan-c16-hidden").exists())

    def test_c17_assets_stay_when_attached_module_already_ships_c17_completion(self):
        camera = self.op / "system/odm/etc/camera"
        self.write(camera / "meishe_lut/stock.bin", b"c17 filter lut\n")
        self.write(camera / "gamma_photo_hdr_conf.json", b"c17 gamma\n")
        self.install_source()
        self.write(self.fw / UNIT, C17["unit"])
        self.write(self.fw / ADAPTER, C17["adapter"])
        log = self.run_compat(release="17")
        self.assertNotIn("c16-filter-assets=hidden", log)
        self.assertTrue((camera / "meishe_lut/stock.bin").is_file())
        self.assertTrue((camera / "gamma_photo_hdr_conf.json").is_file())
        self.assertFalse((camera / "meishe_lut.xpan-c16-hidden").exists())

    def test_production_guard_keeps_exact_pins_gate_and_no_dangerous_ops(self):
        source = RUNTIME.read_text(encoding="utf-8")
        body = source.split("xpan_op_compat() {", 1)[1].split("\nxpan_install() {", 1)[0]
        self.assertIn("ro.build.version.release", body)
        self.assertIn("17|17.*", body)
        self.assertIn("/MANIFEST", body)
        self.assertIn("OPCameraPro", body)
        self.assertIn("op-compat-backup", body)
        self.assertIn("reboot makes it effective", body)
        for forbidden in ("umount", "pm uninstall", "rm -rf", "OPCameraPro/disable"):
            self.assertNotIn(forbidden, body)
        package = (ROOT / "tools/package_module.py").read_text(encoding="utf-8")
        for pin in ("d2b20133252414e26ac7c68255c76fa61cf41826c3b63d423a14c1187af42899",
                    "7418d59955746d8aca56438a5e0e26e1f08ff6ae4d0cacc22057d0041b12aac4"):
            self.assertIn(pin, package)
        self.assertIn('"payload/op-compat/framework/com.oplus.camera.unit.sdk.jar"', package)
        self.assertIn('"payload/op-compat/framework/com.oplus.camera.unit.sdk.adapter.jar"', package)
        self.assertIn("Bundled C17 completion is not the approved stock original", package)
        self.assertIn('"payload/op-compat/MANIFEST"', package)


if __name__ == "__main__":
    unittest.main(verbosity=2)
