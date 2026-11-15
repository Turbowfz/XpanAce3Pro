"""Offline C16 smali ABI, real API compile and fake Xposed runtime tests. No adb/device I/O."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CAMERA = ROOT.parents[1] / "多版本相机/6.020.876"
SDK = ROOT.parent / "XpanPort/11_反编译/usdk_smali"
HOOK = ROOT / "hook/XpanC16Config.java"
RECEIPT = ROOT / "_tmp/profile-build/profiles/c16/receipt.json"
ANDROID = Path(os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME") or
               Path.home() / "AppData/Local/Android/Sdk") / "platforms/android-35/android.jar"
XPOSED = ROOT / "_tmp/api-82.jar"

# Fake classes are only test fixtures. Production is compiled AGAINST REAL jars,
# then linked to the fake runtime in a separate JVM, with neither API stub jar on its path.
FIXTURES = {
    "android/content/Context.java": "package android.content; public class Context {}",
    "android/util/Size.java": r'''package android.util;
public final class Size {
  private final int width, height;
  public Size(int width, int height) { this.width = width; this.height = height; }
  public int getWidth() { return width; } public int getHeight() { return height; }
  public String toString() { return width + "x" + height; }
}''',
    "de/robv/android/xposed/XC_MethodHook.java": r'''package de.robv.android.xposed;
import java.lang.reflect.Member;
import java.util.HashMap;
public abstract class XC_MethodHook {
  protected void beforeHookedMethod(MethodHookParam p) throws Throwable {}
  protected void afterHookedMethod(MethodHookParam p) throws Throwable {}
  public static final class MethodHookParam {
    public Member method; public Object thisObject; public Object[] args;
    private Object result; private Throwable throwable; boolean returnEarly;
    private final HashMap<String,Object> extras = new HashMap<String,Object>();
    public Object getResult() { return result; }
    public void setResult(Object result) { if (XposedBridge.beforeMutation != null) XposedBridge.beforeMutation.run(); this.result = result; throwable = null; returnEarly = true; XposedBridge.mutations++; }
    public Throwable getThrowable() { return throwable; }
    public boolean hasThrowable() { return throwable != null; }
    public void setObjectExtra(String key, Object value) { extras.put(key, value); }
    public Object getObjectExtra(String key) { return extras.get(key); }
    void originalResult(Object value) { result = value; }
    void originalFailure(Throwable value) { throwable = value; }
  }
  public final class Unhook {
    private final java.lang.reflect.Method method;
    Unhook(java.lang.reflect.Method method) { this.method = method; }
    public void unhook() {
      XposedBridge.unhookOrder.add(method.getName());
      if (XposedBridge.failUnhook == method) throw new IllegalStateException("injected unhook failure");
      XposedBridge.hooks.remove(method);
    }
  }
}''',
    "de/robv/android/xposed/XposedBridge.java": r'''package de.robv.android.xposed;
import java.lang.reflect.*;
import java.util.*;
public final class XposedBridge {
  public static final LinkedHashMap<Method,XC_MethodHook> hooks = new LinkedHashMap<Method,XC_MethodHook>();
  public static final ArrayList<String> unhookOrder = new ArrayList<String>();
  public static int attempts, failAt, nullAt, mutations;
  public static Runnable beforeMutation;
  public static Method failUnhook;
  public interface RegistrationProbe { void run(Method method, XC_MethodHook hook) throws Throwable; }
  public static RegistrationProbe registrationProbe;
  public static XC_MethodHook.Unhook hookMethod(Member member, XC_MethodHook hook) {
    Method m = (Method) member; attempts++;
    if (attempts == failAt) throw new IllegalStateException("injected registration failure");
    if (attempts == nullAt) return null;
    hooks.put(m, hook);
    if (registrationProbe != null) {
      try { registrationProbe.run(m, hook); } catch (Throwable e) { throw new AssertionError(e); }
    }
    return hook.new Unhook(m);
  }
  public static void log(String message) {}
  public static void beforeOnly(Method method, XC_MethodHook hook, Object... args) throws Throwable {
    XC_MethodHook.MethodHookParam p = new XC_MethodHook.MethodHookParam();
    p.method = method; p.args = args; hook.beforeHookedMethod(p); hook.afterHookedMethod(p);
  }
  public static Object call(Method m, Object receiver, Object... args) throws Throwable {
    XC_MethodHook.MethodHookParam p = new XC_MethodHook.MethodHookParam();
    p.method = m; p.thisObject = receiver; p.args = args;
    XC_MethodHook h = hooks.get(m);
    if (h != null) h.beforeHookedMethod(p);
    if (!p.returnEarly) {
      try { p.originalResult(m.invoke(receiver, p.args)); }
      catch (InvocationTargetException e) { p.originalFailure(e.getCause()); }
    }
    if (h != null) h.afterHookedMethod(p);
    if (p.hasThrowable()) throw p.getThrowable();
    return p.getResult();
  }
}''',
    "fake/Probe.java": r'''package fake;
public final class Probe {
  public static int appInit, sdkInit, baseInit, keyInit, originals;
  public static int apsInit, couiInit, filterInit, nativeReads, styleReads, xpanBuilders;
  public static final java.util.ArrayList<Boolean> regularStyles = new java.util.ArrayList<Boolean>();
  public static Runnable keyRead, xpanEntry, xpanAfterStyle;
  public static RuntimeException xpanFailure;
  public static boolean throwRaw;
  public static void base() { baseInit++; throw new AssertionError("CameraConfigBase prematurely initialized"); }
}''',
    "com/oplus/camera/configure/CameraConfig.java": r'''package com.oplus.camera.configure;
import android.util.Size;
import fake.Probe;
public class CameraConfig {
  static { Probe.appInit++; }
  public static final Object cache = new Object();
  public static boolean getConfigBooleanValue(String key) { Probe.originals++; return false; }
  public static boolean b(String key, boolean fallback) { Probe.originals++; return fallback; }
  public static int i(int fallback, String key) { Probe.originals++; return fallback; }
  public static Size getSizeConfigValue(String key, boolean front) { Probe.originals++; return new Size(front ? 1234 : 5678, 999); }
  public static int[] h(String key, int[] fallback) { Probe.originals++; return fallback; }
  public static int[] getConfigIntArrayValue(String key) { Probe.originals++; return null; }
}''',
    "com/oplus/ocs/camera/common/util/CameraConfigBase.java": r'''package com.oplus.ocs.camera.common.util;
import fake.Probe;
public class CameraConfigBase {
  static { Probe.base(); }
  public static final class Key<T> {
    static { Probe.keyInit++; }
    private final String mKeyName; private final Class<T> mType;
    Key(String name, Class<T> type) { mKeyName = name; mType = type; }
    public String getName() { if (Probe.keyRead != null) Probe.keyRead.run(); return mKeyName; }
    public Class<T> getType() { return mType; }
  }
}''',
    "com/oplus/ocs/camera/producer/info/CameraConfigHelper.java": r'''package com.oplus.ocs.camera.producer.info;
import android.util.Size;
import fake.Probe;
import de.robv.android.xposed.XposedBridge;
import com.oplus.ocs.camera.common.util.CameraConfigBase.Key;
public final class CameraConfigHelper {
  static { Probe.sdkInit++; }
  public static final Object cache = new Object();
  public static Object getConfigValue(Key<?> key, Object fallback) throws Throwable {
    Probe.originals++;
    String raw = (String) XposedBridge.call(CameraConfigHelper.class.getDeclaredMethod("getConfigValue", String.class), null, key.getName());
    if (raw == null) return fallback;
    if (key.getType() == Size.class) {
      String[] parts = raw.toUpperCase().split("[Xx]");
      return new Size(Integer.parseInt(parts[0]), Integer.parseInt(parts[1]));
    }
    if (key.getType() == String.class) return raw;
    if (key.getType() == Integer.class) return Integer.valueOf(raw);
    if (key.getType() == Boolean.class) return Boolean.valueOf(Byte.parseByte(raw) > 0);
    if (key.getType() == int[].class) {
      String[] parts = raw.split(","); int[] value = new int[parts.length];
      for (int i = 0; i < parts.length; i++) value[i] = Integer.parseInt(parts[i]);
      return value;
    }
    return fallback;
  }
  public static String getConfigValue(String key) {
    Probe.originals++;
    if (Probe.throwRaw) throw new IllegalStateException("original raw failure");
    return "com.oplus.xpan.main.picturesize".equals(key) ? "7000X2000" : null;
  }
}''',
    "com/oplus/ocs/camera/consumer/apsAdapter/adapter/ApsUtils.java": r'''package com.oplus.ocs.camera.consumer.apsAdapter.adapter;
import fake.Probe;
import java.util.HashMap;
public class ApsUtils {
  private static HashMap<String,String> sVendorTagConfigMap = new HashMap<String,String>();
  static { Probe.apsInit++; sVendorTagConfigMap.put("com.oplus.use.hasselblad.style.support", "0"); }
  public static String getVendorTagConfig(String key) {
    Probe.nativeReads++;
    return sVendorTagConfigMap == null ? null : sVendorTagConfigMap.get(key);
  }
}''',
    "com/oplus/ocs/camera/ipusdk/processunit/filter/list/CouiUtil.java": r'''package com.oplus.ocs.camera.ipusdk.processunit.filter.list;
import fake.Probe;
import de.robv.android.xposed.XposedBridge;
import com.oplus.ocs.camera.consumer.apsAdapter.adapter.ApsUtils;
public class CouiUtil {
  private static String sHasselbladStyle;
  static { Probe.couiInit++; }
  public static boolean isHasselbladStyle() throws Throwable {
    Probe.styleReads++;
    if (sHasselbladStyle == null || sHasselbladStyle.isEmpty()) {
      sHasselbladStyle = (String) XposedBridge.call(ApsUtils.class.getDeclaredMethod("getVendorTagConfig", String.class), null,
        "com.oplus.use.hasselblad.style.support");
    }
    return sHasselbladStyle != null && Byte.parseByte(sHasselbladStyle) > 0;
  }
}''',
    "com/oplus/ocs/camera/ipusdk/processunit/filter/list/CommonUtil.java": r'''package com.oplus.ocs.camera.ipusdk.processunit.filter.list;
import de.robv.android.xposed.XposedBridge;
import com.oplus.ocs.camera.consumer.apsAdapter.adapter.ApsUtils;
public class CommonUtil {
  public static String getConfigStringValue(String key) throws Throwable {
    String value = (String) XposedBridge.call(ApsUtils.class.getDeclaredMethod("getVendorTagConfig", String.class), null, key);
    return value == null || value.isEmpty() ? "" : value;
  }
  public static int getConfigIntValue(String key, int fallback) throws Throwable {
    String value = getConfigStringValue(key);
    if (!value.isEmpty()) { try { return Integer.parseInt(value); } catch (Exception ignored) {} }
    return fallback;
  }
}''',
    "com/oplus/ocs/camera/ipusdk/processunit/filter/list/FilterGroupManager.java": r'''package com.oplus.ocs.camera.ipusdk.processunit.filter.list;
import android.content.Context;
import fake.Probe;
import de.robv.android.xposed.XposedBridge;
import java.lang.reflect.Method;
import java.util.ArrayList;
public class FilterGroupManager {
  private static boolean mbIsSupportBlackandwhitePortraPositiveFilterUpdate;
  public static ArrayList<String> sHasselbladXpanFilterGroup = new ArrayList<String>();
  static { Probe.filterInit++; }
  private static boolean style() throws Throwable {
    return (Boolean) XposedBridge.call(CouiUtil.class.getDeclaredMethod("isHasselbladStyle"), null);
  }
  // Reduced original caller order: regular groups before XPAN, regular group after.
  public static void init(Context context, boolean export, boolean brand) throws Throwable {
    initBeforeMonoFilter(context);
    Probe.regularStyles.add(style()); // initAfterMonoFilter -> initStickerFilterGroup
  }
  public static void initBeforeMonoFilter(Context context) throws Throwable {
    Probe.regularStyles.add(style()); // initFilterGroup / portrait / video
    Method builder = FilterGroupManager.class.getDeclaredMethod("initHasselbladXpanFilterGroup");
    builder.setAccessible(true);
    XposedBridge.call(builder, null);
  }
  // Original native style gate, version fallback and LUT choices, not a fake injected list.
  private static void initHasselbladXpanFilterGroup() throws Throwable {
    Probe.xpanBuilders++;
    if (Probe.xpanEntry != null) Probe.xpanEntry.run();
    if (Probe.xpanFailure != null) throw Probe.xpanFailure;
    if (!style()) return;
    sHasselbladXpanFilterGroup.add("default");
    if (Probe.xpanAfterStyle != null) Probe.xpanAfterStyle.run();
    if (CommonUtil.getConfigIntValue("com.oplus.xpan.mode.version", 2) == 3) {
      if (mbIsSupportBlackandwhitePortraPositiveFilterUpdate) {
        sHasselbladXpanFilterGroup.add("blackandwhite_HC_normal.bin");
        sHasselbladXpanFilterGroup.add("portra400_normal.bin");
        sHasselbladXpanFilterGroup.add("positive_normal.bin");
        sHasselbladXpanFilterGroup.add("fuji-nc.bin");
        sHasselbladXpanFilterGroup.add("fuji_cc.bin");
      } else {
        sHasselbladXpanFilterGroup.add("Delta400.3dl.rgb.bin");
        sHasselbladXpanFilterGroup.add("fuji_cc.bin");
        sHasselbladXpanFilterGroup.add("fuji-nc.bin");
      }
      sHasselbladXpanFilterGroup.add("fuji-proNegHi.bin");
      sHasselbladXpanFilterGroup.add("800t.bin");
    } else sHasselbladXpanFilterGroup.add("Delta400.3dl.rgb.bin");
  }
}''',
}

HARNESS = r'''package com.xpanport.hook;
import android.util.Size;
import fake.Probe;
import de.robv.android.xposed.*;
import java.lang.reflect.*;
import java.net.*;
import java.io.File;
import java.util.*;
public final class C16ConfigCheck {
  static final String SUPPORT="com.oplus.feature.xpan.mode.support", VERSION="com.oplus.xpan.mode.version",
    LEGACY="com.oplus.xpan.legacy.ui.style", PICTURE="com.oplus.xpan.main.picturesize",
    PORTRAIT="com.oplus.xpan.crop.region.main.portrait", LANDSCAPE="com.oplus.xpan.crop.region.main.landscape",
    STYLE="com.oplus.use.hasselblad.style.support";
  static final int BINDINGS=13, HOOKS=11;
  static int checks;
  static void require(boolean yes, String why) { checks++; if (!yes) throw new AssertionError(why); }
  static final class Gate implements XpanC16Config.Gate {
    boolean enabled=true, fail; int calls;
    public boolean enabled() { calls++; if (fail) throw new IllegalStateException("gate failure"); return enabled; }
  }
  static final class TargetLoader extends URLClassLoader {
    TargetLoader(String folder) throws Exception { super(new URL[]{new File(folder).toURI().toURL()}, C16ConfigCheck.class.getClassLoader()); }
    protected Class<?> loadClass(String name, boolean resolve) throws ClassNotFoundException {
      if (!name.startsWith("com.oplus.")) return super.loadClass(name, resolve);
      synchronized (getClassLoadingLock(name)) {
        Class<?> c=findLoadedClass(name); if(c==null) c=findClass(name);
        if(resolve) resolveClass(c); return c;
      }
    }
  }
  static Class<?> app, sdk, keyClass, aps, coui, groups, common;
  static Method bool, boolDefault, integer, size, array, arrayDefault, typed, raw;
  static Method builder, nativeStyle, vendor, initGroups, nativeInteger;
  static Field styleCache, vendorMap, xpanList, updateFilters;
  static Constructor<?> keyConstructor;
  static void bind(ClassLoader loader) throws Exception {
    app=Class.forName("com.oplus.camera.configure.CameraConfig", false, loader);
    sdk=Class.forName("com.oplus.ocs.camera.producer.info.CameraConfigHelper", false, loader);
    keyClass=Class.forName("com.oplus.ocs.camera.common.util.CameraConfigBase$Key", false, loader);
    bool=app.getDeclaredMethod("getConfigBooleanValue", String.class);
    boolDefault=app.getDeclaredMethod("b", String.class, boolean.class);
    integer=app.getDeclaredMethod("i", int.class, String.class);
    size=app.getDeclaredMethod("getSizeConfigValue", String.class, boolean.class);
    arrayDefault=app.getDeclaredMethod("h", String.class, int[].class);
    array=app.getDeclaredMethod("getConfigIntArrayValue", String.class);
    typed=sdk.getDeclaredMethod("getConfigValue", keyClass, Object.class);
    raw=sdk.getDeclaredMethod("getConfigValue", String.class);
    keyConstructor=keyClass.getDeclaredConstructor(String.class, Class.class); keyConstructor.setAccessible(true);
    aps=Class.forName("com.oplus.ocs.camera.consumer.apsAdapter.adapter.ApsUtils", false, loader);
    coui=Class.forName("com.oplus.ocs.camera.ipusdk.processunit.filter.list.CouiUtil", false, loader);
    groups=Class.forName("com.oplus.ocs.camera.ipusdk.processunit.filter.list.FilterGroupManager", false, loader);
    common=Class.forName("com.oplus.ocs.camera.ipusdk.processunit.filter.list.CommonUtil", false, loader);
    vendor=aps.getDeclaredMethod("getVendorTagConfig", String.class);
    nativeStyle=coui.getDeclaredMethod("isHasselbladStyle");
    builder=groups.getDeclaredMethod("initHasselbladXpanFilterGroup"); builder.setAccessible(true);
    initGroups=groups.getDeclaredMethod("init", android.content.Context.class, boolean.class, boolean.class);
    nativeInteger=common.getDeclaredMethod("getConfigIntValue", String.class, int.class);
    // Test-only access observes/seeds caches; the production hook never resolves fields.
    styleCache=coui.getDeclaredField("sHasselbladStyle"); styleCache.setAccessible(true);
    vendorMap=aps.getDeclaredField("sVendorTagConfigMap"); vendorMap.setAccessible(true);
    xpanList=groups.getDeclaredField("sHasselbladXpanFilterGroup");
    updateFilters=groups.getDeclaredField("mbIsSupportBlackandwhitePortraPositiveFilterUpdate"); updateFilters.setAccessible(true);
  }
  static Object call(Method m, Object... args) throws Throwable { return XposedBridge.call(m, null, args); }
  static Object key(String name, Class<?> type) throws Exception { return keyConstructor.newInstance(name, type); }
  static void sizeIs(Object value, int width, int height) {
    require(value instanceof Size, "Size return type"); Size s=(Size)value;
    require(s.getWidth()==width && s.getHeight()==height, "Size dimensions " + s);
  }
  static Object[] targetArgs(Method m) {
    if (m.getParameterTypes().length==0) return new Object[]{};
    if (m.getName().equals("getVendorTagConfig")) return new Object[]{VERSION};
    if (m.getName().equals("i")) return new Object[]{1, VERSION};
    if (m.getName().equals("b")) return new Object[]{SUPPORT, false};
    if (m.getName().equals("h")) return new Object[]{PORTRAIT, new int[]{7}};
    if (m.getName().equals("getConfigIntArrayValue")) return new Object[]{LANDSCAPE};
    if (m.getName().equals("getSizeConfigValue")) return new Object[]{PICTURE, false};
    if (m.getParameterTypes().length==2) return new Object[]{null, null};
    return new Object[]{m.getName().equals("getConfigValue") ? PICTURE : SUPPORT};
  }
  static void initUntouched() {
    require(Probe.appInit==0 && Probe.sdkInit==0 && Probe.keyInit==0 && Probe.baseInit==0
      && Probe.apsInit==0 && Probe.couiInit==0 && Probe.filterInit==0,
      "resolve/register initialized target classes");
  }
  static XpanC16Config.InstallResult install(ClassLoader loader, Gate gate) {
    return XpanC16Config.install(loader, "6.020.876", gate);
  }
  static void success(ClassLoader loader) throws Throwable {
    bind(loader); Gate g=new Gate();
    XposedBridge.registrationProbe=(m,h)-> {
      int before=XposedBridge.mutations; XposedBridge.beforeOnly(m,h,targetArgs(m));
      require(XposedBridge.mutations==before, "callback active during registration"); initUntouched();
    };
    XpanC16Config.InstallResult r=install(loader,g);
    require(r.installed && r.validatedBindings==BINDINGS && r.registeredHooks==HOOKS, r.detail);
    initUntouched(); require(g.calls==0, "Gate evaluated during install");
    require(install(loader,g)==r && XposedBridge.attempts==HOOKS, "idempotent install");
    require(!install(loader,new Gate()).installed && XposedBridge.attempts==HOOKS, "different Gate accepted");
    final int[] lastGateRead={g.calls};
    XposedBridge.beforeMutation=()-> {
      require(g.calls>lastGateRead[0], "mutation without a fresh live Gate check");
      require(g.enabled && !g.fail, "mutation while Gate disabled/throwing");
      lastGateRead[0]=g.calls;
    };
    require(Boolean.TRUE.equals(call(bool,SUPPORT)), "support");
    require(Boolean.FALSE.equals(call(bool,LEGACY)), "legacy");
    for (boolean fallback : new boolean[]{false,true}) {
      require(Boolean.TRUE.equals(call(boolDefault,SUPPORT,fallback)), "boolean fallback support");
      require(Boolean.FALSE.equals(call(boolDefault,LEGACY,fallback)), "boolean fallback legacy");
    }
    require(((Integer)call(integer,-8,VERSION))==3, "default,int String ABI");
    sizeIs(call(size,PICTURE,false),4096,1512);
    require(Probe.originals==0, "precise overrides did not short circuit original");
    // Every array invocation returns its own clone, never the fallback or shared constants.
    int[] fallback={41,42};
    for(String name : new String[]{PORTRAIT,LANDSCAPE}) {
      int[] expected=name.equals(PORTRAIT) ? new int[]{780,0,1512,4096} : new int[]{0,780,4096,1512};
      for(Method m : new Method[]{array,arrayDefault}) {
        int[] a=(int[])(m==array ? call(m,name) : call(m,name,fallback));
        require(Arrays.equals(a,expected) && a!=fallback,"receipt array"); a[0]=-999;
        int[] b=(int[])(m==array ? call(m,name) : call(m,name,fallback));
        require(b!=a && Arrays.equals(b,expected),"array not freshly cloned");
      }
    }
    require(Arrays.equals(fallback,new int[]{41,42}),"fallback mutated");
    sizeIs(call(size,PICTURE,true),1234,999); // original front branch ignores rear key
    sizeIs(call(typed,key(PICTURE,Size.class),null),4096,1512);
    require("4096x1512".equals(call(raw,PICTURE)),"SDK raw serialization");
    require(Probe.baseInit==0,"typed Key read initialized outer CameraConfigBase");
    Object appCache=app.getField("cache").get(null), sdkCache=sdk.getField("cache").get(null);
    // Six-key whitelist and getter type boundaries: no prefix matching or foreign feature effects.
    for(String name : new String[]{null,"com.oplus.feature.master.mode.support", "com.oplus.feature.xpan.mode.support.extra",
        "com.oplus.xpan.wide.picturesize", "com.oplus.xpan.crop.region.wide.portrait", "COM.OPLUS.XPAN.MODE.VERSION"}) {
      int before=XposedBridge.mutations;
      require(Boolean.FALSE.equals(call(bool,name)),"foreign boolean");
      require(Boolean.TRUE.equals(call(boolDefault,name,true)),"foreign boolean fallback");
      require(((Integer)call(integer,77,name))==77,"foreign int");
      require(call(arrayDefault,name,fallback)==fallback,"foreign fallback array identity");
      require(call(array,name)==null,"foreign array"); sizeIs(call(size,name,false),5678,999);
      require(call(raw,name)==null,"foreign SDK raw");
      require(XposedBridge.mutations==before,"foreign key mutation");
    }
    for(String name : new String[]{null,SUPPORT,VERSION,LEGACY,PICTURE,PORTRAIT,LANDSCAPE}) {
      int before=XposedBridge.mutations;
      Object result=call(typed,key(name,String.class),fallback);
      if(PICTURE.equals(name)) require("7000X2000".equals(result),"wrong typed key acquired SDK override");
      else {
        require(result==fallback,"unsupported typed key fallback identity");
        require(call(raw,name)==null,"other SDK receipt key unexpectedly overridden");
      }
      require(XposedBridge.mutations==before,"SDK overrides beyond verified Size Key");
    }
    for(Class<?> type : new Class<?>[]{Object.class, Size[].class, int[].class, Boolean.class, int.class}) {
      // int[]/Boolean parse the original raw text and throw; an override must not hide that failure.
      int before=XposedBridge.mutations;
      boolean shouldThrow=type==int[].class || type==Boolean.class;
      try {
        require(call(typed,key(PICTURE,type),fallback)==fallback,"mismatched Key fallback identity");
        require(!shouldThrow,"original typed parser exception suppressed");
      } catch(NumberFormatException expected) { require(shouldThrow,"unexpected original failure"); }
      require(XposedBridge.mutations==before,"mismatched Key type mutation");
      require("4096x1512".equals(call(raw,PICTURE)),"passthrough frame leaked");
    }
    int nullBefore=XposedBridge.mutations;
    try { call(typed,null,fallback); throw new AssertionError("original null Key failure suppressed"); }
    catch(NullPointerException expected) { require(XposedBridge.mutations==nullBefore,"null Key mutated"); }
    require("4096x1512".equals(call(raw,PICTURE)),"null Key cleanup frame leaked");
    // No app getter should accept a receipt key of a different type.
    int before=XposedBridge.mutations;
    require(Boolean.FALSE.equals(call(bool,VERSION)),"int through bool");
    require(((Integer)call(integer,11,SUPPORT))==11,"bool through int");
    require(call(arrayDefault,PICTURE,fallback)==fallback,"Size through array");
    sizeIs(call(size,PORTRAIT,false),5678,999);
    require(XposedBridge.mutations==before,"wrong getter type mutation");
    require(app.getField("cache").get(null)==appCache && sdk.getField("cache").get(null)==sdkCache,"static cache changed");
    gateChecks(g);
  }
  static void gateChecks(Gate g) throws Throwable {
    int[] fallback={9};
    for(boolean failure : new boolean[]{false,true}) {
      g.enabled=false; g.fail=failure; int before=XposedBridge.mutations;
      require(Boolean.FALSE.equals(call(bool,SUPPORT)),"Gate support");
      require(Boolean.TRUE.equals(call(boolDefault,LEGACY,true)),"Gate legacy");
      require(((Integer)call(integer,12,VERSION))==12,"Gate version");
      sizeIs(call(size,PICTURE,false),5678,999);
      require(call(arrayDefault,PORTRAIT,fallback)==fallback,"Gate crop fallback");
      require(call(array,LANDSCAPE)==null,"Gate crop");
      require("7000X2000".equals(call(raw,PICTURE)),"Gate SDK raw");
      sizeIs(call(typed,key(PICTURE,Size.class),fallback),7000,2000);
      require(XposedBridge.mutations==before,"disabled/throwing Gate mutation");
    }
    g.fail=false; g.enabled=true;
    // Metadata read flips live gate immediately before the single mutation boundary.
    Probe.keyRead=()->{g.enabled=false;}; int before=XposedBridge.mutations;
    sizeIs(call(typed,key(PICTURE,Size.class),null),7000,2000);
    require(XposedBridge.mutations==before,"stale Gate captured before metadata");
    Probe.keyRead=null; g.enabled=true;
    require(Boolean.TRUE.equals(call(bool,SUPPORT)),"Gate re-enable");
    // Original exception must escape unchanged, and after must restore the raw passthrough frame.
    Probe.throwRaw=true;
    try { call(typed,key(PICTURE,String.class),null); throw new AssertionError("original failure swallowed"); }
    catch(IllegalStateException expected) { require(expected.getMessage().equals("original raw failure"),"original exception replaced"); }
    Probe.throwRaw=false;
    require("4096x1512".equals(call(raw,PICTURE)),"exception cleanup frame leaked");
    // Nested mismatched typed calls restore both frames, even if Gate changes mid-read.
    final boolean[] nesting={false};
    Probe.keyRead=()-> {
      if(nesting[0]) return; nesting[0]=true;
      try { require("7000X2000".equals(call(typed,key(PICTURE,String.class),null)),"nested typed passthrough"); }
      catch(Throwable t) { throw new AssertionError(t); } finally { nesting[0]=false; }
    };
    require("7000X2000".equals(call(typed,key(PICTURE,String.class),null)),"outer nested typed passthrough");
    Probe.keyRead=null;
    require("4096x1512".equals(call(raw,PICTURE)),"nested cleanup frame leaked");
    // Each thread owns its passthrough scope. A wrong typed Key on a worker may
    // block in its original getter without suppressing an independent raw call.
    final java.util.concurrent.CountDownLatch inside=new java.util.concurrent.CountDownLatch(1);
    final java.util.concurrent.CountDownLatch release=new java.util.concurrent.CountDownLatch(1);
    final java.util.concurrent.atomic.AtomicReference<Throwable> workerFailure=new java.util.concurrent.atomic.AtomicReference<Throwable>();
    Thread worker=new Thread(()-> {
      try { require("7000X2000".equals(call(typed,key(PICTURE,String.class),null)),"worker passthrough result"); }
      catch(Throwable t) { workerFailure.set(t); }
    });
    final int[] workerReads={0};
    Probe.keyRead=()-> {
      if(Thread.currentThread()==worker && ++workerReads[0]==2) {
        inside.countDown();
        try { if(!release.await(5,java.util.concurrent.TimeUnit.SECONDS)) throw new AssertionError("worker wait timed out"); }
        catch(InterruptedException e) { throw new AssertionError(e); }
      }
    };
    worker.start();
    try {
      require(inside.await(5,java.util.concurrent.TimeUnit.SECONDS),"worker did not enter original typed getter");
      require("4096x1512".equals(call(raw,PICTURE)),"passthrough incorrectly shared across threads");
    } finally { release.countDown(); worker.join(5000); Probe.keyRead=null; }
    require(!worker.isAlive() && workerFailure.get()==null,"worker original typed getter failed");
  }
  @SuppressWarnings("unchecked") static ArrayList<String> nativeList() throws Exception {
    return (ArrayList<String>)xpanList.get(null);
  }
  static void nativeChecks(ClassLoader loader) throws Throwable {
    bind(loader); Gate g=new Gate(); XpanC16Config.InstallResult r=install(loader,g);
    require(r.installed && r.validatedBindings==BINDINGS && r.registeredHooks==HOOKS,r.detail);
    initUntouched(); require(g.calls==0,"native install evaluated Gate");
    final int[] lastGateRead={0};
    XposedBridge.beforeMutation=()-> {
      require(g.calls>lastGateRead[0] && g.enabled && !g.fail,"native mutation without fresh live Gate");
      lastGateRead[0]=g.calls;
    };
    Map<String,String> map=(Map<String,String>)vendorMap.get(null);
    Map<String,String> snapshot=new HashMap<String,String>(map);
    final String[] standard={"default","Delta400.3dl.rgb.bin","fuji_cc.bin","fuji-nc.bin","fuji-proNegHi.bin","800t.bin"};
    // Null/empty would populate a global cache if raw style were overridden; cached "0"
    // would skip that lookup entirely. Both must take the native XPAN branch only.
    for(String cached : new String[]{null,"","0","1"}) {
      styleCache.set(null,cached); nativeList().clear(); int originals=Probe.styleReads;
      call(builder);
      require(nativeList().equals(Arrays.asList(standard)),"original XPAN version-3 branch " + cached);
      require(Objects.equals(styleCache.get(null),cached) && Probe.styleReads==originals,"XPAN read/wrote shared style cache");
      int before=XposedBridge.mutations;
      require(call(vendor,VERSION)==null && "0".equals(call(vendor,STYLE)),"native raw leaked outside builder");
      require(Boolean.valueOf("1".equals(cached)).equals(call(nativeStyle)),"ordinary style differs from stock/cache");
      require(XposedBridge.mutations==before,"ordinary style/raw mutated");
    }
    styleCache.set(null,"0"); nativeList().clear(); Probe.regularStyles.clear();
    call(initGroups,null,false,false);
    require(Probe.regularStyles.equals(Arrays.asList(false,false)),"scope expanded to regular-mode parent");
    require(nativeList().equals(Arrays.asList(standard)) && "0".equals(styleCache.get(null)),"parent XPAN branch/cache");
    // PB/trio is an existing native branch, not a new feature override.
    updateFilters.setBoolean(null,true); nativeList().clear(); call(builder);
    require(nativeList().equals(Arrays.asList("default","blackandwhite_HC_normal.bin","portra400_normal.bin",
      "positive_normal.bin","fuji-nc.bin","fuji_cc.bin","fuji-proNegHi.bin","800t.bin")),"stock PB/trio branch changed");
    updateFilters.setBoolean(null,false);
    Probe.xpanEntry=()-> {
      try {
        int before=XposedBridge.mutations;
        for(String key : new String[]{null,STYLE,SUPPORT,LEGACY,PICTURE,PORTRAIT,LANDSCAPE,
            "com.oplus.xpan.mode.version.extra","COM.OPLUS.XPAN.MODE.VERSION","com.oplus.feature.master.mode.support"})
          require(Objects.equals(call(vendor,key),map.get(key)),"foreign native key changed " + key);
        require(XposedBridge.mutations==before,"foreign native key mutation");
        require("3".equals(call(vendor,VERSION)) && ((Integer)call(nativeInteger,VERSION,2))==3,"direct CommonUtil flow");
      } catch(Throwable t) { throw new AssertionError(t); }
    };
    call(builder); Probe.xpanEntry=null;
    require(vendorMap.get(null)==map && map.equals(snapshot),"native vendor map overwritten");
    // Off/throwing Gate keeps original cache and fallback, even at exact native keys.
    for(String cached : new String[]{"0","1"}) for(boolean failure : new boolean[]{false,true}) {
      styleCache.set(null,cached); nativeList().clear(); g.enabled=false; g.fail=failure;
      int before=XposedBridge.mutations; call(builder);
      require(nativeList().equals("1".equals(cached) ? Arrays.asList("default","Delta400.3dl.rgb.bin") : Collections.emptyList()),"Gate native branch");
      require(Objects.equals(styleCache.get(null),cached) && call(vendor,VERSION)==null,"Gate native cache/raw");
      require(XposedBridge.mutations==before,"disabled/throwing native Gate mutation");
    }
    g.enabled=true; g.fail=false; styleCache.set(null,"0");
    Probe.xpanAfterStyle=()->{g.enabled=false;}; nativeList().clear(); call(builder); Probe.xpanAfterStyle=null;
    require(nativeList().equals(Arrays.asList("default","Delta400.3dl.rgb.bin")),"native version used stale entry Gate");
    g.enabled=true; require(Boolean.FALSE.equals(call(nativeStyle)) && call(vendor,VERSION)==null,"Gate-change cleanup leaked");
    RuntimeException failure=new IllegalStateException("original XPAN failure"); Probe.xpanFailure=failure;
    try { call(builder); throw new AssertionError("original native failure swallowed"); }
    catch(IllegalStateException expected) { require(expected==failure,"original native throwable changed"); }
    Probe.xpanFailure=null;
    require(Boolean.FALSE.equals(call(nativeStyle)) && call(vendor,VERSION)==null,"exception scope leaked");
    // Nested exceptional/off-at-entry frames restore the parent, not simply remove it.
    Probe.xpanEntry=()-> {
      Probe.xpanEntry=null;
      try {
        g.enabled=false; Probe.xpanEntry=()->{g.enabled=true;};
        int before=XposedBridge.mutations; call(builder); Probe.xpanEntry=null;
        require(XposedBridge.mutations==before,"off nested entry inherited outer scope");
        require("3".equals(call(vendor,VERSION)),"off nested entry erased parent scope");
        Probe.xpanFailure=failure;
        try { call(builder); throw new AssertionError("nested native failure swallowed"); }
        catch(IllegalStateException expected) { require(expected==failure,"nested native exception changed"); }
        finally { Probe.xpanFailure=null; }
        require("3".equals(call(vendor,VERSION)) && Boolean.TRUE.equals(call(nativeStyle)),"nested exception erased parent scope");
      } catch(Throwable t) { throw new AssertionError(t); }
    };
    nativeList().clear(); call(builder); Probe.xpanEntry=null;
    require(nativeList().equals(Arrays.asList(standard)),"outer builder after nested exception/off entry");
    require(Boolean.FALSE.equals(call(nativeStyle)) && call(vendor,VERSION)==null,"nested outer cleanup leaked");
    // Worker scope cannot turn an unrelated thread into a Hasselblad caller.
    final java.util.concurrent.atomic.AtomicReference<Throwable> workerFailure=new java.util.concurrent.atomic.AtomicReference<Throwable>();
    Probe.xpanEntry=()-> {
      Thread worker=new Thread(()-> {
        try { require(Boolean.FALSE.equals(call(nativeStyle)) && call(vendor,VERSION)==null,"native scope shared between threads"); }
        catch(Throwable t) { workerFailure.set(t); }
      });
      worker.start(); try { worker.join(5000); } catch(InterruptedException e) { throw new AssertionError(e); }
      require(!worker.isAlive() && workerFailure.get()==null,"native worker failed");
    };
    call(builder); Probe.xpanEntry=null;
    require(map.equals(snapshot) && "0".equals(styleCache.get(null)),"final native map/cache changed");
  }
  static void rollback(ClassLoader loader, int failAt, boolean residual, boolean nullHandle) throws Throwable {
    bind(loader); Gate g=new Gate();
    XposedBridge.failAt=nullHandle?0:failAt; XposedBridge.nullAt=nullHandle?failAt:0;
    XposedBridge.registrationProbe=(m,h)-> {
      XposedBridge.beforeOnly(m,h,targetArgs(m)); initUntouched();
      if(residual && XposedBridge.attempts==1) XposedBridge.failUnhook=m;
    };
    XpanC16Config.InstallResult r=install(loader,g);
    require(!r.installed && r.validatedBindings==BINDINGS && r.registeredHooks==(residual?1:0),r.detail);
    require(XposedBridge.mutations==0 && g.calls==0,"partial install active"); initUntouched();
    require(XposedBridge.hooks.size()==(residual?1:0),"rollback missing");
    ArrayList<String> expected=new ArrayList<String>();
    String[] names={"getConfigBooleanValue","b","i","getSizeConfigValue","h","getConfigIntArrayValue",
      "getConfigValue","getConfigValue","initHasselbladXpanFilterGroup","isHasselbladStyle","getVendorTagConfig"};
    for(int i=failAt-2;i>=0;i--) expected.add(names[i]);
    require(XposedBridge.unhookOrder.equals(expected),"rollback not reverse group order");
    if(residual) {
      require(Boolean.FALSE.equals(call(bool,SUPPORT)),"residual hook active");
      int attempts=XposedBridge.attempts; require(install(loader,g)==r,"residual retry not blocked");
      require(XposedBridge.attempts==attempts,"overlapping residual hooks");
    } else {
      XposedBridge.failAt=0; XposedBridge.nullAt=0; XposedBridge.registrationProbe=null;
      require(install(loader,g).installed,"clean rollback not retryable");
      require(Boolean.TRUE.equals(call(bool,SUPPORT)),"retry inactive");
    }
  }
  public static void main(String[] args) throws Throwable {
    ClassLoader loader=new TargetLoader(args[1]);
    String mode=args[0];
    if(mode.equals("success")) success(loader);
    else if(mode.equals("native")) nativeChecks(loader);
    else if(mode.equals("resolve-fail")) {
      XpanC16Config.InstallResult r=install(loader,new Gate());
      require(!r.installed && r.registeredHooks==0,r.detail);
      require(XposedBridge.attempts==0,"hook registered before complete ABI resolve"); initUntouched();
    } else if(mode.equals("refuse")) {
      require(!XpanC16Config.install(loader,"7.013.51",new Gate()).installed,"C17 accepted");
      require(!XpanC16Config.install(loader,"6.020.876-extra",new Gate()).installed,"inexact version accepted");
      require(!XpanC16Config.install(null,"6.020.876",new Gate()).installed,"null loader accepted");
      require(!XpanC16Config.install(loader,"6.020.876",null).installed,"null Gate accepted");
      require(XposedBridge.attempts==0,"refusal registered hooks"); initUntouched();
    } else rollback(loader,Integer.parseInt(args[2]),mode.equals("residual"),mode.equals("null-handle"));
    require(Probe.baseInit==0,"CameraConfigBase initialized");
    System.out.println("C16 fake runtime " + mode + " passed: " + checks + " assertions; device/HAL unverified");
  }
}'''


class C16ConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.javac, cls.java = shutil.which("javac"), shutil.which("java")
        if not cls.javac or not cls.java:
            raise AssertionError("Local javac and java required")
        for file in (ANDROID, XPOSED):
            if not file.is_file():
                raise AssertionError(f"Real API jar required: {file}")
        cls.temp = tempfile.TemporaryDirectory(prefix="c16-config-tests-")
        cls.folder = Path(cls.temp.name)
        cls.real = cls.folder / "real"
        cls.fake = cls.folder / "fake"
        cls.real.mkdir()
        cls.fake.mkdir()
        cp = os.pathsep.join(map(str, (ANDROID, XPOSED)))
        cls.run_command([cls.javac, "-encoding", "UTF-8", "-source", "8", "-target", "8",
                         "-bootclasspath", str(ANDROID), "-classpath", cp,
                         "-d", str(cls.real), str(HOOK)])
        files = cls.write_sources(cls.folder / "sources", FIXTURES)
        harness = cls.folder / "C16ConfigCheck.java"
        harness.write_text(HARNESS, encoding="utf-8")
        cls.run_command([cls.javac, "-encoding", "UTF-8", "-source", "8", "-target", "8",
                         "-classpath", str(cls.real), "-d", str(cls.fake), *map(str, files), str(harness)])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def run_command(cls, command):
        p = subprocess.run(command, cwd=ROOT, text=True, encoding="utf-8", errors="replace",
                           capture_output=True, timeout=90)
        if p.returncode:
            raise AssertionError(f"Local command failed ({p.returncode}):\n{p.stdout}\n{p.stderr}")
        return p.stdout

    @staticmethod
    def write_sources(folder, fixtures):
        files = []
        for relative, content in fixtures.items():
            path = folder / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            files.append(path)
        return files

    def runtime(self, mode, targets=None, *args):
        cp = os.pathsep.join(map(str, (self.real, self.fake)))
        output = self.run_command([self.java, "-cp", cp, "com.xpanport.hook.C16ConfigCheck",
                                   mode, str(targets or self.fake), *map(str, args)])
        self.assertIn("passed:", output)
        print(output.strip())

    def test_real_camera_and_sdk_smali_abi(self):
        cases = {
            CAMERA / "smali/com/oplus/camera/configure/CameraConfig.smali": [
                "getConfigBooleanValue(Ljava/lang/String;)Z", "b(Ljava/lang/String;Z)Z",
                "i(ILjava/lang/String;)I", "getSizeConfigValue(Ljava/lang/String;Z)Landroid/util/Size;",
                "h(Ljava/lang/String;[I)[I", "getConfigIntArrayValue(Ljava/lang/String;)[I"],
            SDK / "smali/com/oplus/ocs/camera/consumer/apsAdapter/adapter/ApsUtils.smali": [
                "getVendorTagConfig(Ljava/lang/String;)Ljava/lang/String;"],
            SDK / "smali/com/oplus/ocs/camera/ipusdk/processunit/filter/list/CouiUtil.smali": [
                "isHasselbladStyle()Z"],
            SDK / "smali/com/oplus/ocs/camera/producer/info/CameraConfigHelper.smali": [
                "getConfigValue(Lcom/oplus/ocs/camera/common/util/CameraConfigBase$Key;Ljava/lang/Object;)Ljava/lang/Object;",
                "getConfigValue(Ljava/lang/String;)Ljava/lang/String;"],
        }
        for path, descriptors in cases.items():
            text = path.read_text(encoding="utf-8")
            for descriptor in descriptors:
                with self.subTest(file=path, descriptor=descriptor):
                    self.assertRegex(text, r"(?m)^\.method public static " + re.escape(descriptor) + r"\s*$")
        groups = (SDK / "smali/com/oplus/ocs/camera/ipusdk/processunit/filter/list/FilterGroupManager.smali").read_text(encoding="utf-8")
        self.assertRegex(groups, r"(?m)^\.method private static initHasselbladXpanFilterGroup\(\)V\s*$")
        key = (SDK / "smali/com/oplus/ocs/camera/common/util/CameraConfigBase$Key.smali").read_text(encoding="utf-8")
        for descriptor in ("getName()Ljava/lang/String;", "getType()Ljava/lang/Class;"):
            self.assertRegex(key, r"(?m)^\.method public " + re.escape(descriptor) + r"\s*$")
        base = (SDK / "smali/com/oplus/ocs/camera/common/util/CameraConfigBase.smali").read_text(encoding="utf-8")
        self.assertRegex(base, r'const-string v1, "com\.oplus\.xpan\.main\.picturesize"\s+'
                         r'const-class v2, Landroid/util/Size;\s+invoke-direct[^\n]+\s+'
                         r'sput-object[^\n]+->KEY_XPAN_MAIN_PICTURE_SIZE:')
        app = (CAMERA / "smali/com/oplus/camera/configure/CameraConfig.smali").read_text(encoding="utf-8")
        front = app.split(".method public static getSizeConfigValue(", 1)[1].split(".end method", 1)[0]
        self.assertIn("if-eqz p1, :cond_0", front)
        self.assertIn('const-string p0, "com.oplus.high.picturesize_front"', front)

    def test_original_sdk_serialization(self):
        helper = (SDK / "smali/com/oplus/ocs/camera/producer/info/CameraConfigHelper.smali").read_text(encoding="utf-8")
        typed = helper.split(".method public static getConfigValue(Lcom/oplus/ocs/camera/common/util/CameraConfigBase$Key;", 1)[1].split(".end method", 1)[0]
        for evidence in ("->getName()Ljava/lang/String;", "->getType()Ljava/lang/Class;",
                         "const-class v3, Landroid/util/Size;", 'const-string v6, "[Xx]"',
                         "-><init>(II)V", "const-class v3, Ljava/lang/Boolean;",
                         "Ljava/lang/Byte;->parseByte(Ljava/lang/String;)B",
                         "const-class v3, [I", 'const-string v4, ","'):
            self.assertIn(evidence, typed)
        self.assertEqual(re.split("[Xx]", "4096x1512"), ["4096", "1512"])

    def test_original_native_consumer_flow_and_shared_cache(self):
        folder = SDK / "smali/com/oplus/ocs/camera"

        def body(path, declaration):
            return path.read_text(encoding="utf-8").split(declaration, 1)[1].split(".end method", 1)[0]

        aps = body(folder / "consumer/apsAdapter/adapter/ApsUtils.smali",
                   ".method public static getVendorTagConfig(Ljava/lang/String;)Ljava/lang/String;")
        self.assertIn("->sVendorTagConfigMap:Ljava/util/HashMap;", aps)
        self.assertIn("Ljava/util/HashMap;->get(Ljava/lang/Object;)Ljava/lang/Object;", aps)
        self.assertNotIn("CameraConfig", aps)
        coui = body(folder / "ipusdk/processunit/filter/list/CouiUtil.smali",
                    ".method public static isHasselbladStyle()Z")
        for evidence in ('"com.oplus.use.hasselblad.style.support"', "->sHasselbladStyle:Ljava/lang/String;",
                         "->getVendorTagConfig(Ljava/lang/String;)Ljava/lang/String;",
                         "Ljava/lang/Byte;->parseByte(Ljava/lang/String;)B", "if-lez v0, :cond_1"):
            self.assertIn(evidence, coui)
        self.assertLess(coui.index("if-eqz v0, :cond_0"), coui.index("->getVendorTagConfig"))
        self.assertIn("sput-object v0", coui)  # A raw style override alone would poison the shared cache.
        common_file = folder / "ipusdk/processunit/filter/list/CommonUtil.smali"
        integer = body(common_file, ".method public static getConfigIntValue(Ljava/lang/String;I)I")
        self.assertIn("->getConfigStringValue(Ljava/lang/String;)Ljava/lang/String;", integer)
        self.assertIn("Ljava/lang/Integer;->parseInt(Ljava/lang/String;)I", integer)
        self.assertIn("return p1", integer)
        self.assertIn("->getVendorTagConfig(Ljava/lang/String;)Ljava/lang/String;",
                      body(common_file, ".method public static getConfigStringValue(Ljava/lang/String;)Ljava/lang/String;"))
        manager = folder / "ipusdk/processunit/filter/list/FilterGroupManager.smali"
        builder = body(manager, ".method private static initHasselbladXpanFilterGroup()V")
        for evidence in ("->isHasselbladStyle()Z", '"com.oplus.xpan.mode.version"', "const/4 v1, 0x2",
                         "->getConfigIntValue(Ljava/lang/String;I)I", "const/4 v2, 0x3", "if-ne v2, v0, :cond_1",
                         '"Delta400.3dl.rgb.bin"', '"fuji_cc.bin"', '"fuji-nc.bin"', '"fuji-proNegHi.bin"',
                         '"800t.bin"', '"blackandwhite_HC_normal.bin"', '"portra400_normal.bin"', '"positive_normal.bin"'):
            self.assertIn(evidence, builder)
        parent = body(manager, ".method public static initBeforeMonoFilter(Landroid/content/Context;)V")
        self.assertLess(parent.index("->initFilterGroup()V"), parent.index("->initHasselbladXpanFilterGroup()V"))
        self.assertLess(parent.index("->initHasselbladXpanFilterGroup()V"), parent.index("->initNightFilterGroup()V"))
        self.assertNotIn("->isHasselbladStyle()Z", parent)
        self.assertNotIn("if-", parent)  # No wider scope/parent bypass needed.
        pipeline = folder / "ipusdk/processunit/filter/FilterProcessUnitSDK.smali"
        self.assertIn("->init(Landroid/content/Context;ZZ)V", body(pipeline, ".method private getAllFilters()V"))
        generated = body(pipeline, ".method private generateFilterList(Ljava/util/HashMap;)V")
        self.assertIn('const-string v0, "xpan"', generated)
        self.assertIn("->sHasselbladXpanFilterGroup:", generated)
        app = body(CAMERA / "smali_classes12/com/oplus/camera/filter/FilterGroupManager.smali",
                   ".method public static initFilterGroupFromIpu(Landroid/content/Context;Ljava/lang/String;Ljava/util/List;)V")
        self.assertIn('"xpan"', app)
        self.assertIn("->sHasselbladXpanFilterGroup:", app)

    def test_receipt_whitelist_and_source_boundaries(self):
        overrides = json.loads(RECEIPT.read_text(encoding="utf-8"))["required_java_getter_overrides"]
        expected = {
            "com.oplus.feature.xpan.mode.support": ("boolean", True),
            "com.oplus.xpan.mode.version": ("int", 3),
            "com.oplus.xpan.legacy.ui.style": ("boolean", False),
            "com.oplus.xpan.main.picturesize": ("size", [4096, 1512]),
            "com.oplus.xpan.crop.region.main.portrait": ("int[]", [780, 0, 1512, 4096]),
            "com.oplus.xpan.crop.region.main.landscape": ("int[]", [0, 780, 4096, 1512]),
        }
        self.assertEqual(len(overrides), 6)
        self.assertEqual({o["key"]: (o["java_type"], o["value"]) for o in overrides}, expected)
        source = HOOK.read_text(encoding="utf-8")
        literals = set(re.findall(r'"(com\.oplus\.(?:feature\.xpan|xpan)\.[^"]+)"', source))
        self.assertEqual(literals, set(expected))
        self.assertLess(source.index("b.resolve(loader);"), source.index("h.register();"))
        self.assertLess(source.index("h.register();"), source.index("h.active = true;"))
        self.assertIn("Class.forName(name, false, loader)", source)
        self.assertIn("getDeclaredMethod(name, params)", source)
        self.assertIn("visibility != access", source)
        self.assertIn("true, Modifier.PRIVATE", source)
        self.assertIn("Modifier.isStatic(flags) != statik", source)
        self.assertIn("m.getReturnType() != result", source)
        self.assertEqual(source.count("p.setResult("), 1)
        self.assertIn("if (!gate.enabled() || !active) return false;", source)
        for forbidden in ("getDeclaredField", "getField(", "setStatic", "CameraConfigBase\", true",
                          "FeatureConfigureAdapter", "SharedPreferences", "FileOutputStream", "System.load",
                          "invokeOriginalMethod", "setThrowable(", ".clear(", "XpanHook"):
            self.assertNotIn(forbidden, source)

    def test_fake_runtime_six_keys_front_sdk_gate_and_array_clones(self):
        self.runtime("success")

    def test_fake_native_xpan_scope_cached_style_gate_and_cleanup(self):
        self.runtime("native")

    def test_fail_closed_profile_and_gate(self):
        self.runtime("refuse")

    def test_registration_failure_rolls_back_every_position(self):
        for position in range(1, 12):
            with self.subTest(position=position):
                self.runtime("rollback", None, position)

    def test_null_registration_handle_rolls_back(self):
        self.runtime("null-handle", None, 11)

    def test_failed_unhook_residual_inactive_blocks_retry(self):
        self.runtime("residual", None, 11)

    def test_whole_abi_resolved_before_any_registration(self):
        app = "com/oplus/camera/configure/CameraConfig.java"
        sdk = "com/oplus/ocs/camera/producer/info/CameraConfigHelper.java"
        key = "com/oplus/ocs/camera/common/util/CameraConfigBase.java"
        groups = "com/oplus/ocs/camera/ipusdk/processunit/filter/list/FilterGroupManager.java"
        coui = "com/oplus/ocs/camera/ipusdk/processunit/filter/list/CouiUtil.java"
        aps = "com/oplus/ocs/camera/consumer/apsAdapter/adapter/ApsUtils.java"
        variants = [
            (app, "public static boolean getConfigBooleanValue", "public static int getConfigBooleanValue", "return false;", "return 0;"),
            (app, "public static boolean b(", "private static boolean b("),
            (app, "i(int fallback, String key)", "i(String key, int fallback)"),
            (app, "public static Size getSizeConfigValue", "public Size getSizeConfigValue"),
            (app, "public static int[] h(", "public static int[] missing_h("),
            (app, "public static int[] getConfigIntArrayValue", "public static int getConfigIntArrayValue", "return null;", "return 0;"),
            (sdk, "public static String getConfigValue(String key)", "private static String getConfigValue(String key)"),
            (sdk, "Object getConfigValue(Key<?> key, Object fallback)", "Object getConfigValue(Key<?> key, String fallback)"),
            (key, "public String getName()", "private String getName()"),
            (key, "public Class<T> getType()", "private Class<T> getType()"),
            (groups, "private static void initHasselbladXpanFilterGroup()", "public static void initHasselbladXpanFilterGroup()"),
            (groups, "private static void initHasselbladXpanFilterGroup()", "private void initHasselbladXpanFilterGroup()"),
            (groups, "private static void initHasselbladXpanFilterGroup()", "private static void initHasselbladXpanFilterGroup(String unexpected)"),
            (coui, "public static boolean isHasselbladStyle()", "public static boolean isHasselbladStyle(String unexpected)"),
            (coui, "public static boolean isHasselbladStyle()", "private static boolean isHasselbladStyle()"),
            (aps, "public static String getVendorTagConfig(String key)", "public String getVendorTagConfig(String key)"),
            (aps, "public static String getVendorTagConfig(String key)", "private static String getVendorTagConfig(String key)"),
        ]
        for index, variant in enumerate(variants):
            with self.subTest(variant=index):
                path, old, new, *extra = variant
                content = FIXTURES[path].replace(old, new)
                if extra:
                    content = content.replace(*extra)
                folder = self.folder / f"variant-{index}"
                files = self.write_sources(folder / "sources", {path: content})
                classes = folder / "classes"
                classes.mkdir()
                cp = os.pathsep.join(map(str, (self.real, self.fake)))
                self.run_command([self.javac, "-encoding", "UTF-8", "-source", "8", "-target", "8",
                                  "-classpath", cp, "-d", str(classes), *map(str, files)])
                # Populate valid remaining targets without recompiling classes which call the mutated ABI.
                for original in (self.fake / "com/oplus").rglob("*.class"):
                    relative = original.relative_to(self.fake)
                    dest = classes / relative
                    if not dest.exists():
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(original, dest)
                self.runtime("resolve-fail", classes)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera-root", type=Path, default=CAMERA)
    parser.add_argument("--sdk-root", type=Path, default=SDK)
    parser.add_argument("--android-jar", type=Path, default=ANDROID)
    parser.add_argument("--xposed-jar", type=Path, default=XPOSED)
    args = parser.parse_args()
    CAMERA, SDK = args.camera_root.resolve(), args.sdk_root.resolve()
    ANDROID, XPOSED = args.android_jar.resolve(), args.xposed_jar.resolve()
    unittest.main(argv=[__file__], verbosity=2)
