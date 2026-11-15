"""Offline C17 XPAN gain drawing isolation: real smali ABI and original-flow fake JNI.

No camera/device access, signing, packaging, HEIF rewrite, or native-pointer inspection.
PARAMS_APPLIED means original drawing arguments changed, not validated device pixels.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hook/XpanGainMapFrame.java"
C17 = ROOT / "_tmp/decoded-PJX110_17.0.1.201/camera"
ANDROID = Path(os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME") or
               Path.home() / "AppData/Local/Android/Sdk") / "platforms/android-35/android.jar"
XPOSED = ROOT / "_tmp/api-82.jar"
HOLDER = "Lys/c;"
TAG = "Lcom/oplus/camera/device/CameraRequestTag;"
SIZE = "Landroid/util/Size;"
BITMAP = "Landroid/graphics/Bitmap;"


def body(source, descriptor):
    match = re.search(r"(?m)^\.method [^\n]* " + re.escape(descriptor) + r"\s*$", source)
    if not match:
        raise AssertionError("missing actual method " + descriptor)
    return source[match.end():].split(".end method", 1)[0]


class XpanGainMapFrameTests(unittest.TestCase):
    def test_actual_c17_descriptors_mode_and_original_lifecycle(self):
        helper = (C17 / "smali_classes7/ol/q0.smali").read_text(encoding="utf-8")
        holder = (C17 / "smali_classes8/ys/c.smali").read_text(encoding="utf-8")
        info = (C17 / "smali_classes8/ys/g.smali").read_text(encoding="utf-8")
        tag = (C17 / "smali_classes2/com/oplus/camera/device/CameraRequestTag.smali").read_text(encoding="utf-8")
        model = (C17 / "smali_classes6/zk/c.smali").read_text(encoding="utf-8")
        parameter = (C17 / "smali_classes8/com/oplus/ocs/camera/ipuapi/process/watermark/entity/WatermarkProcessParameter.smali").read_text(encoding="utf-8")
        assembler = (C17 / "smali_classes8/com/oplus/ocs/ipu/watermark/masterwatermark/MasterWatermarkAssembler.smali").read_text(encoding="utf-8")
        wd = "w(" + HOLDER + "Ljava/util/Map;" + TAG + "I" + SIZE + ")" + HOLDER
        ed = "e(" + HOLDER + TAG + SIZE + BITMAP + "Z)" + HOLDER
        for descriptor in (wd, ed, "h(I)" + SIZE):
            self.assertRegex(helper, r"(?m)^\.method public final " + re.escape(descriptor) + r"\s*$")
        for name in ("h", "i", "j", "k", "D", "N"):
            self.assertRegex(holder, r"(?m)^\.field public " + name + r":I\s*$")
        for name in ("c", "m", "n", "z", "A"):
            self.assertRegex(holder, r"(?m)^\.field public " + name + r":\[I\s*$")
        for name in ("v", "w", "x"):
            self.assertIn(".field public " + name + ":[" + BITMAP, holder)
        for name, kind in (("s", "Lys/g;"), ("q", "J"), ("r", "J"), ("o", BITMAP), ("p", BITMAP),
                           ("E", "Ljava/lang/String;"), ("F", "Ljava/lang/String;")):
            self.assertIn(".field public " + name + ":" + kind, holder)
        self.assertIn(".field public p:" + SIZE, info)
        self.assertIn(".field public k:" + SIZE, info)
        self.assertIn(".field public y:I", info)
        self.assertIn(".field public Z:I", info)
        self.assertIn(".field public z0:Lorg/json/JSONObject;", info)
        reader = (C17 / "smali_classes8/com/oplus/ocs/ipu/watermark/masterwatermark/MasterWatermarkJsonReader.smali").read_text(encoding="utf-8")
        creator = (C17 / "smali_classes8/com/oplus/ocs/ipu/watermark/masterwatermark/MasterWatermarkCreator.smali").read_text(encoding="utf-8")
        self.assertRegex(creator, r"const/16 v4, 0x9[\s\S]*?iput v4, v3, Lys/c;->N:I")
        self.assertIn('"styleId"', reader)
        self.assertIn(".field public e:Ljava/lang/String;", info)
        self.assertIn(".method public final clone()Ljava/lang/Object;", info)
        self.assertIn(".field public C:Ljava/lang/String;", tag)
        self.assertIn(".field public q:Ljava/lang/String;", tag)
        conversion = body(parameter, "covertToWatermarkYuvInfo(Landroid/content/Context;Z)Lys/g;")
        self.assertRegex(conversion, r"mModeName:Ljava/lang/String;[\s\S]*?iput-object v1, v0, Lys/g;->e:Ljava/lang/String;")
        self.assertIn('const-string v2, "highPixel"', helper)
        self.assertIn("CameraRequestTag;->C:Ljava/lang/String;", helper)
        # q is LUT, not mode. e(q) is the exact classifier used by original e.
        self.assertIn('const-string v0, "polaroid_sdr.mslut"', body(model, "e(Ljava/lang/String;)Z"))
        scaling = body(helper, ed)
        self.assertIn("new-instance v1, Lys/c;", scaling)
        self.assertIn("CameraRequestTag;->q:Ljava/lang/String;", scaling)
        self.assertIn("Lzk/c;->e(Ljava/lang/String;)Z", scaling)
        self.assertIn("Lys/g;->clone()Ljava/lang/Object;", scaling)
        for field in ("w", "x"):
            self.assertRegex(scaling, r"iget-object p5, p1, Lys/c;->" + field + r":\[" + re.escape(BITMAP) +
                             r"[\s\S]*?iput-object p5, v1, Lys/c;->" + field)
        gain = body(helper, wd)
        for key in ("gain_map_width", "gain_map_height", "gain_map", "gain_map_format"):
            self.assertIn('"' + key + '"', gain)
        self.assertIn("check-cast v11, Ljava/lang/String;", gain)
        self.assertIn("const v1, 0x20203859", gain)
        self.assertIn("aget-object v2, v2, v3", gain)  # main v[4] selects nooverlay vs overlay.
        self.assertIn('const-string v3, "#000000"', gain)
        for method in ("hardwareBufferAllocate(III)J", "fillBufferWithColor(JLjava/lang/String;)I",
                       "copyBufferToCoord(JJII)I", "releaseHardwareBuffer(J)I"):
            self.assertIn(method, gain)
            self.assertRegex(assembler, r"(?m)^\.method public static native " + re.escape(method) + r"\s*$")
        self.assertIn(".method public static assemble(" + HOLDER + "ILjava/lang/Object;ZI)V", assembler)
        assembled = body(assembler, "assemble(" + HOLDER + "ILjava/lang/Object;ZI)V")
        self.assertIn("iput-wide v9, v6, Lys/c;->q:J", assembled)
        # 0/4/5 release converted bitmap input; stage3 native source remains borrowed.
        self.assertIn("if-eqz v7, :cond_5", assembled)
        self.assertIn("if-eq v12, v7, :cond_5", assembled)
        self.assertIn("if-ne v11, v7, :cond_6", assembled)
        self.assertIn("Lys/c;->r:J", helper)
        self.assertRegex(helper, r'"image_stage_type"[\s\S]*?check-cast v2, Ljava/lang/String;'
                         r'[\s\S]*?parseInt\(Ljava/lang/String;\)I[\s\S]*?move/from16 v41, v2')
        self.assertIn("move/from16 v9, v41", helper)
        self.assertIn("invoke-virtual/range {v1 .. v6}, Lol/q0;->" + wd, helper)
        crop = body(helper, "h(I)" + SIZE)
        self.assertIn("if-ltz p1, :cond_1", crop)
        self.assertIn("const/4 p0, 0x0", crop)
        # These exact branch tolerances are why preflight refuses small frame margins.
        rounded = body(helper, "z(" + HOLDER + SIZE + "FFZ)V")
        self.assertIn("# -7.0f", rounded)
        self.assertIn("# 7.0f", rounded)

    def test_source_has_no_buffer_ownership_or_container_patch(self):
        source = HOOK.read_text(encoding="utf-8")
        self.assertEqual(source.count("p.args[0] = neutral;"), 1)
        self.assertLess(source.index("Bindings b = new Bindings(loader)"), source.index("h.register(b.w"))
        self.assertLess(source.index("h.register(b.e"), source.index("h.ready = true"))
        self.assertIn("h.rollback()", source)
        self.assertIn("Class.forName(name, false, loader)", source)
        self.assertIn("nativePixelsVerified = false", source)
        self.assertIn("scope.remove()", source)
        self.assertIn('field(tag, "C", String.class)', source)
        self.assertIn('b.aiFrame.invoke(null, s.lut)', source)
        self.assertNotIn('"xpan".equals(s.lut)', source)
        for forbidden in ("setThrowable(", "invokeOriginalMethod", "getDeclaredFields(",
                          "System.load", "java.io.", "java.nio.", "HardwareBuffer", "XdrRatioPatch",
                          "getPixels(", "setColorSpace(", ".recycle(", "new Thread(", "->"):
            # Native ABI names include HardwareBuffer but no Android handle/API is used.
            if forbidden == "HardwareBuffer":
                self.assertNotIn("android.hardware.HardwareBuffer", source)
            else:
                self.assertNotIn(forbidden, source)
        self.assertNotRegex(source, r"\.(?:q|r)\.setLong\(")
        self.assertEqual(source.count("p.setResult(Boolean.TRUE)"), 1)
        self.assertIn("call.owner != f || call.previous != null", source)
        self.assertIn('FRAME_STYLE = "hassel_style_xpan_1"', source)
        self.assertIn('field(info, "y", int.class)', source)
        self.assertIn('field(holder, "N", int.class)', source)
        self.assertIn("size.equals(crop)", source)
        self.assertNotRegex(source, r"(?:allocate|fill|copy|release|cropSize)\.invoke\([^)]*\)".replace("|cropSize", ""))

    def test_java8_real_api_compile_original_flow_and_abi_failures(self):
        javac, java = shutil.which("javac"), shutil.which("java")
        self.assertIsNotNone(javac)
        self.assertIsNotNone(java)
        self.assertTrue(ANDROID.is_file(), str(ANDROID))
        self.assertTrue(XPOSED.is_file(), str(XPOSED))
        with tempfile.TemporaryDirectory(prefix="xpan-gain-frame-", dir=ROOT / "_tmp") as folder:
            folder = Path(folder)
            compiled = folder / "hook-classes"
            compiled.mkdir()
            api = os.pathsep.join(map(str, (ANDROID, XPOSED)))
            subprocess.run([javac, "-encoding", "UTF-8", "-source", "8", "-target", "8",
                            "-bootclasspath", str(ANDROID), "-classpath", api,
                            "-d", str(compiled), str(HOOK)], cwd=ROOT, check=True,
                           capture_output=True, text=True, encoding="utf-8", errors="backslashreplace")
            defects = ("", "holder_type", "holder_private_ctor", "static_field", "private_field",
                       "final_field", "wrong_info_size", "wrong_json", "wrong_watermark_mode", "wrong_frame_type", "wrong_mode", "w_static", "w_return",
                       "e_missing", "e_return", "clone_missing", "clone_covariant", "classifier_instance",
                       "crop_missing", "assemble_return", "allocate_return", "fill_return", "copy_instance",
                       "release_not_native")
            for defect in defects:
                with self.subTest(defect=defect or "valid"):
                    case = folder / (defect or "valid")
                    case.mkdir()
                    sources = fake_sources(defect)
                    paths = []
                    for relative, content in sources.items():
                        path = case / relative
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(content, encoding="utf-8")
                        paths.append(str(path))
                    runtime = case / "classes"
                    runtime.mkdir()
                    subprocess.run([javac, "-encoding", "UTF-8", "-source", "8", "-target", "8",
                                    "-classpath", str(compiled), "-d", str(runtime), *paths],
                                   cwd=ROOT, check=True, capture_output=True, text=True,
                                   encoding="utf-8", errors="backslashreplace")
                    classpath = os.pathsep.join(map(str, (runtime, compiled)))
                    scenarios = ("abi_failure",) if defect else (
                        "plan", "pixels", "smaller_pair", "standard_frame", "standard_guards", "standard_scope",
                        "diagnostic_keys", "diagnostic_original", "guards", "gate", "stale", "nested",
                        "original_failures", "rollback", "rollback_failure", "null_handle", "classifier_rollback")
                    for scenario in scenarios:
                        with self.subTest(scenario=scenario):
                            result = subprocess.run([java, "-Xmx256m", "-cp", classpath,
                                                     "com.xpanport.hook.GainFrameCheck", scenario],
                                                    cwd=ROOT, check=False, capture_output=True, text=True,
                                                    encoding="utf-8", errors="backslashreplace")
                            self.assertEqual(result.returncode, 0, scenario + "\n" + result.stdout + result.stderr)
                            self.assertIn("PASS", result.stdout)
                            print(result.stdout.strip())


def fake_sources(defect):
    holder = """package ys; public final class c {
  static { com.xpanport.hook.GainFrameCheck.initializations++; }
  public c() {}
  public int h,i,j,k,D,N; public int[] c,m,n,z,A;
  public android.graphics.Bitmap[] v,w,x; public g s;
  public long q,r; public android.graphics.Bitmap o,p;
  public String E,F; public byte[] Q,R,S,T,U,V;
} """
    info = """package ys; public final class g implements Cloneable {
  static { com.xpanport.hook.GainFrameCheck.initializations++; }
  public android.util.Size p,k; public String e; public float M;
  public org.json.JSONObject z0; public int y,Z;
  public final Object clone() {
    if (com.xpanport.hook.GainFrameCheck.throwClone) throw com.xpanport.hook.GainFrameCheck.HOSTILE;
    if (com.xpanport.hook.GainFrameCheck.aliasClone) return this;
    try { Object c = super.clone();
      if (com.xpanport.hook.GainFrameCheck.brokenCloneSize)
        com.xpanport.hook.GainFrameCheck.put(c,"p",new android.util.Size(8192,4176));
      return c;
    } catch(Exception t) { throw new AssertionError(t); }
  }
} """
    tag = """package com.oplus.camera.device; public final class CameraRequestTag {
  static { com.xpanport.hook.GainFrameCheck.initializations++; }
  public String C,q; public int z0;
} """
    helper = """package ol; import android.util.Size; import android.graphics.Bitmap;
import com.oplus.camera.device.CameraRequestTag;
public final class q0 {
  static { com.xpanport.hook.GainFrameCheck.initializations++; }
  public int g = -1; public java.util.ArrayList h;
  public final ys.c w(ys.c main,java.util.Map raw,CameraRequestTag tag,int stage,Size size) {
    return (ys.c) com.xpanport.hook.GainFrameCheck.originalW(this,main,raw,tag,stage,size);
  }
  public final ys.c e(ys.c main,CameraRequestTag tag,Size size,Bitmap bitmap,boolean gain) {
    return (ys.c) com.xpanport.hook.GainFrameCheck.originalE(this,main,tag,size,bitmap,gain);
  }
  public final Size h(int index) {
    if(h == null || index < 0 || index+1 >= h.size()) return null;
    return (Size)h.get(index+1);
  }
} """
    model = """package zk; public final class c {
  static { com.xpanport.hook.GainFrameCheck.initializations++; }
  public static boolean e(String lut) { return com.xpanport.hook.GainFrameCheck.aiFrame; }
} """
    assembler = """package com.oplus.ocs.ipu.watermark.masterwatermark;
public final class MasterWatermarkAssembler {
  static { com.xpanport.hook.GainFrameCheck.initializations++; }
  public static void assemble(ys.c h,int stage,Object raw,boolean bitmap,int format) {
    com.xpanport.hook.GainFrameCheck.originalAssemble(h,stage,raw,format);
  }
  public static native long hardwareBufferAllocate(int w,int h,int format);
  public static native int fillBufferWithColor(long buffer,String color);
  public static native int copyBufferToCoord(long source,long target,int x,int y);
  public static native int releaseHardwareBuffer(long buffer);
} """
    if defect == "holder_type":
        holder = holder.replace("public long q,r;", "public Object q; public long r;")
    elif defect == "holder_private_ctor":
        holder = holder.replace("public c()", "private c()")
    elif defect == "static_field":
        holder = holder.replace("public String E,F;", "public String E; public static String F;")
    elif defect == "private_field":
        holder = holder.replace("public String E,F;", "private String E; public String F;")
    elif defect == "final_field":
        holder = holder.replace("public int[] c,m,n,z,A;", "public final int[] c=null; public int[] m,n,z,A;")
    elif defect == "wrong_info_size":
        info = info.replace("public android.util.Size p,k;", "public Object p; public android.util.Size k;")
    elif defect == "wrong_json":
        info = info.replace("public org.json.JSONObject z0;", "public Object z0;")
    elif defect == "wrong_watermark_mode":
        info = info.replace("public int y,Z;", "public long y; public int Z;")
    elif defect == "wrong_frame_type":
        holder = holder.replace("public int h,i,j,k,D,N;", "public int h,i,j,k,D; public long N;")
    elif defect == "wrong_mode":
        tag = tag.replace("public String C,q;", "public Object C; public String q;")
    elif defect == "w_static":
        helper = helper.replace("public final ys.c w(", "public static ys.c w(").replace("originalW(this,", "originalW(null,")
    elif defect == "w_return":
        helper = helper.replace("public final ys.c w(", "public final Object w(")
    elif defect == "e_missing":
        helper = helper.replace("public final ys.c e(", "public final ys.c otherE(")
    elif defect == "e_return":
        helper = helper.replace("public final ys.c e(", "public final Object e(")
    elif defect == "clone_missing":
        info = info.replace("public final Object clone()", "public final Object otherClone()")
    elif defect == "clone_covariant":
        info = info.replace("public final Object clone()", "public final g clone()").replace("return c;", "return (g)c;")
    elif defect == "classifier_instance":
        model = model.replace("public static boolean e(", "public boolean e(")
    elif defect == "crop_missing":
        helper = helper.replace("public final Size h(", "public final Size otherH(")
    elif defect == "assemble_return":
        assembler = assembler.replace("public static void assemble(", "public static Object assemble(").replace(
            "originalAssemble(h,stage,raw,format);", "originalAssemble(h,stage,raw,format); return null;")
    elif defect == "allocate_return":
        assembler = assembler.replace("native long hardwareBufferAllocate", "native int hardwareBufferAllocate")
    elif defect == "fill_return":
        assembler = assembler.replace("native int fillBufferWithColor", "native long fillBufferWithColor")
    elif defect == "copy_instance":
        assembler = assembler.replace("public static native int copyBufferToCoord", "public native int copyBufferToCoord")
    elif defect == "release_not_native":
        assembler = assembler.replace("public static native int releaseHardwareBuffer(long buffer);",
                                      "public static int releaseHardwareBuffer(long buffer) { return 0; }")
    return {
        "ys/c.java": holder, "ys/g.java": info, "ol/q0.java": helper,
        "zk/c.java": model, "com/oplus/camera/device/CameraRequestTag.java": tag,
        "com/oplus/ocs/ipu/watermark/masterwatermark/MasterWatermarkAssembler.java": assembler,
        "android/util/Size.java": r"""package android.util; public final class Size {
  private final int w,h; public Size(int w,int h) { this.w=w; this.h=h; }
  public int getWidth() { return w; } public int getHeight() { return h; }
  public boolean equals(Object o) { return o instanceof Size && ((Size)o).w==w && ((Size)o).h==h; }
  public int hashCode() { return w*31+h; } public String toString() { return w+"x"+h; }
} """,
        "android/graphics/Bitmap.java": r"""package android.graphics; public final class Bitmap {
  public final int id; public int colorWrites; public boolean recycled;
  public Bitmap(int i) { id=i; }
  public void recycle() { recycled=true; }
  public void setColorSpace(Object c) { colorWrites++; }
} """,
        "org/json/JSONObject.java": r'''package org.json; public final class JSONObject {
  private final java.util.Map<String,Object> values = new java.util.HashMap<String,Object>();
  public JSONObject put(String key,Object value) { values.put(key,value); return this; }
  public Object opt(String key) { return values.get(key); }
} ''',
        "de/robv/android/xposed/XC_MethodHook.java": XC_HOOK,
        "de/robv/android/xposed/XposedBridge.java": BRIDGE,
        "com/xpanport/hook/GainFrameCheck.java": HARNESS,
    }


XC_HOOK = r'''package de.robv.android.xposed;
import java.util.*; import java.lang.reflect.Member;
public abstract class XC_MethodHook {
  protected void beforeHookedMethod(MethodHookParam p) throws Throwable {}
  protected void afterHookedMethod(MethodHookParam p) throws Throwable {}
  public static class MethodHookParam {
    public Object thisObject; public Object[] args; public Member method;
    public Object result; public Throwable throwable; public boolean returnEarly;
    final Map<String,Object> extra = new HashMap<String,Object>();
    public Object getResult() { return result; } public boolean hasThrowable() { return throwable!=null; }
    public Throwable getThrowable() { return throwable; }
    public void setResult(Object o) {
      if(!(method instanceof java.lang.reflect.Method) || !method.getName().equals("e")
            || !method.getDeclaringClass().getName().equals("zk.c") || !(o instanceof Boolean))
        throw new AssertionError("only scoped original scaling predicate may change");
      result=o;throwable=null;returnEarly=true;
    }
    public void setThrowable(Throwable t) { throw new AssertionError("must not replace original exception"); }
    public void setObjectExtra(String k,Object v) { extra.put(k,v); }
    public Object getObjectExtra(String k) { return extra.get(k); }
  }
  public class Unhook {
    final Member method; public Unhook(Member m) { method=m; }
    public void unhook() {
      XposedBridge.unhookOrder.add(method.getName());
      if(XposedBridge.failUnhook) throw new AssertionError("unhook failed");
      XposedBridge.hooks.remove(method);
    }
  }
} '''

BRIDGE = r'''package de.robv.android.xposed;
import java.util.*; import java.lang.reflect.*;
import com.xpanport.hook.GainFrameCheck;
public final class XposedBridge {
  public static int registrations,failAt; public static boolean failUnhook,nullHandle,throwLog;
  public static XC_MethodHook.MethodHookParam current,lastW;
  public static final Map<Member,XC_MethodHook> hooks=new LinkedHashMap<Member,XC_MethodHook>();
  public static final List<String> logs=new ArrayList<String>(),unhookOrder=new ArrayList<String>();
  public static XC_MethodHook.Unhook hookMethod(Member method,XC_MethodHook hook) {
    registrations++;
    XC_MethodHook.MethodHookParam early=new XC_MethodHook.MethodHookParam(); early.method=method;
    try { hook.beforeHookedMethod(early); hook.afterHookedMethod(early); }
    catch(Throwable t) { throw new AssertionError("registration callback escaped",t); }
    if(registrations==failAt) { if(nullHandle)return null; throw new AssertionError("registration failed"); }
    hooks.put(method,hook); return hook.new Unhook(method);
  }
  public static void log(String message) { if(throwLog)throw new AssertionError("logger failed"); logs.add(message); }
  public static Object call(Method method,Object owner,Object... args) throws Throwable {
    XC_MethodHook.MethodHookParam p=new XC_MethodHook.MethodHookParam();
    p.method=method; p.thisObject=owner; p.args=args;
    XC_MethodHook.MethodHookParam old=current; current=p;
    XC_MethodHook hook=hooks.get(method);
    try {
      if(hook!=null)hook.beforeHookedMethod(p);
      try { if(!p.returnEarly) p.result=Modifier.isNative(method.getModifiers()) ? GainFrameCheck.nativeCall(method.getName(),p.args)
                : method.invoke(owner,p.args); }
      catch(InvocationTargetException t) { p.throwable=t.getCause(); }
      catch(Throwable t) { p.throwable=t; }
      if(hook!=null)hook.afterHookedMethod(p);
      if(method.getName().equals("w"))lastW=p;
      if(p.throwable!=null)throw p.throwable;
      return p.result;
    } finally { current=old; }
  }
  public static void residualCallbacks() throws Throwable {
    for(Map.Entry<Member,XC_MethodHook> entry:hooks.entrySet()) {
      XC_MethodHook.MethodHookParam p=new XC_MethodHook.MethodHookParam(); p.method=entry.getKey();
      entry.getValue().beforeHookedMethod(p); entry.getValue().afterHookedMethod(p);
    }
  }
} '''

HARNESS = r'''package com.xpanport.hook;
import java.util.*; import java.lang.reflect.*;
import android.graphics.Bitmap; import android.util.Size;
import de.robv.android.xposed.XposedBridge;
public final class GainFrameCheck {
  public static int initializations;
  public static boolean aiFrame=true,aliasClone,brokenCloneSize,badScaling,originalThrows,throwClone;
  public static Object lastInput,lastGain;
  public static final long SOURCE=0x200000010L,PRIMARY=0x100000010L,OLD_GAIN=0x300000010L;
  static final String ASSEMBLER="com.oplus.ocs.ipu.watermark.masterwatermark.MasterWatermarkAssembler";
  static final Map<Long,Buffer> buffers=new HashMap<Long,Buffer>();
  static final List<String> events=new ArrayList<String>();
  static long next=0x400000010L;
  static int allocateStatus,fillStatus,copyStatus,releaseStatus,allocations,fills,copies,releases;
  static Throwable nativeFailure; static String failNative;
  static Runnable onFill,onScaling; static Object helper; static Method w,e;
  static final RuntimeException ORIGINAL=new RuntimeException("same original exception");
  public static final Error HOSTILE=new Error() { @Override public String toString() { throw new AssertionError("Throwable.toString must not be called"); } };
  static final class Buffer {
    final int w,h,format; final boolean borrowed; final byte[] pixels; boolean released;
    Buffer(int w,int h,int format,boolean borrowed,boolean samples) {
      this.w=w;this.h=h;this.format=format;this.borrowed=borrowed;
      pixels=samples?new byte[w*h]:null;
    }
  }
  static final class TestGate implements XpanGainMapFrame.Gate {
    boolean on=true,throwsGate; int calls,revokeAt; Runnable action;
    public boolean enabled() {
      calls++; if(action!=null)action.run();
      if(throwsGate)throw new AssertionError("gate failed"); return on && calls!=revokeAt;
    }
  }
  public static void check(boolean v,String why) { if(!v)throw new AssertionError(why); }
  public static Object get(Object o,String f) throws Exception { return o.getClass().getField(f).get(o); }
  public static void put(Object o,String f,Object v) throws Exception { o.getClass().getField(f).set(o,v); }
  static int integer(Object o,String f) throws Exception { return ((Integer)get(o,f)).intValue(); }
  static long pointer(Object o,String f) throws Exception { return ((Long)get(o,f)).longValue(); }
  static Object fresh(String cls) throws Exception { return Class.forName(cls).getConstructor().newInstance(); }
  static Method method(String cls,String name,Class<?>... types) throws Exception { return Class.forName(cls).getMethod(name,types); }
  public static Object scaledE(Object main,Object tag,Size raw) {
    try {
      Object out=fresh("ys.c"),info=get(main,"s"); Size size=(Size)get(info,"p");
      Object copy=info.getClass().getMethod("clone").invoke(info); put(out,"s",copy);
      int pw=size.getWidth(),ph=size.getHeight(); float sx=(float)pw/raw.getWidth(),sy=(float)ph/raw.getHeight();
      float fw=integer(main,"h")/sx,fh=integer(main,"i")/sy;
      int cw=Math.abs(fw-raw.getWidth())<=7?raw.getWidth():(int)fw;
      int ch=Math.abs(fh-raw.getHeight())<=7?raw.getHeight():(int)fh;
      put(out,"h",cw); put(out,"i",badScaling?ch+4:ch);
      put(out,"j",(int)(integer(main,"j")/sx));put(out,"k",(int)(integer(main,"k")/sy));
      put(copy,"p",new Size(even((int)(pw/sx)),even((int)(ph/sy))));
      put(copy,"M",((Float)get(info,"M")).floatValue()/sx);
      put(out,"c",get(main,"c"));
      Bitmap[] v=(Bitmap[])get(main,"v"),up=(Bitmap[])get(main,"w"),down=(Bitmap[])get(main,"x");
      Bitmap[] scaled=new Bitmap[v.length]; put(out,"v",scaled);put(out,"w",up);put(out,"x",down);
      for(String f:new String[]{"m","n","z","A"})put(out,f,new int[v.length]);
      for(int i=0;i<v.length;i++) {
        if(v[i]!=null) { v[i].setColorSpace(null); scaled[i]=new Bitmap(v[i].id+100); }
        if(up[i]!=null)up[i]=scaled[i]; if(down[i]!=null)down[i]=scaled[i];
      }
      for(String f:new String[]{"D","E","F","Q","R","S","T","U","V"})put(out,f,get(main,f));
      return out;
    } catch(Exception t) { throw new AssertionError(t); }
  }
  public static Object originalE(Object owner,Object main,Object tag,Size raw,Bitmap bitmap,boolean gain) {
    try {
      if(onScaling!=null)onScaling.run();
      String lut=(String)get(tag,"q");
      boolean branch=((Boolean)XposedBridge.call(method("zk.c","e",String.class),null,lut)).booleanValue();
      if(!branch) { Object out=fresh("ys.c");return out; }
      return scaledE(main,tag,raw);
    } catch(Throwable t) { sneaky(t); return null; }
  }
  static int even(int n) { return (n&1)==0?n:n+1; }
  public static Object originalW(Object helper,Object main,Map raw,Object tag,int stage,Size size) {
    try {
      lastInput=main;
      if(originalThrows)throw ORIGINAL;
      int sw=Integer.parseInt((String)raw.get("gain_map_width")),sh=Integer.parseInt((String)raw.get("gain_map_height"));
      int format=raw.get("gain_map_format") instanceof String?Integer.parseInt((String)raw.get("gain_map_format")):XpanGainMapFrame.Y8;
      Object gain=XposedBridge.call(e,helper,main,tag,new Size(sw,sh),null,true);lastGain=gain;
      if(((Bitmap[])get(main,"v"))[4]!=null) {
        XposedBridge.call(method(ASSEMBLER,"assemble",Class.forName("ys.c"),int.class,Object.class,boolean.class,int.class),
                           null,gain,stage,raw.get("gain_map"),false,format);
      } else {
        int cw=integer(gain,"h"),ch=integer(gain,"i"),aw=(cw+3)&~3,ah=(ch+3)&~3;
        long out=((Long)callNative("hardwareBufferAllocate",new Class<?>[]{int.class,int.class,int.class},aw,ah,format)).longValue();
        callNative("fillBufferWithColor",new Class<?>[]{long.class,String.class},out,"#000000");
        long source=Long.parseLong((String)raw.get("gain_map"));
        callNative("copyBufferToCoord",new Class<?>[]{long.class,long.class,int.class,int.class},source,out,integer(gain,"j"),integer(gain,"k"));
        put(gain,"q",out);
      }
      return gain;
    } catch(Throwable t) { sneaky(t); return null; }
  }
  public static void originalAssemble(Object gain,int stage,Object raw,int format) {
    try {
      long out=((Long)callNative("hardwareBufferAllocate",new Class<?>[]{int.class,int.class,int.class},integer(gain,"h"),integer(gain,"i"),format)).longValue();
      // Original overlay has non-neutral decorative gain, retained for rejected paths.
      callNative("fillBufferWithColor",new Class<?>[]{long.class,String.class},out,"#AEAEAE");
      if(raw instanceof String)callNative("copyBufferToCoord",new Class<?>[]{long.class,long.class,int.class,int.class},
                         Long.parseLong((String)raw),out,integer(gain,"j"),integer(gain,"k"));
      put(gain,"q",out);
    } catch(Throwable t) { sneaky(t); }
  }
  static void sneaky(Throwable t) { GainFrameCheck.<RuntimeException>throwAny(t); }
  @SuppressWarnings("unchecked") static <T extends Throwable> void throwAny(Throwable t) throws T { throw (T)t; }
  static Object callNative(String name,Class<?>[] types,Object... args) throws Throwable { return XposedBridge.call(method(ASSEMBLER,name,types),null,args); }
  public static Object nativeCall(String name,Object[] a) throws Throwable {
    events.add(name);
    if(name.equals(failNative))throw nativeFailure;
    if(name.equals("hardwareBufferAllocate")) {
      allocations++; if(allocateStatus!=0)return (long)allocateStatus;
      long id=next++; buffers.put(id,new Buffer((Integer)a[0],(Integer)a[1],(Integer)a[2],false,true));return id;
    }
    if(name.equals("fillBufferWithColor")) {
      fills++; if(onFill!=null){Runnable action=onFill;onFill=null;action.run();}
      Buffer b=buffers.get((Long)a[0]); if(b==null || b.released)return -1;
      if(fillStatus!=0)return fillStatus;
      Arrays.fill(b.pixels,(byte)("#000000".equals(a[1])?0:174));return 0;
    }
    if(name.equals("copyBufferToCoord")) {
      copies++; Buffer src=buffers.get((Long)a[0]),dst=buffers.get((Long)a[1]);int x=(Integer)a[2],y=(Integer)a[3];
      if(copyStatus!=0)return copyStatus;
      if(src==null || dst==null || src.released || dst.released || x<0 || y<0 || x+src.w>dst.w || y+src.h>dst.h)return -1;
      if(src.pixels!=null)for(int row=0;row<src.h;row++)System.arraycopy(src.pixels,row*src.w,dst.pixels,(row+y)*dst.w+x,src.w);
      return 0;
    }
    if(name.equals("releaseHardwareBuffer")) {
      releases++;Buffer b=buffers.get((Long)a[0]);check(b!=null && !b.borrowed && !b.released,"borrowed/double release");
      if(releaseStatus!=0)return releaseStatus;b.released=true;return 0;
    }
    throw new AssertionError("unexpected native API "+name);
  }
  static void reset() {
    buffers.clear();events.clear();allocations=fills=copies=releases=0;allocateStatus=fillStatus=copyStatus=releaseStatus=0;
    nativeFailure=null;failNative=null;onFill=onScaling=null;aliasClone=brokenCloneSize=badScaling=originalThrows=throwClone=false;aiFrame=true;
    lastInput=lastGain=null;
  }
  static Object mainHolder(boolean smaller) throws Exception {
    Object main=fresh("ys.c"),info=fresh("ys.g");
    put(info,"p",new Size(smaller?4096:8192,smaller?1512:3024));put(info,"k",get(info,"p"));
    put(info,"e","xpan");put(info,"M",1.0f);put(info,"y",3);put(info,"Z",3);put(main,"s",info);
    put(main,"h",smaller?4256:8504);put(main,"i",smaller?2504:5000);
    put(main,"j",156);put(main,"k",988);put(main,"q",PRIMARY);put(main,"r",OLD_GAIN);
    put(main,"c",new int[]{7,11,19});for(String f:new String[]{"m","n","z","A"})put(main,f,new int[]{10,20,30,40,50,60});
    Bitmap[] v=new Bitmap[6],up=new Bitmap[6],down=new Bitmap[6];for(int i=0;i<6;i++)v[i]=new Bitmap(i);
    up[4]=v[4];down[5]=v[5];put(main,"v",v);put(main,"w",up);put(main,"x",down);
    put(main,"D",2);put(main,"E","#FFFFFF");put(main,"F","original/frame.png");put(main,"o",new Bitmap(80));put(main,"p",new Bitmap(81));
    for(String f:new String[]{"Q","R","S","T","U","V"})put(main,f,new byte[]{1,2,3});
    return main;
  }
  static Object tag() throws Exception { Object tag=fresh("com.oplus.camera.device.CameraRequestTag");put(tag,"C","xpan");put(tag,"q","neutral_sdr.mslut");return tag; }
  static Map<String,Object> raw(boolean smaller) {
    Map<String,Object> map=new LinkedHashMap<String,Object>();map.put("gain_map_width",smaller?"2048":"4096");map.put("gain_map_height",smaller?"756":"1512");
    map.put("gain_map",Long.toString(SOURCE));map.put("gain_map_format",Integer.toString(XpanGainMapFrame.Y8));map.put("image_stage_type","3");
    int sw=smaller?2048:4096,sh=smaller?756:1512;Buffer b=new Buffer(sw,sh,XpanGainMapFrame.Y8,true,true);
    for(int i=0;i<b.pixels.length;i++)b.pixels[i]=(byte)((i*37+17)%251+1);
    buffers.put(SOURCE,b);buffers.put(PRIMARY,new Buffer(smaller?4256:8504,smaller?2504:5000,XpanGainMapFrame.Y8,true,false));
    buffers.put(OLD_GAIN,new Buffer(8,8,XpanGainMapFrame.Y8,true,false));return map;
  }
  static Object invoke(Object main,Map<String,Object> raw,Object tag,int stage,Size size) throws Throwable { return XposedBridge.call(w,helper,main,raw,tag,stage,size); }
  static Object invoke(Object main,Map<String,Object> raw,Object tag) throws Throwable { return invoke(main,raw,tag,3,(Size)get(get(main,"s"),"p")); }
  static void nativeOwnership() { check(releases==0 && !buffers.get(SOURCE).released && !buffers.get(OLD_GAIN).released,"hook took borrowed/old pointer ownership"); }
  static void rejected(Object main,Map<String,Object> map,Object tag,int stage,Size size,XpanGainMapFrame.InstallResult result) throws Throwable {
    long count=result.runtimeStatus().paramsApplied;Map<String,Object> saved=new LinkedHashMap<String,Object>(map);
    try { invoke(main,map,tag,stage,size); } catch(Throwable original) { /* Original rejects malformed SDK payload too. */ }
    check(lastInput==main,"rejected case replaced original holder");check(map.equals(saved),"raw map mutated");
    check(result.runtimeStatus().paramsApplied==count,"rejected candidate counted applied");
    nativeOwnership();
  }
  static void checkPixels(Object gain,boolean smaller) throws Exception {
    Buffer b=buffers.get(pointer(gain,"q")),src=buffers.get(SOURCE);
    int x=integer(gain,"j"),y=integer(gain,"k");
    check(b.w==(smaller?2128:4252) && b.h==(smaller?1252:2500),"real canvas dimensions changed");
    for(int row=0;row<b.h;row++)for(int col=0;col<b.w;col++) {
      byte expected=(row>=y && row<y+src.h && col>=x && col<x+src.w)?src.pixels[(row-y)*src.w+col-x]:0;
      check(b.pixels[row*b.w+col]==expected,"nonzero frame or altered ROI at "+col+","+row);
    }
  }
  static void pixels(XpanGainMapFrame.InstallResult result,boolean smaller) throws Throwable {
    reset();Object main=mainHolder(smaller),tag=tag();Map<String,Object> map=raw(smaller),saved=new LinkedHashMap<String,Object>(map);
    Bitmap[] v=(Bitmap[])get(main,"v"),up=(Bitmap[])get(main,"w"),down=(Bitmap[])get(main,"x");
    Bitmap[] vs=v.clone(),us=up.clone(),ds=down.clone();Object info=get(main,"s"),ownerP=get(main,"p"),ownerO=get(main,"o");
    int[] refs=((int[])get(main,"c")).clone();long oldPrimary=pointer(main,"q"),oldGain=pointer(main,"r");
    byte[] input=buffers.get(SOURCE).pixels.clone();Object gain=invoke(main,map,tag);
    check(lastInput!=main && gain!=lastInput && gain!=main,"independent geometry holders missing");
    check(XposedBridge.lastW.args[0]==main,"original argument was not restored after w");
    check(pointer(main,"q")==oldPrimary && pointer(main,"r")==oldGain,"main q/r mutated before original delivery");
    check(get(main,"s")==info && get(main,"p")==ownerP && get(main,"o")==ownerO,"main resource/metadata owner changed");
    check(get(main,"v")==v && get(main,"w")==up && get(main,"x")==down && Arrays.equals(v,vs) && Arrays.equals(up,us) && Arrays.equals(down,ds),"shared original arrays changed");
    for(Bitmap bm:vs)check(!bm.recycled && bm.colorWrites==0,"original shared Bitmap modified");
    check(Arrays.equals((int[])get(main,"c"),refs) && get(lastInput,"c")!=get(main,"c"),"reference array not isolated");
    for(String f:new String[]{"m","n","z","A"})check(Arrays.equals((int[])get(main,f),new int[]{10,20,30,40,50,60}),"original geometry array modified");
    for(String f:new String[]{"q","r"})check(pointer(lastInput,f)==0,"clone borrowed hardware ownership");
    for(String f:new String[]{"p","o","Q","R","S","T","U","V"})check(get(lastInput,f)==null,"clone retained material/native resources");
    check(map.equals(saved) && Arrays.equals(input,buffers.get(SOURCE).pixels),"source/map modified");
    check(events.equals(Arrays.asList("hardwareBufferAllocate","fillBufferWithColor","copyBufferToCoord")),"hook bypassed original native lifecycle");
    check(allocations==1 && fills==1 && copies==1,"unexpected manual native operations");nativeOwnership();checkPixels(gain,smaller);
    check(result.runtimeStatus().paramsApplied==1 && result.runtimeStatus().originalReturned==1,"geometry/return not observed");
    check(result.runtimeStatus().status==XpanGainMapFrame.Status.PARAMS_APPLIED && !result.runtimeStatus().nativePixelsVerified,"fake result falsely claimed device pixels");
    check(result.runtimeStatus().detail.contains("unverified"),"missing final output qualification");
    // Caller, not hook, delivers q to main.r; later original owner releases exactly once.
    put(main,"r",pointer(gain,"q"));long owned=pointer(gain,"q");
    callNative("releaseHardwareBuffer",new Class<?>[]{long.class},owned);
    check(buffers.get(owned).released && !buffers.get(SOURCE).released && releases==1,"original owner release changed");
  }
  static void standard(Object main) throws Exception {
    Object info=get(main,"s");put(main,"N",9);put(info,"y",3);put(info,"Z",3);put(info,"k",get(info,"p"));
    org.json.JSONObject json=new org.json.JSONObject().put("styleId","hassel_style_xpan_1");put(info,"z0",json);
  }
  static void standardFrame(XpanGainMapFrame.InstallResult result) throws Throwable {
    reset();Object main=mainHolder(false);standard(main);aiFrame=false;
    Object tag=tag();Map<String,Object> map=raw(false);Object info=get(main,"s"),json=get(info,"z0");
    Bitmap[] materials=((Bitmap[])get(main,"v")).clone();long prior=result.runtimeStatus().paramsApplied;
    Object gain=invoke(main,map,tag);
    check(result.runtimeStatus().paramsApplied==prior+1 && result.runtimeStatus().originalReturned==1,"standard frame did not reach scoped scaling");
    checkPixels(gain,false);
    check(get(main,"s")==info && get(info,"z0")==json && integer(main,"N")==9,"standard metadata changed");
    for(Bitmap b:materials)check(!b.recycled && b.colorWrites==0,"standard original material changed");
    check(!((Boolean)XposedBridge.call(method("zk.c","e",String.class),null,get(tag,"q"))).booleanValue(),"AI support leaked outside gain e");
    nativeOwnership();
  }
  static void standardGuards(XpanGainMapFrame.InstallResult result) throws Throwable {
    for(String which:new String[]{"crop_size","stage","type","mode","style_type","inner_canvas","raw_frame","roi_alignment"}) {
      reset();Object main=mainHolder(false);standard(main);aiFrame=false;Object tag=tag();Map<String,Object> map=raw(false);
      Object info=get(main,"s");
      if(which.equals("crop_size"))put(info,"k",new Size(8192,3016));
      if(which.equals("stage"))put(info,"Z",4);
      if(which.equals("type"))put(main,"N",3);
      if(which.equals("mode"))put(info,"y",2);
      if(which.equals("style_type"))((org.json.JSONObject)get(info,"z0")).put("styleId",new Object());
      if(which.equals("inner_canvas")){put(info,"p",new Size(8192,4176));map.put("gain_map_height","2088");}
      if(which.equals("raw_frame"))map.put("gain_map_height","2088");
      if(which.equals("roi_alignment"))put(main,"j",157);
      rejected(main,map,tag,3,(Size)get(info,"p"),result);
    }
  }
  static void standardScope(XpanGainMapFrame.InstallResult result,final TestGate gate) throws Throwable {
    for(final String which:new String[]{"gate","style","crop","tag","recursive_e","final_gate_style","final_gate_stage","final_gate_map"}) {
      reset();final Object main=mainHolder(false);standard(main);aiFrame=false;final Object tag=tag();final Map<String,Object> map=raw(false);
      onScaling=new Runnable(){public void run(){onScaling=null;try {
        if(which.equals("gate"))gate.on=false;
        if(which.equals("style"))((org.json.JSONObject)get(get(main,"s"),"z0")).put("styleId","changed");
        if(which.equals("crop"))put(get(main,"s"),"k",new Size(8192,3024));
        if(which.equals("tag"))put(tag,"q",new String((String)get(tag,"q")));
        if(which.startsWith("final_gate_")) {
          gate.action=new Runnable(){public void run(){try {
            if(which.equals("final_gate_style"))((org.json.JSONObject)get(get(main,"s"),"z0")).put("styleId","changed_by_gate");
            if(which.equals("final_gate_stage"))put(get(main,"s"),"Z",4);
            if(which.equals("final_gate_map"))map.put("gain_map_height",new String("1512"));
          }catch(Exception t){throw new AssertionError(t);}}};
        }
        if(which.equals("recursive_e")) {
          Object nested=mainHolder(false);
          Object out=XposedBridge.call(e,helper,nested,tag,new Size(4096,1512),null,true);
          check(get(out,"s")==null,"nested unrelated e received scaling override");
        }
      }catch(Throwable t){sneaky(t);}}};
      try { invoke(main,map,tag); } catch(Throwable original) { /* Empty original descriptor remains original failure. */ }
      gate.on=true;gate.action=null;
      check(!((Boolean)XposedBridge.call(method("zk.c","e",String.class),null,get(tag,"q"))).booleanValue(),"predicate scope leaked after "+which);
      if(!which.equals("recursive_e"))check(result.runtimeStatus().status==XpanGainMapFrame.Status.GEOMETRY_MISMATCH || result.runtimeStatus().status==XpanGainMapFrame.Status.ORIGINAL_FAILED,"stale scaling accepted");
      check(XposedBridge.lastW.args[0]==main,"standard cleanup left replacement arg");
    }
    reset();Object finalMain=mainHolder(false);standard(finalMain);aiFrame=false;Object gain=invoke(finalMain,raw(false),tag());
    checkPixels(gain,false);check(result.runtimeStatus().status==XpanGainMapFrame.Status.PARAMS_APPLIED,"standard scope did not recover");
  }
  static void diagnosticKeys(XpanGainMapFrame.InstallResult result) throws Throwable {
    reset();Object main=mainHolder(false);((Bitmap[])get(main,"v"))[4]=null;
    Map<String,Object> map=raw(false);map.remove("gain_map_format");XposedBridge.logs.clear();
    invoke(main,map,tag());
    String logs=XposedBridge.logs.toString();
    check(logs.contains("format=absent") && logs.contains("raw=4096x1512") && logs.contains("mapStage=3"),"one absent numeric hid known dimensions");
    check(logs.contains("canvas=8504x5000") && logs.contains("placement=156,988"),"known holder geometry hidden");
    check(!logs.contains(Long.toString(SOURCE)) && !logs.contains("neutral_sdr") && !logs.contains("original/frame.png"),"private pointer/LUT/path logged");
    check(result.runtimeStatus().paramsApplied==0 && lastInput==main,"diagnostics changed nonoverlay parameters");
    reset();main=mainHolder(false);((Bitmap[])get(main,"v"))[4]=null;map=raw(false);map.put("gain_map_width","not-a-number");XposedBridge.logs.clear();
    try {invoke(main,map,tag());} catch(Throwable original) { /* Original numeric rejection retained. */ }
    logs=XposedBridge.logs.toString();
    check(logs.contains("raw=unknownx1512") && logs.contains("mapStage=3") && !logs.contains("not-a-number"),"malformed numeric affected other keys or leaked raw input");
  }
  static void diagnosticOriginal(XpanGainMapFrame.InstallResult result,TestGate gate) throws Throwable {
    reset();Object main=mainHolder(false);standard(main);((Bitmap[])get(main,"v"))[4]=null;
    Object tag=tag();Map<String,Object> map=raw(false);Map<String,Object> saved=new LinkedHashMap<String,Object>(map);
    Object info=get(main,"s"),json=get(info,"z0");Bitmap[] up=(Bitmap[])get(main,"w"),down=(Bitmap[])get(main,"x");
    Bitmap[] us=up.clone(),ds=down.clone();XposedBridge.logs.clear();Object gain=invoke(main,map,tag());
    check(result.runtimeStatus().paramsApplied==0 && lastInput==main,"read-only diagnostic admitted mutation");
    check(map.equals(saved) && get(main,"s")==info && get(info,"z0")==json,"diagnostic changed source owners");
    String logs=XposedBridge.logs.toString();
    check(logs.contains("original gain e branch=two-axis") && logs.contains("input=4096x1512"),"natural e output missing");
    check(logs.contains("observed only, no parameter/result replacement"),"read-only evidence mislabeled applied");
    check(!logs.contains(Long.toString(SOURCE)),"diagnostic pointer logged");
    check(get(gain,"s")!=null,"original output replaced");
    reset();main=mainHolder(false);standard(main);((Bitmap[])get(main,"v"))[4]=null;aiFrame=false;XposedBridge.logs.clear();
    try {invoke(main,raw(false),tag());} catch(Throwable original) { /* Old original bitmap path remains original. */ }
    check(XposedBridge.logs.toString().contains("branch=old-bitmap"),"diagnostic forced two-axis for skipped frame");
    check(result.runtimeStatus().paramsApplied==0,"diagnostic classifier mutated skipped case");
    check(!((Boolean)XposedBridge.call(method("zk.c","e",String.class),null,get(tag,"q"))).booleanValue(),"diagnostic predicate leaked");
    reset();gate.on=false;main=mainHolder(false);((Bitmap[])get(main,"v"))[4]=null;XposedBridge.logs.clear();invoke(main,raw(false),tag());gate.on=true;
    check(!XposedBridge.logs.toString().contains("original gain e"),"diagnostic ignored gate revocation");
  }
  static void plan() {
    int Y=XpanGainMapFrame.Y8;XpanGainMapFrame.NativeFramePlan p=XpanGainMapFrame.NativeFramePlan.create(8192,3024,8504,5000,156,988,4096,1512,Y,3);
    check(p.supported && p.width==4252 && p.height==2500 && p.x==78 && p.y==494,"exact real sample plan");
    check(XpanGainMapFrame.NativeFramePlan.create(4096,1512,4256,2504,156,988,2048,756,Y,3).supported,"known smaller pair");
    int[][] bad={{8192,3024,8504,5000,156,988,4096,2088,Y,3},{8192,4176,8504,5000,156,988,4096,2088,Y,3},
      {8192,3024,8505,5000,156,988,4096,1512,Y,3},{8192,3024,8504,5000,157,988,4096,1512,Y,3},
      {8192,3024,8504,5002,156,988,4096,1512,Y,3},{8192,3024,8504,5000,-2,988,4096,1512,Y,3},
      {8192,3024,8504,5000,400,988,4096,1512,Y,3},{8192,3024,8200,5000,0,988,4096,1512,Y,3},
      {8192,3024,8192,3024,0,0,4096,1512,Y,3},{8192,3024,200000,5000,156,988,4096,1512,Y,3},
      {8192,3024,8504,5000,156,988,4096,1510,Y,3},{8192,3024,8504,5000,156,988,4096,1512,3,3},
      {8192,3024,8504,5000,156,988,4096,1512,Y,1},{Integer.MAX_VALUE,Integer.MAX_VALUE,Integer.MAX_VALUE,Integer.MAX_VALUE,0,0,1,1,Y,3}};
    for(int[] b:bad)check(!XpanGainMapFrame.NativeFramePlan.create(b[0],b[1],b[2],b[3],b[4],b[5],b[6],b[7],b[8],b[9]).supported,"unsafe pure plan");
  }
  static void guards(XpanGainMapFrame.InstallResult result) throws Throwable {
    String[] cases={"photo","lut_not_mode","info_mode","legacy","no_frame","raw_frame","content_frame","format_missing","format_rgb","format_type",
      "stage_map","stage_argument","source_zero","source_negative","source_type","primary_collision","old_collision","primary_zero","cropped_p5","portrait_p5",
      "canvas_odd","offset_fractional","roi_overflow","canvas_alignment","snap_rounding","cropping_cache","v_null","v_length","w_length","x_null","metrics_unknown","alias_clone","broken_clone"};
    for(String which:cases) {
      reset();Object main=mainHolder(false),tag=tag();Map<String,Object> map=raw(false);int stage=3;Size size=(Size)get(get(main,"s"),"p");
      if(which.equals("photo"))put(tag,"C","photo");
      if(which.equals("lut_not_mode")){put(tag,"C","photo");put(tag,"q","xpan");}
      if(which.equals("info_mode"))put(get(main,"s"),"e","photo");
      if(which.equals("legacy"))aiFrame=false;
      if(which.equals("no_frame"))((Bitmap[])get(main,"v"))[4]=null;
      if(which.equals("raw_frame"))map.put("gain_map_height","2088");
      if(which.equals("content_frame")){put(get(main,"s"),"p",new Size(8192,4176));map.put("gain_map_height","2088");}
      if(which.equals("format_missing"))map.remove("gain_map_format");
      if(which.equals("format_rgb"))map.put("gain_map_format","3");
      if(which.equals("format_type"))map.put("gain_map_format",XpanGainMapFrame.Y8);
      if(which.equals("stage_map"))map.put("image_stage_type","2");
      if(which.equals("stage_argument"))stage=2;
      if(which.equals("source_zero"))map.put("gain_map","0");
      if(which.equals("source_negative"))map.put("gain_map","-1");
      if(which.equals("source_type"))map.put("gain_map",SOURCE);
      if(which.equals("primary_collision"))put(main,"q",SOURCE);
      if(which.equals("old_collision"))put(main,"r",SOURCE);
      if(which.equals("primary_zero"))put(main,"q",0L);
      if(which.equals("cropped_p5"))size=new Size(8192,3000);
      if(which.equals("portrait_p5"))size=new Size(3024,8192);
      if(which.equals("canvas_odd"))put(main,"h",8505);
      if(which.equals("offset_fractional"))put(main,"j",157);
      if(which.equals("roi_overflow"))put(main,"j",400);
      if(which.equals("canvas_alignment"))put(main,"i",5002);
      if(which.equals("snap_rounding")){put(main,"h",8200);put(main,"j",0);}
      if(which.equals("cropping_cache")){put(helper,"g",0);put(helper,"h",new ArrayList<Size>(Arrays.asList(size,size)));}
      if(which.equals("v_null"))put(main,"v",null);
      if(which.equals("v_length"))put(main,"v",new Bitmap[5]);
      if(which.equals("w_length"))put(main,"w",new Bitmap[7]);
      if(which.equals("x_null"))put(main,"x",null);
      if(which.equals("metrics_unknown"))put(main,"m",new int[7]);
      if(which.equals("alias_clone"))aliasClone=true;
      if(which.equals("broken_clone"))brokenCloneSize=true;
      rejected(main,map,tag,stage,size,result);put(helper,"g",-1);put(helper,"h",null);
    }
  }
  static void gate(XpanGainMapFrame.InstallResult result,final TestGate gate) throws Throwable {
    reset();gate.on=false;rejected(mainHolder(false),raw(false),tag(),3,new Size(8192,3024),result);
    gate.on=true;long applied=result.runtimeStatus().paramsApplied;gate.revokeAt=gate.calls+2;
    Object finalCandidate=mainHolder(false);invoke(finalCandidate,raw(false),tag());gate.revokeAt=0;
    check(lastInput==finalCandidate && result.runtimeStatus().paramsApplied==applied,"Gate revoked at final assignment was ignored");
    gate.throwsGate=true;rejected(mainHolder(false),raw(false),tag(),3,new Size(8192,3024),result);gate.throwsGate=false;
    reset();Object main=mainHolder(false);onFill=new Runnable(){public void run(){gate.on=false;}};
    invoke(main,raw(false),tag());check(XposedBridge.lastW.args[0]==main,"Gate revocation prevented cleanup");
    gate.on=true;reset();invoke(mainHolder(false),raw(false),tag());check(result.runtimeStatus().paramsApplied==2,"Gate revocation leaked thread scope");
  }
  static void stale(XpanGainMapFrame.InstallResult result,final TestGate gate) throws Throwable {
    String[] changes={"map","geometry","source_owner","material","size","reference_contents","argument"};
    for(final String change:changes) {
      reset();final Object main=mainHolder(false),tag=tag();final Map<String,Object> map=raw(false);
      long before=result.runtimeStatus().paramsApplied;
      final int expected=gate.calls+2;
      gate.action=new Runnable(){public void run(){if(gate.calls!=expected)return;try {
        if(change.equals("map"))map.put("gain_map",new String((String)map.get("gain_map")));
        if(change.equals("geometry"))put(main,"j",158);
        if(change.equals("source_owner"))put(main,"q",PRIMARY+1);
        if(change.equals("material"))((Bitmap[])get(main,"v"))[4]=new Bitmap(99);
        if(change.equals("size"))put(get(main,"s"),"p",new Size(8192,3024));
        if(change.equals("reference_contents"))((int[])get(main,"c"))[0]=17;
        if(change.equals("argument"))XposedBridge.current.args[0]=mainHolder(false);
      }catch(Exception t){throw new AssertionError(t);}}};
      invoke(main,map,tag);gate.action=null;
      check(result.runtimeStatus().paramsApplied==before,"stale "+change+" candidate applied");
      if(!change.equals("argument"))check(lastInput==main,"stale owner replaced");
      else check(lastInput!=main && XposedBridge.lastW.args[0]==lastInput,"another hook's argument stomped");
    }
  }
  static void nested(XpanGainMapFrame.InstallResult result) throws Throwable {
    reset();final Object inner=mainHolder(false),it=tag();final Map<String,Object> im=raw(false);
    final Object outer=mainHolder(false);
    onFill=new Runnable(){public void run(){try {invoke(inner,im,it);check(lastInput==inner,"nested w neutralized");}catch(Throwable t){sneaky(t);}}};
    invoke(outer,im,tag());check(result.runtimeStatus().paramsApplied==1,"nested params applied");
    reset();invoke(mainHolder(false),raw(false),tag());check(result.runtimeStatus().paramsApplied==2,"outer/nested scope leaked");
    long before=result.runtimeStatus().paramsApplied;Object random=mainHolder(false);
    XposedBridge.call(e,helper,random,tag(),new Size(4096,1512),null,true);
    check(result.runtimeStatus().paramsApplied==before,"outside e affected by scope");
  }
  static void originalFailures(XpanGainMapFrame.InstallResult result) throws Throwable {
    reset();throwClone=true;Object rejectedMain=mainHolder(false);
    try { invoke(rejectedMain,raw(false),tag()); throw new AssertionError("hostile original clone exception swallowed"); }
    catch (Throwable t) { check(t instanceof AssertionError && t.getCause() instanceof InvocationTargetException
          && t.getCause().getCause()==HOSTILE,"hook replaced original clone exception"); }
    check(lastInput==rejectedMain && result.runtimeStatus().paramsApplied==0,"hostile qualification changed arguments");
    reset();Object failMain=mainHolder(false);nativeFailure=HOSTILE;failNative="fillBufferWithColor";
    try { invoke(failMain,raw(false),tag()); throw new AssertionError("hostile native exception swallowed"); }
    catch (Throwable t) { check(t==HOSTILE,"hook escaped Throwable.toString or replaced original exception"); }
    check(XposedBridge.lastW.args[0]==failMain,"hostile native failure left replacement argument");
    for(String name:new String[]{"hardwareBufferAllocate","fillBufferWithColor","copyBufferToCoord"}) {
      reset();Object main=mainHolder(false);nativeFailure=ORIGINAL;failNative=name;
      try {invoke(main,raw(false),tag());throw new AssertionError("original native exception swallowed");}
      catch(Throwable t){check(t==ORIGINAL,"original exception identity changed");}
      check(result.runtimeStatus().status==XpanGainMapFrame.Status.ORIGINAL_FAILED,"failure falsely reported applied output");
      check(XposedBridge.lastW.args[0]==main,"failure did not restore original arg");nativeOwnership();
    }
    reset();allocateStatus=0;fillStatus=-1;copyStatus=-1;Object resultOwner=invoke(mainHolder(false),raw(false),tag());
    check(pointer(resultOwner,"q")>0 && result.runtimeStatus().status==XpanGainMapFrame.Status.PARAMS_APPLIED,"changed original native status semantics");
    check(!result.runtimeStatus().nativePixelsVerified && result.runtimeStatus().detail.contains("unverified"),"unchecked original statuses claimed pixel proof");nativeOwnership();
    reset();allocateStatus=-1;invoke(mainHolder(false),raw(false),tag());check(result.runtimeStatus().status==XpanGainMapFrame.Status.ORIGINAL_FAILED,"negative allocation accepted as valid returned owner");
    reset();badScaling=true;invoke(mainHolder(false),raw(false),tag());check(result.runtimeStatus().status==XpanGainMapFrame.Status.GEOMETRY_MISMATCH && result.runtimeStatus().detail.contains("NOT rolled back"),"after e lied about rollback");
    reset();XposedBridge.throwLog=true;invoke(mainHolder(false),raw(false),tag());XposedBridge.throwLog=false;
  }
  public static void main(String[] args) throws Throwable {
    String scenario=args[0];ClassLoader loader=GainFrameCheck.class.getClassLoader();TestGate gate=new TestGate();
    for(String wrong:new String[]{null,"","6.020.876","7.013.510","7.013.51-extra"})check(!XpanGainMapFrame.install(loader,wrong,gate).installed,"inexact version accepted");
    check(!XpanGainMapFrame.install(null,"7.013.51",gate).installed && !XpanGainMapFrame.install(loader,"7.013.51",null).installed,"missing loader/Gate accepted");
    if(scenario.startsWith("rollback") || scenario.equals("null_handle") || scenario.equals("classifier_rollback")) {
      XposedBridge.failAt=scenario.equals("classifier_rollback")?3:2;XposedBridge.failUnhook=scenario.equals("rollback_failure");XposedBridge.nullHandle=scenario.equals("null_handle");
    }
    XpanGainMapFrame.InstallResult result=XpanGainMapFrame.install(loader,"7.013.51",gate);
    check(initializations==0,"reflection initialized camera/JNI classes");
    if(scenario.equals("abi_failure")) {
      check(!result.installed && result.registeredHooks==0 && XposedBridge.registrations==0,"partial hooks before complete ABI resolution");
    } else if(scenario.equals("classifier_rollback")) {
      check(!result.installed && XposedBridge.registrations==3 && result.registeredHooks==0,"partial classifier install active");
      check(XposedBridge.unhookOrder.equals(Arrays.asList("e","w")),"classifier rollback order");
      XposedBridge.residualCallbacks();check(result.runtimeStatus().paramsApplied==0,"partial classifier hooks ran");
    } else if(scenario.startsWith("rollback") || scenario.equals("null_handle")) {
      check(!result.installed && XposedBridge.registrations==2 && !result.runtimeStatus().enabled,"partial install active");
      check(XposedBridge.unhookOrder.equals(Arrays.asList("w")),"rollback order");
      check(result.registeredHooks==(XposedBridge.failUnhook?1:0),"residual hook count");
      XposedBridge.residualCallbacks();check(result.runtimeStatus().paramsApplied==0,"disabled residual hook ran");
      if(XposedBridge.failUnhook)check(XpanGainMapFrame.install(loader,"7.013.51",gate)==result,"residual hooks accumulated on retry");
    } else {
      check(result.installed && result.registeredHooks==3 && result.runtimeStatus().paramsApplied==0,"installation/callback transaction failed");
      check(XpanGainMapFrame.install(loader,"7.013.51",gate)==result && XposedBridge.registrations==3,"duplicate install");
      check(!XpanGainMapFrame.install(loader,"7.013.51",new TestGate()).installed,"cached another Gate accepted");
      helper=fresh("ol.q0");w=method("ol.q0","w",Class.forName("ys.c"),Map.class,Class.forName("com.oplus.camera.device.CameraRequestTag"),int.class,Size.class);
      e=method("ol.q0","e",Class.forName("ys.c"),Class.forName("com.oplus.camera.device.CameraRequestTag"),Size.class,Bitmap.class,boolean.class);
      if(scenario.equals("plan"))plan();
      else if(scenario.equals("pixels"))pixels(result,false);
      else if(scenario.equals("smaller_pair"))pixels(result,true);
      else if(scenario.equals("standard_frame"))standardFrame(result);
      else if(scenario.equals("standard_guards"))standardGuards(result);
      else if(scenario.equals("standard_scope"))standardScope(result,gate);
      else if(scenario.equals("diagnostic_keys"))diagnosticKeys(result);
      else if(scenario.equals("diagnostic_original"))diagnosticOriginal(result,gate);
      else if(scenario.equals("guards"))guards(result);
      else if(scenario.equals("gate"))gate(result,gate);
      else if(scenario.equals("stale"))stale(result,gate);
      else if(scenario.equals("nested"))nested(result);
      else if(scenario.equals("original_failures"))originalFailures(result);
      else throw new AssertionError("unknown scenario");
    }
    System.out.println("PASS XPAN gain frame "+scenario+" (offline parameters/original-flow only)");
  }
} '''


if __name__ == "__main__":
    unittest.main(verbosity=2)
