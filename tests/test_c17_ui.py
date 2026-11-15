"""Offline C17 native UI ordering/guard tests. Fakes prove behavior, never rendered UI."""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hook/XpanC17Ui.java"
DECODED = ROOT / "_tmp/decoded-PJX110_17.0.1.201/camera"
ANDROID = Path(os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME") or
               Path.home() / "AppData/Local/Android/Sdk") / "platforms/android-35/android.jar"
XPOSED = ROOT / "_tmp/api-82.jar"

# These have the exact target descriptors/hierarchy. Only the temporary bridge simulates Xposed;
# production guard callbacks, ABI resolver, weak identity registry, and status are executed as-is.
SOURCES = {
    "android/app/Activity.java": r'''package android.app;
public class Activity { public boolean finishing, destroyed;
 public boolean isFinishing(){return finishing;} public boolean isDestroyed(){return destroyed;} }''',
    "android/view/View.java": r'''package android.view;
public class View { public Object parent; public Object getParent(){return parent;}
 public int hashCode(){throw new AssertionError("view hashCode");}
 public boolean equals(Object x){throw new AssertionError("view equals");} }''',
    "de/robv/android/xposed/XC_MethodHook.java": r'''package de.robv.android.xposed;
import java.lang.reflect.Member;
import java.util.HashMap;
public abstract class XC_MethodHook {
 protected void beforeHookedMethod(MethodHookParam p) throws Throwable {}
 protected void afterHookedMethod(MethodHookParam p) throws Throwable {}
 public void enter(MethodHookParam p) throws Throwable {beforeHookedMethod(p);}
 public void exit(MethodHookParam p) throws Throwable {afterHookedMethod(p);}
 public class Unhook { final Member member;
  public Unhook(Member m){member=m;}
  public void unhook(){if(XposedBridge.failUnhook)throw new IllegalStateException("unhook");
   XposedBridge.hooks.remove(member);} }
 public static class MethodHookParam {
  public Object thisObject,result; public Object[] args; public Member method;
  public Throwable error; public boolean returned; public int writes;
  final HashMap<String,Object> extras=new HashMap<>();
  public void setObjectExtra(String k,Object v){extras.put(k,v);}
  public Object getObjectExtra(String k){return extras.get(k);}
  public boolean hasThrowable(){return error!=null;} public Throwable getThrowable(){return error;}
  public Object getResult(){return result;}
  public void setResult(Object v){result=v;error=null;returned=true;writes++;}
 } }''',
    "de/robv/android/xposed/XposedBridge.java": r'''package de.robv.android.xposed;
import java.lang.reflect.*; import java.util.*;
import de.robv.android.xposed.XC_MethodHook.MethodHookParam;
public final class XposedBridge {
 public static final LinkedHashMap<Member,XC_MethodHook> hooks=new LinkedHashMap<>();
 public static int attempts,failAt,lastWrites; public static boolean failUnhook,failLog;
 public static void log(String s){if(failLog)throw new IllegalStateException("log");}
 public static XC_MethodHook.Unhook hookMethod(Member m,XC_MethodHook cb){
  if(++attempts==failAt)throw new IllegalStateException("registration");
  hooks.put(m,cb);return cb.new Unhook(m); }
 public static Object invoke(Object owner,String name,Class<?>[] types,Object...args)throws Throwable{
  Method m=owner.getClass().getDeclaredMethod(name,types);m.setAccessible(true);
  MethodHookParam p=new MethodHookParam();p.method=m;p.thisObject=owner;p.args=args;
  XC_MethodHook cb=hooks.get(m);if(cb!=null)cb.enter(p);
  if(!p.returned)try{p.result=m.invoke(owner,p.args);}catch(InvocationTargetException e){p.error=e.getCause();}
  if(cb!=null)cb.exit(p);lastWrites=p.writes;
  if(p.error!=null)throw p.error;return p.result; }
 public static void constructed(Object owner,int kind,Object view,Object callback){
  try{Constructor<?> c=owner.getClass().getDeclaredConstructor(int.class,Object.class,Object.class);
   XC_MethodHook cb=hooks.get(c);if(cb==null)return;
   MethodHookParam p=new MethodHookParam();p.method=c;p.thisObject=owner;p.args=new Object[]{kind,view,callback};
   cb.exit(p);
  }catch(Throwable e){throw new AssertionError(e);} }
}''',
    "xg/a.java": "package xg; public interface a {}",
    "io/a.java": "package io; public interface a {}",
    "mo/o.java": "package mo; public class o {}",
    "com/oplus/camera/protocal/ui/context/EventMessageUIContainerContext.java":
        "package com.oplus.camera.protocal.ui.context; public class EventMessageUIContainerContext {}",
    "com/oplus/camera/protocal/ui/a.java":
        "package com.oplus.camera.protocal.ui; public interface a {uq.l gf();}",
    "com/oplus/camera/n6.java":
        "package com.oplus.camera; public interface n6 {cm.q3 e0(); gp.b1 Y();}",
    "gp/t2.java": r'''package gp;
public abstract class t2 implements com.oplus.camera.protocal.ui.a {
 public final android.app.Activity b; public uq.l p;
 protected t2(android.app.Activity a){b=a;} public final uq.l gf(){return p;} }''',
    "gp/b1.java": "package gp; public final class b1 extends t2 {public b1(android.app.Activity a){super(a);}}",
    "cm/i5.java": "package cm; public final class i5 {public com.oplus.camera.module.BaseMode a;}",
    "cm/q3.java": r'''package cm;
public final class q3 { public final i5 a=new i5(); public final com.oplus.camera.n6 X;
 public final android.app.Activity E0;
 public q3(com.oplus.camera.n6 o,android.app.Activity act){X=o;E0=act;}
 public void X(){fake.Native.events.add("commit-refresh");} }''',
    "com/oplus/camera/module/BaseMode.java": r'''package com.oplus.camera.module;
public class BaseMode implements xg.a {public final String U0;public final android.app.Activity Z0;
 public BaseMode(String n,android.app.Activity a){U0=n;Z0=a;}
 public boolean equals(Object o){throw new AssertionError("mode equals");}
 public int hashCode(){throw new AssertionError("mode hashCode");} }''',
    "ic/w.java": r'''package ic;
public abstract class w {public xg.a a;public final com.oplus.camera.protocal.ui.a e;
 public final android.app.Activity f;
 public w(int id,android.app.Activity act,com.oplus.camera.protocal.ui.a ui,String n){e=ui;f=act;} }''',
    "bl/d.java": r'''package bl;
public final class d extends ic.w implements dl.j.f {
 public final dl.j M=new dl.w(); public final b E0=new b(this);
 public final java.util.ArrayList<String> filters=new java.util.ArrayList<>();
 public d(int id,android.app.Activity act,com.oplus.camera.protocal.ui.a ui,String n){
  super(id,act,ui,n);filters.add("native-color");filters.add("native-mono");}
 public static final class b {public final d a;public b(d owner){a=owner;}}
}''',
    "dl/j.java": r'''package dl;
public class j {public android.app.Activity x;public com.oplus.camera.protocal.ui.a y;public f z;
 public interface f {} }''',
    "dl/w.java": r'''package dl;
public final class w extends j {
 public io.a T;public com.oplus.camera.feature.xpan.view.widget.XpanPhysicalFilterView X;
 public com.oplus.camera.feature.xpan.view.widget.c Y;
 public void h(android.app.Activity act,com.oplus.camera.protocal.ui.a ui,j.f p,bl.d.b callback){
  fake.Native.events.add("h");x=act;y=ui;T=ui.gf();z=p;
  androidx.lifecycle.g job=new androidx.lifecycle.g(8,this,callback);
  if(fake.Native.nested!=null){Runnable task=fake.Native.nested;fake.Native.nested=null;task.run();}
  if(fake.Native.immediate)fake.Native.runUnchecked(job);else fake.Native.queue.add(job);
 }
 public void i(){fake.Native.events.add("destroy");
  if(T!=null)((uq.l)T).R5("photo",true);y=null;
 } }''',
    "com/oplus/camera/feature/xpan/view/widget/XpanPhysicalFilterView.java": r'''package com.oplus.camera.feature.xpan.view.widget;
public class XpanPhysicalFilterView extends android.view.View {
 public java.util.List<?> e;public d n; public interface d {}
 public void setXpanFilterList(java.util.List<?> list){e=list;}
 public void setFilterViewListener(d listener){n=listener;} }''',
    "com/oplus/camera/feature/xpan/view/widget/c.java": r'''package com.oplus.camera.feature.xpan.view.widget;
public class c extends android.view.View {public java.util.List<?> c;
 public final java.util.ArrayList<Object> q=new java.util.ArrayList<>();
 public void setXpanFilterList(java.util.List<?> list){c=list;} }''',
    "dl/s.java": r'''package dl;
public final class s implements com.oplus.camera.feature.xpan.view.widget.XpanPhysicalFilterView.d {
 public final w a;public s(w view){a=view;} }''',
    "dl/t.java": r'''package dl;
public final class t implements com.oplus.camera.feature.xpan.view.widget.XpanPhysicalFilterView.d {
 public final w a;public t(w view){a=view;} }''',
    "uq/l.java": r'''package uq;
public final class l implements io.a {
 public final android.app.Activity f;public final com.oplus.camera.n6 g;
 public int C0=-1;public boolean j,physical=true,failInit,rootReady=true;
 public int initCalls,cleanupCalls;
 public com.oplus.camera.feature.xpan.view.widget.XpanPhysicalFilterView B;
 public com.oplus.camera.feature.xpan.view.widget.c C;
 public final Object nativeRoot=new Object(),nativeControlRoot=new Object();
 public final Object unrelatedView=new Object(); public final Object nativePanelCallback=new Object();
 public l(android.app.Activity a,com.oplus.camera.n6 o,mo.o s,
  com.oplus.camera.protocal.ui.context.EventMessageUIContainerContext c){f=a;g=o;}
 public void b7(){initCalls++;fake.Native.events.add("b7");
  if(failInit)throw fake.Native.failure;
  if(!j){B=new com.oplus.camera.feature.xpan.view.widget.XpanPhysicalFilterView();j=true;
   fake.Native.events.add("inflate");}
  C=new com.oplus.camera.feature.xpan.view.widget.c();C.q.add(nativePanelCallback);
  if(rootReady){B.parent=nativeControlRoot;C.parent=nativeRoot;fake.Native.events.add("add");}
  fake.Native.events.add("layout");uh(physical?6:4);fake.Native.events.add("animation");
 }
 public void uh(int value){C0=value;fake.Native.events.add("state:"+value);}
 public void R5(String mode,boolean immediate){cleanupCalls++;fake.Native.events.add("native-cleanup");
  if(C!=null){C.q.clear();C.parent=null;}sh();uh(-1);}
 public void sh(){if(B!=null)B.parent=null;fake.Native.events.add("remove");}
}''',
    "androidx/lifecycle/g.java": r'''package androidx.lifecycle;
public final class g implements Runnable {
 public final int a;public final Object c,b;public int executions;
 public g(int kind,Object view,Object callback){a=kind;c=view;b=callback;
  de.robv.android.xposed.XposedBridge.constructed(this,kind,view,callback);}
 public void run(){executions++;
  if(a!=8){fake.Native.otherRuns++;return;}
  dl.w view=(dl.w)c;
  if(view.y==null||view.T==null||view.z==null){fake.Native.events.add("native-null-skip");return;}
  ((uq.l)view.T).b7();fake.Native.events.add("bind");
  uq.l container=(uq.l)view.T;bl.d p=(bl.d)view.z;
  view.X=container.B;view.X.setXpanFilterList(p.filters);view.X.setFilterViewListener(new dl.s(view));
  view.Y=container.C;view.Y.setXpanFilterList(p.filters);view.Y.q.add(new dl.t(view));
  fake.Native.events.add("filter-bound");
 } }''',
    "fake/Native.java": r'''package fake;
import java.util.*;
public final class Native {
 public static final ArrayList<String> events=new ArrayList<>();
 public static final ArrayList<androidx.lifecycle.g> queue=new ArrayList<>();
 public static boolean immediate;public static int otherRuns;public static Runnable nested;
 public static final RuntimeException failure=new RuntimeException("native-init-failure"){
  public String toString(){throw new AssertionError("Throwable.toString called");} };
 public static void runUnchecked(androidx.lifecycle.g job){try{
  de.robv.android.xposed.XposedBridge.invoke(job,"run",new Class<?>[0]);
 }catch(Throwable e){throw (RuntimeException)e;} }
 public static final class Owner implements com.oplus.camera.n6 {
  public cm.q3 manager;public gp.b1 ui;public cm.q3 e0(){return manager;}public gp.b1 Y(){return ui;}
 }
}''',
}

HARNESS = r'''package com.xpanport.hook;
import android.app.Activity;
import androidx.lifecycle.g;
import de.robv.android.xposed.*;
import fake.Native;
import java.lang.reflect.*;
import java.util.*;
public final class C17UiCheck {
 static void require(boolean ok,String text){if(!ok)throw new AssertionError(text);}
 static final class Toggle implements XpanC17Ui.Gate {
  boolean on=true,fail;int remaining=-1;
  public boolean enabled()throws Throwable{if(fail)throw new Exception("gate");
   if(remaining==0)return false;if(remaining>0)remaining--;return on;}
 }
 static final class Env {
  final Activity act=new Activity();final Native.Owner owner=new Native.Owner();
  final gp.b1 ui=new gp.b1(act);final cm.q3 manager=new cm.q3(owner,act);
  final com.oplus.camera.module.BaseMode xp=new com.oplus.camera.module.BaseMode("xpan",act);
  final com.oplus.camera.module.BaseMode photo=new com.oplus.camera.module.BaseMode("photo",act);
  final uq.l container=new uq.l(act,owner,new mo.o(),new com.oplus.camera.protocal.ui.context.EventMessageUIContainerContext());
  final bl.d presenter=new bl.d(0,act,ui,"xpan");final dl.w view=(dl.w)presenter.M;
  Env(){owner.ui=ui;owner.manager=manager;ui.p=container;manager.a.a=xp;presenter.a=xp;}
  g create()throws Throwable{
   int n=Native.queue.size();
   XposedBridge.invoke(view,"h",new Class<?>[]{Activity.class,com.oplus.camera.protocal.ui.a.class,dl.j.f.class,bl.d.b.class},
    act,ui,presenter,presenter.E0);
   return Native.queue.size()>n?Native.queue.get(Native.queue.size()-1):null;
  }
  void destroy()throws Throwable{XposedBridge.invoke(view,"i",new Class<?>[0]);}
  void commit()throws Throwable{XposedBridge.invoke(manager,"X",new Class<?>[0]);}
 }
 static void run(g job)throws Throwable{XposedBridge.invoke(job,"run",new Class<?>[0]);}
 static XpanC17Ui.InstallResult install(Toggle gate){
  XpanC17Ui.InstallResult r=XpanC17Ui.install(C17UiCheck.class.getClassLoader(),"7.013.51",gate);
  require(r.installed&&r.registeredHooks==5,"five exact hooks");return r;
 }
 static int panelCallbacks(com.oplus.camera.feature.xpan.view.widget.c panel,dl.w view){
  int n=0;for(Object cb:panel.q)if(cb instanceof dl.t&&((dl.t)cb).a==view)n++;return n;
 }
 static void lifecycle()throws Throwable{
  Toggle gate=new Toggle();XpanC17Ui.InstallResult r=install(gate);
  require(!r.runtimeStatus().nativeReady&&!r.runtimeStatus().renderingVerified,"install is not rendering");
  Env e=new Env();g job=e.create();
  XpanC17Ui.RuntimeStatus s=r.runtimeStatus();
  require(s.nativeCreatePending&&!s.nativeReady&&s.containerState==-1,"h return is pending, not failed/woken");
  require(e.container.initCalls==0&&e.container.B==null,"no manual C16-style initialization");
  long epoch=s.epoch;e.commit();r.onActualMode(e.manager,"photo","xpan");
  require(r.runtimeStatus().epoch==epoch,"create before X and main callback share actual epoch");
  run(job);s=r.runtimeStatus();
  require(s.nativeReady&&s.physicalNativeState&&s.nativeFilterBound&&!s.renderingVerified,"live native physical chain facts");
  require(!s.nativeCreatePending&&!s.nativeCreateFailed,"finished native chain");
  require(Native.events.indexOf("b7")<Native.events.indexOf("bind")&&
   Native.events.indexOf("add")<Native.events.indexOf("filter-bound"),"native ordering");
  require(e.view.X==e.container.B&&e.view.Y==e.container.C,"presenter uses exact attached views");
  require(e.view.X.e==e.presenter.filters&&e.view.Y.c==e.presenter.filters,"original native filters preserved");
  require(e.view.Y.q.contains(e.container.nativePanelCallback)&&panelCallbacks(e.view.Y,e.view)==1,
   "native panel callback plus one native presenter callback");
  Object fv=e.view.X,panel=e.view.Y,listener=e.view.X.n,root=e.container.nativeRoot;
  int calls=e.container.initCalls;run(job);
  require(XposedBridge.lastWrites==1&&e.container.initCalls==calls,"same native job executes once");
  require(r.runtimeStatus().nativeReady,"duplicate rejection preserves live native ready status");
  require(e.view.X==fv&&e.view.Y==panel&&e.view.X.n==listener&&e.container.nativeRoot==root,
   "duplicate job cannot replace existing native views/filter/listener");
  r.onActualMode(e.manager,"xpan","photo");
  require("xpan".equals(r.runtimeStatus().committedMode),"requested strings never gate actual state");
  e.manager.a.a=e.photo;r.onActualMode(e.manager,"xpan","photo");e.destroy();
  require(e.container.cleanupCalls==1&&e.container.C0==-1&&e.container.B.parent==null&&e.container.C.parent==null,
   "original native cleanup owns/removes its views and restores state");
  require(!r.runtimeStatus().nativeReady,"leaving XPAN invalidates ready");
  e.manager.a.a=e.xp;g next=e.create();run(next);
  require(e.container.B==fv&&e.container.C!=panel&&r.runtimeStatus().nativeReady,"reentry preserves cached native filter view");
  run(job);require(XposedBridge.lastWrites==1&&e.container.initCalls==calls+1,"old job cannot add orphan panel on reused dl.w");
  g other=new g(7,e.view,e.presenter.E0);run(other);require(Native.otherRuns==1&&XposedBridge.lastWrites==0,"non-V3 R8 branches untouched");
  g untracked=new g(8,e.view,e.presenter.E0);int before=e.container.initCalls;run(untracked);
  require(XposedBridge.lastWrites==0&&e.container.initCalls==before+1,"untagged native jobs are not blanket-hooked");
 }
 static void stale()throws Throwable{
  Toggle gate=new Toggle();XpanC17Ui.InstallResult r=install(gate);Env e=new Env();
  g old=e.create();e.manager.a.a=e.photo;r.onActualMode(e.manager,"xpan","photo");
  run(old);require(XposedBridge.lastWrites==1&&e.container.initCalls==0,"actual exit cancels queued add before native destroy");
  e.destroy();run(old);require(XposedBridge.lastWrites==0&&old.executions==1,"native null guard preserved after native destroy");
  e.manager.a.a=e.xp;g first=e.create(),fresh=e.create();
  run(first);require(XposedBridge.lastWrites==1,"same owner newer native h supersedes old create");
  run(fresh);require(e.container.initCalls==1&&r.runtimeStatus().nativeReady,"latest original create accepted");
  g replaced=e.create();uq.l replacement=new uq.l(e.act,e.owner,new mo.o(),new com.oplus.camera.protocal.ui.context.EventMessageUIContainerContext());
  e.ui.p=replacement;run(replaced);require(XposedBridge.lastWrites==1&&e.container.initCalls==1,"live ui.gf container identity veto");
  e.ui.p=e.container;g dead=e.create();e.act.destroyed=true;run(dead);
  require(XposedBridge.lastWrites==1&&e.container.initCalls==1,"destroyed current Activity never gets a stale create");
  e.act.destroyed=false;g config=e.create();r.onUiChanged(e.act);run(config);
  require(XposedBridge.lastWrites==1&&e.container.initCalls==1,"explicit configuration epoch cancels old job without replay");
  Native.immediate=true;e.create();Native.immediate=false;
  require(e.container.initCalls==2&&r.runtimeStatus().nativeReady,"synchronous ThreadManager.m native job accepted");
 }
 static void failures()throws Throwable{
  Toggle gate=new Toggle();XpanC17Ui.InstallResult r=install(gate);Env e=new Env();
  e.container.failInit=true;g failed=e.create();Throwable original=null;
  try{run(failed);}catch(Throwable ex){original=ex;}
  XpanC17Ui.RuntimeStatus s=r.runtimeStatus();
  require(original==Native.failure&&s.nativeCreateFailed&&!s.nativeReady&&!s.nativeCreatePending&&s.containerState==-1,
   "failed native init keeps original throwable and truthful status");
  e.container.failInit=false;run(failed);
  require(e.container.initCalls==1&&XposedBridge.lastWrites==1&&!r.runtimeStatus().nativeReady,"no automatic failed init retry/count-on");
  e.destroy();require(e.container.cleanupCalls==1,"failed initialization still allows native cleanup");
  e.container.rootReady=false;g noRoot=e.create();run(noRoot);s=r.runtimeStatus();
  require(!s.nativeReady&&!s.nativeViewsAttached&&!s.nativeCreateFailed,"successful return without native mount is not ready");
  e.container.rootReady=true;e.container.physical=false;g ordinary=e.create();run(ordinary);s=r.runtimeStatus();
  require(s.nativeReady&&!s.physicalNativeState&&s.containerState==4,"ordinary native UI is not mislabeled Hasselblad physical UI");
  g off=e.create();gate.on=false;r.onGateChanged();run(off);
  require(XposedBridge.lastWrites==0&&!r.runtimeStatus().enabled,"gateoff leaves all original behavior alone");
  gate.on=true;g cancelled=e.create();gate.on=false;r.onGateChanged();gate.on=true;run(cancelled);
  require(XposedBridge.lastWrites==1,"revocation epoch cancels only tagged pre-revoke job after reenable");
  g revoke=e.create();gate.remaining=1;run(revoke);gate.remaining=-1;
  require(XposedBridge.lastWrites==0,"gate revoked at final write leaves original native run");
  g exception=e.create();gate.fail=true;XposedBridge.failLog=true;run(exception);
  require(XposedBridge.lastWrites==0&&!r.runtimeStatus().enabled,"gate/diagnostic failure cannot alter original native run");
 }
 static void nested()throws Throwable{
  final Toggle gate=new Toggle();install(gate);final Env outer=new Env(),inner=new Env();
  Native.nested=new Runnable(){public void run(){try{
   inner.manager.a.a=inner.photo;inner.create();
  }catch(Throwable e){throw new AssertionError(e);}}};
  g job=outer.create();
  require(Native.queue.size()==2,"nested native create fixture");
  g skipped=Native.queue.get(0);run(skipped);
  require(XposedBridge.lastWrites==0,"skipped nested h doesn't inherit outer tag");
  run(job);require(XposedBridge.lastWrites==0&&outer.container.initCalls==1,"outer scope survives inner skip");
 }
 static final class Missing extends ClassLoader {
  final String blocked;Missing(String name){super(C17UiCheck.class.getClassLoader());blocked=name;}
  protected Class<?> loadClass(String name,boolean resolve)throws ClassNotFoundException{
   if(name.equals(blocked))throw new ClassNotFoundException(name){
    public String toString(){throw new AssertionError("Throwable.toString called");} };
   return super.loadClass(name,resolve);}
 }
 static void abi()throws Throwable{
  Toggle gate=new Toggle();ClassLoader missing=new Missing("dl.t");
  XpanC17Ui.InstallResult r=XpanC17Ui.install(missing,"7.013.51",gate);
  require(!r.installed&&r.registeredHooks==0&&XposedBridge.attempts==0,"late missing ABI registers zero hooks");
  require(!r.runtimeStatus().enabled&&!r.runtimeStatus().nativeReady,"missing ABI status is not enabled/pass");
  require(!XpanC17Ui.install(C17UiCheck.class.getClassLoader(),"7.012.64",gate).installed,"exact version only");
  require(!XpanC17Ui.install(null,"7.013.51",gate).installed,"loader required");
  require(!XpanC17Ui.install(C17UiCheck.class.getClassLoader(),"7.013.51",null).installed,"gate required");
 }
 static void rollback(boolean residual)throws Throwable{
  Toggle gate=new Toggle();XposedBridge.failAt=3;XposedBridge.failUnhook=residual;
  ClassLoader loader=C17UiCheck.class.getClassLoader();
  XpanC17Ui.InstallResult r=XpanC17Ui.install(loader,"7.013.51",gate);
  require(!r.installed&&!r.runtimeStatus().enabled&&r.registeredHooks==(residual?2:0),"rollback reports real residual disabled group");
  require(XposedBridge.hooks.size()==(residual?2:0),"all registered hooks rolled back in failure");
  int attempts=XposedBridge.attempts;
  require(XpanC17Ui.install(loader,"7.013.51",gate)==r&&XposedBridge.attempts==attempts,"failed registration not stacked/retried");
  Env e=new Env();g nativeJob=e.create();run(nativeJob);
  require(e.container.initCalls==1&&XposedBridge.lastWrites==0,"residual disabled hooks preserve native UI");
 }
 public static void main(String[] args)throws Throwable{
  switch(args[0]){
   case "lifecycle":lifecycle();break;case "stale":stale();break;case "failures":failures();break;
   case "nested":nested();break;case "abi":abi();break;case "rollback":rollback(false);break;
   case "residual":rollback(true);break;default:throw new AssertionError(args[0]);
  }
  System.out.println("C17 UI "+args[0]+" native-chain guard checks passed; rendering remains unverified");
 }
}'''


class C17UiTests(unittest.TestCase):
    def smali(self, relative):
        return (DECODED / relative).read_text(encoding="utf-8")

    @staticmethod
    def body(text, descriptor):
        match = re.search(r"(?m)^\.method[^\n]* " + re.escape(descriptor) + r"\s*$", text)
        if not match:
            raise AssertionError(descriptor)
        return text[match.end():text.index(".end method", match.end())]

    def test_actual_native_activate_binding_cleanup_paths(self):
        view = self.smali("smali_classes7/dl/w.smali")
        create = self.body(view, "h(Landroid/app/Activity;Lcom/oplus/camera/protocal/ui/a;Ldl/j$f;Lbl/d$b;)V")
        self.assertIn("->gf()Luq/l;", create)
        self.assertIn("Landroidx/lifecycle/g;-><init>(ILjava/lang/Object;Ljava/lang/Object;)V", create)
        self.assertIn("const/16 p2, 0x8", create)
        self.assertIn("ThreadManager;->m(Ljava/lang/Runnable;)V", create)
        runner = self.body(self.smali("smali/androidx/lifecycle/g.smali"), "run()V")
        native = runner[runner.index("check-cast v0, Ldl/w;"):runner.index("check-cast v0, Ldl/j;")]
        self.assertLess(native.index("->b7()V"), native.index("->P6(I)Landroid/view/View;"))
        for descriptor in ("->V()Ljava/util/ArrayList;", "->Y()Ljava/lang/String;", "->setXpanFilterList(Ljava/util/List;)V",
                           "Ldl/s;-><init>(Ldl/w;)V", "Ldl/t;-><init>(Ldl/w;)V", "->q:Ljava/util/ArrayList;"):
            self.assertIn(descriptor, native)
        self.assertNotIn("BaseMode;->U0:", native)  # Mutable y/T/z null guards, no actual epoch guard.
        container = self.smali("smali_classes8/uq/l.smali")
        init = self.body(container, "b7()V")
        self.assertNotIn("->mh()Z", init)
        self.assertNotIn("->C0:I", init)
        self.assertIn("if-nez v1, :cond_0", init)  # Existing inflated B reused.
        panel = init.index("new-instance v1, Lcom/oplus/camera/feature/xpan/view/widget/c;")
        self.assertGreater(panel, init.index(":cond_0"))  # C re-created even for cached inflated view.
        for path in ("->u6()Landroid/view/ViewGroup;", "->addView(Landroid/view/View;)", "->rh(", "->ih(",
                     "->uh(I)V", "Luq/g;-><init>", "Luq/h;-><init>"):
            self.assertIn(path, init)
        physical = self.body(self.smali("smali_classes34/uq/g.smali"), "a(Z)V")
        self.assertIn("const/4 p1, 0x1", physical)
        self.assertIn("->yh()V", physical)
        destroy = self.body(view, "i()V")
        self.assertIn("->removeCallbacksAndMessages(Ljava/lang/Object;)V", destroy)
        self.assertIn("iput-object v1, p0, Ldl/j;->y:", destroy)
        self.assertNotIn("ThreadManager;", destroy)
        cleanup = self.body(container, "R5(Ljava/lang/String;Z)V")
        self.assertIn("->sh()V", cleanup)
        self.assertIn("->u0(Z)V", cleanup)
        self.assertIn("->clear()V", cleanup)
        panel_remove = self.body(self.smali("smali_classes7/com/oplus/camera/feature/xpan/view/widget/c.smali"), "u0(Z)V")
        self.assertIn("->removeView(Landroid/view/View;)V", panel_remove)
        manager = self.body(self.smali("smali_classes7/cm/q3.smali"), "o0(Ljava/lang/String;ZZ)Z")
        commit = manager.index("iput-object v3, v2, Lcm/i5;->a:")
        enter = manager.index("Leb/i1;->e()V", commit)
        self.assertLess(commit, enter)
        self.assertLess(enter, manager.index("Lcm/q3;->X()V", enter))
        cache = self.smali("smali_classes2/eb/t0.smali")
        self.assertIn("const/16 v4, 0x7537", cache)
        self.assertIn("->put(Ljava/lang/Object;Ljava/lang/Object;)", cache)

    def test_component_has_no_c16_wake_or_global_ui_mutation(self):
        source = HOOK.read_text(encoding="utf-8")
        self.assertLess(source.index("b.resolve(loader);"), source.index("h.register();"))
        self.assertLess(source.index("h.register();"), source.index("h.enabled = true;"))
        self.assertIn("h.rollback()", source)
        self.assertIn("Class.forName(name, false, loader)", source)
        for forbidden in ("setEnabled(", "postDelayed(", "setInt(", "addView(", "removeView(",
                          "setThrowable(", "setXpanFilterList(", "invokeOriginalMethod", "System.loadLibrary",
                          "CameraConfig", "SharedPreferences"):
            self.assertNotIn(forbidden, source)
        self.assertEqual(source.count("p.setResult(null)"), 1)
        self.assertIn("renderingVerified = false", source)
        self.assertIn("found == null || b.jobKind.getInt(p.thisObject) != 8", source)

    def test_compile_with_real_android_xposed_api(self):
        self.assertTrue(ANDROID.is_file(), str(ANDROID))
        self.assertTrue(XPOSED.is_file(), str(XPOSED))
        with tempfile.TemporaryDirectory(prefix="c17-ui-api-", dir=ROOT / "_tmp") as directory:
            subprocess.run([shutil.which("javac"), "-encoding", "UTF-8", "-source", "8", "-target", "8",
                            "-bootclasspath", str(ANDROID), "-classpath", os.pathsep.join(map(str, (ANDROID, XPOSED))),
                            "-d", directory, str(HOOK)], check=True, cwd=ROOT)

    def test_production_guard_against_native_shaped_fakes(self):
        javac, java = shutil.which("javac"), shutil.which("java")
        self.assertIsNotNone(javac, "javac required")
        self.assertIsNotNone(java, "java required")
        with tempfile.TemporaryDirectory(prefix="c17-ui-native-", dir=ROOT / "_tmp") as directory:
            folder = Path(directory)
            files = []
            for name, content in {**SOURCES, "com/xpanport/hook/C17UiCheck.java": HARNESS}.items():
                target = folder / "src" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                files.append(str(target))
            classes = folder / "classes"
            classes.mkdir()
            subprocess.run([javac, "-encoding", "UTF-8", "-source", "8", "-target", "8", "-d", str(classes),
                            str(HOOK), *files], check=True, cwd=ROOT)
            for scenario in ("lifecycle", "stale", "failures", "nested", "abi", "rollback", "residual"):
                with self.subTest(scenario=scenario):
                    subprocess.run([java, "-cp", str(classes), "com.xpanport.hook.C17UiCheck", scenario],
                                   check=True, cwd=ROOT)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decoded-root", type=Path, default=DECODED)
    parser.add_argument("--android-jar", type=Path, default=ANDROID)
    options = parser.parse_args()
    DECODED, ANDROID = options.decoded_root.resolve(), options.android_jar.resolve()
    unittest.main(argv=[__file__], verbosity=2)
