"""Offline C17 APS evidence checks: real API compile, isolated fake JVM, no device/APK.
Synthetic headers are clues only; these tests do not establish complete HEIF or capture ownership.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hook/XpanApsCaptureTrace.java"
SMALI = ROOT / "_tmp/decoded-PJX110_17.0.1.201/sdk/smali/com/oplus/ocs/camera/consumer"
ANDROID = Path(os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME") or
               Path.home() / "AppData/Local/Android/Sdk") / "platforms/android-35/android.jar"
XPOSED = ROOT / "_tmp/api-82.jar"
RESULT = "com.oplus.ocs.camera.consumer.apsAdapter.adapter.ApsResult"
PROCESSOR = "com.oplus.ocs.camera.consumer.ApsProcessor"
FIELDS = {"mCaptureMode": "Ljava/lang/String;", "mWidth": "I", "mHeight": "I",
          "mBufferType": "I", "mbHeifProcessInAps": "Z", "mCopyBuffer": "[B"}


class ApsCaptureTraceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.javac, cls.java = shutil.which("javac"), shutil.which("java")
        if not cls.javac or not cls.java or not ANDROID.is_file() or not XPOSED.is_file():
            raise AssertionError("Local javac/java and real Android/Xposed API jars required")
        if not HOOK.is_file():
            raise AssertionError("Production XpanApsCaptureTrace.java not ready; wait for main")
        cls.temporary = tempfile.TemporaryDirectory(prefix="aps-capture-trace-", dir=ROOT / "_tmp")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.work = Path(cls.temporary.name)
        cls.production = cls.work / "real-api-classes"
        cls.production.mkdir()
        cls.command([cls.javac, "-encoding", "UTF-8", "-source", "8", "-target", "8",
                     "-bootclasspath", str(ANDROID), "-classpath", os.pathsep.join(map(str, (ANDROID, XPOSED))),
                     "-d", str(cls.production), str(HOOK)])
        cls.runtime = cls.compile_fake("valid")

    @staticmethod
    def command(args):
        result = subprocess.run(args, cwd=ROOT / "tools", capture_output=True, text=True,
                                encoding="utf-8", errors="backslashreplace", timeout=90)
        if result.returncode:
            raise AssertionError("Local compile/JVM failed:\n" + result.stdout + result.stderr)
        return result.stdout

    @classmethod
    def compile_fake(cls, defect):
        folder = cls.work / defect
        folder.mkdir()
        paths = []
        for relative, content in fake_sources(defect).items():
            path = folder / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            paths.append(str(path))
        runtime = folder / "classes"
        runtime.mkdir()
        cls.command([cls.javac, "-encoding", "UTF-8", "-source", "8", "-target", "8",
                     "-classpath", str(cls.production), "-d", str(runtime), *paths])
        return runtime

    def scenario(self, name, runtime=None):
        # No Android/Xposed stub jars or APK enter this separate runtime JVM.
        output = self.command([self.java, "-Xmx128m", "-cp",
                               os.pathsep.join(map(str, (runtime or self.runtime, self.production))),
                               "com.xpanport.hook.ApsTraceCheck", name])
        self.assertRegex(output, r"^PASS " + re.escape(name) + r" checks=\d+ .* setters=0\s*$")
        print(output.strip())

    def test_01_real_smali_and_readonly_source_contract(self):
        processor = (SMALI / "ApsProcessor.smali").read_text(encoding="utf-8")
        result = (SMALI / "apsAdapter/adapter/ApsResult.smali").read_text(encoding="utf-8")
        descriptor = "onHeicReceived(L" + RESULT.replace(".", "/") + ";)V"
        self.assertRegex(processor, r"(?m)^\.method public " + re.escape(descriptor) + r"\s*$")
        for name, kind in FIELDS.items():
            self.assertRegex(result, r"(?m)^\.field public " + re.escape(name + ":" + kind) + r"\s*$")
        code = re.sub(r"/\*.*?\*/|//[^\n]*", "", HOOK.read_text(encoding="utf-8"), flags=re.S)
        for name in (RESULT, PROCESSOR):
            self.assertIn('Class.forName("' + name + '", false, loader)', code)
        self.assertEqual(code.count("XposedBridge.hookMethod("), 1)
        self.assertLess(code.index("b.resolve(loader)"), code.index("XposedBridge.hookMethod("))
        self.assertLess(code.index("XposedBridge.hookMethod("), code.index("h.ready = true"))
        self.assertEqual(set(re.findall(r'field\(result, "([^"]+)"', code)), set(FIELDS))
        self.assertIn('getDeclaredMethod("onHeicReceived", result)', code)
        self.assertIn("received.getReturnType() != void.class", code)
        self.assertEqual(code.count("getModifiers() != Modifier.PUBLIC"), 2)
        self.assertIn("Math.min(bytes.length, start == 0 ? 8 : 12)", code)
        for forbidden in ("afterHookedMethod", "setResult(", "setThrowable(", "setObjectExtra(", "toString(",
                          "String.valueOf(", "java.io.", "java.nio.", "System.load", " native ", "new Thread",
                          "getDeclaredFields(", ".invoke(", "invokeOriginalMethod", ".clone(", "Arrays."):
            self.assertNotIn(forbidden, code)
        self.assertNotRegex(code, r"\.(?:set|setInt|setLong|setBoolean)\(")
        self.assertNotRegex(code, r"(?:p\.args|args\[[^]]+\]|bytes\[[^]]+\])\s*=(?!=)")

    def test_02_bounded_metadata_and_unchanged_original(self): self.scenario("metadata")
    def test_03_standalone_known_and_bad_headers(self): self.scenario("headers")
    def test_04_runtime_gate_errors_logger_and_original_failure(self): self.scenario("gates")
    def test_05_final_gate_snapshot_staleness(self): self.scenario("stale")
    def test_06_first_four_then_each_sixty_four(self): self.scenario("sampling")
    def test_07_registration_idempotence_and_uncertain_handle(self):
        self.scenario("install")
        self.scenario("null_handle")

    def test_08_wrong_abi_refused_without_registration_or_initialization(self):
        # One focused runtime ABI mismatch; real smali/source checks cover all six exact fields.
        self.scenario("abi", self.compile_fake("bytes_type"))


def fake_sources(defect):
    fields = "public String mCaptureMode; public int mWidth,mHeight,mBufferType; public boolean mbHeifProcessInAps; public byte[] mCopyBuffer;"
    if defect == "bytes_type":
        fields = fields.replace("byte[] mCopyBuffer", "Object mCopyBuffer")
    result = """package com.oplus.ocs.camera.consumer.apsAdapter.adapter;
public class ApsResult {
  static { com.xpanport.hook.ApsTraceCheck.initializations++; }
  FIELDS
  public long mMeshPtr=0x1122334455667788L,mDataSize=123; public Object mOpaque=new com.xpanport.hook.ApsTraceCheck.Trap();
  private Object mHwbuffer=mOpaque,mImage=mOpaque;
  public Object getImageBuffer() { throw com.xpanport.hook.ApsTraceCheck.HOSTILE; }
  public Object getHardwareBuffer() { throw com.xpanport.hook.ApsTraceCheck.HOSTILE; }
  public String toString() { throw com.xpanport.hook.ApsTraceCheck.HOSTILE; }
}""".replace("FIELDS", fields)
    processor = """package com.oplus.ocs.camera.consumer;
import com.oplus.ocs.camera.consumer.apsAdapter.adapter.ApsResult;
public class ApsProcessor {
  static { com.xpanport.hook.ApsTraceCheck.initializations++; }
  public void onHeicReceived(ApsResult r) { com.xpanport.hook.ApsTraceCheck.original(r); }
  public String toString() { throw com.xpanport.hook.ApsTraceCheck.HOSTILE; }
}"""
    return {RESULT.replace(".", "/") + ".java": result, PROCESSOR.replace(".", "/") + ".java": processor,
            "de/robv/android/xposed/XC_MethodHook.java": METHOD_HOOK,
            "de/robv/android/xposed/XposedBridge.java": BRIDGE,
            "com/xpanport/hook/ApsTraceCheck.java": HARNESS}


METHOD_HOOK = r'''package de.robv.android.xposed;
import java.lang.reflect.Member;
import com.xpanport.hook.ApsTraceCheck;
public abstract class XC_MethodHook {
  public XC_MethodHook() {} public XC_MethodHook(int priority) {}
  protected void beforeHookedMethod(MethodHookParam p) throws Throwable {}
  protected void afterHookedMethod(MethodHookParam p) throws Throwable {}
  public static class MethodHookParam {
    public Member method; public Object thisObject; public Object[] args;
    private final Object result=XposedBridge.TOKEN; private final Throwable error=ApsTraceCheck.HOSTILE;
    public Object getResult() { return result; } public Throwable getThrowable() { return error; }
    public boolean hasThrowable() { return error!=null; }
    public Object getObjectExtra(String key) { return null; }
    public void setResult(Object value) { XposedBridge.setters++; throw ApsTraceCheck.HOSTILE; }
    public void setThrowable(Throwable value) { XposedBridge.setters++; throw ApsTraceCheck.HOSTILE; }
    public void setObjectExtra(String key,Object value) { XposedBridge.setters++; throw ApsTraceCheck.HOSTILE; }
  }
  public final class Unhook {
    public Unhook(Member member) {} public void unhook() { XposedBridge.unhooks++; }
  }
}
'''

BRIDGE = r'''package de.robv.android.xposed;
import java.lang.reflect.*;
import java.util.*;
import com.xpanport.hook.ApsTraceCheck;
import de.robv.android.xposed.XC_MethodHook.MethodHookParam;
public final class XposedBridge {
  public static final Object TOKEN=new Object();
  public static int registrations,setters,unhooks,logCalls,initializationsAtRegistration=-1;
  public static boolean nullHandle,throwLog,throwRegistration;
  public static final List<String> logs=new ArrayList<String>();
  public static Member member; public static XC_MethodHook callback;
  public static XC_MethodHook.Unhook hookMethod(Member m,XC_MethodHook hook) {
    registrations++; initializationsAtRegistration=ApsTraceCheck.initializations;
    if(throwRegistration)throw ApsTraceCheck.HOSTILE;
    member=m; callback=hook;
    try {
      MethodHookParam p=ApsTraceCheck.frame(ApsTraceCheck.owner("xpan",new byte[]{-1,-40,-1}));
      ApsTraceCheck.Snapshot snapshot=new ApsTraceCheck.Snapshot(p);
      hook.beforeHookedMethod(p); snapshot.verify(p);
      ApsTraceCheck.require(logs.isEmpty(),"registration callback active before ready");
    } catch(Throwable failure) { throw new AssertionError("early callback failed"); }
    return nullHandle ? null : hook.new Unhook(m);
  }
  public static void before(MethodHookParam p) throws Throwable { if(callback!=null)callback.beforeHookedMethod(p); }
  public static void log(String value) { logCalls++; if(throwLog)throw ApsTraceCheck.HOSTILE; logs.add(value); }
  public static void log(Throwable value) { ApsTraceCheck.stringCalls++; throw ApsTraceCheck.HOSTILE; }
}
'''

HARNESS = r'''package com.xpanport.hook;
import java.lang.reflect.*;
import java.util.*;
import de.robv.android.xposed.*;
import de.robv.android.xposed.XC_MethodHook.MethodHookParam;
public final class ApsTraceCheck {
  static final String R="com.oplus.ocs.camera.consumer.apsAdapter.adapter.ApsResult",P="com.oplus.ocs.camera.consumer.ApsProcessor";
  static final ClassLoader LOADER=ApsTraceCheck.class.getClassLoader();
  public static int initializations,stringCalls; static int checks,originalCalls,event,beforeLogs;
  static boolean failOriginal; static Object lastOwner;
  public static final Error HOSTILE=new Error("hostile") { public String toString() { stringCalls++; throw new AssertionError("must not format Throwable"); } };
  public static final class Trap { public String toString() { stringCalls++; throw HOSTILE; } }
  static final class Gate implements XpanApsCaptureTrace.Gate {
    int calls,denyAt,throwAt,actAt; boolean allow=true; Runnable action;
    public boolean enabled() { calls++; if(calls==actAt)action.run(); if(calls==throwAt)throw HOSTILE; return allow && calls!=denyAt; }
  }
  public static void require(boolean yes,String why) { checks++; if(!yes)throw new AssertionError(why); }
  static void put(Object r,String name,Object value) throws Exception { r.getClass().getField(name).set(r,value); }
  static Object get(Object r,String name) throws Exception { return r.getClass().getField(name).get(r); }
  public static Object owner(String mode,byte[] bytes) throws Exception {
    Object r=Class.forName(R,true,LOADER).getConstructor().newInstance();
    put(r,"mCaptureMode",mode); put(r,"mWidth",8192); put(r,"mHeight",3024); put(r,"mBufferType",17);
    put(r,"mbHeifProcessInAps",true); put(r,"mCopyBuffer",bytes); return r;
  }
  public static MethodHookParam frame(Object r) throws Exception {
    Class<?> c=Class.forName(P,true,LOADER); MethodHookParam p=new MethodHookParam();
    p.method=c.getDeclaredMethod("onHeicReceived",Class.forName(R,false,LOADER));
    p.thisObject=c.getConstructor().newInstance(); p.args=new Object[]{r}; return p;
  }
  public static Object original(Object r) { originalCalls++; lastOwner=r; beforeLogs=XposedBridge.logs.size(); if(failOriginal)throw HOSTILE; return XposedBridge.TOKEN; }
  public static final class Snapshot {
    final Object[] args,values; final Object receiver,result,owner; final Member method; final Throwable error;
    final List<Field> fields=new ArrayList<Field>(); final List<Object> fieldValues=new ArrayList<Object>(); byte[] bytes,copy;
    public Snapshot(MethodHookParam p) throws Exception {
      args=p.args; values=args==null ? null : args.clone(); receiver=p.thisObject; method=p.method; result=p.getResult(); error=p.getThrowable();
      owner=args!=null && args.length==1 ? args[0] : null;
      if(owner!=null && Class.forName(R,false,LOADER).isInstance(owner)) {
        for(Field f:owner.getClass().getDeclaredFields())if(!Modifier.isStatic(f.getModifiers())) { f.setAccessible(true); fields.add(f); fieldValues.add(f.get(owner)); }
        bytes=(byte[])get(owner,"mCopyBuffer"); copy=bytes==null ? null : bytes.clone();
      }
    }
    public void verify(MethodHookParam p) throws Exception {
      require(p.args==args && p.thisObject==receiver && p.method==method,"param identity changed");
      require(p.getResult()==result && p.getThrowable()==error && XposedBridge.setters==0,"param result/error/extra mutated");
      if(args!=null)for(int i=0;i<args.length;i++)require(args[i]==values[i],"arg element changed");
      for(int i=0;i<fields.size();i++) { Field f=fields.get(i); Object v=f.get(owner),old=fieldValues.get(i); require(f.getType().isPrimitive() ? old.equals(v) : old==v,"capture field changed"); }
      require(Arrays.equals(bytes,copy),"capture bytes changed");
    }
  }
  static Object flow(MethodHookParam p) throws Throwable {
    Snapshot snapshot=new Snapshot(p); XposedBridge.before(p); snapshot.verify(p);
    try { Object value=((Method)p.method).invoke(p.thisObject,p.args); snapshot.verify(p); return value; }
    catch(InvocationTargetException failure) { snapshot.verify(p); throw failure.getCause(); }
  }
  static void unchanged(Object r) throws Throwable { require(flow(frame(r))==null && lastOwner==r,"original void result/owner changed"); }
  static Gate ready() { Gate g=new Gate(); XpanApsCaptureTrace.InstallResult r=XpanApsCaptureTrace.install(LOADER,"7.013.51",g); require(r.installed && r.registeredHooks==1,"install failed"); require(g.calls==0 && XposedBridge.initializationsAtRegistration==0,"resolution initialized targets or consulted pending gate"); return g; }
  static byte[] bmff(int length,int size,String brand) {
    byte[] b=new byte[length]; for(int i=0;i<Math.min(4,length);i++)b[i]=(byte)(size >>> (24-8*i));
    for(int i=4;i<Math.min(8,length);i++)b[i]=(byte)"ftyp".charAt(i-4);
    for(int i=8;i<Math.min(12,length);i++)b[i]=(byte)brand.charAt(i-8); return b;
  }
  static void padToSample() throws Throwable { while(event>=4 && (event+1)%64!=0) { unchanged(owner("xpan",null)); event++; } }
  static void bounded(String s) { require(s.length()<300 && s.matches("\\[XpanApsCaptureTrace\\] seen=\\d+ mode=(xpan|common|unknown|other) size=-?\\d+x-?\\d+ bufferType=-?\\d+ heifInAps=(true|false) copyBytes=-?\\d+ header=(absent|unknown|jpeg-prefix|bmff-ftyp); .*not complete.*proof"),"unbounded/non-metadata log"); }
  static void metadata() throws Throwable {
    ready(); String[] modes={"xpan","common",null,"private-mode\n"+String.join("",Collections.nCopies(1000,"SECRET"))}; String[] labels={"xpan","common","unknown","other"};
    for(int i=0;i<4;i++) { Object r=owner(modes[i],i==0 ? null : bmff(16,16,"heic")); put(r,"mbHeifProcessInAps",i%2==0); unchanged(r);
      String s=XposedBridge.logs.get(i); bounded(s); require(s.contains("mode="+labels[i]+" size=8192x3024 bufferType=17 heifInAps="+(i%2==0)+" copyBytes="+(i==0 ? -1 : 16)),"metadata mismatch"); require(beforeLogs==i+1 && !s.contains("SECRET"),"not before original or raw mode leaked"); }
  }
  static void headers() throws Throwable {
    ready(); byte[][] data={null,new byte[0],new byte[]{-1,-40,-1},new byte[]{-1,-40},bmff(12,12,"heic"),bmff(16,16,"junk"),bmff(11,11,"heic"),bmff(16,0,"heic"),bmff(16,1,"heic"),bmff(16,11,"heic"),bmff(16,17,"heic"),new byte[]{0,0,0,12,'x','f','t','y','p','h','e','i'}};
    String[] kinds={"absent","unknown","jpeg-prefix","unknown","bmff-ftyp","bmff-ftyp","unknown","unknown","unknown","unknown","unknown","unknown"};
    for(int i=0;i<data.length;i++) { padToSample(); int logs=XposedBridge.logs.size(); unchanged(owner("xpan",data[i])); event++;
      require(XposedBridge.logs.size()==logs+1,"sample missing"); String s=XposedBridge.logs.get(logs); bounded(s); require(s.contains("copyBytes="+(data[i]==null ? -1 : data[i].length)+" header="+kinds[i]+";"),"synthetic header misclassified"); }
  }
  static void gates() throws Throwable {
    Gate g=ready(); Object r=owner("xpan",bmff(16,16,"heic")); g.allow=false; for(int i=0;i<6;i++)unchanged(r); require(XposedBridge.logCalls==0,"disabled trace");
    g.allow=true; g.throwAt=g.calls+1; unchanged(r); require(XposedBridge.logCalls==0,"throwing entry gate trace");
    g.denyAt=g.calls+2; unchanged(r); require(XposedBridge.logCalls==0,"denied final gate trace");
    g.throwAt=g.calls+2; unchanged(r); require(XposedBridge.logCalls==0,"throwing final gate trace");
    XposedBridge.throwLog=true; unchanged(r); require(XposedBridge.logCalls==1 && XposedBridge.logs.isEmpty(),"logger failure path missing"); XposedBridge.throwLog=false;
    failOriginal=true; try { flow(frame(r)); throw new AssertionError("original failure suppressed"); } catch(Throwable failure) { require(failure==HOSTILE,"original Throwable replaced"); }
    require(XposedBridge.logs.size()==1,"enabled fourth observation missing"); g.allow=false;
    try { flow(frame(r)); throw new AssertionError("disabled original failure suppressed"); } catch(Throwable failure) { require(failure==HOSTILE,"disabled original Throwable replaced"); }
    require(XposedBridge.logs.size()==1,"runtime-disabled failure traced"); failOriginal=false;
    for(Object[] bad:new Object[][]{null,new Object[0],new Object[]{null},new Object[]{new Trap()},new Object[]{r,r}}) { g.allow=true; MethodHookParam p=frame(r); p.args=bad; Snapshot s=new Snapshot(p); XposedBridge.before(p); s.verify(p); }
    XposedBridge.before(null); require(XposedBridge.logs.size()==1,"malformed args traced");
  }
  static void mutate(int k,MethodHookParam p,Object r) throws Exception {
    if(k==0)p.args=p.args.clone(); else if(k==1)p.args[0]=owner("common",null); else if(k==2)put(r,"mCaptureMode",new String("xpan"));
    else if(k==3)put(r,"mWidth",8193); else if(k==4)put(r,"mHeight",3025); else if(k==5)put(r,"mBufferType",18);
    else if(k==6)put(r,"mbHeifProcessInAps",false); else if(k==7)put(r,"mCopyBuffer",((byte[])get(r,"mCopyBuffer")).clone());
    else if(k==8)((byte[])get(r,"mCopyBuffer"))[0]^=1; else if(k==9)((byte[])get(r,"mCopyBuffer"))[11]^=1;
    else if(k==10)p.args=null; else ((byte[])get(r,"mCopyBuffer"))[12]^=1;
  }
  static void stale() throws Throwable {
    Gate g=ready(); for(int kind=0;kind<12;kind++) { padToSample(); final int k=kind; final Object r=owner("xpan",bmff(16,16,"heic")); final MethodHookParam p=frame(r),old=frame(r);
      final Snapshot[] expected=new Snapshot[2]; int logs=XposedBridge.logs.size(); g.actAt=g.calls+2;
      g.action=()-> { try { mutate(k,p,r); expected[0]=new Snapshot(p); expected[1]=new Snapshot(old); } catch(Exception failure) { throw new AssertionError("fixture mutation failed"); } };
      XposedBridge.before(p); event++; require(expected[0]!=null,"final gate not called"); expected[0].verify(p); expected[1].verify(old);
      require(XposedBridge.logs.size()==logs+(k==11 ? 1 : 0),"stale snapshot logged or read beyond byte 11");
      if(p.args!=null) { Object current=p.args[0]; require(((Method)p.method).invoke(p.thisObject,p.args)==null && lastOwner==current,"current original args replaced"); expected[0].verify(p); }
    }
  }
  static void sampling() throws Throwable {
    Gate g=ready(); for(int n=1;n<=128;n++) { if(n==5) { g.allow=false; for(int i=0;i<10;i++)unchanged(owner("xpan",null)); g.allow=true; } unchanged(owner("xpan",bmff(12,12,"heic"))); }
    int[] seen={1,2,3,4,64,128}; require(XposedBridge.logs.size()==seen.length,"sample count wrong");
    for(int i=0;i<seen.length;i++) { String s=XposedBridge.logs.get(i); bounded(s); require(s.contains("seen="+seen[i]+" mode="),"sampling sequence wrong"); }
    require(g.calls==128+6+10,"entry/final sampling gates wrong");
  }
  static void install(boolean uncertain) throws Throwable {
    Gate g=new Gate(); for(String v:new String[]{null,"","6.020.876","7.013.510"," 7.013.51","7.013.51 ","7.013.52"}) { XpanApsCaptureTrace.InstallResult r=XpanApsCaptureTrace.install(LOADER,v,g); require(!r.installed && r.registeredHooks==0,"inexact version accepted"); }
    require(!XpanApsCaptureTrace.install(null,"7.013.51",g).installed && !XpanApsCaptureTrace.install(LOADER,"7.013.51",null).installed,"null loader/gate accepted"); require(initializations==0 && XposedBridge.registrations==0,"invalid install initialized/registered");
    XposedBridge.nullHandle=uncertain; g.allow=false; XpanApsCaptureTrace.InstallResult r=XpanApsCaptureTrace.install(LOADER,"7.013.51",g);
    require(r.installed==!uncertain && r.registeredHooks==1 && !r.detail.isEmpty(),"install count/detail wrong"); require(g.calls==0 && XposedBridge.initializationsAtRegistration==0,"pending activation or static initialization");
    require(XpanApsCaptureTrace.install(LOADER,"7.013.51",g)==r,"same gate not idempotent"); Gate other=new Gate(); other.throwAt=1;
    XpanApsCaptureTrace.InstallResult refused=XpanApsCaptureTrace.install(LOADER,"7.013.51",other); require(!refused.installed && refused.registeredHooks==1,"other gate accepted/count lost");
    unchanged(owner("xpan",bmff(12,12,"heic"))); require(XposedBridge.logs.isEmpty(),"disabled/pending callback traced"); g.allow=true; XposedBridge.nullHandle=false;
    require(XpanApsCaptureTrace.install(LOADER,"7.013.51",g)==r && XposedBridge.registrations==1,"uncertain/reinstall registered twice"); unchanged(owner("xpan",null));
    require(XposedBridge.logs.size()==(uncertain ? 0 : 1) && other.calls==0 && (!uncertain || g.calls==0),"gate binding or inactive uncertain record broken");
  }
  public static void main(String[] args) throws Throwable {
    String scenario=args[0]; if(scenario.equals("abi")) { Gate g=new Gate(); XpanApsCaptureTrace.InstallResult r=XpanApsCaptureTrace.install(LOADER,"7.013.51",g); require(!r.installed && r.registeredHooks==0 && XposedBridge.registrations==0 && initializations==0,"wrong ABI registered/initialized"); }
    else if(scenario.equals("metadata"))metadata(); else if(scenario.equals("headers"))headers(); else if(scenario.equals("gates"))gates(); else if(scenario.equals("stale"))stale(); else if(scenario.equals("sampling"))sampling(); else if(scenario.equals("install"))install(false); else if(scenario.equals("null_handle"))install(true); else throw new AssertionError("unknown scenario");
    require(XposedBridge.setters==0 && XposedBridge.unhooks==0 && stringCalls==0,"mutation/unhook/hostile formatting");
    System.out.println("PASS "+scenario+" checks="+checks+" originals="+originalCalls+" traces="+XposedBridge.logs.size()+" registrations="+XposedBridge.registrations+" setters="+XposedBridge.setters);
  }
}
'''


if __name__ == "__main__":
    unittest.main(verbosity=2)
