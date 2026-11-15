"""Offline-only C17 ABI and narrow helper checks; never contacts a device or changes config."""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DECODED = ROOT / "_tmp/decoded-PJX110_17.0.1.201"
HOOK = ROOT / "hook/XpanC17Hook.java"
ANDROID = Path(os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME") or
               Path.home() / "AppData/Local/Android/Sdk") / "platforms/android-35/android.jar"
XPOSED = ROOT / "_tmp/api-82.jar"


class C17HookTests(unittest.TestCase):
    def test_exact_decoded_bindings(self):
        # Descriptors, not method-name/arity guesses. These are the coupled install bindings.
        cases = {
            "sdk/smali/com/oplus/ocs/camera/producer/mode/BaseMode.smali": [
                "buildStreamSurface(Lcom/oplus/ocs/camera/common/parameter/SdkCameraDeviceConfig;Ljava/lang/String;)Ljava/util/LinkedList;",
                "getSurfaceWrapper(Lcom/oplus/ocs/camera/common/parameter/SdkCameraDeviceConfig;ILjava/lang/String;Ljava/lang/String;Landroid/util/Size;Landroid/util/Size;ZLjava/lang/String;)Lcom/oplus/ocs/camera/common/surface/SurfaceWrapper;",
                "getSurfaceFormatBySurfaceType(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)I"],
            "sdk/smali/com/oplus/ocs/camera/producer/mode/XpanMode.smali": [
                "getSurfaceSize(Lcom/oplus/ocs/camera/common/parameter/SdkCameraDeviceConfig;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;I)Ljava/util/List;"],
            "sdk/smali/com/oplus/ocs/camera/common/surface/SurfaceWrapper.smali": [
                "<init>(Ljava/lang/String;Ljava/lang/String;Landroid/util/Size;Landroid/util/Size;II)V",
                "getHalSurfaceSize()Landroid/util/Size;", "getAppSurfaceSize()Landroid/util/Size;",
                "setHalSurfaceSize(Landroid/util/Size;)V", "setAppSurfaceSize(Landroid/util/Size;)V",
                "getCameraType()Ljava/lang/String;", "getSurfaceUsage()Ljava/lang/String;", "getFormat()I", "getSurfaceType()I"],
            "sdk/smali/com/oplus/ocs/camera/producer/device/Camera2Impl.smali": [
                "takePicture(Lcom/oplus/ocs/camera/common/util/CameraRequestTag;Lcom/oplus/ocs/camera/metadata/parameter/Parameter;Landroid/os/Handler;ZZ)V"],
            "camera/smali_classes2/eb/i1.smali": [
                "f(Landroid/app/Activity;Ljava/lang/String;IILcom/oplus/camera/filter/IEffectProcessor;Lcom/oplus/camera/module/BaseMode;Lgp/a;)V"],
            "camera/smali_classes2/eb/f0.smali": [
                "c(ILandroid/app/Activity;Lcom/oplus/camera/protocal/ui/a;Ljava/lang/String;Ljava/lang/String;)Lcom/oplus/camera/mg;"],
            "camera/smali_classes8/com/oplus/ocs/camera/config/FeatureConfigureAdapter.smali": [
                "getFeatureInfoList(I)Ljava/util/Set;"],
            "camera/smali_classes7/bl/d.smali": [
                "<init>(ILandroid/app/Activity;Lcom/oplus/camera/protocal/ui/a;Ljava/lang/String;)V"],
            "camera/smali_classes7/dl/w.smali": [
                "h(Landroid/app/Activity;Lcom/oplus/camera/protocal/ui/a;Ldl/j$f;Lbl/d$b;)V"],
            "camera/smali_classes7/com/oplus/camera/protocal/ui/a.smali": ["gf()Luq/l;"],
            "camera/smali_classes8/uq/l.smali": ["uh(I)V"],
            "camera/smali_classes7/cm/q3.smali": ["o0(Ljava/lang/String;ZZ)Z", "X()V"],
            "camera/smali_classes7/fl/g.smali": ["i(F)V", "j(F)V", "h()F", "g(F)F"],
            "camera/smali_classes2/ab/c.smali": ["d0(Ljava/lang/String;)V"],
        }
        zoom = "Hc(FLfl/h;FFLjava/util/ArrayList;Ljava/util/ArrayList;Ljava/util/ArrayList;IZZZZZZLjava/util/ArrayList;ZZFLjava/util/ArrayList;Ljava/lang/String;)V"
        cases["camera/smali_classes7/com/oplus/camera/feature/zoom/view/ZoomSeekBar.smali"] = [zoom]
        cases["camera/smali_classes7/com/oplus/camera/feature/zoom/view/f.smali"] = [zoom]
        for relative, descriptors in cases.items():
            text = (DECODED / relative).read_text(encoding="utf-8")
            actual = re.findall(r"^\.method .*? (\S+)\s*$", text, re.MULTILINE)
            for descriptor in descriptors:
                with self.subTest(file=relative, descriptor=descriptor):
                    self.assertIn(descriptor, actual)
        fields = {
            "camera/smali_classes7/cm/q3.smali": "a:Lcm/i5;",
            "camera/smali_classes7/cm/i5.smali": "a:Lcom/oplus/camera/module/BaseMode;",
            "camera/smali_classes7/com/oplus/camera/module/BaseMode.smali": "U0:Ljava/lang/String;",
            "camera/smali_classes7/dl/w.smali": "T:Lio/a;",
            "camera/smali_classes8/uq/l.smali": "C0:I",
            "camera/smali_classes7/fl/g.smali": "j:Lab/c;",
            "camera/smali_classes2/ab/c.smali": "W0:Ljava/lang/String;",
            "sdk/smali/com/oplus/ocs/camera/common/util/CameraRequestTag.smali": "mCaptureMode:Ljava/lang/String;",
        }
        for relative, field in fields.items():
            text = (DECODED / relative).read_text(encoding="utf-8")
            with self.subTest(field=field):
                self.assertRegex(text, r"(?m)^\.field (?!.*\bstatic\b).* " + re.escape(field) + r"\s*$")
        model = (DECODED / "camera/smali_classes7/fl/g.smali").read_text(encoding="utf-8")
        self.assertIn('.source "ZoomModel.java"', model)
        self.assertIn('const-string v0, "surround_record"', model)  # d is explicitly NOT mode
        wrapper = (DECODED / "sdk/smali/com/oplus/ocs/camera/common/surface/SurfaceWrapper.smali").read_text(encoding="utf-8")
        getter = wrapper.split('.method public getHalSurfaceSize()', 1)[1].split('.end method', 1)[0]
        self.assertIn('->mAppSurfaceSize:Landroid/util/Size;', getter)
        manager = (DECODED / "camera/smali_classes7/cm/q3.smali").read_text(encoding="utf-8")
        switch = manager.split('.method public final o0(', 1)[1].split('.end method', 1)[0]
        self.assertLess(switch.index('Leb/i1;->f('), switch.index('iput-object v3, v2, Lcm/i5;->a:'))

    def test_mutation_boundaries(self):
        source = HOOK.read_text(encoding="utf-8")
        self.assertLess(source.index('b.resolve(loader);'), source.index('h.register();'))
        self.assertLess(source.index('h.register();'), source.index('h.enabled = true;'))
        self.assertIn('h.rollback()', source)
        self.assertIn('Callbacks cb, Gate gate)', source)
        self.assertIn('enabled = h != null && h.active();', source)
        self.assertIn('h.lastDiagnostic = detail;', source)
        catch = source.split('} catch (Throwable failure) {', 1)[1].split('private final Bindings', 1)[0]
        self.assertIn('INSTALLS.put(loader, result);', catch)
        self.assertIn('return result;', catch)
        self.assertIn('if (!enabled) return false;', source)
        self.assertIn('try { return gate.enabled(); }', source)
        self.assertIn('catch (Throwable ignored)', source)
        self.assertIn('getDeclaredMethod(name, params)', source)
        self.assertIn('m.getReturnType() != result', source)
        self.assertIn('g.mask != (Integer) p.args[0]', source)
        self.assertIn('p.setResult(copy)', source)
        self.assertIn('!XPAN.equals(p.args[19])', source)
        self.assertIn('p.args[4] = clicks;', source)
        self.assertIn('p.args[5] = points;', source)
        self.assertNotIn('p.args[6] =', source)
        self.assertNotIn('p.args[14] =', source)
        self.assertNotIn('p.args[18] =', source)
        self.assertNotIn('p.setThrowable(', source)
        self.assertIn('if (scope != null && !scope) return null;', source)
        self.assertIn('ArrayList<SurfaceRecord> xpanSurfaces', source)
        self.assertIn('b.format.invoke(wrapper)', source)
        self.assertIn('b.surfaceType.invoke(wrapper)', source)
        self.assertIn('StreamIdentity((String) p.args[2], (String) p.args[3])', source)
        self.assertIn('!safeFormat(stream, format)', source)
        self.assertNotIn('usageTarget(', source)
        self.assertNotIn('WeakHashMap<Object', source)
        for forbidden in ('zl.q', 'bh.c', 'dh.w', 'getConfigBooleanValue',
                          'FileOutputStream', 'SharedPreferences', 'System.loadLibrary', 'invokeOriginalMethod'):
            self.assertNotIn(forbidden, source)

    def test_verified_format_and_surface_kind_mapping(self):
        sdk = DECODED / "sdk/smali/com/oplus/ocs/camera"
        base = (sdk / "producer/mode/BaseMode.smali").read_text(encoding="utf-8")
        body = base.split('.method public getSurfaceFormatBySurfaceType(', 1)[1].split('.end method', 1)[0]
        self.assertRegex(body, r'const-string p0, "raw12_output"[\s\S]+?const/16 p0, 0x26')
        self.assertIn('const/16 p2, 0x20', body)  # raw16_output -> RAW_SENSOR
        self.assertIn('const/16 v1, 0x25', body)  # RAW10
        self.assertIn('const p0, 0x32315659', body)  # tuning vendor payload, NOT safe image
        self.assertIn(':cond_11\n    :goto_5\n    const/16 p0, 0x23', body)
        usage = base.split('.method public getSurfaceUsageBySurfaceType(', 1)[1].split('.end method', 1)[0]
        dispatch = usage.split(':pswitch_data_0', 2)[2].split('.end packed-switch', 1)[0]
        targets = re.findall(r'^\s+(:pswitch_\w+)\s*$', dispatch, re.MULTILINE)
        for stream, index in {"raw_output": 3, "capture_raw": 11, "capture_yuv": 12,
                              "raw16_output": 13, "capture": 14, "raw12_output": 25}.items():
            with self.subTest(stream=stream):
                self.assertIn(f'const-string p0, "{stream}"', usage)
                self.assertEqual(targets[index], ':pswitch_12')
        self.assertIn(':pswitch_12\n    const-string p0, "surface_key_picture"', usage)
        factory = base.split('.method private getSurfaceWrapper(', 1)[1].split('.end method', 1)[0]
        self.assertIn('move-object/from16 v2, p3', factory)
        self.assertIn('move-object/from16 v7, p4', factory)
        self.assertIn('->getSurfaceUsageBySurfaceType(Ljava/lang/String;)', factory)
        reader = (sdk / "producer/decision/SurfaceDecision.smali").read_text(encoding="utf-8")
        self.assertRegex(reader, r'new-instance v0, Lcom/oplus/ocs/camera/common/surface/SurfaceWrapper;\s+const/4 v7, 0x3')
        kinds = (sdk / "common/util/CameraConstant$SurfaceType.smali").read_text(encoding="utf-8")
        for name, value in {"IMAGE_READER": 3, "IMAGE_READER_RAW": 8,
                            "IMAGE_READER_TUNING_RAW": 10, "IMAGE_READER_TUNING_YUV": 9,
                            "SURFACE_VIEW": 1, "SURFACE_TEXTURE": 2, "PREVIEW_IMAGE": 5,
                            "PREVIEW_FRAME": 6}.items():
            self.assertIn(f'.field public static final {name}:I = 0x{value:x}', kinds)

    def test_compile_and_one_x_helper(self):
        javac = shutil.which("javac")
        java = shutil.which("java")
        self.assertIsNotNone(javac, "javac required")
        self.assertIsNotNone(java, "java required")
        self.assertTrue(ANDROID.is_file(), str(ANDROID))
        self.assertTrue(XPOSED.is_file(), str(XPOSED))
        harness = r'''package com.xpanport.hook;
import java.util.ArrayList;
import java.util.Arrays;
import java.lang.reflect.*;
import android.graphics.ImageFormat;
import android.util.Size;
public class C17HelperCheck {
  private static void require(boolean yes) { if (!yes) throw new AssertionError(); }
  public static class FakeWrapper {
    int format, type = 3;
    String camera = "rear_main", usage = "surface_key_picture";
    public int getFormat() { return format; }
    public int getSurfaceType() { return type; }
    public String getCameraType() { return camera; }
    public String getSurfaceUsage() { return usage; }
    public int hashCode() { throw new AssertionError("identity registry called hashCode"); }
    public boolean equals(Object o) { throw new AssertionError("identity registry called equals"); }
  }
  private static void put(Object owner, String field, Object value) throws Exception {
    Field f = owner.getClass().getDeclaredField(field); f.setAccessible(true); f.set(owner, value);
  }
  private static void checkSurfaceRegression() throws Exception {
    require(ImageFormat.RAW_SENSOR == 32 && ImageFormat.RAW_PRIVATE == 36 && ImageFormat.RAW10 == 37
        && ImageFormat.RAW12 == 38 && ImageFormat.PRIVATE == 34 && ImageFormat.YUV_420_888 == 35
        && ImageFormat.JPEG == 256 && ImageFormat.HEIC == 1212500294);
    Class<?> unsafe = Class.forName("sun.misc.Unsafe");
    Field uf = unsafe.getDeclaredField("theUnsafe"); uf.setAccessible(true); Object u = uf.get(null);
    Method allocate = unsafe.getMethod("allocateInstance", Class.class);
    XpanC17Hook h = (XpanC17Hook) allocate.invoke(u, XpanC17Hook.class);
    Class<?> bindingClass = Class.forName("com.xpanport.hook.XpanC17Hook$Bindings");
    Constructor<?> bc = bindingClass.getDeclaredConstructor(); bc.setAccessible(true); Object bindings = bc.newInstance();
    put(bindings, "cameraType", FakeWrapper.class.getMethod("getCameraType"));
    put(bindings, "usage", FakeWrapper.class.getMethod("getSurfaceUsage"));
    put(bindings, "format", FakeWrapper.class.getMethod("getFormat"));
    put(bindings, "surfaceType", FakeWrapper.class.getMethod("getSurfaceType"));
    put(h, "b", bindings); put(h, "xpanSurfaces", new ArrayList<Object>());
    put(h, "enabled", true); put(h, "gate", new XpanC17Hook.Gate() {
      public boolean enabled() { return true; }
    });
    ThreadLocal<Boolean> build = new ThreadLocal<>(); put(h, "building", build);
    ThreadLocal<Object> stream = new ThreadLocal<>(); put(h, "streamIdentity", stream);
    Size capture = (Size) allocate.invoke(u, Size.class), preview = (Size) allocate.invoke(u, Size.class);
    put(h, "capture", capture); put(h, "preview", preview);
    Class<?> identity = Class.forName("com.xpanport.hook.XpanC17Hook$StreamIdentity");
    Constructor<?> ic = identity.getDeclaredConstructor(String.class, String.class); ic.setAccessible(true);
    Method mark = XpanC17Hook.class.getDeclaredMethod("markSurface", Object.class, identity); mark.setAccessible(true);
    Method target = XpanC17Hook.class.getDeclaredMethod("wrapperTarget", Object.class); target.setAccessible(true);
    Method constructorTarget = XpanC17Hook.class.getDeclaredMethod("sizeTarget", identity, String.class,
        String.class, int.class, int.class); constructorTarget.setAccessible(true);
    FakeWrapper w = new FakeWrapper(); Object captureId = ic.newInstance("capture_yuv", "rear_main");
    mark.invoke(h, w, captureId);
    for (int f : new int[]{32,36,37,38,0,34,4101,0x32315659,0x21,9999}) {
      w.format = f; require(target.invoke(h, w) == null);
      require(constructorTarget.invoke(h, captureId, w.camera, w.usage, f, 3) == null);
    }
    for (int f : new int[]{35,256,1212500294}) {
      w.format = f; require(target.invoke(h, w) == capture);
      require(constructorTarget.invoke(h, captureId, w.camera, w.usage, f, 3) == capture);
    }
    // RAW identity vetoes even a spoofed safe YUV format / identical picture usage / reader type=3.
    w.format = 35;
    for (String raw : new String[]{"capture_raw","raw_output","raw12_output","raw16_output","capture_raw_dol","tuning_data_yuv"}) {
      mark.invoke(h, w, ic.newInstance(raw, "rear_main")); require(target.invoke(h, w) == null);
      require(!XpanC17Hook.safeSurface(raw, "rear_main", "surface_key_picture", 35, 3));
    }
    mark.invoke(h, w, captureId);
    for (int type : new int[]{8,9,10,7,4,-1,0,99}) { w.type = type; require(target.invoke(h, w) == null); }
    w.type = 3; build.set(false); require(target.invoke(h, w) == null); build.remove();
    build.set(true); stream.set(captureId); require(target.invoke(h, w) == capture);
    w.format = 32; require(target.invoke(h, w) == null); w.format = 35;
    stream.set(ic.newInstance("capture_raw", "rear_main")); require(target.invoke(h, w) == null);
    build.remove(); stream.remove();
    FakeWrapper untracked = new FakeWrapper(); untracked.format = 35; require(target.invoke(h, untracked) == null);
    w.usage = "surface_key_preview"; mark.invoke(h, w, ic.newInstance("preview", "rear_main"));
    require(target.invoke(h, w) == preview); w.format = 34; require(target.invoke(h, w) == null);
    // Constructor and getter/setter share this exact safety predicate; unknown kinds/formats fail closed.
    require(XpanC17Hook.safeSurface("capture_yuv", "rear_main", "surface_key_picture", 35, 3));
    require(!XpanC17Hook.safeSurface("capture_yuv", "rear_main", "surface_key_picture", 32, 3));
    require(!XpanC17Hook.safeSurface(null, "rear_main", "surface_key_picture", 35, 3));
    require(!XpanC17Hook.safeSurface("capture_yuv", "rear_wide", "surface_key_picture", 35, 3));
  }
  public static void main(String[] args) throws Exception {
    ArrayList<Float> input = new ArrayList<>(Arrays.asList(0.6f, 1.0f, 2.0f, 1.0f));
    ArrayList<Float> only = XpanC17Hook.oneX(input);
    require(only != input && only.size() == 1 && only.get(0) == 1.0f && input.size() == 4);
    only.add(9.0f); require(input.size() == 4);
    require(XpanC17Hook.oneX(new ArrayList<Float>()) == null);
    require(XpanC17Hook.oneX(new ArrayList<>(Arrays.asList(0.6f, 2.0f))) == null);
    require(XpanC17Hook.oneX(Arrays.asList(1.0f)) == null);
    require(XpanC17Hook.oneX(new ArrayList<>(Arrays.asList(1, 2))) == null);
    require(XpanC17Hook.oneX(new ArrayList<>(Arrays.asList(1.0f, null))) == null);
    require(XpanC17Hook.oneX(null) == null);
    require(!XpanC17Hook.install(C17HelperCheck.class.getClassLoader(), "7.012.64", null).installed);
    require(!XpanC17Hook.install(null, "7.013.51", null).installed);
    checkSurfaceRegression();
    System.out.println("C17 isolated compile/helper and RAW surface regression checks passed");
  }
}'''
        with tempfile.TemporaryDirectory(prefix="c17-helper-", dir=ROOT / "_tmp") as folder:
            folder = Path(folder)
            test_java = folder / "C17HelperCheck.java"
            test_java.write_text(harness, encoding="utf-8")
            classes = folder / "classes"
            classes.mkdir()
            classpath = os.pathsep.join(map(str, (ANDROID, XPOSED)))
            subprocess.run([javac, "-encoding", "UTF-8", "-source", "8", "-target", "8",
                            "-bootclasspath", str(ANDROID), "-classpath", classpath,
                            "-d", str(classes), str(HOOK), str(test_java)], check=True, cwd=ROOT)
            subprocess.run([java, "-cp", os.pathsep.join(map(str, (classes, ANDROID, XPOSED))),
                            "com.xpanport.hook.C17HelperCheck"], check=True, cwd=ROOT)
    def test_gate_and_nested_hook_frames(self):
        # Exercise the production callbacks, not a Python model. API jars are Stub! at runtime,
        # so the temporary Xposed bridge records callbacks and supplies per-call extras/results.
        stubs = {
            "de/robv/android/xposed/XC_MethodHook.java": r'''package de.robv.android.xposed;
import java.lang.reflect.Member;
import java.util.HashMap;
public abstract class XC_MethodHook {
  protected void beforeHookedMethod(MethodHookParam p) throws Throwable {}
  protected void afterHookedMethod(MethodHookParam p) throws Throwable {}
  public void enter(MethodHookParam p) throws Throwable { beforeHookedMethod(p); }
  public void exit(MethodHookParam p) throws Throwable { afterHookedMethod(p); }
  public class Unhook {
    public void unhook() { if (XposedBridge.failUnhook) throw new IllegalStateException("unhook"); }
  }
  public static class MethodHookParam {
    public Object thisObject, result;
    public Object[] args;
    public Member method;
    public Throwable error;
    public int mutations;
    private final HashMap<String, Object> extras = new HashMap<>();
    public void setObjectExtra(String key, Object value) { extras.put(key, value); }
    public Object getObjectExtra(String key) { return extras.get(key); }
    public Object getResult() { return result; }
    public void setResult(Object value) { result = value; error = null; mutations++; }
    public boolean hasThrowable() { return error != null; }
    public Throwable getThrowable() { return error; }
  }
}''',
            "de/robv/android/xposed/XposedBridge.java": r'''package de.robv.android.xposed;
import java.lang.reflect.Member;
import java.util.LinkedHashMap;
public final class XposedBridge {
  public static final LinkedHashMap<String, XC_MethodHook> callbacks = new LinkedHashMap<>();
  public static boolean failUnhook, failLog;
  public static void log(String value) { if (failLog) throw new IllegalStateException("log"); }
  public static XC_MethodHook.Unhook hookMethod(Member m, XC_MethodHook cb) {
    callbacks.put(m.getName(), cb); return cb.new Unhook();
  }
}''',
            "android/util/Size.java": r'''package android.util;
public final class Size {
  public final int width, height;
  public Size(int w, int h) { width = w; height = h; }
}''',
            "android/util/Pair.java": r'''package android.util;
public class Pair<F,S> {
  public final F first; public final S second;
  public Pair(F f, S s) { first = f; second = s; }
  public static <F,S> Pair<F,S> create(F f, S s) { return new Pair<>(f,s); }
}''',
        }
        harness = r'''package com.xpanport.hook;
import java.lang.reflect.*;
import java.util.*;
import android.util.Size;
import android.util.Pair;
import de.robv.android.xposed.*;
import de.robv.android.xposed.XC_MethodHook.MethodHookParam;
public class C17GateCheck {
  static final String FEATURE = "com.oplus.camera.feature.xpan";
  static void require(boolean ok, String message) { if (!ok) throw new AssertionError(message); }
  public static class Toggle implements XpanC17Hook.Gate {
    boolean on = true, fail;
    int remaining = -1;
    public boolean enabled() throws Throwable {
      if (fail) throw new Exception("gate failure");
      if (remaining == 0) return false;
      if (remaining > 0) remaining--;
      return on;
    }
  }
  public static class Callbacks implements XpanC17Hook.Callbacks {
    int before, after;
    String actual;
    Throwable original;
    boolean failBefore;
    public Object beforeModeChange(Object manager, String old, String requested) throws Throwable {
      before++; if (failBefore) throw new Exception("watermark failure"); return this;
    }
    public void afterModeChange(Object manager, String old, String requested, String actual,
        Object token, Throwable original) {
      after++; this.actual = actual; this.original = original;
    }
  }
  public static class Mode { public String U0 = "photo"; }
  public static class Running { public Mode a = new Mode(); }
  public static class Manager { public Running a = new Running(); }
  public static class Configure { public String W0 = "xpan"; }
  public static class Model { public Configure j = new Configure(); }
  public static class FakeMode { public int format(String s, String c, String d) { return 35; } }
  public static class FakeWrapper {
    public String camera() { return "rear_main"; }
    public String usage() { return "surface_key_picture"; }
    public int format() { return 35; }
    public int type() { return 3; }
    public int hashCode() { throw new AssertionError("hashCode"); }
    public boolean equals(Object o) { throw new AssertionError("equals"); }
  }
  public static class Targets {
    public void switchMode() {} public void commitRefresh() {} public void generate() {}
    public void featureList() {} public void factory() {} public void viewCreate() {}
    public void setContainerState() {} public void build() {} public void surfaceFactory() {}
    public void surfaceSize() {} public void setHal() {} public void setApp() {}
    public void getHal() {} public void zoomInit() {} public void zoomSubclassInit() {}
    public void zoomUpdate() {} public void zoomRemember() {} public void zoomReal() {}
    public void zoomRead() {} public void takePicture() {}
  }
  static Object get(Object owner, String name) throws Exception {
    Field f = owner.getClass().getDeclaredField(name); f.setAccessible(true); return f.get(owner);
  }
  static void put(Object owner, String name, Object value) throws Exception {
    Field f = owner.getClass().getDeclaredField(name); f.setAccessible(true); f.set(owner, value);
  }
  static Object call(Object owner, String name) throws Exception {
    Method m = owner.getClass().getDeclaredMethod(name); m.setAccessible(true); return m.invoke(owner);
  }
  static XpanC17Hook create(Toggle gate, Callbacks cb) throws Exception {
    Class<?> bc = Class.forName("com.xpanport.hook.XpanC17Hook$Bindings");
    Constructor<?> ctor = bc.getDeclaredConstructor(); ctor.setAccessible(true); Object b = ctor.newInstance();
    for (Field f : bc.getDeclaredFields()) {
      if (f.getType() == Method.class) {
        f.setAccessible(true);
        try { f.set(b, Targets.class.getMethod(f.getName())); } catch (NoSuchMethodException ignored) {}
      }
    }
    put(b, "wrapper", FakeWrapper.class); put(b, "xpanMode", FakeMode.class);
    put(b, "presenter", Object.class);
    put(b, "wrapperConstructor", FakeWrapper.class.getConstructor());
    put(b, "running", Manager.class.getField("a")); put(b, "runningMode", Running.class.getField("a"));
    put(b, "modeName", Mode.class.getField("U0"));
    put(b, "zoomConfigure", Model.class.getField("j")); put(b, "zoomMode", Configure.class.getField("W0"));
    put(b, "cameraType", FakeWrapper.class.getMethod("camera"));
    put(b, "usage", FakeWrapper.class.getMethod("usage"));
    put(b, "format", FakeWrapper.class.getMethod("format"));
    put(b, "surfaceType", FakeWrapper.class.getMethod("type"));
    put(b, "formatByStream", FakeMode.class.getMethod("format", String.class, String.class, String.class));
    Constructor<?> hc = XpanC17Hook.class.getDeclaredConstructor(bc, XpanC17Hook.Callbacks.class, XpanC17Hook.Gate.class);
    hc.setAccessible(true); XpanC17Hook h = (XpanC17Hook) hc.newInstance(b, cb, gate);
    XposedBridge.callbacks.clear(); call(h, "register"); put(h, "enabled", true); return h;
  }
  static MethodHookParam frame(Object owner, Object... args) {
    MethodHookParam p = new MethodHookParam(); p.thisObject = owner; p.args = args; return p;
  }
  static XC_MethodHook hook(String name) { return XposedBridge.callbacks.get(name); }
  static ThreadLocal<?> local(XpanC17Hook h, String name) throws Exception { return (ThreadLocal<?>) get(h, name); }
  static boolean status(XpanC17Hook h) throws Exception {
    Constructor<?> c = XpanC17Hook.RuntimeStatus.class.getDeclaredConstructor(XpanC17Hook.class);
    c.setAccessible(true); return ((XpanC17Hook.RuntimeStatus) c.newInstance(h)).enabled;
  }
  static MethodHookParam scopeFrame(String name, boolean outer) {
    if (name.equals("generate")) return frame(null, null, outer ? "xpan" : "photo", 0, outer ? 7 : 8);
    if (name.equals("build")) return frame(outer ? new FakeMode() : new Object());
    return frame(null, null, 3, outer ? "capture_yuv" : "preview", "rear_main");
  }
  static void checkScopes(XpanC17Hook h, Toggle gate) throws Throwable {
    String[] names = {"generate", "build", "surfaceFactory"};
    String[] locals = {"generation", "building", "streamIdentity"};
    for (int i = 0; i < names.length; i++) {
      XC_MethodHook cb = hook(names[i]); ThreadLocal<?> tl = local(h, locals[i]);
      MethodHookParam outer = scopeFrame(names[i], true); gate.on = true; cb.enter(outer);
      Object saved = tl.get(); require(saved != null, names[i] + " outer entered");
      MethodHookParam skipped = scopeFrame(names[i], false); gate.on = false; cb.enter(skipped);
      // A skipped inner after must not remove/restore the active outer, whether gate stays off or returns.
      cb.exit(skipped); require(tl.get() == saved, names[i] + " skipped after gateoff");
      MethodHookParam skippedAgain = scopeFrame(names[i], false); cb.enter(skippedAgain);
      gate.on = true; cb.exit(skippedAgain); require(tl.get() == saved, names[i] + " skipped after gateon");
      MethodHookParam inner = scopeFrame(names[i], false); cb.enter(inner);
      Object innerValue = tl.get(); require(innerValue != null && innerValue != saved, names[i] + " nested entered");
      gate.on = false; inner.error = new Exception("original"); cb.exit(inner);
      require(tl.get() == saved, names[i] + " finally restores after revoke/throwable");
      cb.exit(outer); require(tl.get() == null, names[i] + " outer cleanup while gateoff");
      gate.on = true; MethodHookParam throwing = scopeFrame(names[i], true); cb.enter(throwing);
      gate.fail = true; XposedBridge.failLog = true; cb.exit(throwing);
      require(tl.get() == null, names[i] + " gate and diagnostic failure still restore");
      gate.fail = false; XposedBridge.failLog = false;
    }
    // Revocation before normal build/factory return must not unmark or mark wrappers.
    FakeWrapper w = new FakeWrapper(); Class<?> id = Class.forName("com.xpanport.hook.XpanC17Hook$StreamIdentity");
    Constructor<?> ic = id.getDeclaredConstructor(String.class, String.class); ic.setAccessible(true);
    Method mark = XpanC17Hook.class.getDeclaredMethod("markSurface", Object.class, id); mark.setAccessible(true);
    Object identity = ic.newInstance("capture_yuv", "rear_main"); gate.on = true; mark.invoke(h, w, identity);
    List<?> records = (List<?>) get(h, "xpanSurfaces"); require(records.size() == 1, "surface precondition");
    MethodHookParam build = scopeFrame("build", false); hook("build").enter(build);
    build.result = new LinkedList<>(Arrays.asList(w)); gate.on = false; hook("build").exit(build);
    require(records.size() == 1, "gateoff build cannot unmark");
    gate.on = true; MethodHookParam surface = scopeFrame("surfaceFactory", true); hook("surfaceFactory").enter(surface);
    surface.result = new FakeWrapper(); gate.on = false; hook("surfaceFactory").exit(surface);
    require(records.size() == 1 && local(h, "streamIdentity").get() == null, "gateoff factory cannot mark");
  }
  static void checkMutations(XpanC17Hook h, Toggle gate) throws Throwable {
    Model model = new Model(); Size original = new Size(1, 2);
    Object[] zoom = new Object[20]; zoom[4] = new ArrayList<>(Arrays.asList(0.6f, 1.0f, 2.0f));
    zoom[5] = new ArrayList<>(Arrays.asList(1.0f, 2.0f)); zoom[19] = "xpan";
    MethodHookParam lists = frame(null, zoom);
    MethodHookParam feature = frame(null, 7); feature.result = new LinkedHashSet<>(Arrays.asList("native"));
    MethodHookParam factory = frame(null, 0, null, null, FEATURE, "photo");
    MethodHookParam constructor = frame(new FakeWrapper(), "rear_main", "surface_key_picture", original, original, 35, 3);
    MethodHookParam size = frame(new FakeMode(), null, "capture_yuv", "rear_main", "rear_main", 3);
    size.result = new ArrayList<>(Arrays.asList(Pair.create(original, original)));
    MethodHookParam setter = frame(new FakeWrapper(), original);
    MethodHookParam getter = frame(setter.thisObject); getter.result = original;
    MethodHookParam update = frame(model, 2.0f), remember = frame(model, 2.0f), real = frame(model, 2.0f);
    MethodHookParam read = frame(model); read.result = 2.0f;
    MethodHookParam generation = scopeFrame("generate", true), build = scopeFrame("build", true);
    MethodHookParam surface = scopeFrame("surfaceFactory", true);
    gate.on = true; hook("generate").enter(generation); hook("build").enter(build); hook("surfaceFactory").enter(surface);
    String[] names = {"featureList", "factory", "com.xpanport.hook.C17GateCheck$FakeWrapper", "surfaceSize",
        "setHal", "setApp", "getHal", "zoomInit", "zoomSubclassInit", "zoomUpdate", "zoomRemember", "zoomReal", "zoomRead"};
    MethodHookParam[] frames = {feature, factory, constructor, size, setter, setter, getter, lists, lists, update, remember, real, read};
    boolean[] after = {true, false, false, true, false, false, true, false, false, false, false, false, true};
    for (int i = 0; i < names.length; i++) {
      MethodHookParam p = frames[i]; Object[] args = p.args.clone(); Object result = p.result;
      gate.on = false;
      if (after[i]) hook(names[i]).exit(p); else hook(names[i]).enter(p);
      require(p.mutations == 0 && p.result == result && Arrays.equals(p.args, args), names[i] + " gateoff unchanged");
      gate.on = true; gate.remaining = 1; // Entry approved, final write check revoked.
      if (after[i]) hook(names[i]).exit(p); else hook(names[i]).enter(p);
      require(p.mutations == 0 && p.result == result && Arrays.equals(p.args, args), names[i] + " revoked before mutation");
      gate.remaining = -1;
    }
    hook("zoomUpdate").enter(update); require(update.args[0].equals(1.0f), "gateon zoom positive control");
    hook("featureList").exit(feature); require(((Set<?>) feature.result).contains(FEATURE), "gateon feature positive control");
    hook("surfaceSize").exit(size); require(size.result != null && size.mutations == 1, "gateon surface positive control");
    gate.on = false; hook("surfaceFactory").exit(surface); hook("build").exit(build); hook("generate").exit(generation);
  }
  static void checkSwitch(XpanC17Hook h, Toggle gate, Callbacks cb) throws Throwable {
    Manager manager = new Manager(); MethodHookParam p = frame(manager, "xpan", false, false);
    gate.on = true; hook("switchMode").enter(p); require(cb.before == 1, "before notified");
    manager.a.a.U0 = "xpan"; gate.on = false; hook("switchMode").exit(p);
    require(cb.after == 1 && "xpan".equals(cb.actual), "notified after reconciles actual while gateoff");
    require(get(h, "committedMode") == null, "no new witness state while gateoff");
    MethodHookParam skipped = frame(manager, "photo", false, false); hook("switchMode").enter(skipped);
    manager.a.a.U0 = "photo"; gate.on = true; hook("switchMode").exit(skipped);
    require(cb.before == 1 && cb.after == 1, "gateoff before cannot start callback after enable");
    cb.failBefore = true; MethodHookParam failed = frame(manager, "xpan", false, false);
    hook("switchMode").enter(failed); manager.a.a.U0 = "xpan"; gate.on = false;
    failed.error = new Exception("original switch failure"); hook("switchMode").exit(failed);
    require(cb.before == 2 && cb.after == 2 && cb.original == failed.error, "failed before still reconciles actual/original");
    cb.failBefore = false; gate.on = true;
    MethodHookParam unreadable = frame(manager, "photo", false, false); hook("switchMode").enter(unreadable);
    unreadable.thisObject = new Object(); gate.on = false; XposedBridge.failLog = true;
    hook("switchMode").exit(unreadable); XposedBridge.failLog = false;
    require(cb.before == 3 && cb.after == 3 && cb.actual == null && cb.original == null,
        "committed read failure still compensates without inventing original throwable");
  }
  public static void main(String[] args) throws Throwable {
    Toggle gate = new Toggle(); Callbacks callbacks = new Callbacks(); XpanC17Hook h = create(gate, callbacks);
    require(status(h), "runtime enabled with active gate"); gate.on = false; require(!status(h), "runtime gateoff false");
    // Gateoff/exception callbacks must not inspect malformed args or alter original failures/results.
    for (boolean fail : new boolean[]{false, true}) {
      gate.fail = fail; XposedBridge.failLog = fail;
      for (Map.Entry<String, XC_MethodHook> e : XposedBridge.callbacks.entrySet()) {
        MethodHookParam p = frame(null); p.result = new Object(); p.error = new Exception("camera");
        Object original = p.result; Throwable error = p.error;
        e.getValue().enter(p); e.getValue().exit(p);
        require(p.result == original && p.error == error && p.mutations == 0, e.getKey() + " failclosed");
      }
      require(!status(h), "runtime failclosed");
    }
    gate.fail = false; XposedBridge.failLog = false;
    require(((java.util.concurrent.atomic.AtomicInteger) get(h, "failures")).get() > 0, "gate exceptions diagnosed");
    checkScopes(h, gate); checkMutations(h, gate); checkSwitch(h, gate, callbacks);
    gate.on = true; XposedBridge.failUnhook = true;
    require(((Integer) call(h, "rollback")) > 0, "residual rollback test");
    require(!status(h), "residual hooks runtime disabled");
    MethodHookParam p = frame(new Model(), 2.0f); hook("zoomUpdate").enter(p);
    require(p.args[0].equals(2.0f), "residual hooks dormant");
    require(!XpanC17Hook.install(C17GateCheck.class.getClassLoader(), "7.013.51", callbacks, null).installed,
        "4arg null gate refused");
    System.out.println("C17 gate, nested frames, mutation revocation and reconciliation checks passed");
  }
}'''
        with tempfile.TemporaryDirectory(prefix="c17-gate-", dir=ROOT / "_tmp") as folder:
            folder = Path(folder)
            sources = []
            for name, content in {**stubs, "com/xpanport/hook/C17GateCheck.java": harness}.items():
                target = folder / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                sources.append(str(target))
            classes = folder / "classes"
            classes.mkdir()
            subprocess.run([shutil.which("javac"), "-encoding", "UTF-8", "-source", "8", "-target", "8",
                            "-classpath", str(ANDROID), "-d", str(classes), str(HOOK), *sources],
                           check=True, cwd=ROOT)
            subprocess.run([shutil.which("java"), "-cp", os.pathsep.join(map(str, (classes, ANDROID))),
                            "com.xpanport.hook.C17GateCheck"], check=True, cwd=ROOT)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decoded-root", type=Path, default=DECODED)
    parser.add_argument("--android-jar", type=Path, default=ANDROID)
    options = parser.parse_args()
    DECODED, ANDROID = options.decoded_root.resolve(), options.android_jar.resolve()
    unittest.main(argv=[__file__], verbosity=2)
