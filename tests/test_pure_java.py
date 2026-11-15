"""Run dependency-free Java regression tests without creating device artifacts."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ANDROID = Path(os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME") or
               Path.home() / "AppData/Local/Android/Sdk") / "platforms/android-35/android.jar"
XPOSED = ROOT / "_tmp/api-82.jar"


class PureJavaTests(unittest.TestCase):
    def test_version_ratio_parser_and_inactive_postprocessor(self):
        with tempfile.TemporaryDirectory(prefix="pure-java-", dir=ROOT / "_tmp") as folder:
            classes = Path(folder) / "classes"
            classes.mkdir()
            sources = [ROOT / "hook" / (name + ".java") for name in
                       ("CameraProfile", "XdrRatioPatch")]
            sources.append(ROOT / "tests/support/XdrPostProcessor.java")
            sources.extend(ROOT / "tests" / (name + "Test.java") for name in
                           ("CameraProfile", "XdrRatioPatch", "XdrPostProcessor"))
            subprocess.run([shutil.which("javac"), "-encoding", "UTF-8", "-source", "8", "-target", "8",
                            "-bootclasspath", str(ANDROID), "-cp", os.pathsep.join(map(str, (ANDROID, XPOSED))),
                            "-d", str(classes), *map(str, sources)], check=True, cwd=ROOT)
            for name in ("CameraProfile", "XdrRatioPatch", "XdrPostProcessor"):
                subprocess.run([shutil.which("java"), "-cp", os.pathsep.join(map(str, (classes, ANDROID, XPOSED))),
                                "com.xpanport.hook." + name + "Test"], check=True, cwd=ROOT, timeout=120)


if __name__ == "__main__":
    unittest.main(verbosity=2)
