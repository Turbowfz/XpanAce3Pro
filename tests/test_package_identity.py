"""Focused offline identity tests: python -I tests/test_package_identity.py.

Execute only AWK extracted from trusted sources; no PM, adb, DB or shell CLI.
The capture is sanitized: preserve structure, not device paths or package lists.
"""
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCES = (("module/lifecycle.sh", "ID_LINES"), ("hook/uninstall.sh", "ID_LINES"),
           ("module/runtime.sh", "X_ID_LINES"))
HEADER = "  Package [com.xpanport.hook] (fixture):\n"
PATH = "/data/app/fixture/com.xpanport.hook"
FIRST = "2026-11-14 12:39:48"
LAST = "2026-11-14 12:40:00-new-1"
GLOBAL = f"    codePath={PATH}\n    lastUpdateTime={LAST}\n"
LEGACY = HEADER + f"    codePath={PATH}\n    firstInstallTime={FIRST}\n    lastUpdateTime={LAST}\n"


def user(uid, time=FIRST, installed="true"):
    return (f"    User {uid}: installed={installed} hidden=false\n      installReason=0\n"
            + (f"      firstInstallTime={time}\n" if time is not None else ""))


def normalized(first=FIRST, uid=None):
    text = f"codePath={PATH}\nfirstInstallTime={first}\nlastUpdateTime={LAST}\n"
    return text + (f"identity_version=2;firstInstallTimeUser={uid}\n" if uid is not None else "")


CAPTURE = ("Packages:\n" + HEADER + GLOBAL + user(0) + user(999, installed="false")
           + "\nQueries:\n  queryable via interaction:\n    User 0:\n    User 999:\n"
             "Dexopt state:\n  [com.xpanport.hook]\n")


def trusted_programs():
    programs = []
    for relative, variable in SOURCES:
        source = (ROOT / relative).read_text(encoding="utf-8")
        pattern = rf"{variable}=\$\(printf[^\n]+\| LC_ALL=C awk '\n(.*?)\n    '\)"
        matches = re.findall(pattern, source, re.DOTALL)
        if len(matches) != 1:
            raise AssertionError(f"expected one trusted identity AWK in {relative}")
        programs.append(matches[0])
    return programs


class PackageIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.awk = shutil.which("awk")
        if not cls.awk:
            raise unittest.SkipTest("host AWK is required")
        cls.programs = trusted_programs()
        cls.temp = tempfile.TemporaryDirectory(prefix="xpan-identity-test.")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        for index, program in enumerate(cls.programs):
            (cls.work / f"identity-{index}.awk").write_text(program, encoding="utf-8", newline="\n")

    def parse(self, dump, index=0):
        # -f avoids Windows argv regex escaping; BINMODE prevents host CRLF stripping.
        return subprocess.run([self.awk, "-v", "BINMODE=3", "-f", f"./identity-{index}.awk"], input=dump.encode("utf-8"),
                              capture_output=True, cwd=self.work, timeout=4)

    def accepts(self, dump, expected):
        result = self.parse(dump)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, expected.encode("utf-8"))
        return result.stdout

    def rejects(self, *dumps):
        for index, dump in enumerate(dumps):
            with self.subTest(variant=index):
                result = self.parse(dump)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b"", "unknown must not publish partial identity")
                self.assertEqual(result.stderr, b"")

    def test_three_copies_are_identical_and_execute_same_contract(self):
        self.assertEqual(self.programs[0], self.programs[1])
        self.assertEqual(self.programs[0], self.programs[2])
        for index in range(3):
            self.assertEqual(self.parse(CAPTURE, index).stdout, normalized(uid=0).encode())
            self.assertEqual(self.parse(LEGACY, index).stdout, normalized().encode())
            result = self.parse(CAPTURE + CAPTURE, index)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")

    def test_legacy_three_fields_keep_exact_stored_sha256(self):
        old = "".join(line.lstrip() + "\n" for line in LEGACY.splitlines()
                      if re.match(r"\s*(codePath|firstInstallTime|lastUpdateTime)=", line)).encode()
        result = self.accepts(LEGACY, normalized())
        self.assertEqual(hashlib.sha256(result).hexdigest(), hashlib.sha256(old).hexdigest())

    def test_globals_canonical_order_and_legacy_opaque_timestamps(self):
        self.accepts(HEADER + f"    lastUpdateTime={LAST}\n    firstInstallTime={FIRST}\n    codePath={PATH}\n",
                     normalized())
        self.accepts(LEGACY.replace(FIRST, "fixture-first").replace(LAST, "new-1"),
                     normalized().replace(FIRST, "fixture-first").replace(LAST, "new-1"))

    def test_sanitized_actual_capture_ignores_nonpackage_user_headings(self):
        self.accepts(CAPTURE, normalized(uid=0))
        self.assertNotEqual(hashlib.sha256(normalized().encode()).digest(),
                            hashlib.sha256(normalized(uid=0).encode()).digest())

    def test_distinct_user_times_choose_owner_not_first_line(self):
        for blocks in (user(999, "clone-time") + user(0), user(0) + user(999, "clone-time")):
            self.accepts(HEADER + GLOBAL + blocks, normalized(uid=0))
        self.accepts(HEADER + GLOBAL + user(0, installed="false") + user(10, "work-time"), normalized(uid=0))

    def test_absent_owner_chooses_numeric_lowest_with_time(self):
        for blocks in (user(999, "clone-time") + user(10), user(10) + user(999, "clone-time")):
            self.accepts(HEADER + GLOBAL + blocks, normalized(uid=10))
        self.accepts(HEADER + GLOBAL + user(1, None) + user(10, installed="false"), normalized(uid=10))

    def test_user10_only_supported_and_missing_owner_time_unknown(self):
        self.accepts(HEADER + GLOBAL + user(10), normalized(uid=10))
        self.rejects(HEADER + GLOBAL + user(0, None) + user(10), HEADER + GLOBAL + user(10, None))

    def test_global_time_with_matching_users_preserves_legacy_hash(self):
        self.accepts(LEGACY + user(0) + user(999, installed="false"), normalized())
        self.rejects(LEGACY + user(0, "mismatch"), LEGACY + user(999, "mismatch"))

    def test_duplicate_global_fields_and_malformed_prefixes_rejected(self):
        for key, value in (("codePath", PATH), ("firstInstallTime", FIRST), ("lastUpdateTime", LAST)):
            self.rejects(LEGACY + f"    {key}={value}\n", LEGACY + f"    {key} = {value}\n",
                         LEGACY + f"    {key}Extra={value}\n", LEGACY + f"    {key}\n")

    def test_duplicate_users_and_times_rejected_even_if_values_equal(self):
        base = HEADER + GLOBAL
        self.rejects(base + user(0) + user(0), base + user(0) + user(999) + user(999),
                     base + user(0) + f"      firstInstallTime={FIRST}\n",
                     base + user(0) + user(999) + "      firstInstallTime=clone-time\n")

    def test_malformed_headers_and_indentation_do_not_hide_duplicates(self):
        for header in ("User x:", "User -1:", "User 00:", "User 10 :", "User 10", "User: 10",
                       "User 2147483648:", "User 10: ", "User 10:\r", "User\t10:"):
            self.rejects(HEADER + GLOBAL + user(0) + f"    {header}\n      firstInstallTime={FIRST}\n")
        self.rejects(HEADER + GLOBAL + user(0) + f"      User 999:\n        firstInstallTime={FIRST}\n",
                     HEADER + GLOBAL + user(0) + f"\tUser 999:\n      firstInstallTime={FIRST}\n",
                     HEADER + "    appId=fixture\n" + f"      codePath={PATH}\n      lastUpdateTime={LAST}\n"
                     + "      User malformed:\n" + f"        firstInstallTime={FIRST}\n",
                     HEADER + GLOBAL + f"    User 0:\n    firstInstallTime={FIRST}\n",
                     HEADER + GLOBAL + f"    User 0:\n      installReason=0\n        firstInstallTime={FIRST}\n")

    def test_empty_whitespace_control_and_duplicate_user_prefix_rejected(self):
        for value in ("", " ", " time", "time ", "time\r", "time\x00"):
            self.rejects(LEGACY.replace(FIRST, value), HEADER + GLOBAL + user(0) + user(999, value))
        self.rejects(HEADER + GLOBAL + user(0) + "      firstInstallTimeExtra=extra\n",
                     LEGACY + "\tfirstInstallTime=extra\n")

    def test_missing_fields_and_partial_inputs_do_not_emit_identity(self):
        self.rejects("", HEADER, HEADER + GLOBAL, GLOBAL + user(0), LEGACY.replace(f"    codePath={PATH}\n", ""),
                     LEGACY.replace(f"    lastUpdateTime={LAST}\n", ""), HEADER + GLOBAL + user(0, ""))
        for key in ("codePath", "firstInstallTime", "lastUpdateTime"):
            self.rejects(LEGACY[:LEGACY.index(key) + len(key)])

    def test_exact_single_package_and_outside_fields_required(self):
        self.rejects(CAPTURE + CAPTURE, LEGACY.replace("com.xpanport.hook]", "com.xpanport.hook.extra]"),
                     LEGACY + HEADER.replace("com.xpanport.hook", "com.other.app"),
                     LEGACY.replace("(fixture):", "(fixture): extra"),
                     GLOBAL + LEGACY, LEGACY + "Queries:\n    firstInstallTime=outside\n")

    def test_globals_cannot_be_per_user_and_nested_times_not_identity(self):
        self.rejects(HEADER + GLOBAL + user(0) + "      lastUpdateTime=extra\n",
                     HEADER + GLOBAL + user(0) + f"      codePath={PATH}\n",
                     HEADER + GLOBAL + user(0) + f"    lastUpdateTime={LAST}\n",
                     HEADER + GLOBAL + "    timeStamp=not-first-install\n    User 0:\n      timeStamp=not-first-install\n")

    def test_codepath_unknown_or_malformed_fails_closed(self):
        for path in ("", "/system/app/hook", "/data/app/", "/data/app/../other", "/data/app//hook",
                     "/data/app/with space/hook", "/data/app/hook\\extra"):
            self.rejects(LEGACY.replace(PATH, path))

    def test_resource_limits_reject_after_otherwise_complete_identity(self):
        self.rejects(LEGACY + "x" * 4097 + "\n", LEGACY + "\n" * 8192,
                     LEGACY + ("x" * 4096 + "\n") * 256,
                     HEADER + GLOBAL + "".join(user(i) for i in range(65)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
