"""Offline production-helper and C16 scope/ABI checks; no device or camera-data operations."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hook/XpanHook.java"
ANDROID = Path(os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME") or
               Path.home() / "AppData/Local/Android/Sdk") / "platforms/android-35/android.jar"
C16 = ROOT.parent / "XpanPort/11_反编译"
C16_STORAGE = ROOT.parents[1] / "多版本相机/6.020.876"
C17_STORAGE = ROOT / "_tmp/decoded-PJX110_17.0.1.201/camera"
WM_NAMES = ("pref_ai_master_watermark_photo_style_id", "pref_ai_master_watermark_photo_open_state",
            "pref_watermark_function_key", "pref_hasselblad_watermark_function_key")


def smali_method(source, descriptor):
    match = re.search(r"^\.method [^\n]* " + re.escape(descriptor) + r"\n(.*?)^\.end method",
                      source, re.M | re.S)
    if match is None:
        raise AssertionError("missing exact smali ABI: " + descriptor)
    return match.group(1)


def instructions(body):
    return "\n".join(line.strip() for line in body.splitlines()
                     if line.strip() and not line.strip().startswith((".", "#")))


class WatermarkTests(unittest.TestCase):
    def test_production_transaction_with_java_fakes(self):
        with tempfile.TemporaryDirectory(prefix="watermark-pure-") as temporary:
            subprocess.run([shutil.which("javac"), "-encoding", "UTF-8", "-source", "8", "-target", "8",
                            "-d", temporary, str(ROOT / "hook/WatermarkState.java"),
                            str(ROOT / "tests/WatermarkStateTest.java")], check=True, cwd=ROOT)
            subprocess.run([shutil.which("java"), "-cp", temporary,
                            "com.xpanport.hook.WatermarkStateTest"], check=True, cwd=ROOT)

    def test_current_hook_compiles(self):
        self.assertTrue(ANDROID.is_file(), str(ANDROID))
        xposed = ROOT / "_tmp/api-82.jar"
        self.assertTrue(xposed.is_file(), str(xposed))
        with tempfile.TemporaryDirectory(prefix="watermark-hook-") as temporary:
            subprocess.run([shutil.which("javac"), "-encoding", "UTF-8", "-source", "8", "-target", "8",
                            "-bootclasspath", str(ANDROID), "-cp", os.pathsep.join((str(ANDROID), str(xposed))),
                            "-d", temporary, *(str(p) for p in sorted((ROOT / "hook").glob("*.java")))],
                           check=True, cwd=ROOT)

    def test_c16_actual_abi_and_commit_order(self):
        q3 = (C16 / "smali_classes12/wh/q3.smali").read_text(encoding="utf-8")
        w4 = (C16 / "smali_classes12/wh/w4.smali").read_text(encoding="utf-8")
        base = (C16 / "smali_classes12/com/oplus/camera/module/BaseMode.smali").read_text(encoding="utf-8")
        generate = (C16 / "smali/q7/x0.smali").read_text(encoding="utf-8")
        sdk = (C16 / "usdk_smali/smali/com/oplus/ocs/camera/producer/mode/BaseMode.smali").read_text(encoding="utf-8")
        self.assertIn(".field public final a:Lwh/w4;", q3)
        self.assertIn(".field public volatile a:Lcom/oplus/camera/module/BaseMode;", w4)
        self.assertIn(".field public final a0:Ljava/lang/String;", base)
        body = q3.split(".method public final d0(Ljava/lang/String;ZZ)Z", 1)[1].split(".end method", 1)[0]
        rebuilt = body.index("Lq7/x0;->e(")
        committed = re.search(r"iput-object[^\n]+Lwh/w4;->a:", body[rebuilt:])
        self.assertIsNotNone(committed)
        committed = rebuilt + committed.start()
        self.assertLess(rebuilt, committed)
        self.assertLess(committed, body.index("Lq7/x0;->d(", committed))
        self.assertLess(body.index("Lq7/x0;->d(", committed), body.index("Lwh/q3;->G(", committed))
        descriptor = "e(Landroid/app/Activity;Ljava/lang/String;IILcom/oplus/camera/filter/IEffectProcessor;Lcom/oplus/camera/module/BaseMode;Lnk/b;)V"
        self.assertIn(descriptor, generate)
        generation_body = generate.split(descriptor, 1)[1].split(".end method", 1)[0]
        self.assertIn("move/from16 v1, p4", generation_body)
        self.assertIn("{v5, v1}", generation_body)
        self.assertIn("getSurfaceFormatBySurfaceType(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)I", sdk)

    def test_actual_storage_absence_removal_and_global_factories(self):
        descriptor = "(Ljava/lang/Class;Ljava/lang/String;)Lcom/oplus/camera/data/DataKey;"
        versions = ((C16_STORAGE, "smali", "smali_classes25", "smali_classes27/c6/d.smali", "f", "i7", "h7/f", "h7/b", "smali_classes27", "smali_classes23"),
                    (C17_STORAGE, "smali_classes2", "smali", "smali/m9/d.smali", "b0", "wa", "va/d", "va/a", "smali_classes2", "smali_classes8"))
        bodies = []
        for root, dex, factory_dex, static_path, factory_name, storage, proxy, migrate, migrate_dex, mmkv_dex in versions:
            with self.subTest(version=str(root)):
                read = lambda path: (root / path).read_text(encoding="utf-8")
                factory_source = read(factory_dex + "/androidx/preference/e.smali")
                factory = instructions(smali_method(factory_source, factory_name + descriptor))
                self.assertIn('const-string v2, "com.oplus.camera_preferences"', factory)
                self.assertNotIn('"com.oplus.camera_preferences_"', factory)
                self.assertIn("const/4 v5, 0x0", factory)  # no from-other-app suffix
                self.assertIn("const/4 v6, 0x0", factory)
                self.assertIn("DataKey;-><init>(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/lang/Class;ZLjava/lang/Integer;)V", factory)
                static = read(static_path)
                self.assertIn("const-class v2, Ljava/lang/String;", static)
                self.assertIn("const-class v3, Ljava/lang/Boolean;", static)
                for index, name in enumerate(WM_NAMES):
                    tail = static.split('const-string v0, "' + name + '"', 1)[1]
                    self.assertRegex(tail, r"^\s*(?:\.line \d+\s*)*invoke-static \{v" + ("3" if index == 1 else "2") + r", v0\}, Landroidx/preference/e;->" + factory_name + re.escape(descriptor))
                if factory_name == "b0":
                    wrong = instructions(smali_method(factory_source, "M0" + descriptor))
                    self.assertIn('const-string v2, "com.oplus.camera_preferences_"', wrong)
                scheme = instructions(smali_method(read(migrate_dex + "/" + migrate + ".smali"), "a()Ljava/lang/String;"))
                self.assertIn('"sp"', scheme)
                self.assertIn('"mmkv"', scheme)
                self.assertNotIn('"memory"', scheme)
                dm = read(dex + "/com/oplus/camera/data/DataManager.smali")
                raw = instructions(smali_method(dm, "c(Lcom/oplus/camera/data/DataKey;)Ljava/lang/Object;"))
                self.assertIn("const/4 p1, 0x0", raw)
                self.assertIn("L" + proxy + ";->a(Landroid/net/Uri;Ljava/lang/Class;Ljava/lang/Object;)Ljava/lang/Object;", raw)
                reset = instructions(smali_method(dm, "f(Lcom/oplus/camera/data/DataKey;)V"))
                self.assertIn("DataKey;->h()Landroid/net/Uri;", reset)
                self.assertIn("L" + proxy + ";->c(Landroid/net/Uri;)V", reset)
                self.assertIn("L" + proxy + ";->d(Landroid/net/Uri;Ljava/lang/Class;Ljava/lang/Object;Z)V",
                              smali_method(dm, "g(Lcom/oplus/camera/data/DataKey;Ljava/lang/Object;)V"))
                proxy_source = read(dex + "/" + proxy + ".smali")
                getter = instructions(smali_method(proxy_source, "a(Landroid/net/Uri;Ljava/lang/Class;Ljava/lang/Object;)Ljava/lang/Object;"))
                self.assertLess(getter.index("->contains(Ljava/lang/String;)Z"), getter.index("->getString("))
                self.assertIn("if-nez p2, :cond_2", getter)
                self.assertIn("if-nez v3, :cond_1", getter)
                self.assertIn("move-object p2, v1\ngoto/16 :goto_c", getter)  # absent returns null, not Boolean false
                remover = instructions(smali_method(proxy_source, "c(Landroid/net/Uri;)V"))
                self.assertIn("->a(Landroid/net/Uri;)L" + storage + "/b;", remover)
                self.assertIn("->getFragment()Ljava/lang/String;", remover)
                self.assertIn("L" + storage + "/b;->b(Ljava/lang/String;)V", remover)
                self.assertIn("L" + storage + "/b;->apply()V", remover)
                router = instructions(smali_method(read(dex + "/" + storage + "/a.smali"), "a(Landroid/net/Uri;)L" + storage + "/b;"))
                self.assertIn('"com.oplus.camera_preferences"', router)
                self.assertIn('"memory"', router)
                self.assertIn("new-instance p1, L" + storage + "/f;", router)
                self.assertIn("new-instance p1, L" + storage + "/d;", router)
                sp = read(dex + "/" + storage + "/h.smali")
                self.assertIn("SharedPreferences$Editor;->remove(Ljava/lang/String;)", smali_method(sp, "b(Ljava/lang/String;)V"))
                self.assertIn("SharedPreferences;->contains(Ljava/lang/String;)Z", smali_method(sp, "contains(Ljava/lang/String;)Z"))
                mmkv = read(dex + "/" + storage + "/d.smali")
                self.assertIn("MMKV;->remove(Ljava/lang/String;)", smali_method(mmkv, "b(Ljava/lang/String;)V"))
                self.assertIn("MMKV;->contains(Ljava/lang/String;)Z", smali_method(mmkv, "contains(Ljava/lang/String;)Z"))
                native = read(mmkv_dex + "/com/tencent/mmkv/MMKV.smali")
                self.assertIn("->removeValueForKey(JLjava/lang/String;)V", smali_method(native, "remove(Ljava/lang/String;)Landroid/content/SharedPreferences$Editor;"))
                # Static-memory store really removes one fragment from all typed maps. Not an alias
                # of the global factory; present null cannot be inserted into ConcurrentHashMap.
                memory = read(dex + "/" + storage + "/f.smali")
                memory_remove = instructions(smali_method(memory, "b(Ljava/lang/String;)V"))
                self.assertEqual(memory_remove.count("ConcurrentHashMap;->remove(Ljava/lang/Object;)Ljava/lang/Object;"), 8)
                self.assertEqual(instructions(smali_method(memory, "contains(Ljava/lang/String;)Z")).count("->containsKey(Ljava/lang/Object;)Z"), 8)
                self.assertIn("ConcurrentHashMap;->put(Ljava/lang/Object;Ljava/lang/Object;)", smali_method(memory, "d(Ljava/lang/String;Ljava/lang/String;)Z"))
                self.assertIn("Optional;->orElse(Ljava/lang/Object;)", smali_method(memory, "getString(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;"))
                dk = instructions(smali_method(read(dex + "/com/oplus/camera/data/DataKey.smali"), "h()Landroid/net/Uri;"))
                for component in ('"com.oplus.camera"', '"class_type"', "->scheme(", "->path(", "->fragment("):
                    self.assertIn(component, dk)
                bodies.append((getter.replace("i7/", "wa/"), remover.replace("i7/", "wa/")))
        self.assertEqual(bodies[0], bodies[1], "C16/C17 raw read/removal control flow diverged")

    def test_atomic_lazy_abi_presence_and_archive_contract(self):
        source = HOOK.read_text(encoding="utf-8")
        watermark = source.split("private static final class WmAbi", 1)[1].split("/** X-Pan 仅允许", 1)[0]
        install = watermark.split("private void installWatermarkIsolation", 1)[1].split("private static Method wmStaticMethod", 1)[0]
        self.assertNotIn("wmInitAbi()", install)
        self.assertNotIn("dataKeyFactory", watermark)
        self.assertNotIn('Class.forName("c6.d"', watermark)
        self.assertNotIn('Class.forName("m9.d"', watermark)
        self.assertIn('factoryName = "f"; repoName = "i7.a"; storageName = "i7.b";', watermark)
        self.assertIn('factoryName = "b0"; repoName = "wa.a"; storageName = "wa.b";', watermark)
        init = watermark.split("synchronized WmAbi wmInitAbi()", 1)[1].split("private static Object wmRead", 1)[0]
        for binding in ('"c", Object.class, dk', '"g", void.class, dk, Object.class', '"f", void.class, dk',
                        '"h", android.net.Uri.class', '"contains", boolean.class, String.class'):
            self.assertIn(binding, init)
            self.assertLess(init.index(binding), init.index("sWmAbi = ready"))
        for component in ('"sp".equals(u.getScheme())', '"mmkv".equals(u.getScheme())',
                          '"com.oplus.camera".equals(u.getAuthority())', '"/com.oplus.camera_preferences".equals(u.getPath())',
                          'WM_NAME[i].equals(u.getFragment())', 'WM_TYPE[i].getName().equals(u.getQueryParameter("class_type"))'):
            self.assertIn(component, init)
        self.assertIn("present != (value != null)", watermark)
        self.assertIn("abi.remove.invoke(abi.dm, abi.keys[i])", watermark)
        self.assertIn("if (wmRead(i) != null)", watermark)
        self.assertIn('if (committed && !"xpan".equals(actualMode)) wmSave();', watermark)
        self.assertIn("String archive = sWatermark.archive();", watermark)
        self.assertIn('sWatermark.loadArchive(new String(buf, 0, n, "UTF-8"))', watermark)
        helper = (ROOT / "hook/WatermarkState.java").read_text(encoding="utf-8")
        self.assertIn('out.append("u\\t")', helper)
        self.assertIn('out.append("n\\t")', helper)
        self.assertIn('if ("u".equals(p[1]) || "n".equals(p[1]))', helper)
        self.assertIn("loadMine(saved, known); // publish only after every row is validated", helper)
        self.assertIn("if (wantsXpan && !mineKnown[i]) continue;", helper)
        self.assertIn("t.wrote = true; // a setter/remover may mutate before throwing", helper)
        restore = helper.split("private boolean restore(Transition t)", 1)[1].split("private boolean gateEnabled()", 1)[0]
        self.assertIn("if (!t.wrote) { t.restored = true; return true; }", restore)
        self.assertNotIn("!t.wrote && !t.staged", restore)
        self.assertLess(restore.index("if (!t.wrote)"), restore.index("put(i, t.live[i]);"))
        self.assertLess(helper.index("t.wrote = true;"), helper.index("put(i, t.target[i]);"))
        self.assertLess(helper.index("t.live = readAll();"), helper.index("put(i, t.target[i]);"))

    def test_no_delayed_watermark_guesses_or_requested_exit(self):
        source = HOOK.read_text(encoding="utf-8")
        watermark = source.split("private static Object wmRead", 1)[1].split("private static void wmSave", 1)[0]
        self.assertNotIn("postDelayed", watermark)
        self.assertNotIn("1500L", watermark)
        self.assertNotIn("wmEnterXpan", source)
        self.assertNotIn("wmExitXpan", source)
        self.assertNotIn("installXpanExitCleanup", source)
        self.assertIn("new XpanC17Hook.Gate()", source)
        self.assertIn("if (!config.installed || !installModeGate(lp)) return;", source)
        hook = source.split("private boolean installModeGate", 1)[1].split("private void installHalSizeFix", 1)[0]
        self.assertIn('modeMethod(manager, "d0", boolean.class, String.class, boolean.class, boolean.class)', hook)
        self.assertIn('modeField(holder, "a", actualBase, Modifier.VOLATILE)', hook)
        self.assertIn('modeField(actualBase, "a0", String.class, Modifier.FINAL)', hook)
        self.assertNotIn("param.setResult(", hook)
        self.assertNotIn("param.setThrowable(", hook)
        self.assertIn("Delivered despite gateoff/throwable", hook)
        stream = hook.split("hookMethod(bss", 1)[1].split("hookMethod(generate", 1)[0]
        self.assertNotIn("sXpanActive =", stream)
        self.assertNotIn("wmBeforeModeChange", stream)
        self.assertNotIn("wmAfterModeChange", stream)
        after = stream.split("afterHookedMethod", 1)[1]
        self.assertNotIn("moduleEnabled()", after)
        self.assertIn("sXpanBuilding.remove()", after)
        generation = hook.split("hookMethod(generate", 1)[1]
        after = generation.split("afterHookedMethod", 1)[1].split("sC16SurfaceFormat =", 1)[0]
        self.assertNotIn("moduleEnabled()", after)
        self.assertIn("sFeatureGeneration.remove()", after)
        self.assertIn('generation.mask != (Integer) param.args[0]', source)
        self.assertIn('!"xpan".equals(generation.mode)', source)

    def test_raw_private_and_unknown_size_boundaries(self):
        source = HOOK.read_text(encoding="utf-8")
        target = source.split("private static Size targetFor", 1)[1].split("private static String describe", 1)[0]
        self.assertIn('!"rear_main".equals(cameraTypeOf(wrapper))', target)
        self.assertIn('getter.getReturnType() != int.class', target)
        self.assertIn('ImageFormat.YUV_420_888', target)
        self.assertIn('ImageFormat.JPEG', target)
        self.assertIn('ImageFormat.HEIC', target)
        self.assertNotIn('raw_output', target)
        self.assertNotIn('raw12_output', target)
        self.assertNotIn('startsWith("capture")', target)
        self.assertIn('!"rear_main".equals(a[3])', target)
        self.assertIn('sC16SurfaceFormat', target)
        self.assertIn('return building == null ? sXpanActive : building;', target)


if __name__ == "__main__":
    unittest.main(verbosity=2)
