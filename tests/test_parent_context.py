"""Host-only mountinfo fixtures and real directory pins; no device operations."""
from pathlib import Path
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "module/parent-context.sh"
NORMAL = "1 0 dev / / rw - mock mock rw"
LEAVES = ("camera_unit_config", "camera_unit_feature_config.protobuf", "oplus_camera_config")
ACTUAL_KSU = (r"273 33 0:136 / /odm/etc rw,relatime shared:76 - overlay KSU\040 "
              "ro,seclabel,lowerdir=/mnt/vendor/u3ve5haz7/odm/etc:/odm/etc")
BOUNDED = 'xpan_bounded() { timeout -s KILL 2 "$@"; }\n'
HARNESS = '''
. "$1" || exit 95
XP_PARENT_HASH=untrusted_hash; XP_PARENT_INODE=untrusted_inode; XP_PARENT_KIND=untrusted_kind
CALL
rc=$?
printf '%s\\n' "$rc" "$XP_PARENT_HASH" "$XP_PARENT_INODE" "$XP_PARENT_KIND"
'''
CALL = 'xpan_parent_observe "$2" "$3" "$4" "$5"'


class ParentContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bash = shutil.which("bash")
        cls.cygpath = shutil.which("cygpath") if os.name == "nt" else None
        if not cls.bash or (os.name == "nt" and not cls.cygpath):
            raise unittest.SkipTest("Bash and host path conversion are required")
        cls.env = os.environ.copy()
        cls.env.pop("BASH_ENV", None)
        cls.env.pop("ENV", None)
        cls.env.update(LC_ALL="C.UTF-8", MSYS2_ARG_CONV_EXCL="*", MSYS="winsymlinks:lnk")
        cls.temp_root = ROOT / "_tmp/xpan-runtime-test.parentcontext"
        cls.temp_root.mkdir(parents=True, exist_ok=True)
        cls.module = cls.posix(MODULE)

    @classmethod
    def posix(cls, path):
        if cls.cygpath:
            return subprocess.check_output([cls.cygpath, "-au", str(path)],
                                           env=cls.env, cwd=ROOT, timeout=10).decode("utf-8").strip()
        return str(Path(path).absolute())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="parent-context-", dir=self.temp_root)
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.stock = self.work / "stock"
        self.stock.mkdir()
        self.info = self.work / "mountinfo"
        self.parent_point = self.posix(self.work)
        self.stock_point = self.posix(self.stock)

    def bash_run(self, script, *args):
        return subprocess.run([self.bash, "--noprofile", "--norc", "-c", script,
                               "parent-context-test", *args], cwd=ROOT, env=self.env,
                              capture_output=True, encoding="utf-8", timeout=20)

    def observe(self, lines=(NORMAL,), *, stock=None, parent=None, partition="/",
                info=None, bounded=BOUNDED, call=CALL):
        data = lines if isinstance(lines, bytes) else ("\n".join(lines) + "\n").encode("utf-8")
        self.info.write_bytes(data)
        args = [self.module, self.posix(self.info) if info is None else str(info),
                self.stock_point if stock is None else str(stock),
                self.parent_point if parent is None else str(parent), str(partition)]
        result = self.bash_run(bounded + HARNESS.replace("CALL", call), *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", "Helper exposed a diagnostic")
        fields = result.stdout.splitlines()
        self.assertEqual(len(fields), 4, "Helper exposed unexpected output")
        return int(fields[0]), fields[1], fields[2], fields[3]

    def rejected(self, lines=(NORMAL,), **kwargs):
        result = self.observe(lines, **kwargs)
        self.assertNotEqual(result[0], 0)
        self.assertEqual(result[1:], ("", "", ""))

    @staticmethod
    def digest(lines):
        return hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()

    def inode(self, path):
        result = self.bash_run('stat -Lc "%d:%i" "$1"', self.posix(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def ksu(self, parent=None, mount_id=273, parent_id=1):
        parent = self.parent_point if parent is None else parent
        return f"{mount_id} {parent_id} " + ACTUAL_KSU.split(" ", 2)[2].replace(" / /odm/etc ", " / " + parent + " ", 1)

    @staticmethod
    def mount(mount_id, parent_id, point):
        return f"{mount_id} {parent_id} 0:{mount_id} / {point} rw - mock mock rw"

    def production_scope(self):
        partition = self.work / "odm"
        parent = partition / "etc"
        stock = parent / "camera/config"
        stock.mkdir(parents=True, exist_ok=True)
        part, par, sto = map(self.posix, (partition, parent, stock))
        partition_line = f"33 1 253:4 / {part} ro,relatime - erofs /dev/block/mock ro,seclabel"
        return [NORMAL, partition_line, self.ksu(par, parent_id=33)], dict(
            stock=sto, parent=par, partition=part)

    def test_source_only_defines_functions(self):
        script = '''
XP_PARENT_HASH=keep
xpan_bounded() { printf 'unexpected execution\\n'; return 9; }
timeout() { printf 'unexpected timeout\\n'; return 9; }
. "$1" || exit 1
printf '%s\\n' "$XP_PARENT_HASH"
'''
        result = self.bash_run(script, self.module)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "keep\n", ""))

    def test_valid_normal_and_standalone_timeout(self):
        for bounded in (BOUNDED, ""):
            with self.subTest(bounded=bool(bounded)):
                self.assertEqual(self.observe(bounded=bounded),
                                 (0, self.digest([NORMAL]), self.inode(self.stock), "normal"))

    def test_actual_ksu_shape_with_real_partition_and_stat(self):
        lines, scope = self.production_scope()
        self.assertTrue(lines[2].startswith("273 33 0:136 / "))
        for vfs in ("rw,relatime", "ro,relatime"):
            for optional in ("shared:76", "master:18", "shared:76 master:18", ""):
                with self.subTest(vfs=vfs, optional=optional):
                    changed = lines[:2] + [lines[2].replace("rw,relatime shared:76 -",
                                                         vfs + (" " + optional if optional else "") + " -")]
                    self.assertEqual(self.observe(changed, **scope),
                                     (0, self.digest(changed), self.inode(Path(self.work / "odm/etc/camera/config")), "ksu"))

    def test_normal_root_and_partition_do_not_need_known_fstype(self):
        lines, scope = self.production_scope()
        lines[0] = "1 0 0:1 / / rw - overlay other rw,lowerdir=/normal,upperdir=/upper,workdir=/work"
        lines[1] = lines[1].replace("erofs /dev/block/mock ro,seclabel", "unknown source ro,nodev")
        lines.pop()
        self.assertEqual(self.observe(lines, **scope)[::3], (0, "normal"))
        self.assertEqual(self.observe(lines, **scope)[1], self.digest(lines))

    def test_exact_three_leaves_are_not_ancestor_hash_sources(self):
        base = [NORMAL, self.ksu()]
        pins = self.observe(base)
        leaves = [self.mount(400 + i, 273, self.stock_point + "/" + name)
                  for i, name in enumerate(LEAVES)]
        self.assertEqual(self.observe(base + leaves), pins)
        self.assertEqual(self.observe(base + [line.replace(" rw -", " ro -") for line in leaves]), pins)

    def test_stock_camera_parent_and_deeper_mounts_rejected(self):
        stock = self.work / "camera/config"
        stock.mkdir(parents=True)
        point = self.posix(stock)
        for mounted in (point, self.posix(stock.parent), point + "/unknown",
                        point + "/camera_unit_config/child", point + "/camera_unit_config_suffix"):
            with self.subTest(point=mounted):
                self.rejected([NORMAL, self.mount(2, 1, mounted)], stock=point)

    def test_extra_intermediate_ancestor_rejected(self):
        extra = self.mount(2, 1, self.posix(self.work.parent))
        self.rejected([NORMAL, extra, self.ksu()])
        lines, scope = self.production_scope()
        self.rejected(lines + [self.mount(44, 1, self.parent_point)], **scope)

    def test_duplicate_ids_or_points_rejected(self):
        for extra in (NORMAL, self.mount(1, 0, "/unrelated"), self.mount(2, 1, "/")):
            with self.subTest(extra=extra):
                self.rejected([NORMAL, extra])
        self.rejected([NORMAL, self.ksu(), self.ksu(mount_id=274)])
        unrelated = [self.mount(2, 1, "/unrelated"), self.mount(3, 2, "/unrelated")]
        self.assertEqual(self.observe([NORMAL, *unrelated])[0], 0)

    def test_empty_or_missing_root_or_partition_rejected(self):
        for lines in (b"", b"\n", [self.mount(2, 1, "/unrelated")]):
            with self.subTest(lines=lines):
                self.rejected(lines)
        lines, scope = self.production_scope()
        self.rejected([lines[0], lines[2]], **scope)
        self.rejected(lines[1:], **scope)

    def test_malformed_rows_and_control_bytes_rejected(self):
        rows = ["garbage", NORMAL + " extra", NORMAL.replace(" - ", " "),
                NORMAL.replace("dev", "baddev"), NORMAL.replace("/ /", "relative /"),
                NORMAL.replace("/ /", "/../bad /"), NORMAL.replace(" rw -", " rw,rw -"),
                NORMAL.replace(" rw -", " rw,ro -"), NORMAL.replace(" rw -", " rw,flag= -"),
                NORMAL.replace(" rw -", " rw shared:1 shared:2 -"),
                NORMAL.replace(" rw -", " rw mystery:1 -"),
                NORMAL.replace("mock rw", r"mock\041 rw"),
                NORMAL.replace("/ /", r"/ /escaped\057path"),
                NORMAL.replace("1 0", "01 0"), NORMAL.replace("1 0", "1 1"),
                NORMAL.replace("1 0", "1 -1"), NORMAL.replace("1 0", "1 00"),
                NORMAL.replace(" rw ", "  rw "), " " + NORMAL, NORMAL + " "]
        for row in rows:
            with self.subTest(row=row):
                self.rejected([row])
        for control in (b"\x00", b"\x01", b"\x09", b"\x0d", b"\x7f"):
            with self.subTest(control=control):
                self.rejected(NORMAL.encode() + control + b"\n")
        self.rejected((NORMAL + "\n\n").encode())

    def test_parent_requires_exact_ksu_source_overlay_and_root(self):
        line = self.ksu()
        for changed in (self.mount(273, 1, self.parent_point),
                        line.replace(" / " + self.parent_point, " /bind " + self.parent_point),
                        line.replace("- overlay ", "- tmpfs "),
                        line.replace(r"KSU\040", "KSU"), line.replace(r"KSU\040", r"KSU2\040"),
                        line.replace(r"KSU\040", r"KSU\040\040"),
                        line.replace("273 1 ", "2731 ")):
            with self.subTest(line=changed):
                self.rejected([NORMAL, changed])

    def test_ksu_vfs_and_optional_fields_are_exact(self):
        line = self.ksu()
        replacements = [line.replace("rw,relatime", value, 1)
                        for value in ("rw", "ro", "relatime,rw", "rw,relatime,nodev", "rw,ro,relatime")]
        replacements += [line.replace("shared:76", value)
                         for value in ("shared:", "shared:x", "shared:76 shared:77",
                                       "propagate_from:18", "unbindable", "idmapped")]
        for changed in replacements:
            with self.subTest(line=changed):
                self.rejected([NORMAL, changed])

    def test_ksu_super_options_and_lower_route_are_exact(self):
        line = self.ksu()
        route = "/mnt/vendor/u3ve5haz7/odm/etc:/odm/etc"
        prefix, _ = line.rsplit(" ", 1)
        bad_super = ["rw,seclabel,lowerdir=" + route, "seclabel,ro,lowerdir=" + route,
                     "ro,lowerdir=" + route]
        bad_super += ["ro,seclabel,lowerdir=" + route + suffix
                      for suffix in (",upperdir=/upper", ",workdir=/work", ",nodev", ":/extra")]
        bad_routes = ["relative:" + self.parent_point, "/mnt/vendor/../" + route,
                      route.replace("u3ve5haz7", ""), route.replace("u3ve5haz7", "bad.token"),
                      route.replace("u3ve5haz7", r"bad\040token"),
                      route.replace("/mnt/vendor/", "/mnt/vendor//"),
                      route.replace(":/odm/etc", ":/wrong"), route + "/",
                      "/mnt/vendor/u3ve5haz7" + self.parent_point + ":" + self.parent_point, route.replace(":", "/../:"),
                      route + ",lowerdir=" + route]
        bad_super += ["ro,seclabel,lowerdir=" + value for value in bad_routes]
        for super_options in bad_super:
            with self.subTest(super_options=super_options):
                self.rejected([NORMAL, prefix + " " + super_options])

    def test_ancestor_chain_cannot_be_missing_hidden_or_cyclic(self):
        lines, scope = self.production_scope()
        variants = [lines[:2] + [lines[2].replace("273 33", "273 1")],
                    lines[:2] + [lines[2].replace("273 33", "273 99")],
                    [lines[0], lines[1].replace("33 1", "33 99"), lines[2]],
                    [lines[0].replace("1 0", "1 33"), *lines[1:]],
                    [lines[0], self.mount(44, 1, "/unrelated"), lines[1],
                     lines[2].replace("273 33", "273 44")]]
        for changed in variants:
            with self.subTest(lines=changed):
                self.rejected(changed, **scope)
        self.rejected([NORMAL.replace("1 0", "1 2"), self.mount(2, 1, "/unrelated")])

    def test_scope_paths_and_real_directory_requirements(self):
        for value in ("relative", self.stock_point + "/", self.stock_point + "/..",
                      self.stock_point + "//", self.parent_point + "/space name",
                      self.parent_point + "/bad\tname", self.parent_point + r"/bad\134name"):
            with self.subTest(stock=value):
                self.rejected(stock=value)
        for key, value in (("parent", self.stock_point), ("parent", "/"),
                           ("parent", "/unrelated"), ("partition", "/unrelated"),
                           ("info", self.parent_point), ("info", self.parent_point + "/missing"),
                           ("info", "relative")):
            with self.subTest(key=key, value=value):
                self.rejected(**{key: value})
        missing = self.work / "missing"
        self.rejected(stock=self.posix(missing))
        regular = self.work / "regular"
        regular.write_text("not a directory", encoding="utf-8")
        self.rejected(stock=self.posix(regular))

    def test_stock_ancestor_and_info_symlinks_rejected(self):
        target = self.work / "target"
        (target / "stock").mkdir(parents=True)
        link = self.work / "link"
        linked = self.bash_run('ln -s "$1" "$2" && test -L "$2"',
                               self.posix(target), self.posix(link))
        if linked.returncode:
            self.skipTest("Host cannot create unprivileged filesystem symlinks")
        self.rejected(stock=self.posix(link))
        self.rejected(stock=self.posix(link / "stock"))
        info_link = self.work / "info-link"
        self.info.write_text(NORMAL + "\n", encoding="utf-8")
        linked = self.bash_run('ln -s "$1" "$2" && test -L "$2"',
                               self.posix(self.info), self.posix(info_link))
        self.assertEqual(linked.returncode, 0, linked.stderr)
        self.rejected(info=self.posix(info_link))

    def test_mountinfo_byte_row_and_line_bounds(self):
        def padded(mount_id, length):
            prefix = f"{mount_id} 1 0:{mount_id} / /unrelated-{mount_id} rw - mock "
            self.assertGreaterEqual(length, len(prefix) + 4)
            return prefix + "x" * (length - len(prefix) - 3) + " rw"

        # Large valid fixtures use the helper's real 15s budget, not the 2s fault budget.
        self.assertEqual(self.observe([NORMAL, padded(2, 4096)], bounded="")[0], 0)
        self.rejected([NORMAL, padded(2, 4097)], bounded="")
        rows = [NORMAL] + [self.mount(i, 1, f"/unrelated-{i}") for i in range(2, 8193)]
        self.assertEqual(self.observe(rows, bounded="")[0], 0)
        self.rejected(rows + [self.mount(8193, 1, "/unrelated-8193")], bounded="")
        lines = [NORMAL]
        used = len(NORMAL) + 1
        while used < 1048576:
            remaining = 1048576 - used
            length = min(4096, remaining - 1)
            if 0 < remaining - length - 1 < 128:
                length -= 128
            lines.append(padded(len(lines) + 1, length))
            used += length + 1
        data = ("\n".join(lines) + "\n").encode()
        self.assertEqual(len(data), 1048576)
        self.assertEqual(self.observe(data, bounded="")[0], 0)
        self.rejected(data + b"\n", bounded="")

    def test_hash_preserves_order_and_complete_raw_ancestor_context(self):
        lines, scope = self.production_scope()
        lines[0] = "1 0 8:1 / / rw,relatime - ext4 /dev/block/root rw,seclabel"
        pins = self.observe(lines, **scope)
        self.assertEqual(pins[1], self.digest(lines))
        self.assertEqual(self.observe(lines, **scope), pins)
        reordered = [lines[2], lines[0], lines[1]]
        changed = self.observe(reordered, **scope)
        self.assertEqual(changed[1], self.digest(reordered))
        self.assertNotEqual(changed[1], pins[1])
        mutations = [(0, "1 0", "9 0"), (0, "8:1", "8:2"), (0, "/ /", "/bind-root /"),
                     (0, "rw,relatime", "ro,relatime"), (0, "ext4", "otherfs"),
                     (0, "/dev/block/root", "/dev/block/other"), (0, "rw,seclabel", "rw,seclabel,nodev"),
                     (1, "253:4", "253:5"), (2, "273 33", "274 33"), (2, "0:136", "0:137"),
                     (2, "shared:76", "shared:77"), (2, "rw,relatime", "ro,relatime"),
                     (2, "u3ve5haz7", "new_token-7")]
        for index, old, new in mutations:
            with self.subTest(index=index, old=old):
                mutated = lines.copy()
                mutated[index] = mutated[index].replace(old, new, 1)
                if old == "1 0":
                    mutated[1] = mutated[1].replace("33 1", "33 9", 1)
                observed = self.observe(mutated, **scope)
                self.assertEqual(observed[0], 0)
                self.assertEqual(observed[1], self.digest(mutated))
                self.assertNotEqual(observed[1], pins[1])
        unrelated = self.mount(444, 1, "/unrelated")
        self.assertEqual(self.observe(lines + [unrelated], **scope), pins)

    def test_same_mountinfo_bytes_new_config_directory_inode(self):
        lines = [NORMAL, self.ksu()]
        pins = self.observe(lines)
        replacement = self.work / "replacement"
        replacement.mkdir()
        self.stock.rename(self.work / "old-stock")
        replacement.rename(self.stock)
        changed = self.observe(lines)
        self.assertEqual(changed[0], 0)
        self.assertEqual(changed[1], pins[1])
        self.assertNotEqual(changed[2], pins[2])
        self.assertEqual(changed[2], self.inode(self.stock))

    def test_bounded_command_failures_are_not_masked_or_printed(self):
        for command in ("head", "awk", "stat", "sha256sum"):
            with self.subTest(command=command):
                wrapper = '''xpan_bounded() {
    timeout -s KILL 2 "$@"
    rc=$?
    if [ "$1" = COMMAND ]; then printf 'private diagnostic\\n' >&2; return 7; fi
    return "$rc"
}
'''.replace("COMMAND", command)
                self.rejected(bounded=wrapper)

    def test_untrusted_input_and_wrong_arity_reset_all_outputs(self):
        for call in ('xpan_parent_observe', 'xpan_parent_observe "$2" "$3" "$4"', CALL + ' extra',
                     CALL + ' || exit 97\n' + CALL + ' extra'):
            with self.subTest(call=call):
                self.rejected(call=call)
        marker = self.work / "must-not-exist"
        malicious = ("XP_PARENT_HASH=approved; touch '" + self.posix(marker) + "'\n").encode()
        self.rejected(malicious)
        self.assertFalse(marker.exists())
        self.rejected([NORMAL, self.mount(2, 1, self.parent_point)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
