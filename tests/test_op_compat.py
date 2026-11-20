"""OPCameraPro 兼容离线测试，只写项目 _tmp，不证明设备滤镜恢复。"""
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
C16 = {"unit": b"fake C16 unit bytes\n", "adapter": b"fake C16 adapter bytes\n"}
C17 = {"unit": b"fake C17 unit bytes\n", "adapter": b"fake C17 adapter bytes\n"}
MEISHE = ("odm/lib64/libmsnativefilter.so", "product/lib64/libMsEffectSdk.so")
TONE = "odm/lib64/libBasicTonePhoto.so"
HIDDEN = ".xpan-c16-hidden"
CAMERA = "odm/etc/camera"
HARNESS = r'''
RUNTIME=$1 MOD=$2 STATE=$3 OPDIR=$4 STOCK=$5 REL=$6 INFO=$7 DEVICE=$8 INJECT=$9
set --
. "$RUNTIME"
X_SECONDS=10 X_MOD=$MOD X_STATE=$STATE X_INFO=$INFO
export XPAN_TEST_MODE=1 XPAN_TEST_OP_DIR=$OPDIR XPAN_TEST_ROM_RELEASE=$REL XPAN_TEST_ROOT=$DEVICE
unset XPAN_TEST_STOCK_ROOT
if [ "$STOCK" != - ]; then export XPAN_TEST_STOCK_ROOT=$STOCK; fi
eval "$INJECT"
xpan_op_compat
printf 'rc=%s\nchanged=%s\n' "$?" "${X_OC_CHANGED:-0}"
'''
NORMAL = (
    "1 0 0:1 / / rw - rootfs rootfs rw",
    "2 1 254:10 / /odm ro,relatime - erofs /dev/block/dm-10 ro,seclabel",
    "3 1 254:11 / /product ro,relatime - erofs /dev/block/dm-11 ro,seclabel",
    "4 1 254:7 / /my_product ro,relatime - erofs /dev/block/dm-7 ro,seclabel",
    "5 1 254:8 / /my_stock ro,relatime - erofs /dev/block/dm-8 ro,seclabel",
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


class OpCompatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bash = shutil.which("bash")
        cls.cygpath = shutil.which("cygpath") if os.name == "nt" else None
        if not cls.bash:
            raise unittest.SkipTest("Bash is required")
        cls.env = {k: v for k, v in os.environ.items()
                   if k not in ("BASH_ENV", "ENV") and not k.startswith("XPAN_TEST_")}
        cls.env.update(LC_ALL="C.UTF-8", MSYS2_ARG_CONV_EXCL="*", MSYS="winsymlinks:lnk")
        cls.temp_root = ROOT / "_tmp/xpan-runtime-test.opcompat"
        cls.temp_root.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="op-compat-", dir=self.temp_root))
        self.addCleanup(shutil.rmtree, self.work, True)
        self.mod = self.work / "module"
        self.op = self.work / "OPCameraPro"
        self.fw = self.op / "system/product/framework"
        self.cam = self.op / "system" / CAMERA
        self.state = self.work / "state"
        self.stock = self.work / "stock"
        self.device = self.work / "device"
        self.info = self.work / "mountinfo"
        self.payload = self.mod / "payload/op-compat"
        for path in (self.mod, self.fw, self.cam, self.state, self.stock, self.device,
                     self.payload / "framework"):
            path.mkdir(parents=True, exist_ok=True)
        self.text(self.op / "module.prop", "id=OPCameraPro\nversion=3.1.22\n")
        self.text(self.info, "")
        self.pins = {"XPAN_TEST_OP_C16_UNIT": sha(C16["unit"]),
                     "XPAN_TEST_OP_C16_ADAPTER": sha(C16["adapter"])}

    def posix(self, path):
        if self.cygpath:
            return subprocess.check_output([self.cygpath, "-au", str(path)],
                                           cwd=ROOT, env=self.env, timeout=20).decode("utf-8").strip()
        return str(Path(path).absolute())

    def text(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8", newline="\n")

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def target(self, rel):
        return self.op / "system" / rel

    def hidden(self, path):
        return Path(str(path) + HIDDEN)

    def backup(self, rel):
        return self.state / "op-compat-backup/mirror" / rel

    def snapshot(self, root):
        return {str(p.relative_to(root)): p.read_bytes()
                for p in root.rglob("*") if p.is_file() and not p.is_symlink()}

    def install_source(self, unit=True, adapter=True):
        lines = []
        for role, name, enabled in (("unit", UNIT, unit), ("adapter", ADAPTER, adapter)):
            if enabled:
                self.write(self.payload / "framework" / name, C17[role])
                lines.append(name + "\t" + sha(C17[role]))
        self.text(self.payload / "MANIFEST", "\n".join(lines) + "\n")

    def install_target(self, version=16):
        data = C16 if version == 16 else C17
        for role, name in (("unit", UNIT), ("adapter", ADAPTER)):
            self.write(self.fw / name, data[role])

    def install_native(self, clean=False, tone=False):
        for rel in (*MEISHE, *((TONE,) if tone else ())):
            self.write(self.target(rel), b"OP native " + rel.encode() + b"\n")
            if clean:
                self.write(self.stock / rel, b"stock C17 native " + rel.encode() + b"\n")

    def mounts(self, *extra):
        self.text(self.info, "\n".join((*NORMAL, *extra)) + "\n")

    def overlay(self, mount_id, parent_id, point, lower):
        return (f"{mount_id} {parent_id} 0:{mount_id} / {point} rw,relatime - "
                f"overlay KSU\\040 ro,seclabel,lowerdir={lower}")

    def run_compat(self, release="17", use_stock=True, stock_root=None, inject=""):
        result = subprocess.run(
            [self.bash, "--noprofile", "--norc", "-c", HARNESS, "op-compat-test",
             self.posix(RUNTIME), self.posix(self.mod), self.posix(self.state), self.posix(self.op),
             self.posix(stock_root or self.stock) if use_stock else "-", release,
             self.posix(self.info), self.posix(self.device), inject],
            cwd=ROOT, env=dict(self.env, **self.pins), capture_output=True,
            encoding="utf-8", timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rc=0", result.stdout)
        self.last_changed = int(result.stdout.split("changed=", 1)[1].strip())
        return result.stderr

    def status(self):
        return dict(line.split("=", 1) for line in
                    (self.state / "op.compat").read_text(encoding="utf-8").splitlines())

    def bash_run(self, script, *args):
        return subprocess.run([self.bash, "--noprofile", "--norc", "-c", script,
                               "op-compat-test", *(self.posix(p) for p in args)],
                              cwd=ROOT, env=self.env, capture_output=True,
                              encoding="utf-8", timeout=20)

    def link(self, source, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        result = self.bash_run('ln -s "$1" "$2" && test -L "$2"', source, target)
        if result.returncode:
            self.skipTest("Host cannot create unprivileged filesystem symlinks")
        self.assertEqual(self.bash_run('test -L "$1"', target).returncode, 0)

    def test_exact_c16_pair_is_replaced_and_backed_up_once(self):
        self.install_source()
        self.install_target()
        log = self.run_compat()
        for role, name in (("unit", UNIT), ("adapter", ADAPTER)):
            self.assertIn(name + "=replaced", log)
            self.assertEqual((self.fw / name).read_bytes(), C17[role])
            self.assertEqual((self.state / "op-compat-backup" / name).read_bytes(), C16[role])
        self.assertIn("reboot makes it effective", log)
        self.assertEqual(self.status()["changed"], "1")
        before = self.snapshot(self.op), self.snapshot(self.state / "op-compat-backup")
        log = self.run_compat()
        self.assertIn(UNIT + "=current", log)
        self.assertNotIn("reboot", log)
        self.assertEqual(self.last_changed, 0)
        self.assertEqual(before, (self.snapshot(self.op), self.snapshot(self.state / "op-compat-backup")))

    def test_c16_rom_has_no_writes_including_native_assets_and_hidden(self):
        self.install_source()
        self.install_target()
        self.install_native(clean=True, tone=True)
        self.write(self.cam / "meishe_lut/800t.bin", b"OP extra\n")
        self.write(self.cam / "filters_lut.xpan-c16-hidden/gr.bin", b"old hidden\n")
        before = self.snapshot(self.work)
        log = self.run_compat(release="16")
        self.assertIn("is not C17", log)
        self.assertEqual(self.snapshot(self.work), before)
        self.assertEqual(self.last_changed, 0)

    def test_unknown_rom_has_no_writes(self):
        self.install_native()
        before = self.snapshot(self.work)
        self.run_compat(release="18")
        self.assertEqual(self.snapshot(self.work), before)

    def test_non_c17_bytes_for_bundled_name_are_backed_up_without_c16_gate(self):
        self.install_source(unit=False)
        old = b"another OP adapter\n"
        self.write(self.fw / ADAPTER, old)
        log = self.run_compat()
        self.assertIn(ADAPTER + "=replaced", log)
        self.assertEqual((self.fw / ADAPTER).read_bytes(), C17["adapter"])
        self.assertEqual((self.state / "op-compat-backup" / ADAPTER).read_bytes(), old)

    def test_absent_attached_module_is_untouched(self):
        shutil.rmtree(self.op)
        self.install_source()
        before = self.snapshot(self.work)
        log = self.run_compat()
        self.assertIn("no OPCameraPro attached module", log)
        self.assertEqual(self.snapshot(self.work), before)

    def test_identity_mismatch_has_no_writes(self):
        self.install_source()
        self.install_target()
        self.install_native()
        self.text(self.op / "module.prop", "id=SomethingElse\n")
        before = self.snapshot(self.work)
        self.assertIn("identity mismatch", self.run_compat())
        self.assertEqual(self.snapshot(self.work), before)

    def test_duplicate_module_identity_has_no_writes(self):
        self.install_native()
        self.text(self.op / "module.prop", "id=OPCameraPro\nid=Other\n")
        before = self.snapshot(self.work)
        self.assertIn("identity mismatch", self.run_compat())
        self.assertEqual(self.snapshot(self.work), before)

    def test_missing_or_wrong_bundled_source_keeps_original(self):
        self.install_target()
        self.write(self.payload / "framework" / UNIT, b"wrong bundled source\n")
        self.text(self.payload / "MANIFEST", UNIT + "\t" + sha(C17["unit"]) + "\n" +
                  ADAPTER + "\t" + sha(C17["adapter"]) + "\n")
        log = self.run_compat()
        self.assertIn(UNIT + "=source-invalid", log)
        self.assertIn(ADAPTER + "=source-invalid", log)
        self.assertEqual((self.fw / UNIT).read_bytes(), C16["unit"])
        self.assertEqual((self.fw / ADAPTER).read_bytes(), C16["adapter"])
        self.assertEqual(self.last_changed, 0)

    def test_absent_jar_targets_not_created_and_markers_untouched(self):
        self.install_source()
        self.write(self.fw / UNIT, C16["unit"])
        for marker in ("disable", "remove"):
            self.write(self.op / marker, b"leave marker\n")
        log = self.run_compat()
        self.assertIn(ADAPTER + "=absent", log)
        self.assertFalse((self.fw / ADAPTER).exists())
        for marker in ("disable", "remove"):
            self.assertEqual((self.op / marker).read_bytes(), b"leave marker\n")

    def test_c17_jars_do_not_skip_exact_meishe_native_sync(self):
        self.install_source()
        self.install_target(version=17)
        self.install_native(clean=True)
        old = {rel: self.target(rel).read_bytes() for rel in MEISHE}
        stock = self.snapshot(self.stock)
        log = self.run_compat()
        self.assertIn(UNIT + "=current", log)
        for rel in MEISHE:
            self.assertIn(rel + "=synced", log)
            self.assertEqual(self.target(rel).read_bytes(), (self.stock / rel).read_bytes())
            self.assertEqual(self.backup(rel).read_bytes(), old[rel])
            self.assertFalse(self.hidden(self.target(rel)).exists())
        self.assertEqual(self.snapshot(self.stock), stock)
        self.assertEqual(self.status()["rom_release"], "17")
        self.assertEqual(self.last_changed, 1)
        self.assertNotIn("reboot", self.run_compat())
        self.assertEqual(self.last_changed, 0)

    def test_unknown_stock_hides_only_two_meishe_native_and_keeps_tone(self):
        self.install_source()
        self.install_target(version=17)
        self.install_native(tone=True)
        self.write(self.target("odm/lib64/unrelated.so"), b"other module lib\n")
        old = {rel: self.target(rel).read_bytes() for rel in (*MEISHE, TONE)}
        log = self.run_compat(use_stock=False)
        self.assertIn("native-hidden", log)
        self.assertIn("stock-pending", log)
        for rel in MEISHE:
            self.assertFalse(self.target(rel).exists())
            self.assertEqual(self.hidden(self.target(rel)).read_bytes(), old[rel])
            self.assertEqual(self.backup(rel).read_bytes(), old[rel])
        self.assertEqual(self.target(TONE).read_bytes(), old[TONE])
        self.assertFalse(self.hidden(self.target(TONE)).exists())
        self.assertEqual(self.target("odm/lib64/unrelated.so").read_bytes(), b"other module lib\n")
        before = self.snapshot(self.op), self.snapshot(self.state / "op-compat-backup")
        log = self.run_compat(use_stock=False)
        self.assertEqual(self.last_changed, 0)
        self.assertNotIn("reboot", log)
        self.assertEqual(before, (self.snapshot(self.op), self.snapshot(self.state / "op-compat-backup")))

    def test_hidden_native_is_restored_from_clean_stock_not_old_bytes(self):
        self.install_native()
        old = {rel: self.target(rel).read_bytes() for rel in MEISHE}
        self.run_compat(use_stock=False)
        for rel in MEISHE:
            self.write(self.stock / rel, b"clean recovered C17 " + rel.encode() + b"\n")
        log = self.run_compat()
        self.assertIn("reboot makes it effective", log)
        for rel in MEISHE:
            self.assertEqual(self.target(rel).read_bytes(), (self.stock / rel).read_bytes())
            self.assertEqual(self.hidden(self.target(rel)).read_bytes(), old[rel])
            self.assertEqual(self.backup(rel).read_bytes(), old[rel])
        self.run_compat()
        self.assertEqual(self.last_changed, 0)

    def test_hidden_native_conflict_never_overwrites_existing_hidden(self):
        self.install_native()
        old = {rel: self.target(rel).read_bytes() for rel in MEISHE}
        for rel in MEISHE:
            self.write(self.hidden(self.target(rel)), b"previous hidden native\n")
        log = self.run_compat(use_stock=False)
        self.assertIn("hidden-conflict", log)
        for rel in MEISHE:
            self.assertEqual(self.target(rel).read_bytes(), old[rel])
            self.assertEqual(self.hidden(self.target(rel)).read_bytes(), b"previous hidden native\n")
        self.assertEqual(self.last_changed, 0)

    def test_basic_tone_syncs_only_with_clean_counterpart(self):
        self.install_native(clean=True, tone=True)
        before = self.target(TONE).read_bytes()
        self.run_compat()
        self.assertEqual(self.target(TONE).read_bytes(), (self.stock / TONE).read_bytes())
        self.assertEqual(self.backup(TONE).read_bytes(), before)
        self.assertFalse(self.hidden(self.target(TONE)).exists())

    def test_native_is_processed_without_framework_directory(self):
        shutil.rmtree(self.fw)
        self.install_native()
        self.run_compat(use_stock=False)
        self.assertEqual(self.last_changed, 1)
        for rel in MEISHE:
            self.assertTrue(self.hidden(self.target(rel)).is_file())

    def test_stock_assets_win_same_name_and_op_extras_are_preserved(self):
        self.install_source()
        self.install_target(version=17)
        for name in ("meishe_lut/qing_tou.bin", "filters_lut/stock.bin", "basictone/tone.bin",
                     "gamma_photo_hdr_conf.json"):
            self.write(self.cam / name, b"old same-name asset\n")
            self.write(self.stock / CAMERA / name, b"clean C17 same-name asset\n")
        for name in ("meishe_lut/800t_sdr.bin", "meishe_lut/portra400_master.bin",
                     "meishe_lut/gr/nested.bin", "filters_lut/jw.bin"):
            self.write(self.cam / name, b"OP extra " + name.encode() + b"\n")
        extra = {name: (self.cam / name).read_bytes() for name in
                 ("meishe_lut/800t_sdr.bin", "meishe_lut/portra400_master.bin",
                  "meishe_lut/gr/nested.bin", "filters_lut/jw.bin")}
        log = self.run_compat()
        self.assertIn("mirror-diff,synced", log)
        for name in ("meishe_lut/qing_tou.bin", "filters_lut/stock.bin", "basictone/tone.bin",
                     "gamma_photo_hdr_conf.json"):
            self.assertEqual((self.cam / name).read_bytes(), b"clean C17 same-name asset\n")
            self.assertEqual(self.backup(CAMERA + "/" + name).read_bytes(), b"old same-name asset\n")
        for name, data in extra.items():
            self.assertEqual((self.cam / name).read_bytes(), data)
        for directory in ("meishe_lut", "filters_lut", "basictone"):
            self.assertFalse(self.hidden(self.cam / directory).exists())
        self.run_compat()
        self.assertEqual(self.last_changed, 0)

    def test_unknown_stock_keeps_active_and_legacy_assets_pending(self):
        self.write(self.cam / "meishe_lut/op-extra.bin", b"active OP asset\n")
        self.write(self.cam / "filters_lut.xpan-c16-hidden/gr.bin", b"legacy OP asset\n")
        self.write(self.cam / "gamma_photo_hdr_conf.json.xpan-c16-hidden", b"legacy gamma\n")
        before = self.snapshot(self.op)
        log = self.run_compat(use_stock=False)
        self.assertIn("stock-pending", log)
        self.assertNotIn("extra-restored", log)
        self.assertEqual(self.snapshot(self.op), before)
        self.assertEqual(self.last_changed, 0)

    def test_legacy_hidden_migrates_per_file_and_keeps_all_old_bytes(self):
        self.install_source()
        self.install_target(version=17)
        hidden_data = {}
        for directory in ("meishe_lut", "filters_lut", "basictone"):
            for name in ("shared.bin", "OP-extra.bin"):
                path = self.cam / (directory + HIDDEN) / name
                data = (directory + "/" + name).encode() + b" old OP\n"
                self.write(path, data)
                hidden_data[path] = data
            self.write(self.stock / CAMERA / directory / "shared.bin", b"stock C17 shared\n")
        gamma = self.cam / "gamma_photo_hdr_conf.json"
        self.write(self.hidden(gamma), b"old gamma\n")
        self.write(self.stock / CAMERA / gamma.name, b"stock gamma\n")
        hidden_data[self.hidden(gamma)] = b"old gamma\n"
        extra_gamma = self.cam / "gamma_op_conf.json"
        self.write(self.hidden(extra_gamma), b"OP-only gamma\n")
        hidden_data[self.hidden(extra_gamma)] = b"OP-only gamma\n"
        log = self.run_compat()
        self.assertIn("extra-restored", log)
        for directory in ("meishe_lut", "filters_lut", "basictone"):
            self.assertEqual((self.cam / directory / "shared.bin").read_bytes(), b"stock C17 shared\n")
            self.assertEqual((self.cam / directory / "OP-extra.bin").read_bytes(),
                             hidden_data[self.cam / (directory + HIDDEN) / "OP-extra.bin"])
        self.assertEqual(gamma.read_bytes(), b"stock gamma\n")
        self.assertEqual(extra_gamma.read_bytes(), b"OP-only gamma\n")
        for path, data in hidden_data.items():
            self.assertEqual(path.read_bytes(), data)
        before = self.snapshot(self.op)
        self.run_compat()
        self.assertEqual(self.last_changed, 0)
        self.assertEqual(self.snapshot(self.op), before)

    def test_legacy_conflict_keeps_updated_target_and_hidden_bytes(self):
        for name in ("meishe_lut/extra.bin", "filters_lut/stock.bin"):
            directory, leaf = name.split("/", 1)
            self.write(self.cam / (directory + HIDDEN) / leaf, b"old hidden asset\n")
            self.write(self.cam / name, b"new OP update\n")
        self.write(self.stock / CAMERA / "filters_lut/stock.bin", b"new stock\n")
        self.write(self.hidden(self.cam / "gamma_photo_hdr_conf.json"), b"old hidden gamma\n")
        self.write(self.cam / "gamma_photo_hdr_conf.json", b"new OP gamma\n")
        self.write(self.stock / CAMERA / "gamma_photo_hdr_conf.json", b"stock gamma\n")
        before = self.snapshot(self.op)
        log = self.run_compat()
        self.assertIn("hidden-conflict", log)
        self.assertEqual(self.snapshot(self.op), before)
        self.assertEqual(self.last_changed, 0)

    def test_config_markers_other_libraries_modules_and_apps_untouched(self):
        for rel in (CAMERA + "/config/camera_unit_config", CAMERA + "/is_mount",
                    CAMERA + "/outside.json", "odm/lib64/unrelated.so",
                    "product/framework/unrelated.jar", "product/app/OPCamera/base.apk"):
            self.write(self.target(rel), b"protected OP bytes\n")
            self.write(self.stock / rel, b"different stock bytes\n")
        self.write(self.cam / "meishe_lut/stock.bin", b"old lut\n")
        self.write(self.stock / CAMERA / "meishe_lut/stock.bin", b"stock lut\n")
        other = self.work / "data/adb/modules/AnotherModule"
        app = self.work / "data/app/com.oplus.camera/base.apk"
        self.write(other / "system/odm/lib64/libmsnativefilter.so", b"another module\n")
        self.write(app, b"system app fixture\n")
        untouched = {p: p.read_bytes() for p in self.op.rglob("*") if p.is_file()}
        untouched.pop(self.cam / "meishe_lut/stock.bin")
        untouched[other / "system/odm/lib64/libmsnativefilter.so"] = b"another module\n"
        untouched[app] = b"system app fixture\n"
        self.run_compat()
        for path, data in untouched.items():
            self.assertEqual(path.read_bytes(), data)

    def test_clean_lower_uses_deepest_mount_not_effective_overlay(self):
        rel = CAMERA + "/meishe_lut/qing_tou.bin"
        self.write(self.target(rel), b"OP effective bytes\n")
        self.write(self.device / rel, b"OP effective bytes\n")
        self.write(self.device / "my_stock/odm/etc/camera/meishe_lut/qing_tou.bin", b"broad lower\n")
        self.write(self.device / "my_product/product_overlay/meishe_lut/qing_tou.bin", b"deep clean lower\n")
        self.mounts(self.overlay(90, 2, "/odm/etc", "/my_stock/odm/etc"),
                    self.overlay(100, 90, "/odm/etc/camera/meishe_lut",
                                 "/my_product/product_overlay/meishe_lut"))
        self.run_compat(use_stock=False)
        self.assertEqual(self.target(rel).read_bytes(), b"deep clean lower\n")
        self.assertEqual(self.backup(rel).read_bytes(), b"OP effective bytes\n")
        self.assertEqual((self.device / rel).read_bytes(), b"OP effective bytes\n")

    def test_actual_ksu_module_lower_and_self_reference_are_not_stock(self):
        rel = CAMERA + "/meishe_lut/stock.bin"
        self.write(self.target(rel), b"OP asset\n")
        self.write(self.device / rel, b"OP asset\n")
        self.write(self.device / "mnt/vendor/u3ve5haz7" / rel, b"OP staged overlay\n")
        self.mounts(self.overlay(273, 2, "/odm/etc", "/mnt/vendor/u3ve5haz7/odm/etc:/odm/etc"))
        log = self.run_compat(use_stock=False)
        self.assertIn("stock-pending", log)
        self.assertEqual(self.target(rel).read_bytes(), b"OP asset\n")
        self.assertFalse(self.backup(rel).exists())
        self.assertEqual(self.last_changed, 0)

    def test_data_overlay_lower_is_rejected_even_with_good_partition_ancestor(self):
        rel = CAMERA + "/meishe_lut/stock.bin"
        self.write(self.target(rel), b"OP asset\n")
        self.write(self.device / "data/adb/modules/Other/system" / rel, b"other module bytes\n")
        self.mounts(
            "6 1 254:20 / /data ro - ext4 /dev/block/dm-20 ro",
            self.overlay(90, 2, "/odm/etc", "/data/adb/modules/Other/system/odm/etc"))
        self.assertIn("stock-pending", self.run_compat(use_stock=False))
        self.assertEqual(self.target(rel).read_bytes(), b"OP asset\n")
        self.assertEqual(self.last_changed, 0)

    def test_effective_native_overlay_is_not_compared_to_itself(self):
        self.install_native()
        for rel in MEISHE:
            self.write(self.device / rel, self.target(rel).read_bytes())
            self.write(self.device / "mnt/vendor/u3ve5haz7" / rel, self.target(rel).read_bytes())
        self.mounts(self.overlay(276, 2, "/odm/lib64", "/mnt/vendor/u3ve5haz7/odm/lib64:/odm/lib64"),
                    self.overlay(280, 3, "/product/lib64", "/mnt/vendor/u3ve5haz7/product/lib64:/product/lib64"))
        self.run_compat(use_stock=False)
        self.assertEqual(self.last_changed, 1)
        for rel in MEISHE:
            self.assertFalse(self.target(rel).exists())
            self.assertTrue(self.hidden(self.target(rel)).is_file())

    def test_known_stock_path_wins_under_stacked_product_overlay(self):
        rel = MEISHE[1]
        self.write(self.target(rel), b"OP effect sdk\n")
        self.write(self.device / "my_product/lib64/libMsEffectSdk.so", b"clean product stock\n")
        self.write(self.device / rel, b"OP effect sdk\n")
        self.mounts(self.overlay(175, 3, "/product/lib64", "/my_product/lib64:/product/lib64"),
                    self.overlay(280, 175, "/product/lib64", "/mnt/vendor/token/product/lib64:/product/lib64"))
        self.run_compat(use_stock=False)
        self.assertEqual(self.target(rel).read_bytes(), b"clean product stock\n")
        self.assertEqual(self.backup(rel).read_bytes(), b"OP effect sdk\n")
        self.assertFalse(self.hidden(self.target(rel)).exists())

    def test_proven_clean_lower_catalog_restores_legacy_extra(self):
        self.write(self.cam / "meishe_lut.xpan-c16-hidden/op-extra.bin", b"legacy extra\n")
        (self.device / "my_product/product_overlay/meishe_lut").mkdir(parents=True)
        self.mounts(self.overlay(90, 2, "/odm/etc/camera/meishe_lut",
                                 "/my_product/product_overlay/meishe_lut"))
        self.run_compat(use_stock=False)
        self.assertEqual((self.cam / "meishe_lut/op-extra.bin").read_bytes(), b"legacy extra\n")
        self.assertEqual((self.cam / "meishe_lut.xpan-c16-hidden/op-extra.bin").read_bytes(), b"legacy extra\n")

    def test_invalid_mountinfo_keeps_assets_but_native_fallback_still_runs(self):
        self.install_native()
        self.write(self.cam / "meishe_lut/stock.bin", b"OP asset\n")
        self.text(self.info, "malformed mountinfo\n")
        log = self.run_compat(use_stock=False)
        self.assertIn("stock-pending", log)
        self.assertEqual((self.cam / "meishe_lut/stock.bin").read_bytes(), b"OP asset\n")
        for rel in MEISHE:
            self.assertTrue(self.hidden(self.target(rel)).is_file())

    def test_test_stock_root_cannot_point_back_to_op_overlay(self):
        self.install_native()
        self.write(self.cam / "meishe_lut/stock.bin", b"OP asset\n")
        log = self.run_compat(stock_root=self.op / "system")
        self.assertIn("stock-pending", log)
        self.assertEqual((self.cam / "meishe_lut/stock.bin").read_bytes(), b"OP asset\n")
        for rel in MEISHE:
            self.assertTrue(self.hidden(self.target(rel)).is_file())

    def test_failed_copy_keeps_native_and_asset_originals(self):
        self.install_native(clean=True)
        self.write(self.cam / "meishe_lut/stock.bin", b"OP asset\n")
        self.write(self.stock / CAMERA / "meishe_lut/stock.bin", b"stock asset\n")
        before = self.snapshot(self.op)
        inject = 'xpan_bounded() { if [ "$1" = cat ]; then printf partial; return 1; fi; timeout -s KILL 10 "$@"; }'
        log = self.run_compat(inject=inject)
        self.assertIn("backup-failed", log)
        self.assertEqual(self.snapshot(self.op), before)
        self.assertEqual(self.last_changed, 0)
        self.assertFalse(list(self.op.rglob("*.xpan-compat.*")))

    def test_failed_native_hide_keeps_original_and_backup(self):
        self.install_native()
        before = self.snapshot(self.op)
        inject = 'mv() { case "$*" in *xpan-c16-hidden*) return 1 ;; esac; command mv "$@"; }'
        log = self.run_compat(use_stock=False, inject=inject)
        self.assertIn("hide-failed", log)
        self.assertEqual(self.snapshot(self.op), before)
        self.assertEqual(self.last_changed, 0)
        for rel in MEISHE:
            self.assertEqual(self.backup(rel).read_bytes(), self.target(rel).read_bytes())

    def test_failed_atomic_replace_keeps_jar_original(self):
        self.install_source()
        self.install_target()
        inject = 'mv() { case "$2" in "$X_OC_FW/"*.jar.xpan-compat.*) return 1 ;; esac; command mv "$@"; }'
        log = self.run_compat(inject=inject)
        self.assertIn("replace-failed", log)
        self.assertEqual((self.fw / UNIT).read_bytes(), C16["unit"])
        self.assertEqual((self.fw / ADAPTER).read_bytes(), C16["adapter"])
        self.assertEqual(self.last_changed, 0)
        self.assertFalse(list(self.fw.glob("*.xpan-compat*")))

    def test_copy_hash_mismatch_is_detected_before_original_is_replaced(self):
        self.install_source(adapter=False)
        self.write(self.fw / UNIT, C16["unit"])
        inject = ('xpan_bounded() { if [ "$1" = cat ] && [ "$2" = "$X_MOD/payload/op-compat/framework/'
                  + UNIT + '" ]; then printf wrong; return 0; fi; timeout -s KILL 10 "$@"; }')
        log = self.run_compat(inject=inject)
        self.assertIn("replace-failed", log)
        self.assertEqual((self.fw / UNIT).read_bytes(), C16["unit"])
        self.assertEqual(self.last_changed, 0)

    def test_post_write_readback_failure_rolls_back_original(self):
        self.install_source(adapter=False)
        self.write(self.fw / UNIT, C16["unit"])
        inject = ('xpan_bounded() { if [ "$1" = sha256sum ] && '
                  '[ "$2" = "$X_OC_FW/' + UNIT + '" ] && '
                  '[ -f "$2.xpan-compat-old.$$" ] && command grep -q C17 "$2"; '
                  'then return 1; fi; timeout -s KILL 10 "$@"; }')
        log = self.run_compat(inject=inject)
        self.assertIn("replace-failed", log)
        self.assertEqual((self.fw / UNIT).read_bytes(), C16["unit"])
        self.assertEqual(self.last_changed, 0)
        self.assertFalse(list(self.fw.glob("*.xpan-compat*")))

    def test_first_backup_and_later_distinct_old_native_both_survive(self):
        self.install_native(clean=True)
        old = self.target(MEISHE[0]).read_bytes()
        self.run_compat()
        newer_op = b"later OP release native\n"
        self.write(self.target(MEISHE[0]), newer_op)
        self.run_compat()
        backup = self.backup(MEISHE[0])
        self.assertEqual(backup.read_bytes(), old)
        self.assertEqual(Path(str(backup) + "." + sha(newer_op)).read_bytes(), newer_op)
        self.assertEqual(self.target(MEISHE[0]).read_bytes(), (self.stock / MEISHE[0]).read_bytes())
        self.run_compat()
        self.assertEqual(self.last_changed, 0)

    def test_symlinked_native_and_lut_targets_leave_referents_untouched(self):
        external = self.work / "external"
        native = external / "lib.so"
        lut = external / "lut.bin"
        self.write(native, b"external native\n")
        self.write(lut, b"external asset\n")
        self.link(native, self.target(MEISHE[0]))
        self.link(lut, self.cam / "meishe_lut/stock.bin")
        self.write(self.stock / MEISHE[0], b"stock native\n")
        self.write(self.stock / CAMERA / "meishe_lut/stock.bin", b"stock asset\n")
        self.run_compat()
        self.assertEqual(native.read_bytes(), b"external native\n")
        self.assertEqual(lut.read_bytes(), b"external asset\n")
        self.assertEqual(self.bash_run('test -L "$1"', self.target(MEISHE[0])).returncode, 0)
        self.assertEqual(self.bash_run('test -L "$1"', self.cam / "meishe_lut/stock.bin").returncode, 0)
        self.assertEqual(self.last_changed, 0)

    def test_symlinked_asset_directory_and_hidden_target_are_protected(self):
        external = self.work / "external"
        self.write(external / "stock.bin", b"external asset\n")
        self.link(external, self.cam / "meishe_lut")
        self.write(self.stock / CAMERA / "meishe_lut/stock.bin", b"stock asset\n")
        self.write(self.cam / "filters_lut.xpan-c16-hidden/extra.bin", b"legacy asset\n")
        self.link(external, self.cam / "filters_lut")
        self.run_compat()
        self.assertEqual((external / "stock.bin").read_bytes(), b"external asset\n")
        self.assertFalse((external / "extra.bin").exists())
        self.assertEqual(self.last_changed, 0)

    def test_symlinked_stock_source_keeps_asset_original(self):
        target = self.cam / "meishe_lut/stock.bin"
        self.write(target, b"OP asset\n")
        external = self.work / "untrusted.bin"
        self.write(external, b"not original stock\n")
        self.link(external, self.stock / CAMERA / "meishe_lut/stock.bin")
        self.run_compat()
        self.assertEqual(target.read_bytes(), b"OP asset\n")
        self.assertEqual(external.read_bytes(), b"not original stock\n")
        self.assertEqual(self.last_changed, 0)

    def test_symlinked_backup_directory_prevents_replace_and_hide(self):
        self.install_native()
        external = self.work / "backup-external"
        external.mkdir()
        self.link(external, self.state / "op-compat-backup")
        before = self.snapshot(self.op)
        log = self.run_compat(use_stock=False)
        self.assertIn("backup-failed", log)
        self.assertEqual(self.snapshot(self.op), before)
        self.assertEqual(self.snapshot(external), {})
        self.assertEqual(self.last_changed, 0)

    def test_state_rom_release_is_not_overwritten_by_file_relative_path(self):
        self.install_native(clean=True)
        self.write(self.cam / "meishe_lut/stock.bin", b"old lut\n")
        self.write(self.stock / CAMERA / "meishe_lut/stock.bin", b"new lut\n")
        self.run_compat(release="17.0")
        self.assertEqual(self.status()["rom_release"], "17.0")
        self.assertEqual(self.status()["compat_version"], "3")
        self.assertEqual(self.status()["changed"], "1")

    def test_production_contract_is_allowlisted_reversible_and_not_device_proof(self):
        source = RUNTIME.read_text(encoding="utf-8")
        block = source.split("xpan_op_backup() {", 1)[1].split("\nxpan_install() {", 1)[0]
        body = block.split("xpan_op_compat() {", 1)[1]
        for marker in ("ro.build.version.release", "17|17.*", "/MANIFEST", "OPCameraPro",
                       "op-compat-backup", "reboot makes it effective", "filters not device-verified"):
            self.assertIn(marker, body)
        self.assertIn("rom_release=$X_OC_ROM_RELEASE", body)
        self.assertIn("xpan_op_native", body)
        self.assertIn("xpan_op_hide_c16_filters", body)
        for rel in MEISHE:
            self.assertIn("/" + rel, block)
        for forbidden in ("umount", "pm uninstall", "rm -rf", "OPCameraPro/disable",
                          "filter_type=default", "find \"$X_OC_DIR/system\"", "xpan_op_compat || return"):
            self.assertNotIn(forbidden, block)
        package = (ROOT / "tools/package_module.py").read_text(encoding="utf-8")
        for pin in ("d2b20133252414e26ac7c68255c76fa61cf41826c3b63d423a14c1187af42899",
                    "7418d59955746d8aca56438a5e0e26e1f08ff6ae4d0cacc22057d0041b12aac4"):
            self.assertIn(pin, package)
        for path in ("payload/op-compat/framework/" + UNIT,
                     "payload/op-compat/framework/" + ADAPTER, "payload/op-compat/MANIFEST"):
            self.assertIn('"' + path + '"', package)
        self.assertIn("Bundled C17 completion is not the approved stock original", package)


if __name__ == "__main__":
    unittest.main(verbosity=2)
