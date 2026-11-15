"""C17 getter-only geometry regression tests: real API compile plus offlineFake JVM.

Decoded C17 inputs are read as text only. No device, network, APK build or native code.
Fakes are generated only under ROOT/_tmp and never become production build inputs.
The final ABI has eight methods plus two read-only instance metadata fields and six hooks.
"""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TRUSTED_CWD = ROOT / "tools"
DECODED = ROOT / "_tmp/decoded-PJX110_17.0.1.201"
HOOK = ROOT / "hook/XpanC17Config.java"
ANDROID = Path(os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME") or
               Path.home() / "AppData/Local/Android/Sdk") / "platforms/android-35/android.jar"
XPOSED = ROOT / "_tmp/api-82.jar"
APP_SOURCE = "com/oplus/camera/configure/CameraConfig.java"
SDK_SOURCE = "com/oplus/ocs/camera/producer/info/CameraConfigHelper.java"
KEY_SOURCE = "com/oplus/ocs/camera/common/util/CameraConfigBase.java"
SURFACE_SOURCE = "com/oplus/ocs/camera/producer/mode/XpanMode.java"
CONFIG_SOURCE = "com/oplus/ocs/camera/common/parameter/SdkCameraDeviceConfig.java"
PICTURE = "com.oplus.xpan.main.picturesize"
PORTRAIT = "com.oplus.xpan.crop.region.main.portrait"
LANDSCAPE = "com.oplus.xpan.crop.region.main.landscape"

# Production is compiled against REAL Android/Xposed jars first. These deliberately
# separate classes supply only the subsequent JVM runtime, not a production ABI jar.
FIXTURES = {
    "android/util/Size.java": r'''package android.util;
public final class Size {
  private final int width, height;
  public Size(int w, int h) { width=w; height=h; }
  public int getWidth() { return width; }
  public int getHeight() { return height; }
  public String toString() { return width + "x" + height; }
}''',
    "android/util/Pair.java": r'''package android.util;
public final class Pair<F,S> {
  public final F first; public final S second;
  public Pair(F f, S s) { first=f; second=s; }
}''',
    "de/robv/android/xposed/XC_MethodHook.java": r'''package de.robv.android.xposed;
import java.lang.reflect.*;
import java.util.*;
public abstract class XC_MethodHook {
  protected void beforeHookedMethod(MethodHookParam p) throws Throwable {}
  protected void afterHookedMethod(MethodHookParam p) throws Throwable {}
  public static final class MethodHookParam {
    public Member method; public Object thisObject; public Object[] args;
    private Object result; private Throwable error; boolean returnEarly;
    private final HashMap<String,Object> extras=new HashMap<String,Object>();
    public Object getResult() { return result; }
    public void setResult(Object value) {
      if (XposedBridge.mutationProbe!=null) XposedBridge.mutationProbe.check(this);
      result=value; error=null; returnEarly=true; XposedBridge.mutations++;
    }
    public Throwable getThrowable() { return error; }
    public boolean hasThrowable() { return error!=null; }
    public void setObjectExtra(String key,Object value) { extras.put(key,value); }
    public Object getObjectExtra(String key) { return extras.get(key); }
    public int extraCount() { return extras.size(); }
    void originalResult(Object value) { result=value; }
    void originalFailure(Throwable failure) { error=failure; }
  }
  public final class Unhook {
    final Method method;
    Unhook(Method m) { method=m; }
    public void unhook() {
      XposedBridge.unhookOrder.add(method);
      if (XposedBridge.failUnhooks.contains(method)) throw new IllegalStateException("injected unhook failure");
      XposedBridge.hooks.remove(method);
    }
  }
}''',
    "de/robv/android/xposed/XposedBridge.java": r'''package de.robv.android.xposed;
import java.lang.reflect.*;
import java.util.*;
import de.robv.android.xposed.XC_MethodHook.MethodHookParam;
public final class XposedBridge {
  public static final LinkedHashMap<Method,XC_MethodHook> hooks=new LinkedHashMap<Method,XC_MethodHook>();
  public static final ArrayList<Method> registrations=new ArrayList<Method>(), unhookOrder=new ArrayList<Method>();
  public static final HashSet<Method> failUnhooks=new HashSet<Method>();
  public static int attempts, failAt, nullAt, mutations;
  public static boolean addOnNull, failLog;
  public interface RegistrationProbe { void check(Method m,XC_MethodHook cb) throws Throwable; }
  public interface MutationProbe { void check(MethodHookParam p); }
  public static RegistrationProbe registrationProbe;
  public static MutationProbe mutationProbe;
  private static final ThreadLocal<MethodHookParam> current=new ThreadLocal<MethodHookParam>();
  public static MethodHookParam current() { return current.get(); }
  public static XC_MethodHook.Unhook hookMethod(Member member,XC_MethodHook cb) {
    Method m=(Method)member; attempts++;
    if (attempts==failAt) throw new IllegalStateException("injected pre-registration failure");
    if (attempts==nullAt && !addOnNull) return null;
    hooks.put(m,cb); registrations.add(m);
    if (registrationProbe!=null) try { registrationProbe.check(m,cb); }
      catch (Throwable t) { throw new AssertionError(t); }
    if (attempts==nullAt) return null; // Registered, but its handle was lost.
    return cb.new Unhook(m);
  }
  public static void log(String text) { if (failLog) throw new IllegalStateException("injected logger failure"); }
  private static MethodHookParam param(Method m,Object receiver,Object[] args) {
    // Equal reflection Members need not be the identical Method object.
    MethodHookParam p=new MethodHookParam(); p.method=m; p.thisObject=receiver; p.args=args; return p;
  }
  private static void restore(MethodHookParam prior) { if (prior==null) current.remove(); else current.set(prior); }
  public static MethodHookParam beforeOnly(Method m,XC_MethodHook cb,Object... args) throws Throwable {
    MethodHookParam p=param(m,null,args), prior=current.get(); current.set(p);
    try { cb.beforeHookedMethod(p); cb.afterHookedMethod(p); return p; }
    finally { restore(prior); }
  }
  public static MethodHookParam open(Method m,Object receiver,Object... args) throws Throwable {
    MethodHookParam p=param(m,receiver,args), prior=current.get(); current.set(p);
    try { XC_MethodHook cb=hooks.get(m); if(cb!=null)cb.beforeHookedMethod(p); return p; }
    finally { restore(prior); }
  }
  public static void close(MethodHookParam p) throws Throwable {
    MethodHookParam prior=current.get(); current.set(p);
    try { XC_MethodHook cb=hooks.get((Method)p.method); if(cb!=null)cb.afterHookedMethod(p); }
    finally { restore(prior); }
  }
  public static Object call(Method m,Object receiver,Object... args) throws Throwable {
    MethodHookParam p=param(m,receiver,args), prior=current.get(); current.set(p);
    try {
      XC_MethodHook cb=hooks.get(m); if(cb!=null)cb.beforeHookedMethod(p);
      if(!p.returnEarly) {
        try { p.originalResult(m.invoke(receiver,p.args)); }
        catch(InvocationTargetException t) { p.originalFailure(t.getCause()); }
      }
      if(cb!=null)cb.afterHookedMethod(p);
      if(p.hasThrowable())throw p.getThrowable();
      return p.getResult();
    } finally { restore(prior); }
  }
}''',
    "fake/Probe.java": r'''package fake;
public final class Probe {
  public static int appInit,sdkInit,keyInit,baseInit,surfaceInit,configInit;
  public static int originals,appSizeReads,appArrayReads,appDefaultReads,typedReads,rawReads,surfaceReads;
  public static boolean failAppInit,failSdkInit,failKeyInit,failSurfaceInit,failConfigInit;
  public static final RuntimeException initFailure=new IllegalStateException("injected target initializer failure");
  public static RuntimeException appFailure,typedFailure,rawFailure,surfaceFailure;
  public interface MetadataProbe { void read(Object key,int count); }
  public static volatile MetadataProbe nameProbe,typeProbe;
  public static volatile Runnable typedEntry,rawEntry,surfaceEntry,surfaceExit;
  public static Object surfaceKey,lastSurfaceList,lastSurfacePair;
  public static String surfaceRaw;
  public static void base() { baseInit++; throw new AssertionError("CameraConfigBase static Key/cache read"); }
}''',
    "fake/ConfigSource.java": r'''package fake;
import android.util.Size;
import java.util.*;
public final class ConfigSource {
  public static final String PICTURE="com.oplus.xpan.main.picturesize",
    PORTRAIT="com.oplus.xpan.crop.region.main.portrait", LANDSCAPE="com.oplus.xpan.crop.region.main.landscape",
    FRONT="com.oplus.high.picturesize_front", WIDE="com.oplus.xpan.wide.picturesize",
    OTHER_ARRAY="com.oplus.photo.crop.region", MASTER="com.oplus.feature.master.mode.support";
  public static final Map<String,String> appValues=new LinkedHashMap<String,String>(), sdkValues=new LinkedHashMap<String,String>();
  public static final Map<String,Size> appSizes=new LinkedHashMap<String,Size>();
  public static final Map<String,int[]> appArrays=new LinkedHashMap<String,int[]>();
  public static final Map<String,Object> appCache=new LinkedHashMap<String,Object>(), sdkCache=new LinkedHashMap<String,Object>();
  public static final Object configurationJson=new Object();
  public static final List<String> filterGroup=new ArrayList<String>(Arrays.asList("stock", "other-module-lut"));
  public static final Size nativeSensorSize=new Size(10000,7500);
  private static void geometry(int w,int h,String p,String l) {
    String size=w+"X"+h;
    appValues.put(PICTURE,size); sdkValues.put(PICTURE,size); appSizes.put(PICTURE,new Size(w,h));
    appValues.put(PORTRAIT,p); sdkValues.put(PORTRAIT,p); appArrays.put(PORTRAIT,parseArray(p));
    appValues.put(LANDSCAPE,l); sdkValues.put(LANDSCAPE,l); appArrays.put(LANDSCAPE,parseArray(l));
    appCache.put(PICTURE,appSizes.get(PICTURE)); appCache.put(PORTRAIT,appArrays.get(PORTRAIT));
    appCache.put(LANDSCAPE,appArrays.get(LANDSCAPE)); sdkCache.put(PICTURE,size);
  }
  public static void reset() {
    appValues.clear(); sdkValues.clear(); appSizes.clear(); appArrays.clear(); appCache.clear(); sdkCache.clear();
    geometry(4096,1512,"780,0,1512,4096","0,780,4096,1512");
    for(String[] e:new String[][]{{FRONT,"3200X2400"},{WIDE,"4000X1500"},
      {OTHER_ARRAY,"17,23,31,47"},{MASTER,"1"},{"com.oplus.feature.xpan.mode.support","0"},
      {"com.oplus.xpan.mode.version","2"},{"com.oplus.xpan.legacy.ui.style","1"},
      {"com.oplus.use.hasselblad.style.support","0"},{"com.oplus.photo.exposure","77"},
      {"com.oplus.other.module.setting","other-module-value"}}) {
      appValues.put(e[0],e[1]); sdkValues.put(e[0],e[1]);
    }
    appSizes.put(FRONT,new Size(3200,2400)); appSizes.put(WIDE,new Size(4000,1500));
    appArrays.put(OTHER_ARRAY,parseArray("17,23,31,47"));
    appCache.put("ordinary-cache",new Object()); sdkCache.put("ordinary-cache",new Object());
  }
  // Verified OP UpdateHelper source values, not values injected by the config hook.
  public static void updateHelper() { geometry(7872,2912,"360,64,2912,7872","64,360,7872,2912"); }
  public static int[] parseArray(String raw) {
    String[] parts=raw.split(","); int[] values=new int[parts.length];
    for(int i=0;i<parts.length;i++)values[i]=Integer.parseInt(parts[i]); return values;
  }
  public static Size parseSize(String raw) {
    String[] parts=raw.toUpperCase(java.util.Locale.ROOT).split("[Xx]");
    return parts.length==2 ? new Size(Integer.parseInt(parts[0]),Integer.parseInt(parts[1])) : null;
  }
}''',
    APP_SOURCE: r'''package com.oplus.camera.configure;
import android.util.Size;
import fake.*;
import de.robv.android.xposed.XposedBridge;
public class CameraConfig {
  static { Probe.appInit++; if(Probe.failAppInit)throw Probe.initFailure; }
  public static final Object cache=ConfigSource.appCache;
  public static String w(String key,boolean ignored) { Probe.originals++; return ConfigSource.appValues.get(key); }
  public static Size x(String key) { Probe.originals++; return ConfigSource.appSizes.get(key); }
  public static boolean getConfigBooleanValue(String key) { Probe.originals++; return "1".equals(ConfigSource.appValues.get(key)); }
  public static int m(int fallback,String key) {
    Probe.originals++; String raw=ConfigSource.appValues.get(key); return raw==null ? fallback : Integer.parseInt(raw);
  }
  public static Size getSizeConfigValue(String key, boolean front) {
    Probe.originals++; Probe.appSizeReads++; if(Probe.appFailure!=null)throw Probe.appFailure;
    return ConfigSource.appSizes.get(front ? ConfigSource.FRONT : key);
  }
  public static int[] l(String key, int[] fallback) throws Throwable {
    Probe.originals++; Probe.appDefaultReads++; if(Probe.appFailure!=null)throw Probe.appFailure;
    int[] value=(int[])XposedBridge.call(CameraConfig.class.getDeclaredMethod("getConfigIntArrayValue",String.class),null,key);
    return value==null || value.length==0 ? fallback : value;
  }
  public static int[] getConfigIntArrayValue(String key) {
    Probe.originals++; Probe.appArrayReads++; if(Probe.appFailure!=null)throw Probe.appFailure;
    return ConfigSource.appArrays.get(key);
  }
}''',
    KEY_SOURCE: r'''package com.oplus.ocs.camera.common.util;
import android.util.Size;
import fake.Probe;
public class CameraConfigBase {
  static { Probe.base(); }
  public static final Key<Size> KEY_XPAN_MAIN_PICTURE_SIZE=null;
  public static final class Key<T> {
    static { Probe.keyInit++; if(Probe.failKeyInit)throw Probe.initFailure; }
    private String mKeyName; private Class<T> mType; private int names,types;
    Key(String n,Class<T> t) { mKeyName=n; mType=t; }
    public String getName() { if(Probe.nameProbe!=null)Probe.nameProbe.read(this,++names); return mKeyName; }
    public Class<T> getType() { if(Probe.typeProbe!=null)Probe.typeProbe.read(this,++types); return mType; }
  }
}''',
    SDK_SOURCE: r'''package com.oplus.ocs.camera.producer.info;
import android.util.Size;
import fake.*;
import de.robv.android.xposed.XposedBridge;
import com.oplus.ocs.camera.common.util.CameraConfigBase.Key;
public class CameraConfigHelper {
  static { Probe.sdkInit++; if(Probe.failSdkInit)throw Probe.initFailure; }
  public static final Object cache=ConfigSource.sdkCache;
  public static Object getConfigValue(Key<?> key, Object fallback) throws Throwable {
    Probe.originals++; Probe.typedReads++;
    if(Probe.typedEntry!=null)Probe.typedEntry.run(); if(Probe.typedFailure!=null)throw Probe.typedFailure;
    // Real C17 reads name/raw outside its parser's Exception -> fallback boundary.
    String raw=(String)XposedBridge.call(CameraConfigHelper.class.getDeclaredMethod("getConfigValue",String.class),null,key.getName());
    if(raw==null)return fallback;
    try {
      Class<?> type=key.getType();
      if(type==String.class)return raw;
      if(type==Size.class) { Size parsed=ConfigSource.parseSize(raw); return parsed==null ? fallback : parsed; }
      if(type==Size[].class) {
        String[] parts=raw.split(","); Size[] sizes=new Size[parts.length];
        for(int i=0;i<parts.length;i++)sizes[i]=ConfigSource.parseSize(parts[i]); return sizes;
      }
      if(type==Integer.class)return Integer.valueOf(raw);
      if(type==Float.class)return Float.valueOf(raw);
      if(type==Double.class)return Double.valueOf(raw);
      if(type==Long.class)return Long.valueOf(raw);
      if(type==Boolean.class)return Boolean.valueOf(Byte.parseByte(raw)>0);
      if(type==int[].class)return ConfigSource.parseArray(raw);
      if(type==float[].class) {
        String[] parts=raw.split(","); float[] values=new float[parts.length];
        for(int i=0;i<parts.length;i++)values[i]=Float.parseFloat(parts[i]); return values;
      }
      if(type==String[].class)return raw.split(",");
    } catch(Exception ignored) { return fallback; }
    return fallback;
  }
  public static String getConfigValue(String key) {
    Probe.originals++; Probe.rawReads++; if(Probe.rawEntry!=null)Probe.rawEntry.run();
    if(Probe.rawFailure!=null)throw Probe.rawFailure;
    return ConfigSource.sdkValues.get(key);
  }
}''',
    CONFIG_SOURCE: r'''package com.oplus.ocs.camera.common.parameter;
import fake.Probe;
public final class SdkCameraDeviceConfig {
  static { Probe.configInit++; if(Probe.failConfigInit)throw Probe.initFailure; }
}''',
    SURFACE_SOURCE: r'''package com.oplus.ocs.camera.producer.mode;
import android.util.*;
import java.util.*;
import fake.*;
import de.robv.android.xposed.XposedBridge;
import com.oplus.ocs.camera.common.util.CameraConfigBase.Key;
import com.oplus.ocs.camera.common.parameter.SdkCameraDeviceConfig;
import com.oplus.ocs.camera.producer.info.CameraConfigHelper;
public class XpanMode {
  static { Probe.surfaceInit++; if(Probe.failSurfaceInit)throw Probe.initFailure; }
  public List<Pair<Size,Size>> getSurfaceSize(SdkCameraDeviceConfig config, String usage, String cameraType, String cameraId, int format) throws Throwable {
    Probe.originals++; Probe.surfaceReads++; if(Probe.surfaceEntry!=null)Probe.surfaceEntry.run();
    if(Probe.surfaceFailure!=null)throw Probe.surfaceFailure;
    Size main=(Size)XposedBridge.call(CameraConfigHelper.class.getDeclaredMethod("getConfigValue",Key.class,Object.class),null,Probe.surfaceKey,null);
    Probe.surfaceRaw=(String)XposedBridge.call(CameraConfigHelper.class.getDeclaredMethod("getConfigValue",String.class),null,ConfigSource.PICTURE);
    // Reduced original tuple dependency: RAW/DOL first is the same typed main Size;
    // second remains the original sensor result. No core stream resizer is simulated.
    boolean raw=usage!=null && usage.contains("raw");
    Pair<Size,Size> pair=new Pair<Size,Size>(main,raw ? ConfigSource.nativeSensorSize : main);
    List<Pair<Size,Size>> result=new ArrayList<Pair<Size,Size>>(); result.add(pair);
    Probe.lastSurfacePair=pair; Probe.lastSurfaceList=result;
    if(Probe.surfaceExit!=null)Probe.surfaceExit.run(); return result;
  }
}''',
}

HARNESS = r'''package com.xpanport.hook;
import android.util.*;
import fake.*;
import de.robv.android.xposed.*;
import de.robv.android.xposed.XC_MethodHook.MethodHookParam;
import java.lang.reflect.*;
import java.net.*;
import java.io.File;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
public final class C17ConfigCheck {
  static final String PICTURE=ConfigSource.PICTURE,PORTRAIT=ConfigSource.PORTRAIT,LANDSCAPE=ConfigSource.LANDSCAPE;
  static final String OP="7872X2912", VERSION="7.013.51";
  static final int BINDINGS=10,HOOKS=6;
  static final AtomicInteger checks=new AtomicInteger();
  static void require(boolean yes,String why) { checks.incrementAndGet(); if(!yes)throw new AssertionError(why); }
  interface Work { void run() throws Throwable; }
  static void unchecked(Work work) { try { work.run(); } catch(Throwable t) { throw new AssertionError(t); } }
  static void sameFailure(Work work,Throwable expected,String why) throws Throwable {
    try { work.run(); throw new AssertionError(why+" was suppressed"); }
    catch(Throwable t) { require(t==expected,why+" was replaced: "+t); }
  }
  static final class Gate implements XpanC17Config.Gate {
    volatile boolean enabled=true,fail,error;
    final AtomicInteger calls=new AtomicInteger(); volatile Runnable action;
    public boolean enabled() {
      calls.incrementAndGet(); Runnable next=action;
      if(next!=null) { action=null; next.run(); }
      if(error)throw new AssertionError("gate error");
      if(fail)throw new IllegalStateException("gate failure"); return enabled;
    }
  }
  static final class TargetLoader extends URLClassLoader {
    final String denied;
    TargetLoader(String folder,String denied) throws Exception {
      super(new URL[]{new File(folder).toURI().toURL()},C17ConfigCheck.class.getClassLoader()); this.denied=denied;
    }
    protected Class<?> loadClass(String name,boolean resolve) throws ClassNotFoundException {
      if(!name.startsWith("com.oplus."))return super.loadClass(name,resolve);
      if(name.equals(denied))throw new ClassNotFoundException("injected missing ABI class: "+name);
      synchronized(getClassLoadingLock(name)) {
        Class<?> c=findLoadedClass(name); if(c==null)c=findClass(name); if(resolve)resolveClass(c); return c;
      }
    }
  }
  static Class<?> app,sdk,keyClass,xpan,configClass;
  static Method size,arrayDefault,array,typed,raw,surface,lowerSize,lowerRaw,bool,integer;
  static Constructor<?> keyConstructor;
  static Object surfaceReceiver,deviceConfig;
  static void bind(ClassLoader loader) throws Exception {
    app=Class.forName("com.oplus.camera.configure.CameraConfig",false,loader);
    sdk=Class.forName("com.oplus.ocs.camera.producer.info.CameraConfigHelper",false,loader);
    keyClass=Class.forName("com.oplus.ocs.camera.common.util.CameraConfigBase$Key",false,loader);
    xpan=Class.forName("com.oplus.ocs.camera.producer.mode.XpanMode",false,loader);
    configClass=Class.forName("com.oplus.ocs.camera.common.parameter.SdkCameraDeviceConfig",false,loader);
    size=app.getDeclaredMethod("getSizeConfigValue",String.class,boolean.class);
    arrayDefault=app.getDeclaredMethod("l",String.class,int[].class);
    array=app.getDeclaredMethod("getConfigIntArrayValue",String.class);
    typed=sdk.getDeclaredMethod("getConfigValue",keyClass,Object.class);
    raw=sdk.getDeclaredMethod("getConfigValue",String.class);
    surface=xpan.getDeclaredMethod("getSurfaceSize",configClass,String.class,String.class,String.class,int.class);
    lowerSize=app.getDeclaredMethod("x",String.class); lowerRaw=app.getDeclaredMethod("w",String.class,boolean.class);
    bool=app.getDeclaredMethod("getConfigBooleanValue",String.class); integer=app.getDeclaredMethod("m",int.class,String.class);
    keyConstructor=keyClass.getDeclaredConstructor(String.class,Class.class); keyConstructor.setAccessible(true);
  }
  static Object key(String name,Class<?> type) throws Exception { return keyConstructor.newInstance(name,type); }
  static Object call(Method m,Object... args) throws Throwable { return XposedBridge.call(m,null,args); }
  static void sizeIs(Object value,int w,int h) {
    require(value instanceof Size,"not Size: "+value); Size s=(Size)value;
    require(s.getWidth()==w && s.getHeight()==h,"wrong Size: "+s+", expected "+w+"x"+h);
  }
  static Object[] targetArgs(Method m) {
    if(m.equals(size))return new Object[]{PICTURE,false};
    if(m.equals(arrayDefault))return new Object[]{PORTRAIT,new int[]{9}};
    if(m.equals(array))return new Object[]{LANDSCAPE};
    if(m.equals(typed))return new Object[]{null,null};
    if(m.equals(surface))return new Object[]{null,"capture_raw_dol","rear_main","0",32};
    return new Object[]{PICTURE};
  }
  static void initUntouched() {
    require(Probe.appInit==0 && Probe.sdkInit==0 && Probe.keyInit==0 && Probe.baseInit==0 &&
      Probe.surfaceInit==0 && Probe.configInit==0,"ABI resolve/register initialized target classes");
  }
  static XpanC17Config.InstallResult install(ClassLoader loader,Gate g) { return XpanC17Config.install(loader,VERSION,g); }
  static Gate ready(ClassLoader loader) throws Throwable {
    bind(loader); Gate g=new Gate();
    XposedBridge.registrationProbe=(m,cb)-> {
      int writes=XposedBridge.mutations;
      MethodHookParam p=XposedBridge.beforeOnly(m,cb,targetArgs(m));
      require(XposedBridge.mutations==writes && p.extraCount()==0,"callback active before complete registration");
      initUntouched();
    };
    XpanC17Config.InstallResult r=install(loader,g);
    require(r.installed && r.validatedBindings==BINDINGS && r.registeredHooks==HOOKS,r.detail);
    require(r.detail!=null && !r.detail.isEmpty(),"empty install diagnostic");
    require(XposedBridge.hooks.size()==HOOKS && g.calls.get()==0,"wrong hook count/install consulted Gate");
    require(XposedBridge.registrations.equals(Arrays.asList(size,arrayDefault,array,surface,typed,raw)),"unexpected hook ABI or order");
    initUntouched();
    require(install(loader,g)==r && XposedBridge.attempts==HOOKS,"same Gate install not idempotent");
    require(!install(loader,new Gate()).installed && XposedBridge.attempts==HOOKS,"another Gate overlapped loader");
    XposedBridge.registrationProbe=null;
    final ThreadLocal<Integer> lastGateRead=new ThreadLocal<Integer>();
    XposedBridge.mutationProbe=p-> {
      Integer last=lastGateRead.get(); int count=g.calls.get();
      require(count>(last==null ? 0 : last),"setResult without fresh live Gate check");
      require(g.enabled && !g.fail && !g.error,"setResult after Gate revocation/failure");
      lastGateRead.set(count);
    };
    ConfigSource.updateHelper(); return g;
  }
  static void rawFixed() throws Throwable { require("4096x1512".equals(call(raw,PICTURE)),"SDK frame leaked outside call"); }
  static void sourceGeometry() {
    sizeIs(ConfigSource.appSizes.get(PICTURE),7872,2912);
    require(Arrays.equals(ConfigSource.appArrays.get(PORTRAIT),new int[]{360,64,2912,7872}),"OP portrait fixture mismatch");
    require(Arrays.equals(ConfigSource.appArrays.get(LANDSCAPE),new int[]{64,360,7872,2912}),"OP landscape fixture mismatch");
    require(OP.equals(ConfigSource.appValues.get(PICTURE)) && OP.equals(ConfigSource.sdkValues.get(PICTURE)),"source maps overwritten");
  }
  static void geometry(ClassLoader loader) throws Throwable {
    ready(loader);
    Map<String,String> appValues=new LinkedHashMap<String,String>(ConfigSource.appValues),sdkValues=new LinkedHashMap<String,String>(ConfigSource.sdkValues);
    Map<String,Object> appCache=new LinkedHashMap<String,Object>(ConfigSource.appCache),sdkCache=new LinkedHashMap<String,Object>(ConfigSource.sdkCache);
    Object json=ConfigSource.configurationJson; List<String> filters=new ArrayList<String>(ConfigSource.filterGroup);
    sourceGeometry(); int originals=Probe.originals;
    sizeIs(call(size,PICTURE,false),4096,1512); sizeIs(call(typed,key(PICTURE,Size.class),null),4096,1512); rawFixed();
    require(Probe.originals==originals,"verified getter override did not short-circuit source");
    int[] fallback={41,42}; Set<int[]> identities=Collections.newSetFromMap(new IdentityHashMap<int[],Boolean>());
    for(String name:new String[]{PORTRAIT,LANDSCAPE})for(Method getter:new Method[]{arrayDefault,array}) {
      int[] expected=name.equals(PORTRAIT) ? new int[]{780,0,1512,4096} : new int[]{0,780,4096,1512};
      int[] first=(int[])(getter.equals(array) ? call(getter,name) : call(getter,name,fallback));
      require(Arrays.equals(first,expected) && first!=fallback && first!=ConfigSource.appArrays.get(name),"array aliases source/fallback");
      require(identities.add(first),"array aliases another getter/orientation"); first[0]=-999;
      int[] second=(int[])(getter.equals(array) ? call(getter,name) : call(getter,name,fallback));
      require(Arrays.equals(second,expected) && identities.add(second),"array is not independently cloned per invocation");
      if(getter.equals(arrayDefault))require(Arrays.equals((int[])call(getter,name,(Object)null),expected),"null fallback changed override");
    }
    require(Arrays.equals(fallback,new int[]{41,42}),"fallback contents mutated"); sourceGeometry();
    for(String name:new String[]{PICTURE,null,ConfigSource.WIDE,"other"}) {
      int before=XposedBridge.mutations; Object result=call(size,name,true);
      require(result==ConfigSource.appSizes.get(ConfigSource.FRONT),"front branch not original"); sizeIs(result,3200,2400);
      require(XposedBridge.mutations==before,"front=true mutation");
    }
    sizeIs(call(lowerSize,PICTURE),7872,2912);
    require(OP.equals(call(lowerRaw,PICTURE,false)),"lower config/JSON source changed instead of last getter");
    for(String name:new String[]{null,"unknown",PICTURE+".extra","COM.OPLUS.XPAN.MAIN.PICTURESIZE",
      "com.oplus.xpan.crop.region.main.portrait.extra",ConfigSource.WIDE,ConfigSource.OTHER_ARRAY,
      ConfigSource.MASTER,"com.oplus.feature.xpan.mode.support","com.oplus.xpan.mode.version",
      "com.oplus.xpan.legacy.ui.style","com.oplus.use.hasselblad.style.support","com.oplus.other.module.setting"}) {
      int before=XposedBridge.mutations; int[] expected=ConfigSource.appArrays.get(name);
      require(call(size,name,false)==ConfigSource.appSizes.get(name),"non-whitelist Size changed: "+name);
      require(call(array,name)==expected,"non-whitelist array identity changed: "+name);
      require(call(arrayDefault,name,fallback)==(expected==null ? fallback : expected),"ordinary array/fallback changed: "+name);
      require(Objects.equals(call(raw,name),ConfigSource.sdkValues.get(name)),"non-whitelist raw changed: "+name);
      Object value=call(typed,key(name,String.class),fallback);
      require(Objects.equals(value,ConfigSource.sdkValues.containsKey(name) ? ConfigSource.sdkValues.get(name) : fallback),"ordinary typed config changed: "+name);
      require(XposedBridge.mutations==before,"non-XPAN/exact-name mutation: "+name);
    }
    require(Boolean.TRUE.equals(call(bool,ConfigSource.MASTER)),"other module boolean lost");
    require(Boolean.FALSE.equals(call(bool,"com.oplus.feature.xpan.mode.support")),"C16 support override leaked");
    require(((Integer)call(integer,-1,"com.oplus.xpan.mode.version"))==2,"C16 version override leaked");
    require(((Integer)call(integer,-1,"com.oplus.photo.exposure"))==77,"ordinary int changed");
    int before=XposedBridge.mutations;
    require(call(array,PICTURE)==null && call(arrayDefault,PICTURE,fallback)==fallback,"Size accepted by array getter");
    require(call(size,PORTRAIT,false)==null && call(size,LANDSCAPE,false)==null,"crop accepted by Size getter");
    for(String name:new String[]{PORTRAIT,LANDSCAPE}) {
      require(Objects.equals(call(raw,name),ConfigSource.sdkValues.get(name)),"SDK raw crop overridden");
      require(Arrays.equals((int[])call(typed,key(name,int[].class),null),ConfigSource.appArrays.get(name)),"unsupported SDK crop Key overridden");
    }
    require(XposedBridge.mutations==before,"wrong getter/type boundary mutation");
    require(ConfigSource.appValues.equals(appValues) && ConfigSource.sdkValues.equals(sdkValues),"source config map changed");
    require(ConfigSource.appCache.equals(appCache) && ConfigSource.sdkCache.equals(sdkCache),"ordinary/geometry caches changed");
    require(app.getField("cache").get(null)==ConfigSource.appCache && sdk.getField("cache").get(null)==ConfigSource.sdkCache,"cache field replaced");
    require(ConfigSource.configurationJson==json && ConfigSource.filterGroup.equals(filters),"JSON/filter group changed");
    sourceGeometry(); require(Probe.baseInit==0,"static Key field was read");
  }
  static void gateAndTypes(ClassLoader loader) throws Throwable {
    Gate g=ready(loader); Object fallback=new Object();
    int before=XposedBridge.mutations;
    require(OP.equals(call(typed,key(PICTURE,String.class),fallback)),"same-name String acquired Size serialization");
    Size[] sizes=(Size[])call(typed,key(PICTURE,Size[].class),fallback); require(sizes.length==1,"original Size[] parse"); sizeIs(sizes[0],7872,2912);
    String[] strings=(String[])call(typed,key(PICTURE,String[].class),fallback); require(Arrays.equals(strings,new String[]{OP}),"original String[] parse changed");
    for(Class<?> type:new Class<?>[]{Object.class,int[].class,float[].class,Integer.class,Float.class,Double.class,
      Long.class,Boolean.class,int.class,boolean.class,Void.class,null})
      require(call(typed,key(PICTURE,type),fallback)==fallback,"wrong/unsupported Key type acquired override: "+type);
    for(String name:new String[]{null,"unknown",PICTURE+".extra"})
      require(call(typed,key(name,Size.class),fallback)==fallback,"unknown/null name lost original fallback");
    sizeIs(call(typed,key(ConfigSource.WIDE,Size.class),fallback),4000,1500);
    try { call(typed,null,fallback); throw new AssertionError("null Key original failure suppressed"); }
    catch(NullPointerException expected) { require(true,"null Key original NPE"); }
    require(XposedBridge.mutations==before,"wrong typed/null Key mutation"); rawFixed();
    for(int state=0;state<3;state++) {
      g.enabled=state!=0; g.fail=state==1; g.error=state==2; before=XposedBridge.mutations;
      sizeIs(call(size,PICTURE,false),7872,2912);
      require(call(array,PORTRAIT)==ConfigSource.appArrays.get(PORTRAIT),"Gate array original identity");
      require(call(arrayDefault,LANDSCAPE,new int[]{3})==ConfigSource.appArrays.get(LANDSCAPE),"Gate default-array original identity");
      require(OP.equals(call(raw,PICTURE)),"off/throwing Gate raw override");
      sizeIs(call(typed,key(PICTURE,Size.class),fallback),7872,2912);
      require(XposedBridge.mutations==before,"off/throwing Gate setResult");
    }
    g.enabled=true; g.fail=false; g.error=false;
    RuntimeException failure=new IllegalStateException("original SDK raw failure"); Probe.rawFailure=failure;
    sameFailure(()->call(typed,key(PICTURE,String.class),fallback),failure,"original typed raw throwable");
    sameFailure(()->call(raw,"unknown"),failure,"original foreign raw throwable");
    g.enabled=false; sameFailure(()->call(raw,PICTURE),failure,"original disabled raw throwable");
    Probe.rawFailure=null; g.enabled=true; rawFixed();
    Probe.typedFailure=failure;
    sameFailure(()->call(typed,key(PICTURE,String.class),fallback),failure,"original typed throwable");
    g.enabled=false; sameFailure(()->call(typed,key(PICTURE,Size.class),fallback),failure,"disabled typed throwable");
    Probe.typedFailure=null; g.enabled=true; rawFixed();
    Probe.appFailure=failure; g.enabled=false;
    sameFailure(()->call(size,PICTURE,false),failure,"original app Size throwable");
    sameFailure(()->call(array,PORTRAIT),failure,"original app crop throwable");
    sameFailure(()->call(arrayDefault,LANDSCAPE,new int[0]),failure,"original app fallback crop throwable");
    g.enabled=true; sameFailure(()->call(size,ConfigSource.WIDE,false),failure,"foreign app throwable");
    Probe.appFailure=null; rawFixed();
    Probe.nameProbe=(k,n)-> { throw failure; }; XposedBridge.failLog=true;
    sameFailure(()->call(typed,key(PICTURE,Size.class),fallback),failure,"original metadata name throwable");
    Probe.nameProbe=null; XposedBridge.failLog=false; rawFixed();
    Probe.typeProbe=(k,n)-> { throw failure; }; before=XposedBridge.mutations;
    require(call(typed,key(PICTURE,Size.class),fallback)==fallback,"original parser's metadata Exception fallback suppressed");
    require(XposedBridge.mutations==before,"unavailable metadata mutation"); Probe.typeProbe=null; rawFixed();
    Error error=new AssertionError("original metadata type Error"); Probe.typeProbe=(k,n)-> { throw error; };
    sameFailure(()->call(typed,key(PICTURE,Size.class),fallback),error,"original metadata type Error");
    Probe.typeProbe=null; rawFixed(); sourceGeometry();
  }
  static void metadata(ClassLoader loader) throws Throwable {
    Gate g=ready(loader);
    for(boolean type:new boolean[]{false,true})for(boolean throwing:new boolean[]{false,true}) {
      g.enabled=true; g.fail=false; Object k=key(PICTURE,Size.class); int before=XposedBridge.mutations;
      Probe.MetadataProbe probe=(value,n)-> { if(value==k && n==1) { g.enabled=false; g.fail=throwing; } };
      if(type)Probe.typeProbe=probe; else Probe.nameProbe=probe;
      sizeIs(call(typed,k,null),7872,2912);
      require(XposedBridge.mutations==before,"stale Gate after first "+(type ? "type" : "name")+" metadata read");
      Probe.typeProbe=null; Probe.nameProbe=null; g.enabled=true; g.fail=false; rawFixed();
    }
    // Once the final Gate is checked, read-only validated metadata fields must
    // replace any second callback-capable getName/getType invocation.
    for(boolean type:new boolean[]{false,true}) {
      Object k=key(PICTURE,Size.class); final int[] reads={0};
      Probe.MetadataProbe probe=(value,n)-> { if(value==k) { reads[0]++; if(n>1)g.enabled=false; } };
      if(type)Probe.typeProbe=probe; else Probe.nameProbe=probe;
      sizeIs(call(typed,k,null),4096,1512);
      require(reads[0]==1 && g.enabled,"metadata called again after final Gate");
      Probe.typeProbe=null; Probe.nameProbe=null; g.enabled=true; rawFixed();
    }
    for(boolean type:new boolean[]{false,true}) {
      Object k=key(PICTURE,Size.class); int before=XposedBridge.mutations;
      Probe.MetadataProbe probe=(value,n)-> {
        if(value==k)unchecked(()-> { int writes=XposedBridge.mutations;
          require(OP.equals(call(raw,PICTURE)),"raw override entered Key metadata read");
          require(XposedBridge.mutations==writes,"metadata raw setResult");
        });
      };
      if(type)Probe.typeProbe=probe; else Probe.nameProbe=probe;
      sizeIs(call(typed,k,null),4096,1512);
      require(XposedBridge.mutations==before+1,"typed metadata raw changed extra result");
      Probe.typeProbe=null; Probe.nameProbe=null; rawFixed();
    }
    for(boolean type:new boolean[]{false,true}) {
      Object k=key(PICTURE,Size.class); RuntimeException failure=new IllegalStateException("metadata unavailable once");
      Probe.MetadataProbe probe=(value,n)-> { if(value==k && n==1)throw failure; };
      if(type)Probe.typeProbe=probe; else Probe.nameProbe=probe;
      int before=XposedBridge.mutations; sizeIs(call(typed,k,null),7872,2912);
      require(XposedBridge.mutations==before,"metadata exception let original raw acquire override");
      Probe.typeProbe=null; Probe.nameProbe=null; rawFixed();
    }
    // Gate false at final permission, but re-enabled by the ORIGINAL typed getter.
    g.enabled=false; Probe.typedEntry=()-> { g.enabled=true; Probe.typedEntry=null; };
    int before=XposedBridge.mutations; sizeIs(call(typed,key(PICTURE,Size.class),null),7872,2912);
    require(XposedBridge.mutations==before,"re-enabled original typed raw lost passthrough"); rawFixed();
    Object outer=key(PICTURE,Size.class);
    Probe.nameProbe=(value,n)-> { if(value==outer && n==1)unchecked(()-> {
      require(OP.equals(call(typed,key(PICTURE,String.class),null)),"metadata nested wrong type lost original");
      require(OP.equals(call(raw,PICTURE)),"metadata nested after erased outer prior");
    }); };
    sizeIs(call(typed,outer,null),4096,1512); Probe.nameProbe=null; rawFixed();
  }
  static void argsAndFinalBoundary(ClassLoader loader) throws Throwable {
    Gate g=ready(loader); int before;
    g.action=()->XposedBridge.current().args[0]=ConfigSource.WIDE; before=XposedBridge.mutations;
    sizeIs(call(size,PICTURE,false),4000,1500); require(XposedBridge.mutations==before,"Gate changed Size key but setResult");
    g.action=()->XposedBridge.current().args[1]=Boolean.TRUE; before=XposedBridge.mutations;
    sizeIs(call(size,PICTURE,false),3200,2400); require(XposedBridge.mutations==before,"Gate changed front but setResult");
    g.action=()-> { MethodHookParam p=XposedBridge.current(); p.args=p.args.clone(); }; before=XposedBridge.mutations;
    sizeIs(call(size,PICTURE,false),7872,2912); require(XposedBridge.mutations==before,"Gate replaced Size args array but setResult");
    for(Method getter:new Method[]{array,arrayDefault}) {
      g.action=()->XposedBridge.current().args[0]=ConfigSource.OTHER_ARRAY; before=XposedBridge.mutations;
      Object result=getter.equals(array) ? call(getter,PORTRAIT) : call(getter,PORTRAIT,new int[]{8});
      require(result==ConfigSource.appArrays.get(ConfigSource.OTHER_ARRAY),"Gate changed array key but override returned");
      require(XposedBridge.mutations==before,"Gate changed array key setResult");
    }
    // beforeOnly isolates the default getter's boundary from a fresh, legitimate
    // inner getConfigIntArrayValue call that its original implementation may make.
    for(Runnable change:new Runnable[]{()->XposedBridge.current().args[1]=new int[]{77},
      ()->{ MethodHookParam p=XposedBridge.current(); p.args=p.args.clone(); },
      ()->XposedBridge.current().args=null}) {
      g.action=change; before=XposedBridge.mutations;
      XposedBridge.beforeOnly(arrayDefault,XposedBridge.hooks.get(arrayDefault),PORTRAIT,new int[]{8});
      require(XposedBridge.mutations==before,"Gate changed fallback/args identity but default getter setResult");
    }
    g.action=()->XposedBridge.current().args[0]="com.oplus.xpan.mode.version"; before=XposedBridge.mutations;
    require("2".equals(call(raw,PICTURE)),"raw final key not rechecked"); require(XposedBridge.mutations==before,"raw changed-key mutation");
    g.action=()-> { MethodHookParam p=XposedBridge.current(); p.args=p.args.clone(); }; before=XposedBridge.mutations;
    require(OP.equals(call(raw,PICTURE)),"raw final args array identity not rechecked"); require(XposedBridge.mutations==before,"raw changed-args mutation");
    Object replacement=key(PICTURE,String.class);
    g.action=()->XposedBridge.current().args[0]=replacement; before=XposedBridge.mutations;
    require(OP.equals(call(typed,key(PICTURE,Size.class),null)),"typed replaced Key acquired Size"); require(XposedBridge.mutations==before,"typed replaced-Key mutation"); rawFixed();
    for(Runnable change:new Runnable[]{()->XposedBridge.current().args[1]=new Object(),
      ()->{ MethodHookParam p=XposedBridge.current(); p.args=p.args.clone(); }}) {
      g.action=change; before=XposedBridge.mutations; sizeIs(call(typed,key(PICTURE,Size.class),null),7872,2912);
      require(XposedBridge.mutations==before,"typed changed args/fallback mutation"); rawFixed();
    }
    final Object retyped=key(PICTURE,Size.class);
    g.action=()->unchecked(()-> { Field field=keyClass.getDeclaredField("mType"); field.setAccessible(true); field.set(retyped,String.class); }); before=XposedBridge.mutations;
    require(OP.equals(call(typed,retyped,null)),"same Key changed type was not rechecked"); require(XposedBridge.mutations==before,"changed metadata type mutation"); rawFixed();
    final Object renamed=key(PICTURE,Size.class);
    g.action=()->unchecked(()-> { Field field=keyClass.getDeclaredField("mKeyName"); field.setAccessible(true); field.set(renamed,ConfigSource.WIDE); }); before=XposedBridge.mutations;
    sizeIs(call(typed,renamed,null),4000,1500); require(XposedBridge.mutations==before,"changed metadata name mutation"); rawFixed();
    for(int state=0;state<3;state++) {
      final int current=state;
      g.action=()-> { if(current==0)g.enabled=false; else if(current==1)g.fail=true; else throw new AssertionError("Gate action error"); };
      before=XposedBridge.mutations; sizeIs(call(typed,key(PICTURE,Size.class),null),7872,2912);
      require(XposedBridge.mutations==before,"final Gate action revocation/throw mutated result");
      g.enabled=true; g.fail=false; rawFixed();
    }
    prepareSurface();
    Method equalRaw=sdk.getDeclaredMethod("getConfigValue",String.class);
    require(equalRaw.equals(raw) && equalRaw!=raw,"reflection test needs equal distinct raw Method");
    for(boolean nativeScope:new boolean[]{false,true}) {
      final MethodHookParam[] frame={null}; final boolean scope=nativeScope;
      g.action=()->unchecked(()-> {
        frame[0]=scope ? XposedBridge.open(surface,surfaceReceiver,deviceConfig,"capture_raw_dol","rear_main","0",32)
          : XposedBridge.open(typed,null,key(PICTURE,String.class),null);
      });
      before=XposedBridge.mutations;
      try { require(OP.equals(call(equalRaw,PICTURE)),"raw Gate-entered passthrough/surface scope not rechecked"); }
      finally { if(frame[0]!=null)XposedBridge.close(frame[0]); }
      require(XposedBridge.mutations==before,"raw final scope setResult"); rawFixed();
    }
    sourceGeometry();
  }
  static void nesting(ClassLoader loader) throws Throwable {
    Gate g=ready(loader); Object outer=key(PICTURE,String.class);
    Probe.nameProbe=(value,n)-> { if(value==outer && n==1)unchecked(()-> {
      require(OP.equals(call(typed,key(PICTURE,String.class),null)),"nested wrong-type result changed");
      sizeIs(call(typed,key(PICTURE,Size.class),null),4096,1512);
      require(OP.equals(call(raw,PICTURE)),"nested successful Size erased outer passthrough");
      RuntimeException failure=new IllegalStateException("nested original failure"); Probe.typedFailure=failure;
      try { sameFailure(()->call(typed,key(PICTURE,String.class),null),failure,"nested typed throwable"); }
      finally { Probe.typedFailure=null; }
      require(OP.equals(call(raw,PICTURE)),"nested throwable erased outer passthrough");
    }); };
    require(OP.equals(call(typed,outer,null)),"outer nested typed result changed"); Probe.nameProbe=null; rawFixed();
    RuntimeException failure=new IllegalStateException("outer original raw failure"); Probe.rawFailure=failure;
    Probe.rawEntry=()-> { g.enabled=false; g.fail=true; };
    sameFailure(()->call(typed,key(PICTURE,String.class),null),failure,"outer throwable with Gate off");
    Probe.rawFailure=null; Probe.rawEntry=null; g.enabled=true; g.fail=false; rawFixed();
    final CountDownLatch inside=new CountDownLatch(1),release=new CountDownLatch(1);
    final AtomicReference<Throwable> workerFailure=new AtomicReference<Throwable>();
    final Object workerKey=key(PICTURE,String.class);
    Thread worker=new Thread(()-> {
      try { require(OP.equals(call(typed,workerKey,null)),"worker original typed result"); }
      catch(Throwable t) { workerFailure.set(t); }
    });
    Probe.nameProbe=(value,n)-> { if(value==workerKey && n==2) {
      inside.countDown(); try { if(!release.await(5,TimeUnit.SECONDS))throw new AssertionError("typed worker release timeout"); }
      catch(InterruptedException e) { throw new AssertionError(e); }
    } };
    worker.start();
    try { require(inside.await(5,TimeUnit.SECONDS),"worker did not enter original typed frame"); rawFixed(); }
    finally { release.countDown(); worker.join(5000); Probe.nameProbe=null; }
    require(!worker.isAlive() && workerFailure.get()==null,"typed passthrough shared across threads: "+workerFailure.get());
    rawFixed();
  }
  static void prepareSurface() throws Exception {
    if(surfaceReceiver==null)surfaceReceiver=xpan.getDeclaredConstructor().newInstance();
    if(deviceConfig==null)deviceConfig=configClass.getDeclaredConstructor().newInstance();
    Probe.surfaceKey=key(PICTURE,Size.class);
  }
  static Object surfaceCall(String usage,int format) throws Throwable {
    return XposedBridge.call(surface,surfaceReceiver,deviceConfig,usage,"rear_main","0",format);
  }
  static void surfaceOriginal(String usage,int format) throws Throwable {
    int writes=XposedBridge.mutations; Object result=surfaceCall(usage,format);
    require(result==Probe.lastSurfaceList,"surface return identity replaced");
    List<?> pairs=(List<?>)result; require(pairs.size()==1 && pairs.get(0)==Probe.lastSurfacePair,"surface list/Pair replaced");
    Pair<?,?> pair=(Pair<?,?>)pairs.get(0); sizeIs(pair.first,7872,2912);
    if(usage!=null && usage.contains("raw"))require(pair.second==ConfigSource.nativeSensorSize,"RAW/DOL native tuple second changed");
    else require(pair.second==pair.first,"native non-RAW tuple identity changed");
    require(OP.equals(Probe.surfaceRaw),"surface raw getter acquired geometry override");
    require(XposedBridge.mutations==writes,"surface SDK getter/tuple mutation");
  }
  static void surfaceChecks(ClassLoader loader) throws Throwable {
    Gate g=ready(loader); prepareSurface();
    for(String usage:new String[]{"capture_raw_mfnr","capture_raw10_fullsize","raw_output","capture_raw","capture_yuv",
      "raw16_output","capture","capture_raw_dol","raw12_output","heic","preview","unknown",null})
      for(int format:new int[]{32,35,256})surfaceOriginal(usage,format);
    require(g.calls.get()==0,"surface exclusion unnecessarily evaluated live Gate"); rawFixed();
    Probe.surfaceEntry=()->unchecked(()-> {
      int writes=XposedBridge.mutations;
      sizeIs(call(typed,key(PICTURE,Size.class),null),7872,2912);
      require(OP.equals(call(typed,key(PICTURE,String.class),null)),"surface wrong-type raw changed");
      require(OP.equals(call(raw,PICTURE)),"surface direct raw changed");
      require(XposedBridge.mutations==writes,"SDK override in surface entry");
    });
    surfaceOriginal("capture_raw_dol",32); Probe.surfaceEntry=null; rawFixed();
    Probe.surfaceExit=()-> { g.enabled=false; g.fail=true; };
    surfaceOriginal("capture_yuv",35); Probe.surfaceExit=null; g.enabled=true; g.fail=false; rawFixed();
    RuntimeException failure=new IllegalStateException("original surface failure"); Probe.surfaceFailure=failure;
    Probe.surfaceEntry=()-> { g.enabled=false; g.fail=true; };
    sameFailure(()->surfaceCall("capture_raw_dol",32),failure,"surface original throwable");
    Probe.surfaceFailure=null; Probe.surfaceEntry=null; g.enabled=true; g.fail=false; rawFixed();
    Probe.surfaceEntry=()->unchecked(()-> {
      Probe.surfaceEntry=null; g.enabled=false;
      Probe.surfaceEntry=()-> { g.enabled=true; Probe.surfaceEntry=null; };
      surfaceOriginal("capture_raw",32);
      require(OP.equals(call(raw,PICTURE)),"nested surface removed outer prior depth");
      Probe.surfaceFailure=failure;
      try { sameFailure(()->surfaceCall("capture_raw_dol",32),failure,"nested surface throwable"); }
      finally { Probe.surfaceFailure=null; }
      require(OP.equals(call(raw,PICTURE)),"nested exceptional surface removed outer prior depth");
    });
    surfaceOriginal("capture_yuv",35); Probe.surfaceEntry=null; rawFixed();
    Object outer=key(PICTURE,String.class);
    Probe.nameProbe=(value,n)-> { if(value==outer && n==1)unchecked(()-> {
      surfaceOriginal("capture_raw_dol",32);
      require(OP.equals(call(raw,PICTURE)),"surface cleanup erased enclosing typed passthrough");
    }); };
    require(OP.equals(call(typed,outer,null)),"typed enclosing surface result changed"); Probe.nameProbe=null; rawFixed();
    final CountDownLatch inside=new CountDownLatch(1),release=new CountDownLatch(1);
    final AtomicReference<Throwable> workerFailure=new AtomicReference<Throwable>();
    Thread worker=new Thread(()-> {
      try {
        List<?> values=(List<?>)surfaceCall("capture_raw_dol",32); Pair<?,?> pair=(Pair<?,?>)values.get(0);
        sizeIs(pair.first,7872,2912); require(pair.second==ConfigSource.nativeSensorSize,"worker RAW native tuple changed");
        require(OP.equals(Probe.surfaceRaw),"worker surface raw override");
      } catch(Throwable t) { workerFailure.set(t); }
    });
    Probe.surfaceEntry=()-> { if(Thread.currentThread()==worker) {
      inside.countDown(); try { if(!release.await(5,TimeUnit.SECONDS))throw new AssertionError("surface worker release timeout"); }
      catch(InterruptedException e) { throw new AssertionError(e); }
    } };
    worker.start();
    try { require(inside.await(5,TimeUnit.SECONDS),"worker surface did not enter"); rawFixed(); }
    finally { release.countDown(); worker.join(5000); Probe.surfaceEntry=null; }
    require(!worker.isAlive() && workerFailure.get()==null,"surface ThreadLocal shared across threads: "+workerFailure.get());
    rawFixed(); sourceGeometry();
  }
  static void refusal(ClassLoader loader) throws Throwable {
    Gate g=new Gate(); g.fail=true;
    for(String version:new String[]{null,"","6.020.876","7.012.64","7.013.51-extra"," 7.013.51"}) {
      XpanC17Config.InstallResult r=XpanC17Config.install(loader,version,g);
      require(!r.installed && r.validatedBindings==0 && r.registeredHooks==0,"inexact profile accepted");
    }
    require(!XpanC17Config.install(null,VERSION,g).installed,"null loader accepted");
    require(!XpanC17Config.install(loader,VERSION,null).installed,"null Gate accepted");
    require(XposedBridge.attempts==0 && g.calls.get()==0,"refusal registered hooks/evaluated Gate"); initUntouched();
    require(install(loader,new Gate()).installed,"refusal poisoned later exact install"); initUntouched();
  }
  static void initialization(ClassLoader loader,String kind) throws Throwable {
    String name;
    if(kind.equals("app")) { Probe.failAppInit=true; name="com.oplus.camera.configure.CameraConfig"; }
    else if(kind.equals("sdk")) { Probe.failSdkInit=true; name="com.oplus.ocs.camera.producer.info.CameraConfigHelper"; }
    else if(kind.equals("key")) { Probe.failKeyInit=true; name="com.oplus.ocs.camera.common.util.CameraConfigBase$Key"; }
    else if(kind.equals("surface")) { Probe.failSurfaceInit=true; name="com.oplus.ocs.camera.producer.mode.XpanMode"; }
    else { Probe.failConfigInit=true; name="com.oplus.ocs.camera.common.parameter.SdkCameraDeviceConfig"; }
    ready(loader); initUntouched();
    try { Class.forName(name,true,loader); throw new AssertionError("initializer failure suppressed"); }
    catch(ExceptionInInitializerError expected) { require(expected.getCause()==Probe.initFailure,"target initializer throwable replaced"); }
    require(XposedBridge.mutations==0,"initializer failure caused geometry mutation");
    require(Probe.appInit+Probe.sdkInit+Probe.keyInit+Probe.surfaceInit+Probe.configInit==1,"unexpected extra target initialization");
    require(Probe.baseInit==0,"nested Key initialized outer static registry");
  }
  static void rollback(ClassLoader loader,String mode,int position,int residualPosition) throws Throwable {
    bind(loader); Gate g=new Gate(); boolean nullHandle=mode.startsWith("null"),residual=mode.equals("residual");
    XposedBridge.failAt=nullHandle ? 0 : position; XposedBridge.nullAt=nullHandle ? position : 0;
    XposedBridge.addOnNull=mode.equals("null-after"); XposedBridge.failLog=true;
    XposedBridge.registrationProbe=(m,cb)-> {
      int writes=XposedBridge.mutations;
      MethodHookParam p=XposedBridge.beforeOnly(m,cb,targetArgs(m));
      require(p.extraCount()==0 && XposedBridge.mutations==writes,"partial callback active"); initUntouched();
      if(residual && XposedBridge.attempts==residualPosition)XposedBridge.failUnhooks.add(m);
    };
    XpanC17Config.InstallResult r=install(loader,g); int remaining=(nullHandle || residual) ? 1 : 0;
    require(!r.installed && r.validatedBindings==BINDINGS && r.registeredHooks==remaining,r.detail);
    require(XposedBridge.attempts==position && XposedBridge.mutations==0 && g.calls.get()==0,"failed install not inactive"); initUntouched();
    ArrayList<Method> expected=new ArrayList<Method>(Arrays.asList(size,arrayDefault,array,surface,typed,raw).subList(0,position-1));
    Collections.reverse(expected); require(XposedBridge.unhookOrder.equals(expected),"rollback is not reverse registration order");
    int actualHooks=mode.equals("null-before") ? 0 : remaining;
    require(XposedBridge.hooks.size()==actualHooks,"residual/unknown hook accounting mismatch");
    if(remaining!=0) {
      ConfigSource.updateHelper(); sizeIs(call(size,PICTURE,false),7872,2912);
      require(call(array,PORTRAIT)==ConfigSource.appArrays.get(PORTRAIT),"residual array callback active");
      require(call(arrayDefault,LANDSCAPE,new int[]{8})==ConfigSource.appArrays.get(LANDSCAPE),"residual fallback-array callback active");
      require(OP.equals(call(raw,PICTURE)),"residual raw callback active");
      sizeIs(call(typed,key(PICTURE,Size.class),null),7872,2912); prepareSurface(); surfaceOriginal("capture_raw_dol",32);
      require(g.calls.get()==0 && XposedBridge.mutations==0,"residual callback consulted Gate/mutated");
      int attempts=XposedBridge.attempts;
      require(install(loader,g)==r && !install(loader,new Gate()).installed,"failed/uncertain install retry not rejected");
      require(XposedBridge.attempts==attempts,"overlapping registration after residual/null handle");
    } else {
      XposedBridge.failAt=0; XposedBridge.registrationProbe=null; XposedBridge.failLog=false;
      XpanC17Config.InstallResult retry=install(loader,g);
      require(retry.installed && retry.validatedBindings==BINDINGS && retry.registeredHooks==HOOKS,"clean rollback not retryable");
      require(XposedBridge.hooks.size()==HOOKS && XposedBridge.attempts==position+HOOKS,"clean retry hook count"); rawFixed();
    }
  }
  public static void main(String[] args) throws Throwable {
    String mode=args[0]; ConfigSource.reset();
    try(TargetLoader loader=new TargetLoader(args[1],mode.equals("resolve-fail") && args.length>2 ? args[2] : "")) {
      if(mode.equals("geometry"))geometry(loader);
      else if(mode.equals("gates-types"))gateAndTypes(loader);
      else if(mode.equals("metadata"))metadata(loader);
      else if(mode.equals("args"))argsAndFinalBoundary(loader);
      else if(mode.equals("nesting"))nesting(loader);
      else if(mode.equals("surface"))surfaceChecks(loader);
      else if(mode.equals("refuse"))refusal(loader);
      else if(mode.equals("initialization"))initialization(loader,args[2]);
      else if(mode.equals("resolve-fail")) {
        Gate g=new Gate(); XpanC17Config.InstallResult r=install(loader,g);
        require(!r.installed && r.validatedBindings<BINDINGS && r.registeredHooks==0,r.detail);
        require(XposedBridge.attempts==0 && g.calls.get()==0,"registration/Gate before whole ABI validation"); initUntouched();
      } else rollback(loader,mode,Integer.parseInt(args[2]),args.length>3 ? Integer.parseInt(args[3]) : 0);
      require(Probe.baseInit==0,"CameraConfigBase static initializer ran");
      System.out.println("C17 fake runtime "+mode+" passed: "+checks.get()+" assertions; device/coexistence/HAL unverified");
    } catch(Throwable failure) {
      System.out.println("C17 fake runtime "+mode+" failed: "+checks.get()+" assertions; device/coexistence/HAL unverified"); throw failure;
    }
  }
}'''


def smali_body(path, declaration):
    """Text-only extraction; no input classes/scripts are executed or imported."""
    return path.read_text(encoding="utf-8").split(declaration, 1)[1].split(".end method", 1)[0]


class C17AbiAndSourceTests(unittest.TestCase):
    def test_real_c17_smali_exact_bindings(self):
        cases = {
            "camera/smali/com/oplus/camera/configure/CameraConfig.smali": ("public static", [
                "getSizeConfigValue(Ljava/lang/String;Z)Landroid/util/Size;",
                "l(Ljava/lang/String;[I)[I", "getConfigIntArrayValue(Ljava/lang/String;)[I"]),
            "sdk/smali/com/oplus/ocs/camera/producer/info/CameraConfigHelper.smali": ("public static", [
                "getConfigValue(Lcom/oplus/ocs/camera/common/util/CameraConfigBase$Key;Ljava/lang/Object;)Ljava/lang/Object;",
                "getConfigValue(Ljava/lang/String;)Ljava/lang/String;"]),
            "sdk/smali/com/oplus/ocs/camera/common/util/CameraConfigBase$Key.smali": ("public", [
                "getName()Ljava/lang/String;", "getType()Ljava/lang/Class;"]),
            "sdk/smali/com/oplus/ocs/camera/producer/mode/XpanMode.smali": ("public", [
                "getSurfaceSize(Lcom/oplus/ocs/camera/common/parameter/SdkCameraDeviceConfig;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;I)Ljava/util/List;"]),
        }
        bindings = 0
        for relative, (access, descriptors) in cases.items():
            text = (DECODED / relative).read_text(encoding="utf-8")
            for descriptor in descriptors:
                with self.subTest(file=relative, descriptor=descriptor):
                    self.assertRegex(text, r"(?m)^\.method " + access + " " + re.escape(descriptor) + r"\s*$")
                bindings += 1
        self.assertEqual(bindings, 8)
        key = (DECODED / "sdk/smali/com/oplus/ocs/camera/common/util/CameraConfigBase$Key.smali").read_text(encoding="utf-8")
        self.assertRegex(key, r"(?m)^\.class public final Lcom/oplus/ocs/camera/common/util/CameraConfigBase\$Key;\s*$")
        for field in ("mKeyName:Ljava/lang/String;", "mType:Ljava/lang/Class;"):
            self.assertRegex(key, r"(?m)^\.field private " + re.escape(field) + r"\s*$")
        self.assertEqual(bindings + 2, 10)
        config = (DECODED / "sdk/smali/com/oplus/ocs/camera/common/parameter/SdkCameraDeviceConfig.smali").read_text(encoding="utf-8")
        self.assertRegex(config, r"(?m)^\.class public final Lcom/oplus/ocs/camera/common/parameter/SdkCameraDeviceConfig;\s*$")

    def test_real_app_front_and_default_array_order(self):
        app = DECODED / "camera/smali/com/oplus/camera/configure/CameraConfig.smali"
        front = smali_body(app, ".method public static getSizeConfigValue(Ljava/lang/String;Z)Landroid/util/Size;")
        self.assertIn("if-eqz p1, :cond_0", front)
        self.assertIn('const-string p0, "com.oplus.high.picturesize_front"', front)
        self.assertEqual(front.count("->x(Ljava/lang/String;)Landroid/util/Size;"), 2)
        default = smali_body(app, ".method public static l(Ljava/lang/String;[I)[I")
        self.assertIn("->getConfigIntArrayValue(Ljava/lang/String;)[I", default)
        self.assertLess(default.index("->getConfigIntArrayValue"), default.index("return-object p1"))
        array = smali_body(app, ".method public static getConfigIntArrayValue(Ljava/lang/String;)[I")
        for evidence in ("->w(Ljava/lang/String;Z)Ljava/lang/String;", 'const-string v1, ","',
                         "Ljava/lang/Integer;->parseInt(Ljava/lang/String;)I", "new-array v2, v1, [I"):
            self.assertIn(evidence, array)

    def test_real_sdk_size_key_serialization_and_original_failure_boundary(self):
        folder = DECODED / "sdk/smali/com/oplus/ocs/camera"
        helper = folder / "producer/info/CameraConfigHelper.smali"
        typed = smali_body(helper, ".method public static getConfigValue(Lcom/oplus/ocs/camera/common/util/CameraConfigBase$Key;Ljava/lang/Object;)Ljava/lang/Object;")
        for evidence in ("->getName()Ljava/lang/String;", "->getType()Ljava/lang/Class;",
                         "const-class v3, Landroid/util/Size;", 'const-string v6, "[Xx]"',
                         "-><init>(II)V", "const-class v3, [Landroid/util/Size;", "->parserSizeLists",
                         "const-class v3, Ljava/lang/Boolean;", "Ljava/lang/Byte;->parseByte(Ljava/lang/String;)B",
                         "const-class v3, [I", 'const-string v4, ","', ".catch Ljava/lang/Exception;", "return-object p1"):
            self.assertIn(evidence, typed)
        self.assertLess(typed.index("->getName()"), typed.index(":try_start_0"))
        self.assertLess(typed.index("->getConfigValue(Ljava/lang/String;)"), typed.index(":try_start_0"))
        raw = smali_body(helper, ".method public static getConfigValue(Ljava/lang/String;)Ljava/lang/String;")
        for evidence in ("->sVendorTagMap:Ljava/util/Map;", "Ljava/util/Map;->get(Ljava/lang/Object;)Ljava/lang/Object;",
                         "->getVendorTagConfig(Ljava/lang/String;)Ljava/lang/String;"):
            self.assertIn(evidence, raw)
        base = (folder / "common/util/CameraConfigBase.smali").read_text(encoding="utf-8")
        self.assertRegex(base, r'const-string v1, "com\.oplus\.xpan\.main\.picturesize"\s+'
                         r'const-class v2, Landroid/util/Size;\s+invoke-direct[^\n]+\s+'
                         r'sput-object[^\n]+->KEY_XPAN_MAIN_PICTURE_SIZE:')
        self.assertEqual(re.split("[Xx]", "4096x1512"), ["4096", "1512"])

    def test_real_surface_raw_dol_tuple_depends_on_same_size_key(self):
        path = DECODED / "sdk/smali/com/oplus/ocs/camera/producer/mode/XpanMode.smali"
        body = smali_body(path, ".method public getSurfaceSize(Lcom/oplus/ocs/camera/common/parameter/SdkCameraDeviceConfig;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;I)Ljava/util/List;")
        for usage in ("capture_raw_dol", "capture_raw", "raw_output", "raw16_output", "capture_yuv", "capture"):
            self.assertIn('"' + usage + '"', body)
        for block in (":pswitch_1", ":pswitch_2"):
            raw = body.split("\n    " + block + "\n", 1)[1].split("return-object v0", 1)[0]
            self.assertIn("->KEY_XPAN_MAIN_PICTURE_SIZE:", raw)
            self.assertIn("->getConfigValue(Lcom/oplus/ocs/camera/common/util/CameraConfigBase$Key;Ljava/lang/Object;)", raw)
            self.assertIn("invoke-direct {p2, p0, p1}, Landroid/util/Pair;-><init>", raw)
        self.assertIn("->getCustomRawMaxSize", body)
        self.assertIn("->getSizeByFormat", body)

    def test_production_three_key_and_no_global_native_mutation_boundaries(self):
        source = HOOK.read_text(encoding="utf-8")
        literals = set(re.findall(r'"(com\.oplus\.(?:xpan|feature\.xpan)\.[^"]+)"', source))
        self.assertEqual(literals, {PICTURE, PORTRAIT, LANDSCAPE})
        self.assertLess(source.index("b.resolve(loader);"), source.index("h.register();"))
        self.assertLess(source.index("h.register();"), source.index("h.active = true;"))
        self.assertIn("Class.forName(name, false, loader)", source)
        self.assertIn("Modifier.isStatic(flags)", source)
        self.assertIn("Modifier.isAbstract(flags)", source)
        self.assertIn("m.getReturnType() != result", source)
        self.assertGreaterEqual(source.count("ThreadLocal<Integer>"), 2)
        # Only the two verified private instance metadata fields may be resolved,
        # and only read. Cache/static-Key/filter/JSON field writes remain forbidden.
        field_names = set(re.findall(r'"(mKeyName|mType)"', source))
        self.assertEqual(field_names, {"mKeyName", "mType"})
        self.assertNotRegex(source, r"\b(?:keyNameValue|keyTypeValue)\.set(?:\w*)\s*\(")
        self.assertNotRegex(source, r"\bField\s*\.\s*set\s*\(")
        for forbidden in ("getField(", "setStatic", "JSONObject", "SharedPreferences",
                          "FileOutputStream", "System.load", "invokeOriginalMethod", "setThrowable(",
                          "FeatureConfigureAdapter", "FilterGroupManager", "CouiUtil", "ApsUtils", "XpanC16",
                          "setHalSurfaceSize", "setAppSurfaceSize"):
            self.assertNotIn(forbidden, source)
        self.assertNotRegex(source, r"new\s+Size\s*\(\s*8192\b")
        self.assertNotRegex(source, r"\bnative\s+\w+\s+\w+\s*\(")


def replace_java_method(content, declaration, replacement):
    """Replace an exact fake method, not any production source or decoded input."""
    if content.count(declaration) != 1:
        raise AssertionError(f"Non-unique fixture declaration: {declaration}")
    start = content.index(declaration)
    opening = content.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        if content[end] == "{":
            depth += 1
        elif content[end] == "}":
            depth -= 1
        end += 1
    return content[:start] + replacement + content[end:]


def abi_variants():
    # For EVERY binding: missing method, visibility, staticness, return type and
    # exact parameters. Abstract instance ABI and missing owner classes are extra.
    specs = [
        (APP_SOURCE, "public static Size getSizeConfigValue(String key, boolean front)", "Object",
         "public static Size getSizeConfigValue(boolean front, String key)"),
        (APP_SOURCE, "public static int[] l(String key, int[] fallback) throws Throwable", "Object",
         "public static int[] l(int[] fallback, String key) throws Throwable"),
        (APP_SOURCE, "public static int[] getConfigIntArrayValue(String key)", "Object",
         "public static int[] getConfigIntArrayValue(CharSequence key)"),
        (KEY_SOURCE, "public String getName()", "Object", "public String getName(String unexpected)"),
        (KEY_SOURCE, "public Class<T> getType()", "Object", "public Class<T> getType(Class<?> unexpected)"),
        (SDK_SOURCE, "public static Object getConfigValue(Key<?> key, Object fallback) throws Throwable", "String",
         "public static Object getConfigValue(Key<?> key, String fallback) throws Throwable"),
        (SDK_SOURCE, "public static String getConfigValue(String key)", "Object",
         "public static String getConfigValue(CharSequence key)"),
        (SURFACE_SOURCE, "public List<Pair<Size,Size>> getSurfaceSize(SdkCameraDeviceConfig config, String usage, String cameraType, String cameraId, int format) throws Throwable", "Object",
         "public List<Pair<Size,Size>> getSurfaceSize(SdkCameraDeviceConfig config, String usage, String cameraType, String cameraId, Integer format) throws Throwable"),
    ]
    variants = []
    for index, (path, declaration, return_type, parameters) in enumerate(specs):
        parts = declaration.split(" ", 2 if "public static " in declaration else 1)
        actual_return = parts[-1].split(" ", 1)[0]
        name = declaration.split("(", 1)[0].rsplit(" ", 1)[1]
        static = declaration.replace("public static ", "public ", 1) if "public static " in declaration else declaration.replace("public ", "public static ", 1).replace("Class<T>", "Class<?>")
        replacements = {
            "missing": declaration.replace(name + "(", "missing_" + name + "(", 1),
            "access": declaration.replace("public ", "private ", 1),
            "static": static,
            "return": declaration.replace(actual_return + " ", return_type + " ", 1),
            "parameters": parameters,
        }
        for kind, replacement in replacements.items():
            variants.append((f"binding-{index}-{name}-{kind}", path,
                             replace_java_method(FIXTURES[path], declaration, replacement + " { return null; }")))
        if "public static " not in declaration:
            abstract = replace_java_method(FIXTURES[path], declaration,
                                           declaration.replace("public ", "public abstract ", 1) + ";")
            if path == KEY_SOURCE:
                abstract = abstract.replace("public static final class Key<T>", "public static abstract class Key<T>")
            else:
                abstract = abstract.replace("public class XpanMode", "public abstract class XpanMode")
            variants.append((f"binding-{index}-{name}-abstract", path, abstract))
    for name, declaration, return_declaration in (
        ("mKeyName", "private String mKeyName;", "public String getName()"),
        ("mType", "private Class<T> mType;", "public Class<T> getType()"),
    ):
        base = FIXTURES[KEY_SOURCE]
        for kind, field in (("access", declaration.replace("private ", "public ", 1)),
                            ("type", "private Object " + name + ";"),
                            ("static", declaration.replace("private ", "private static ", 1).replace("Class<T>", "Class<?>")),
                            ("final", declaration.replace("private ", "private final ", 1))):
            content = base.replace(declaration, field)
            if kind in ("type", "static"):
                content = replace_java_method(content, return_declaration, return_declaration + " { return null; }")
            variants.append((f"field-{name}-{kind}", KEY_SOURCE, content))
        variants.append((f"field-{name}-missing", KEY_SOURCE, base.replace(name, "missing_" + name)))
    return variants


class C17OfflineFakeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.javac, cls.java = shutil.which("javac"), shutil.which("java")
        cls.commands = cls.runtime_attempts = cls.runtime_passed = cls.java_assertions = cls.variant_count = 0
        if not cls.javac or not cls.java:
            raise AssertionError("Local javac/java required; no tool download is attempted")
        for path in (HOOK, ANDROID, XPOSED):
            if not path.is_file():
                raise AssertionError(f"Required local source/API jar missing: {path}")
        if not TRUSTED_CWD.is_dir():
            raise AssertionError(f"Trusted repo tools cwd required: {TRUSTED_CWD}")
        (ROOT / "_tmp").mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix="c17-config-tests-", dir=ROOT / "_tmp")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.folder = Path(cls.temp.name)
        cls.real, cls.fake = cls.folder / "real", cls.folder / "offlineFake"
        cls.real.mkdir()
        cls.fake.mkdir()
        cp = os.pathsep.join(map(str, (ANDROID, XPOSED)))
        cls.run_command([cls.javac, "-encoding", "UTF-8", "-source", "8", "-target", "8",
                         "-bootclasspath", str(ANDROID), "-classpath", cp,
                         "-d", str(cls.real), str(HOOK)])
        files = cls.write_sources(cls.folder / "fixture-sources", FIXTURES)
        harness = cls.folder / "harness/com/xpanport/hook/C17ConfigCheck.java"
        harness.parent.mkdir(parents=True)
        harness.write_text(HARNESS, encoding="utf-8")
        cls.run_command([cls.javac, "-encoding", "UTF-8", "-source", "8", "-target", "8",
                         "-classpath", str(cls.real), "-d", str(cls.fake), *map(str, files), str(harness)])

    @classmethod
    def tearDownClass(cls):
        print(f"C17 offline summary: {cls.runtime_passed}/{cls.runtime_attempts} JVM cases passed; "
              f"{cls.java_assertions} assertions executed; {cls.variant_count} ABI mutants; "
              f"{cls.commands} local commands. Device/coexistence/HAL unverified.")

    @classmethod
    def run_command(cls, command):
        cls.commands += 1
        process = subprocess.run(command, cwd=TRUSTED_CWD, text=True, encoding="utf-8",
                                 errors="replace", capture_output=True, timeout=90)
        for count in re.findall(r"C17 fake runtime \S+ (?:passed|failed): (\d+) assertions", process.stdout):
            cls.java_assertions += int(count)
        if process.returncode:
            raise AssertionError(f"Local command failed ({process.returncode}):\n{process.stdout}\n{process.stderr}")
        return process.stdout

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
        type(self).runtime_attempts += 1
        # Neither REAL API jar is on the runtime classpath; no APK is built.
        cp = os.pathsep.join(map(str, (self.real, self.fake)))
        output = self.run_command([self.java, "-cp", cp, "com.xpanport.hook.C17ConfigCheck",
                                   mode, str(targets or self.fake), *map(str, args)])
        self.assertIn("passed:", output)
        type(self).runtime_passed += 1
        print(output.strip())

    def test_op_source_three_last_getters_front_array_clones_and_ordinary_caches(self):
        self.runtime("geometry")

    def test_sdk_wrong_types_null_unknown_keys_original_failures_and_live_gate(self):
        self.runtime("gates-types")

    def test_metadata_raw_reentry_exceptions_first_read_gateoff_and_no_post_gate_callback(self):
        self.runtime("metadata")

    def test_final_gate_args_key_metadata_and_passthrough_scope_revalidation(self):
        self.runtime("args")

    def test_typed_nested_success_exception_gateoff_and_thread_isolation(self):
        self.runtime("nesting")

    def test_sdk_surface_all_streams_raw_dol_tuple_gateoff_throwable_nesting_and_threads(self):
        self.runtime("surface")

    def test_exact_version_loader_and_gate_refusal(self):
        self.runtime("refuse")

    def test_static_initializers_not_run_by_resolve_register_or_inactive_callbacks(self):
        for kind in ("app", "sdk", "key", "surface", "config"):
            with self.subTest(kind=kind):
                self.runtime("initialization", None, kind)

    def test_registration_failure_at_every_position_reverse_rollback_and_clean_retry(self):
        for position in range(1, 7):
            with self.subTest(position=position):
                self.runtime("rollback", None, position)

    def test_null_handles_before_and_after_registration_always_block_unsafe_retry(self):
        for mode in ("null-before", "null-after"):
            for position in range(1, 7):
                with self.subTest(mode=mode, position=position):
                    self.runtime(mode, None, position)

    def test_every_removable_hook_unhook_failure_leaves_disabled_residual_and_blocks_retry(self):
        # Failure at the sixth registration leaves the first five available to
        # inject an unhook failure individually, including the surface/typed scopes.
        for residual_position in range(1, 6):
            with self.subTest(residual_position=residual_position):
                self.runtime("residual", None, 6, residual_position)

    def test_complete_abi_preflight_failure_matrix(self):
        cp = os.pathsep.join(map(str, (self.real, self.fake)))
        for index, (name, path, content) in enumerate(abi_variants()):
            with self.subTest(variant=name):
                folder = self.folder / f"abi-{index}"
                files = self.write_sources(folder / "sources", {path: content})
                classes = folder / "classes"
                classes.mkdir()
                self.run_command([self.javac, "-encoding", "UTF-8", "-source", "8", "-target", "8",
                                  "-classpath", cp, "-d", str(classes), *map(str, files)])
                type(self).variant_count += 1
                # Link otherwise valid targets without recompiling their callers
                # against the deliberately invalid ABI under test.
                for original in (self.fake / "com/oplus").rglob("*.class"):
                    relative = original.relative_to(self.fake)
                    destination = classes / relative
                    if not destination.exists():
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(original, destination)
                self.runtime("resolve-fail", classes)
        for name in ("com.oplus.camera.configure.CameraConfig",
                     "com.oplus.ocs.camera.producer.info.CameraConfigHelper",
                     "com.oplus.ocs.camera.common.util.CameraConfigBase$Key",
                     "com.oplus.ocs.camera.producer.mode.XpanMode",
                     "com.oplus.ocs.camera.common.parameter.SdkCameraDeviceConfig"):
            with self.subTest(missing_class=name):
                self.runtime("resolve-fail", None, name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decoded-root", type=Path, default=DECODED,
                        help="One C17 decode containing both camera/ and sdk/; no mixed SDK fixture")
    parser.add_argument("--android-jar", type=Path, default=ANDROID)
    parser.add_argument("--xposed-jar", type=Path, default=XPOSED)
    parser.add_argument("--mode", choices=("all", "abi", "offlineFake"), default="all")
    args = parser.parse_args()
    DECODED, ANDROID, XPOSED = args.decoded_root.resolve(), args.android_jar.resolve(), args.xposed_jar.resolve()
    suite = unittest.TestSuite()
    if args.mode in ("all", "abi"):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(C17AbiAndSourceTests))
    if args.mode in ("all", "offlineFake"):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(C17OfflineFakeTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
