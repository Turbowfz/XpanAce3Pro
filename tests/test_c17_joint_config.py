"""Offline production Core+Config joint checks; no device/priority-order/HAL claim.

Each production source is compiled independently against REAL Android/Xposed APIs.
Existing test fixtures supply only JVM targets; production callbacks are never copied.
Both default-same-priority registration arrangements are simulated, not device sorting.
"""
import ast
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TRUSTED_CWD = ROOT / "tools"


def config_fixtures():
    spec = importlib.util.spec_from_file_location("joint_config_fixtures", ROOT / "tests/test_c17_config.py")
    module = importlib.util.module_from_spec(spec)
    prior = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = prior
    return module


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise AssertionError("Existing fixture changed: " + old)
    return text.replace(old, new, 1)


def extend_class(text, addition):
    return text.rstrip()[:-1] + addition + "\n}\n"


def joint_fixtures(config):
    # Reuse the local fake classes and reflective create/register from the Core test.
    tree = ast.parse((ROOT / "tests/test_c17_hook.py").read_text(encoding="utf-8"))
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                  and n.name == "test_gate_and_nested_hook_frames")
    literals = {n.targets[0].id: ast.literal_eval(n.value) for n in method.body
                if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
                and n.targets[0].id in ("stubs", "harness")}
    helper = literals["harness"].split("  static MethodHookParam frame(", 1)[0] + "}\n"
    sdk = "com.oplus.ocs.camera.producer.mode.XpanMode.class"
    helper = replace_once(helper, 'put(b, "xpanMode", FakeMode.class);',
                          'put(b, "xpanMode", ' + sdk + ');')
    helper = replace_once(helper,
        'put(b, "formatByStream", FakeMode.class.getMethod("format", String.class, String.class, String.class));',
        'put(b, "formatByStream", ' + sdk + '.getMethod("getSurfaceFormatBySurfaceType", String.class, String.class, String.class));\n'
        '    put(b, "surfaceSize", ' + sdk + '.getMethod("getSurfaceSize", '
        'com.oplus.ocs.camera.common.parameter.SdkCameraDeviceConfig.class, String.class, String.class, String.class, int.class));')
    helper = replace_once(helper, 'XposedBridge.callbacks.clear(); call(h, "register");',
                          'call(h, "register");')
    fixtures = dict(config.FIXTURES)
    fixtures["android/util/Pair.java"] = literals["stubs"]["android/util/Pair.java"]
    fixtures["de/robv/android/xposed/XC_MethodHook.java"] = METHOD_HOOK
    fixtures["de/robv/android/xposed/XposedBridge.java"] = BRIDGE
    fixtures["fake/Probe.java"] = extend_class(fixtures["fake/Probe.java"], r'''
  public static int jointFormat=35,formatCalls;
  public static Object formatTyped;
  public static String formatRaw;
''')
    fixtures[config.SURFACE_SOURCE] = extend_class(fixtures[config.SURFACE_SOURCE], r'''
  // Old-layer probe only: the REAL Core calls this during its after callback.
  // Config-first retains the SDK scope then; Core-first has already restored it.
  public int getSurfaceFormatBySurfaceType(String usage,String camera,String id) throws Throwable {
    Probe.formatCalls++;
    Probe.formatTyped=XposedBridge.call(CameraConfigHelper.class.getDeclaredMethod("getConfigValue",Key.class,Object.class),null,Probe.surfaceKey,null);
    Probe.formatRaw=(String)XposedBridge.call(CameraConfigHelper.class.getDeclaredMethod("getConfigValue",String.class),null,ConfigSource.PICTURE);
    return Probe.jointFormat;
  }
''')
    fixtures["com/xpanport/hook/C17GateCheck.java"] = helper
    fixtures["com/xpanport/hook/C17JointCheck.java"] = HARNESS
    return fixtures


METHOD_HOOK = r'''package de.robv.android.xposed;
import java.lang.reflect.Member;
import java.util.*;
public abstract class XC_MethodHook {
  public static final int PRIORITY_DEFAULT=50;
  public final int priority;
  public XC_MethodHook() { this(PRIORITY_DEFAULT); }
  public XC_MethodHook(int priority) { this.priority=priority; }
  protected void beforeHookedMethod(MethodHookParam p) throws Throwable {}
  protected void afterHookedMethod(MethodHookParam p) throws Throwable {}
  public static final class MethodHookParam {
    public Member method; public Object thisObject; public Object[] args;
    private Object result; private Throwable error; boolean returnEarly;
    private final Map<String,Object> extras=new HashMap<>();
    public Object getResult() { return result; }
    public Throwable getThrowable() { return error; }
    public boolean hasThrowable() { return error!=null; }
    public Object getResultOrThrowable() throws Throwable { if(error!=null)throw error; return result; }
    public void setResult(Object value) { restore(value,null); XposedBridge.mutations++; }
    public void setThrowable(Throwable value) { restore(null,value); XposedBridge.mutations++; }
    public void setObjectExtra(String key,Object value) { extras.put(key,value); }
    public Object getObjectExtra(String key) { return extras.get(key); }
    void restore(Object value,Throwable failure) { result=value; error=failure; returnEarly=true; }
  }
  public final class Unhook {
    private final Member member;
    Unhook(Member member) { this.member=member; }
    public void unhook() {
      List<XC_MethodHook> hooks=XposedBridge.hookMap.get(member);
      if(hooks==null)return;
      for(Iterator<XC_MethodHook> i=hooks.iterator();i.hasNext();)
        if(i.next()==XC_MethodHook.this) { i.remove(); break; }
      if(hooks.isEmpty())XposedBridge.hookMap.remove(member);
    }
  }
}
'''

BRIDGE = r'''package de.robv.android.xposed;
import java.lang.reflect.*;
import java.util.*;
import de.robv.android.xposed.XC_MethodHook.MethodHookParam;
public final class XposedBridge {
  // Member equality (including overloads), not identity or a method-name key.
  public static final Map<Member,List<XC_MethodHook>> hookMap=new LinkedHashMap<>();
  public static int mutations,callbackFailures;
  private static final ThreadLocal<MethodHookParam> current=new ThreadLocal<>();
  public static MethodHookParam current() { return current.get(); }
  public static XC_MethodHook.Unhook hookMethod(Member member,XC_MethodHook cb) {
    hookMap.computeIfAbsent(member,m->new ArrayList<>()).add(cb);
    return cb.new Unhook(member);
  }
  public static void log(String message) {}
  public static Object call(Method method,Object receiver,Object... args) throws Throwable {
    MethodHookParam p=new MethodHookParam(); p.method=method; p.thisObject=receiver; p.args=args;
    MethodHookParam prior=current.get(); current.set(p);
    List<XC_MethodHook> registered=hookMap.get(method);
    List<XC_MethodHook> callbacks=registered==null ? Collections.emptyList() : new ArrayList<>(registered);
    int entered=0;
    try {
      for(XC_MethodHook cb:callbacks) {
        entered++;
        try { cb.beforeHookedMethod(p); }
        catch(Throwable failure) { callbackFailures++; p.restore(null,null); p.returnEarly=false; }
        if(p.returnEarly)break;
      }
      if(!p.returnEarly) {
        try { p.restore(method.invoke(receiver,p.args),null); }
        catch(InvocationTargetException failure) { p.restore(null,failure.getCause()); }
        catch(Throwable failure) { p.restore(null,failure); }
      }
      for(int i=entered-1;i>=0;i--) {
        Object result=p.getResult(); Throwable error=p.getThrowable();
        try { callbacks.get(i).afterHookedMethod(p); }
        catch(Throwable failure) { callbackFailures++; p.restore(result,error); }
      }
      return p.getResultOrThrowable();
    } finally { if(prior==null)current.remove(); else current.set(prior); }
  }
}
'''

HARNESS = r'''package com.xpanport.hook;
import android.util.*;
import fake.*;
import java.lang.reflect.*;
import java.nio.ByteBuffer;
import java.util.*;
import de.robv.android.xposed.*;
import de.robv.android.xposed.XC_MethodHook.MethodHookParam;
import com.oplus.camera.configure.CameraConfig;
import com.oplus.ocs.camera.common.util.CameraConfigBase.Key;
import com.oplus.ocs.camera.common.parameter.SdkCameraDeviceConfig;
import com.oplus.ocs.camera.producer.info.CameraConfigHelper;
import com.oplus.ocs.camera.producer.mode.XpanMode;
public final class C17JointCheck {
  static final String PICTURE=ConfigSource.PICTURE,OP="7872X2912";
  static int checks;
  static boolean configFirst;
  static Object configOwner;
  static Gate gate;
  static Method typed,raw,surface;
  static Constructor<?> keyConstructor;
  static final class Gate extends C17GateCheck.Toggle implements XpanC17Config.Gate {
    public boolean enabled() { if(fail)throw new IllegalStateException("gate failure"); return on; }
  }
  public static final class OtherMode extends XpanMode {}
  interface Work { void run() throws Throwable; }
  static void require(boolean yes,String why) { checks++; if(!yes)throw new AssertionError(why); }
  static void unchecked(Work work) { try { work.run(); } catch(Throwable t) { throw new AssertionError(t); } }
  static void sameFailure(Work work,Throwable expected) throws Throwable {
    try { work.run(); throw new AssertionError("original failure suppressed"); }
    catch(Throwable t) { require(t==expected,"original Throwable identity replaced: "+t); }
  }
  static Object key(String name,Class<?> type) throws Exception { return keyConstructor.newInstance(name,type); }
  static Object call(Method m,Object... args) throws Throwable { return XposedBridge.call(m,null,args); }
  static void sizeIs(Object value,int w,int h) {
    require(value instanceof Size,"not Size: "+value); Size s=(Size)value;
    require(s.getWidth()==w && s.getHeight()==h,"wrong Size: "+s+" expected "+w+"x"+h);
  }
  static Object depth(String name) throws Exception {
    return ((ThreadLocal<?>)C17GateCheck.get(configOwner,name)).get();
  }
  static void scopes(int expected) throws Exception {
    require(Objects.equals(depth("sdkSurfaceDepth"),expected==0 ? null : Integer.valueOf(expected)),"SDK surface depth != "+expected);
    require(depth("sdkPassthrough")==null,"typed passthrough leaked");
  }
  static void fixed() throws Throwable {
    scopes(0); sizeIs(call(typed,key(PICTURE,Size.class),null),4096,1512);
    require("4096x1512".equals(call(raw,PICTURE)),"raw scope leaked"); scopes(0);
    require(XposedBridge.current()==null,"shared param frame leaked");
  }
  static void originalReads(int expectedDepth) throws Throwable {
    scopes(expectedDepth); int writes=XposedBridge.mutations;
    sizeIs(call(typed,key(PICTURE,Size.class),null),7872,2912);
    require(OP.equals(call(raw,PICTURE)),"original raw acquired fixed geometry inside SDK surface");
    require(XposedBridge.mutations==writes,"Config mutated SDK original read"); scopes(expectedDepth);
  }
  static void installConfig() {
    XpanC17Config.InstallResult result=XpanC17Config.install(C17JointCheck.class.getClassLoader(),"7.013.51",gate);
    require(result.installed && result.registeredHooks==6,result.detail);
    try { configOwner=C17GateCheck.get(result,"owner"); } catch(Exception e) { throw new AssertionError(e); }
  }
  static void ready(String order) throws Throwable {
    configFirst=order.equals("config-first"); gate=new Gate();
    keyConstructor=Key.class.getDeclaredConstructor(String.class,Class.class); keyConstructor.setAccessible(true);
    typed=CameraConfigHelper.class.getDeclaredMethod("getConfigValue",Key.class,Object.class);
    raw=CameraConfigHelper.class.getDeclaredMethod("getConfigValue",String.class);
    surface=XpanMode.class.getDeclaredMethod("getSurfaceSize",SdkCameraDeviceConfig.class,String.class,String.class,String.class,int.class);
    if(configFirst)installConfig();
    C17GateCheck.create(gate,new C17GateCheck.Callbacks());
    if(!configFirst)installConfig();
    List<XC_MethodHook> hooks=XposedBridge.hookMap.get(surface);
    require(hooks!=null && hooks.size()==2,"both production surface callbacks must coexist");
    for(XC_MethodHook cb:hooks)require(cb.priority==XC_MethodHook.PRIORITY_DEFAULT,"not default same priority");
    require(hooks.get(0).getClass().getName().startsWith(configFirst ? "com.xpanport.hook.XpanC17Config$" : "com.xpanport.hook.XpanC17Hook$"),"arrangement not simulated");
    Method equal=XpanMode.class.getDeclaredMethod("getSurfaceSize",SdkCameraDeviceConfig.class,String.class,String.class,String.class,int.class);
    require(equal!=surface && equal.equals(surface) && XposedBridge.hookMap.get(equal)==hooks,"Member equality lookup failed");
    ConfigSource.reset(); ConfigSource.updateHelper(); Probe.surfaceKey=key(PICTURE,Size.class); fixed();
  }
  static Object surfaceCall(Object receiver,String stream,String camera,String id,int format) throws Throwable {
    int prior=Probe.jointFormat; Probe.jointFormat=format;
    try { return XposedBridge.call(surface,receiver,new SdkCameraDeviceConfig(),stream,camera,id,format); }
    finally { Probe.jointFormat=prior; }
  }
  static Object surfaceCall(String stream,int format) throws Throwable {
    return surfaceCall(new XpanMode(),stream,"rear_main","rear_main",format);
  }
  static byte[] tupleBytes(List<?> list) {
    ByteBuffer bytes=ByteBuffer.allocate(4+16*list.size()); bytes.putInt(list.size());
    for(Object item:list) { Pair<?,?> p=(Pair<?,?>)item;
      for(Object size:new Object[]{p.first,p.second}) { Size s=(Size)size; bytes.putInt(s.getWidth()); bytes.putInt(s.getHeight()); }
    }
    return bytes.array();
  }
  static final class TupleSnapshot {
    final List<?> list=(List<?>)Probe.lastSurfaceList;
    final Pair<?,?> pair=(Pair<?,?>)Probe.lastSurfacePair;
    final Object first=pair.first,second=pair.second;
    final byte[] bytes=tupleBytes(list);
    void unchanged() {
      require(list.size()==1 && list.get(0)==pair && pair.first==first && pair.second==second,"oldList/Pair/Size identity changed");
      require(Arrays.equals(bytes,tupleBytes(list)),"old tuple bytes changed");
      sizeIs(first,7872,2912);
    }
  }
  static TupleSnapshot saved;
  static void saveOriginal() { saved=new TupleSnapshot(); }
  static void result(Object value,int w,int h,boolean copy) {
    saved.unchanged(); List<?> list=(List<?>)value; Pair<?,?> pair=(Pair<?,?>)list.get(0);
    require(list.size()==1,"tuple count changed");
    require(copy ? list!=saved.list && pair!=saved.pair : list==saved.list && pair==saved.pair,"copy/passthrough identity wrong");
    sizeIs(pair.first,w,h); if(copy) { sizeIs(pair.second,w,h); require(pair.first==pair.second,"copy HAL/App target diverged"); }
    require(OP.equals(Probe.surfaceRaw),"original SDK raw not OP");
  }
  static void probeScope() {
    // These different after observations are expected, not a production bug or device-order evidence.
    sizeIs(Probe.formatTyped,configFirst ? 7872 : 4096,configFirst ? 2912 : 1512);
    require((configFirst ? OP : "4096x1512").equals(Probe.formatRaw),"old-layer probe scope mismatch");
  }
  static void positive(String stream,int w,int h) throws Throwable {
    SourceSnapshot source=new SourceSnapshot();
    Probe.surfaceEntry=()->unchecked(()->originalReads(1)); Probe.surfaceExit=C17JointCheck::saveOriginal;
    int probes=Probe.formatCalls; Object value=surfaceCall(stream,35);
    result(value,w,h,true); require(Probe.formatCalls==probes+1,"production Core format probe not run"); probeScope();
    Probe.surfaceEntry=null; Probe.surfaceExit=null; fixed(); source.unchanged();
  }
  static void unchangedStreams() throws Throwable {
    SourceSnapshot source=new SourceSnapshot(); Probe.surfaceExit=C17JointCheck::saveOriginal;
    for(String stream:new String[]{"capture_raw","raw_output","raw12_output","raw16_output","capture_raw_dol","tuning_data_yuv","unknown",null}) {
      Object value=surfaceCall(stream,35); result(value,7872,2912,false);
      if(stream!=null && stream.contains("raw"))require(saved.second==ConfigSource.nativeSensorSize,"RAW/DOL native tuple second changed");
      fixed();
    }
    for(int format:new int[]{32,36,37,38,34,4101,9999}) {
      Object value=surfaceCall("capture_yuv",format); result(value,7872,2912,false); fixed();
    }
    for(String[] camera:new String[][]{{"rear_wide","rear_main"},{"rear_main","0"},{"front_main","front_main"}}) {
      Object value=surfaceCall(new XpanMode(),"capture_yuv",camera[0],camera[1],35); result(value,7872,2912,false); fixed();
    }
    Object value=surfaceCall(new OtherMode(),"capture_yuv","rear_main","rear_main",35);
    result(value,7872,2912,false); fixed(); Probe.surfaceExit=null; source.unchanged();
  }
  static void nested(String mode) throws Throwable {
    SourceSnapshot source=new SourceSnapshot();
    RuntimeException innerFailure=new IllegalStateException("inner original"),outerFailure=new IllegalStateException("outer original");
    Probe.surfaceExit=C17JointCheck::saveOriginal;
    Probe.surfaceEntry=()->unchecked(()-> {
      Probe.surfaceEntry=null; MethodHookParam outer=XposedBridge.current(); originalReads(1);
      if(mode.equals("gateoff"))gate.on=false;
      Probe.surfaceEntry=()->unchecked(()-> { Probe.surfaceEntry=null; originalReads(2); });
      if(mode.equals("throwable")) {
        Probe.surfaceFailure=innerFailure;
        sameFailure(()->surfaceCall("capture_raw_dol",32),innerFailure); Probe.surfaceFailure=null;
      } else {
        Object inner=surfaceCall("capture_yuv",35); result(inner,mode.equals("gateoff") ? 7872 : 8192,mode.equals("gateoff") ? 2912 : 3024,!mode.equals("gateoff"));
      }
      require(XposedBridge.current()==outer,"nested shared param failed to restore outer"); originalReads(1);
      if(mode.equals("throwable")) { Probe.surfaceFailure=outerFailure; gate.on=false; gate.fail=true; }
      if(mode.equals("gateoff"))gate.fail=true;
    });
    if(mode.equals("throwable"))sameFailure(()->surfaceCall("capture_yuv",35),outerFailure);
    else { Object outer=surfaceCall("capture_yuv",35); boolean copy=mode.equals("normal"); result(outer,copy ? 8192 : 7872,copy ? 3024 : 2912,copy); }
    scopes(0); Probe.surfaceEntry=null; Probe.surfaceExit=null; Probe.surfaceFailure=null;
    gate.on=true; gate.fail=false; fixed(); source.unchanged();
  }
  static final class SourceSnapshot {
    final List<Map<?,?>> maps=Arrays.asList(ConfigSource.appValues,ConfigSource.sdkValues,ConfigSource.appSizes,ConfigSource.appArrays,ConfigSource.appCache,ConfigSource.sdkCache);
    final List<Map<?,?>> copies=new ArrayList<>(); final Map<String,int[]> arrays=new LinkedHashMap<>();
    final List<String> filters=new ArrayList<>(ConfigSource.filterGroup);
    final Object json=ConfigSource.configurationJson;
    SourceSnapshot() { for(Map<?,?> m:maps)copies.add(new LinkedHashMap<>(m)); for(Map.Entry<String,int[]> e:ConfigSource.appArrays.entrySet())arrays.put(e.getKey(),e.getValue().clone()); }
    void unchanged() {
      for(int i=0;i<maps.size();i++) { Map<?,?> m=maps.get(i),copy=copies.get(i);
        require(m.keySet().equals(copy.keySet()),"source/cache keys changed");
        for(Object key:copy.keySet())require(m.get(key)==copy.get(key),"source/cache value identity changed: "+key);
      }
      for(String key:arrays.keySet())require(Arrays.equals(arrays.get(key),ConfigSource.appArrays.get(key)),"source array bytes changed");
      require(ConfigSource.filterGroup.equals(filters) && ConfigSource.configurationJson==json,"filter/JSON source changed");
      require(OP.equals(ConfigSource.appValues.get(PICTURE)) && OP.equals(ConfigSource.sdkValues.get(PICTURE)),"verified OP source overwritten");
    }
  }
  static void ordinary() throws Throwable {
    SourceSnapshot source=new SourceSnapshot(); Object fallback=new Object();
    require(OP.equals(call(typed,key(PICTURE,String.class),fallback)),"wrong String Key overridden");
    Size[] sizes=(Size[])call(typed,key(PICTURE,Size[].class),fallback); require(sizes.length==1,"Size[] parse changed"); sizeIs(sizes[0],7872,2912);
    require(Arrays.equals((String[])call(typed,key(PICTURE,String[].class),fallback),new String[]{OP}),"String[] parse changed");
    for(Class<?> type:new Class<?>[]{Integer.class,Boolean.class,int[].class,Object.class})
      require(call(typed,key(PICTURE,type),fallback)==fallback,"wrong Key type lost original fallback");
    require("other-module-value".equals(call(typed,key("com.oplus.other.module.setting",String.class),fallback)),"ordinary SDK config changed");
    Method bool=CameraConfig.class.getDeclaredMethod("getConfigBooleanValue",String.class),integer=CameraConfig.class.getDeclaredMethod("m",int.class,String.class);
    require(Boolean.TRUE.equals(call(bool,ConfigSource.MASTER)),"ordinary master mode changed");
    require(Integer.valueOf(2).equals(call(integer,0,"com.oplus.xpan.mode.version")),"ordinary version changed");
    sizeIs(call(CameraConfig.class.getDeclaredMethod("getSizeConfigValue",String.class,boolean.class),PICTURE,true),3200,2400);
    require("77".equals(call(raw,"com.oplus.photo.exposure")),"ordinary raw config changed");
    fixed(); source.unchanged();
  }
  public static final class DispatchTarget {
    static int originals; static Throwable failure;
    public static Object original() throws Throwable { originals++; if(failure!=null)throw failure; return "native"; }
  }
  static void dispatch() throws Throwable {
    Method target=DispatchTarget.class.getDeclaredMethod("original"); List<String> events=new ArrayList<>();
    MethodHookParam[] shared=new MethodHookParam[1]; Throwable error=new IllegalStateException("after error");
    XC_MethodHook.Unhook first=XposedBridge.hookMethod(target,new XC_MethodHook() {
      protected void beforeHookedMethod(MethodHookParam p) { shared[0]=p; p.setObjectExtra("joint",events); events.add("before-A"); }
      protected void afterHookedMethod(MethodHookParam p) { require(p==shared[0] && p.getObjectExtra("joint")==events,"param/extras not shared"); require(p.getThrowable()==error,"Throwable primitive lost"); p.setResult("recovered"); events.add("after-A"); }
    });
    XC_MethodHook.Unhook second=XposedBridge.hookMethod(target,new XC_MethodHook() {
      protected void beforeHookedMethod(MethodHookParam p) { require(p==shared[0],"before param differs"); events.add("before-B"); p.setResult("early"); }
      protected void afterHookedMethod(MethodHookParam p) { require("early".equals(p.getResult()) && !p.hasThrowable(),"early result lost"); p.setThrowable(error); require(p.getResult()==null,"setThrowable did not clear result"); events.add("after-B"); }
    });
    XC_MethodHook.Unhook third=XposedBridge.hookMethod(target,new XC_MethodHook() {
      protected void beforeHookedMethod(MethodHookParam p) { events.add("unexpected-before"); }
      protected void afterHookedMethod(MethodHookParam p) { events.add("unexpected-after"); }
    });
    require("recovered".equals(call(target)),"setResult did not recover Throwable");
    require(events.equals(Arrays.asList("before-A","before-B","after-B","after-A")) && DispatchTarget.originals==0,"entered-only reverse after/returnEarly violated");
    second.unhook(); require(XposedBridge.hookMap.get(target).size()==2,"Unhook removed peer callbacks"); first.unhook(); third.unhook();
    DispatchTarget.failure=error;
    XposedBridge.hookMethod(target,new XC_MethodHook() {
      protected void afterHookedMethod(MethodHookParam p) { events.add(p.getThrowable()==error ? "original-throwable" : "wrong-throwable"); p.setResult("discarded"); throw new IllegalStateException("callback failure"); }
    });
    sameFailure(()->call(target),error); require(events.contains("original-throwable"),"after did not see original Throwable");
    require(XposedBridge.callbackFailures==1,"callback failure not isolated/restored"); fixed();
  }
  public static void main(String[] args) throws Throwable {
    ready(args[1]);
    switch(args[0]) {
      case "capture": positive("capture_yuv",8192,3024); break;
      case "preview": positive("preview",2304,1048); break;
      case "unchanged": unchangedStreams(); break;
      case "normal": case "throwable": case "gateoff": nested(args[0]); break;
      case "ordinary": ordinary(); break;
      case "dispatch": dispatch(); break;
      default: throw new AssertionError("unknown joint case");
    }
    require(XposedBridge.callbackFailures==(args[0].equals("dispatch") ? 1 : 0),"production callback unexpectedly threw");
    System.out.println("C17 joint "+args[0]+" "+args[1]+" passed: "+checks+" assertions; offline arrangement only");
  }
}
'''


class C17JointConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config = config_fixtures()
        cls.javac, cls.java = shutil.which("javac"), shutil.which("java")
        if not cls.javac or not cls.java or not TRUSTED_CWD.is_dir():
            raise AssertionError("Local Java tools and trusted ROOT/tools cwd required")
        production = (ROOT / "hook/XpanC17Hook.java", ROOT / "hook/XpanC17Config.java")
        for path in (*production, config.ANDROID, config.XPOSED):
            if not path.is_file():
                raise AssertionError(f"Required local production source/API missing: {path}")
        cls.temp = tempfile.TemporaryDirectory(prefix="c17-joint-tests-", dir=ROOT / "_tmp")
        cls.addClassCleanup(cls.temp.cleanup)
        folder = Path(cls.temp.name)
        cls.real, cls.fake = folder / "real", folder / "offlineFake"
        cls.real.mkdir()
        cls.fake.mkdir()
        api = os.pathsep.join(map(str, (config.ANDROID, config.XPOSED)))
        # Separate REAL API compiles, no fake source/ABI on either compile classpath.
        for source in production:
            cls.run_command([cls.javac, "-encoding", "UTF-8", "-source", "8", "-target", "8",
                             "-bootclasspath", str(config.ANDROID), "-classpath", api,
                             "-d", str(cls.real), str(source)])
        sources = config.C17OfflineFakeTests.write_sources(folder / "fixture-sources", joint_fixtures(config))
        cls.run_command([cls.javac, "-encoding", "UTF-8", "-source", "8", "-target", "8",
                         "-classpath", str(cls.real), "-d", str(cls.fake), *map(str, sources)])

    @staticmethod
    def run_command(command):
        process = subprocess.run(command, cwd=TRUSTED_CWD, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=90)
        if process.returncode:
            raise AssertionError(f"Local command failed ({process.returncode}):\n{process.stdout}\n{process.stderr}")
        return process.stdout

    def joint(self, case):
        for order in ("config-first", "core-first"):
            with self.subTest(order=order):
                # REAL API jars are deliberately absent at runtime.
                output = self.run_command([self.java, "-cp", os.pathsep.join(map(str, (self.real, self.fake))),
                                           "com.xpanport.hook.C17JointCheck", case, order])
                self.assertIn("passed:", output)
                print(output.strip())

    def test_capture_original_op_tuple_and_final_copy_both_arrangements(self):
        self.joint("capture")

    def test_preview_final_copy_and_old_layer_scope_observations(self):
        self.joint("preview")

    def test_raw_dol_private_unknown_camera_and_nonexact_keep_tuple_identity(self):
        self.joint("unchanged")

    def test_nested_normal_restores_inner_outer_sdk_depth(self):
        self.joint("normal")

    def test_nested_original_throwables_restore_inner_outer_sdk_depth(self):
        self.joint("throwable")

    def test_nested_gateoff_and_gate_failure_restore_inner_outer_sdk_depth(self):
        self.joint("gateoff")

    def test_wrong_key_types_ordinary_modes_and_source_caches_unchanged(self):
        self.joint("ordinary")

    def test_shared_param_returnearly_throwable_and_entered_only_reverse_after(self):
        self.joint("dispatch")


if __name__ == "__main__":
    unittest.main(verbosity=2)
