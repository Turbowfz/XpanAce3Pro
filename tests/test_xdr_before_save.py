"""Offline C16/C17 before-save ABI/owner-clone tests. No device, install or original file writes."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hook/XdrBeforeSave.java"
C16 = ROOT.parents[1] / "多版本相机/6.020.876"
C17 = ROOT / "_tmp/decoded-PJX110_17.0.1.201/camera"
ANDROID = Path(os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME") or
               Path.home() / "AppData/Local/Android/Sdk") / "platforms/android-35/android.jar"
XPOSED = ROOT / "_tmp/api-82.jar"
PICTURE = "Lcom/oplus/camera/picturestore/CameraPicture;"


class BeforeSaveTests(unittest.TestCase):
    def test_decoded_public_static_bindings_and_owner_handoffs(self):
        cases = [(C16, "smali_classes12", "ui/t", "P", "h", "i", "l", "W", "V", "d"),
                 (C17, "smali_classes7", "gn/r", "c0", "i", "k", "n", "j0", "i0", "e")]
        for root, dex, storage, update, data, hardware, fmt, initial, writer, exif in cases:
            with self.subTest(storage=storage):
                source = (root / dex / (storage + ".smali")).read_text(encoding="utf-8")
                picture = (root / dex / "com/oplus/camera/picturestore/CameraPicture.smali").read_text(encoding="utf-8")
                self.assertIn('.source "Storage.java"', source)
                for name, params in [("a", PICTURE), (update, PICTURE + "Landroid/content/ContentValues;")]:
                    descriptor = name + "(" + params + ")" + PICTURE
                    self.assertRegex(source, r"(?m)^\.method public static " + re.escape(descriptor) + r"\s*$")
                    body = source.split(".method public static " + descriptor, 1)[1].split(".end method", 1)[0]
                    self.assertIn("CameraPicture;->" + data + ":[B", body)
                    self.assertIn("CameraPicture;->" + hardware + ":Landroid/hardware/HardwareBuffer;", body)
                    self.assertIn("CameraPicture;->" + fmt + ":Ljava/lang/String;", body)
                    self.assertIn('"heic_8bits"', body)
                    self.assertIn('"heic_10bits"', body)
                    self.assertIn("L" + storage + ";->" + initial + "(Landroid/net/Uri;" + PICTURE + ")V", body)
                for name, kind in [(data, "[B"), (hardware, "Landroid/hardware/HardwareBuffer;"),
                                   (fmt, "Ljava/lang/String;")]:
                    self.assertRegex(picture, r"(?m)^\.field public " + re.escape(name + ":" + kind) + r"\s*$")
                # Both EXIF load and full encoded-array write read the OWNER.
                initial_body = source.split(".method public static " + initial + "(", 1)[1].split(".end method", 1)[0]
                self.assertIn("CameraPicture;->" + data + ":[B", initial_body)
                self.assertIn("Lcom/oplus/exif/OplusExifInterface;->writeExif([BLjava/io/OutputStream;)V", initial_body)
                self.assertIn("L" + storage + ";->" + exif + "(" + PICTURE + ")Lcom/oplus/exif/OplusExifInterface;", initial_body)
                exif_body = source.split(".method public static " + exif + "(" + PICTURE, 1)[1].split(".end method", 1)[0]
                self.assertIn("CameraPicture;->" + data + ":[B", exif_body)
                self.assertIn("Lcom/oplus/exif/OplusExifInterface;->readExif([B)V", exif_body)
                self.assertIn("L" + storage + ";->" + writer + "(Landroid/net/Uri;[BLandroid/hardware/HardwareBuffer;JJ)Z", source)
                update_body = source.split(".method public static " + update + "(", 1)[1].split(".end method", 1)[0]
                self.assertIn("Landroid/media/ExifInterface;-><init>(Ljava/io/InputStream;)V", update_body)

    def test_source_boundaries(self):
        source = HOOK.read_text(encoding="utf-8")
        self.assertLessEqual(len(source.splitlines()), 200)
        self.assertLess(source.index('Method update = method('), source.index('h.register(save)'))
        self.assertLess(source.index('h.register(update)'), source.index('h.enabled = true'))
        self.assertIn('h.rollback()', source)
        self.assertIn('Class.forName("com.oplus.camera.picturestore.CameraPicture", false, loader)', source)
        self.assertIn('m.getReturnType() != result', source)
        self.assertIn('!Modifier.isStatic(m.getModifiers())', source)
        self.assertIn('XdrRatioPatch.raiseRatioMax(input, 8.0)', source)
        self.assertEqual(source.count('bytes.set(owner, copy)'), 1)
        for forbidden in ('java.io.', 'java.nio.file', 'FileOutputStream', 'Thread(', 'ThreadLocal',
                          'System.load', 'Build.MODEL', 'getDeclaredFields(', '.isClosed(', '.close(',
                          'setResult(', 'setThrowable(', 'p.args[0] =', 'invokeOriginalMethod', '->', 'rename'):
            self.assertNotIn(forbidden, source)

    def test_java8_real_api_compile_and_fake_runtime(self):
        javac, java = shutil.which("javac"), shutil.which("java")
        self.assertIsNotNone(javac, "javac required")
        self.assertIsNotNone(java, "java required")
        self.assertTrue(ANDROID.is_file(), str(ANDROID))
        self.assertTrue(XPOSED.is_file(), str(XPOSED))
        with tempfile.TemporaryDirectory(prefix="xdr-before-save-", dir=ROOT / "_tmp") as temporary:
            temporary = Path(temporary)
            hook_classes = temporary / "hook-classes"
            hook_classes.mkdir()
            api = os.pathsep.join(map(str, (ANDROID, XPOSED)))
            subprocess.run([javac, "-encoding", "UTF-8", "-source", "8", "-target", "8",
                            "-bootclasspath", str(ANDROID), "-classpath", api, "-d", str(hook_classes),
                            str(HOOK), str(ROOT / "hook/XdrRatioPatch.java"),
                            str(ROOT / "tests/XdrRatioPatchTest.java")], check=True, cwd=ROOT)
            for generation in ("c16", "c17"):
                for defect in ("", "field_type", "final_bytes", "static_hardware", "private_format",
                               "return_type", "instance_save", "missing_update"):
                    with self.subTest(generation=generation, defect=defect):
                        folder = temporary / (generation + "-" + (defect or "valid"))
                        folder.mkdir()
                        sources = self._fake_sources(generation, defect)
                        paths = []
                        for relative, source in sources.items():
                            path = folder / relative
                            path.parent.mkdir(parents=True, exist_ok=True)
                            path.write_text(source, encoding="utf-8")
                            paths.append(str(path))
                        runtime = folder / "classes"
                        runtime.mkdir()
                        subprocess.run([javac, "-encoding", "UTF-8", "-source", "8", "-target", "8",
                                        "-classpath", str(hook_classes), "-d", str(runtime)] + paths,
                                       check=True, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="backslashreplace")
                        classpath = os.pathsep.join(map(str, (runtime, hook_classes)))
                        scenarios = ["abi_failure"] if defect else ["semantics", "gate", "rollback", "rollback_failure", "null_handle"]
                        for scenario in scenarios:
                            completed = subprocess.run([java, "-cp", classpath,
                                                        "com.xpanport.hook.BeforeSaveCheck", generation, scenario],
                                                       check=True, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="backslashreplace")
                            self.assertIn("PASS", completed.stdout)
                            print(completed.stdout.strip())

    @staticmethod
    def _fake_sources(generation, defect):
        c16 = generation == "c16"
        data, hardware, fmt = ("h", "i", "l") if c16 else ("i", "k", "n")
        package, storage, update = ("ui", "t", "P") if c16 else ("gn", "r", "c0")
        byte_field = "public byte[] " + data + ";"
        hw_field = "public android.hardware.HardwareBuffer " + hardware + ";"
        fmt_field = "public String " + fmt + ";"
        if defect == "field_type":
            byte_field = "public Object " + data + ";"
        if defect == "final_bytes":
            byte_field = "public final byte[] " + data + " = null;"
        if defect == "static_hardware":
            hw_field = "public static android.hardware.HardwareBuffer " + hardware + ";"
        if defect == "private_format":
            fmt_field = "private String " + fmt + ";"
        save_modifiers = "public" if defect == "instance_save" else "public static"
        result = "Object" if defect == "return_type" else "CameraPicture"
        update_source = "" if defect == "missing_update" else (
            "public static CameraPicture " + update + "(CameraPicture p, android.content.ContentValues v) { return original(p); }")
        storage_source = """package PACKAGE;
import com.oplus.camera.picturestore.CameraPicture;
public class STORAGE {
  static { com.xpanport.hook.BeforeSaveCheck.initializations++; }
  public static byte[] seenWrite, seenReload;
  SAVE_MOD RESULT a(CameraPicture p) { return original(p); }
  UPDATE
  private static CameraPicture original(CameraPicture p) {
    try {
      java.lang.reflect.Field f = CameraPicture.class.getField("DATA");
      seenWrite = (byte[]) f.get(p); seenReload = (byte[]) f.get(p); return p;
    } catch (Exception e) { throw new AssertionError(e); }
  }
} """
        for key, value in (("PACKAGE", package), ("STORAGE", storage), ("SAVE_MOD", save_modifiers),
                           ("RESULT", result), ("UPDATE", update_source), ("DATA", data)):
            storage_source = storage_source.replace(key, value)
        return {
            "android/content/ContentValues.java": "package android.content; public final class ContentValues {}",
            "android/hardware/HardwareBuffer.java": """package android.hardware;
public final class HardwareBuffer {
  public boolean closed;
  public void close() { closed = true; }
  public boolean isClosed() { throw new AssertionError("must not inspect hardware even if closed"); }
} """,
            "de/robv/android/xposed/XC_MethodHook.java": r"""package de.robv.android.xposed;
public abstract class XC_MethodHook {
  protected void beforeHookedMethod(MethodHookParam p) throws Throwable {}
  public static class MethodHookParam {
    public Object[] args; public java.lang.reflect.Member method;
    public void setResult(Object r) { throw new AssertionError("must not short circuit original"); }
    public void setThrowable(Throwable t) { throw new AssertionError("must not fail original"); }
  }
  public class Unhook {
    final java.lang.reflect.Member member;
    public Unhook(java.lang.reflect.Member m) { member = m; }
    public void unhook() {
      XposedBridge.unhookOrder.add(member.getName());
      if (XposedBridge.failUnhook) throw new AssertionError("unhook failed");
      XposedBridge.active.remove(member);
    }
  }
} """,
            "de/robv/android/xposed/XposedBridge.java": r"""package de.robv.android.xposed;
import java.lang.reflect.*;
import java.util.*;
public final class XposedBridge {
  public static int registrations, failAt;
  public static boolean failUnhook, throwLog, nullHandle;
  public static final Map<Member, XC_MethodHook> active = new LinkedHashMap<Member, XC_MethodHook>();
  public static final List<String> logs = new ArrayList<String>(), unhookOrder = new ArrayList<String>();
  public static XC_MethodHook.Unhook hookMethod(Member m, XC_MethodHook hook) {
    registrations++;
    // Pending coupled hooks must be harmless before all registrations succeed.
    XC_MethodHook.MethodHookParam early = new XC_MethodHook.MethodHookParam();
    early.method = m; early.args = new Object[]{null};
    try { hook.beforeHookedMethod(early); } catch (Throwable t) { throw new AssertionError(t); }
    if (registrations == failAt) {
      if (nullHandle) return null;
      throw new AssertionError("registration failed");
    }
    active.put(m, hook); return hook.new Unhook(m);
  }
  public static void log(String message) {
    if (throwLog) throw new AssertionError("logger failed");
    logs.add(message);
  }
  public static Object save(Method m, Object owner, Object values) throws Throwable {
    XC_MethodHook.MethodHookParam p = new XC_MethodHook.MethodHookParam();
    p.method = m; p.args = m.getParameterTypes().length == 1 ? new Object[]{owner} : new Object[]{owner, values};
    XC_MethodHook hook = active.get(m);
    if (hook != null) hook.beforeHookedMethod(p);
    return m.invoke(null, p.args);
  }
  public static void brokenArgs(Method m) throws Throwable {
    XC_MethodHook.MethodHookParam p = new XC_MethodHook.MethodHookParam();
    p.method = m; p.args = null; active.get(m).beforeHookedMethod(p);
  }
} """,
            "com/oplus/camera/picturestore/CameraPicture.java": (
                "package com.oplus.camera.picturestore; public class CameraPicture {\n"
                "static { com.xpanport.hook.BeforeSaveCheck.initializations++; }\n" +
                byte_field + hw_field + fmt_field + "\n}"),
            package + "/" + storage + ".java": storage_source,
            "com/xpanport/hook/BeforeSaveCheck.java": HARNESS,
        }


HARNESS = r'''package com.xpanport.hook;
import java.lang.reflect.*;
import java.util.Arrays;
import com.oplus.camera.picturestore.CameraPicture;
import android.content.ContentValues;
import android.hardware.HardwareBuffer;
import de.robv.android.xposed.XposedBridge;
public class BeforeSaveCheck {
  public static int initializations;
  static class TestGate implements XdrBeforeSave.Gate {
    int calls, revokeAt; boolean allow = true, fail; Runnable onCall;
    public boolean enabled() {
      calls++; if (onCall != null) onCall.run();
      if (fail) throw new AssertionError("gate failure"); return allow && calls != revokeAt;
    }
  }
  static void check(boolean value, String why) { if (!value) throw new AssertionError(why); }
  static void put(Object owner, String name, Object value) throws Exception {
    Field f = owner.getClass().getDeclaredField(name); f.setAccessible(true); f.set(owner, value);
  }
  static byte[] fixture(boolean noTmap) throws Exception {
    Class<?> options = Class.forName("com.xpanport.hook.XdrRatioPatchTest$Options");
    Constructor<?> constructor = options.getDeclaredConstructor(); constructor.setAccessible(true);
    Object o = constructor.newInstance(); put(o, "noTmap", noTmap);
    Method f = XdrRatioPatchTest.class.getDeclaredMethod(noTmap ? "fixture" : "safeFixture", options);
    f.setAccessible(true); return (byte[]) f.invoke(null, o);
  }
  static CameraPicture owner(String data, String hardware, String format, byte[] input, String kind, HardwareBuffer h) throws Exception {
    CameraPicture p = new CameraPicture(); put(p, data, input); put(p, hardware, h); put(p, format, kind); return p;
  }
  static byte[] bytes(CameraPicture p, String data) throws Exception { return (byte[]) p.getClass().getField(data).get(p); }
  static void unchanged(Method m, Class<?> storage, CameraPicture p, String data) throws Throwable {
    byte[] original = bytes(p, data), snapshot = original == null ? null : original.clone();
    check(XposedBridge.save(m, p, null) == p, "original result changed");
    check(bytes(p, data) == original && Arrays.equals(original, snapshot), "rejected owner/input changed");
    check(storage.getField("seenWrite").get(null) == original && storage.getField("seenReload").get(null) == original,
        "original writer/reload received wrong rejected reference");
  }
  public static void main(String[] args) throws Throwable {
    boolean c16 = "c16".equals(args[0]); String scenario = args[1];
    String version = c16 ? "6.020.876" : "7.013.51";
    String data = c16 ? "h" : "i", hardware = c16 ? "i" : "k", format = c16 ? "l" : "n";
    ClassLoader loader = BeforeSaveCheck.class.getClassLoader();
    for (String wrong : new String[]{null,"","7.013.510","6.020.876-extra","7.012.64"})
      check(!XdrBeforeSave.install(loader, wrong).installed, "inexact version accepted");
    check(!XdrBeforeSave.install(null, version).installed, "null loader accepted");
    check(!XdrBeforeSave.install(new ClassLoader(null) {}, version).installed, "unavailable classes accepted");
    check(XposedBridge.registrations == 0 && initializations == 0, "refusal initialized or registered target");
    if (scenario.startsWith("rollback") || "null_handle".equals(scenario)) {
      XposedBridge.failAt = 2; XposedBridge.failUnhook = "rollback_failure".equals(scenario);
      XposedBridge.nullHandle = "null_handle".equals(scenario);
    }
    TestGate gate = new TestGate();
    XdrBeforeSave.InstallResult result = XdrBeforeSave.install(loader, version, gate);
    check(initializations == 0, "reflection initialized app classes");
    if ("abi_failure".equals(scenario)) {
      check(!result.installed && result.registeredHooks == 0 && XposedBridge.registrations == 0,
          "partial registration before complete validation");
    } else if ("gate".equals(scenario)) {
      check(result.installed, "gate install failed");
      Class<?> storage = Class.forName(c16 ? "ui.t" : "gn.r");
      Method save = storage.getMethod("a", CameraPicture.class);
      byte[] input = fixture(false);
      gate.allow = false;
      unchanged(save, storage, owner(data, hardware, format, input, "heic_8bits", null), data);
      check(result.runtimeStatus().checked == 0, "disabled gate parsed owner");
      gate.allow = true; gate.revokeAt = gate.calls + 2;
      unchanged(save, storage, owner(data, hardware, format, input, "heic_8bits", null), data);
      check(result.runtimeStatus().patched == 0 && result.runtimeStatus().detail.contains("gate revoked"),
          "gate revoked during parse still assigned clone");
      gate.revokeAt = 0;
      final CameraPicture replaced = owner(data, hardware, format, input, "heic_8bits", null);
      final byte[] replacement = input.clone();
      final String byteField = data;
      final int at = gate.calls + 2;
      final TestGate changingGate = gate;
      gate.onCall = new Runnable() {
        public void run() { if (changingGate.calls == at) try { put(replaced, byteField, replacement); }
          catch (Exception e) { throw new AssertionError(e); } }
      };
      XposedBridge.save(save, replaced, null);
      check(bytes(replaced, data) == replacement, "stale owner overwritten after blocking final qualification");
      check(result.runtimeStatus().patched == 0, "stale owner counted as patched");
      gate.onCall = null; gate.fail = true;
      unchanged(save, storage, owner(data, hardware, format, input, "heic_8bits", null), data);
      check(result.runtimeStatus().failures == 1, "gate exception escaped/was not recorded");
    } else if (!"semantics".equals(scenario)) {
      check(!result.installed && XposedBridge.registrations == 2, "registration failure not detected");
      check(result.registeredHooks == (XposedBridge.failUnhook ? 1 : 0), "residual hook count");
      check(XposedBridge.unhookOrder.equals(Arrays.asList("a")), "rollback did not unhook first save");
      check(!result.runtimeStatus().enabled && result.runtimeStatus().checked == 0, "partial hooks ran");
      if (XposedBridge.failUnhook) {
        XposedBridge.brokenArgs((Method) XposedBridge.active.keySet().iterator().next());
        check(result.runtimeStatus().checked == 0, "residual hook not disabled");
        check(XdrBeforeSave.install(loader, version) == result && XposedBridge.registrations == 2,
            "retry accumulated disabled residual hooks");
      }
    } else {
      check(result.installed && result.registeredHooks == 2 && result.runtimeStatus().enabled, "not installed");
      check(result.runtimeStatus().checked == 0, "hook ran during registration");
      check(result == XdrBeforeSave.install(loader, version) && XposedBridge.registrations == 2, "duplicate install");
      check(!XdrBeforeSave.install(loader, c16 ? "7.013.51" : "6.020.876").installed
          && XposedBridge.registrations == 2, "cached install falsely claimed other version");
      Class<?> storage = Class.forName(c16 ? "ui.t" : "gn.r");
      Method save = storage.getMethod("a", CameraPicture.class);
      Method update = storage.getMethod(c16 ? "P" : "c0", CameraPicture.class, ContentValues.class);
      byte[] input = fixture(false), snapshot = input.clone();
      CameraPicture p = owner(data, hardware, format, input, "heic_8bits", null);
      CameraPicture shared = owner(data, hardware, format, input, "heic_8bits", null);
      check(XposedBridge.save(save, p, null) == p, "save result/owner replaced");
      byte[] copy = bytes(p, data);
      check(copy != input && copy.length == input.length && Arrays.equals(input, snapshot), "input clone semantics");
      check(bytes(shared, data) == input && Arrays.equals(bytes(shared, data), snapshot), "shared consumer mutated");
      check(storage.getField("seenWrite").get(null) == copy && storage.getField("seenReload").get(null) == copy,
          "owner patched too late or only writer argument patched");
      check(XdrRatioPatch.raiseRatioMax(copy, 8).status == XdrRatioPatch.Status.ALREADY_SUFFICIENT, "patch not 8x");
      byte[] expected = XdrRatioPatch.raiseRatioMax(input, 8).bytes();
      check(Arrays.equals(copy, expected), "bytes differ from pure validated parser output");
      unchanged(update, storage, p, data);
      check(bytes(p, data) == copy, "repeat assignment/clone");
      CameraPicture ten = owner(data, hardware, format, input, "heic_10bits", null);
      XposedBridge.save(update, ten, new ContentValues());
      check(bytes(ten, data) != input && Arrays.equals(bytes(ten, data), expected), "update/10bit not patched");
      for (String kind : new String[]{null,"jpeg","heic","HEIC_8BITS","heic_8bits ","raw"})
        unchanged(save, storage, owner(data, hardware, format, input, kind, null), data);
      for (boolean closed : new boolean[]{false,true}) {
        HardwareBuffer h = new HardwareBuffer(); if (closed) h.close();
        unchanged(update, storage, owner(data, hardware, format, input, "heic_10bits", h), data);
        check(h.closed == closed, "hardware touched");
      }
      byte[] noTmap = fixture(true);
      check(XdrRatioPatch.raiseRatioMax(noTmap, 8).status == XdrRatioPatch.Status.NO_TMAP, "no tmap fixture");
      XposedBridge.logs.clear();
      for (int i = 0; i < 64; i++) unchanged(save, storage, owner(data, hardware, format, noTmap, "heic_8bits", null), data);
      check(XposedBridge.logs.size() == 2 && XposedBridge.logs.get(0).contains("status=NO_TMAP")
          && XposedBridge.logs.get(1).contains("noTmap="), "first no-tmap unchanged/summary missing or log flood");
      for (byte[] bad : new byte[][]{null,new byte[0],new byte[]{1,2,3},Arrays.copyOf(input,input.length-1)})
        unchanged(save, storage, owner(data, hardware, format, bad, "heic_8bits", null), data);
      XposedBridge.throwLog = true;
      XposedBridge.brokenArgs(save); // Callback exception and logger exception never escape.
      CameraPicture logFail = owner(data, hardware, format, input, "heic_8bits", null);
      XposedBridge.save(save, logFail, null);
      check(Arrays.equals(bytes(logFail, data), expected), "logging failure affected original");
      XdrBeforeSave.RuntimeStatus status = result.runtimeStatus();
      check(status.checked == 80 && status.patched == 3 && status.already == 1 && status.unsupported == 77
          && status.noTmap == 64 && status.failures == 1, "runtime counters incorrect: checked=" + status.checked
          + " patched=" + status.patched + " already=" + status.already + " unsupported=" + status.unsupported);
      check(status.detail.contains("status=PATCHED") && status.detail.contains("tmap=1"), "runtime evidence absent");
    }
    System.out.println("PASS " + args[0] + " " + scenario + "; offline ABI/clone semantics, not C17 capture/final-XDR proof");
  }
}'''


if __name__ == "__main__":
    unittest.main()
