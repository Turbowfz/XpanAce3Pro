"""Offline C17 XPAN menu tests. Only temporary SQLite fixtures/JVM fakes; never a device DB.

The default adapter publishes a process-local native menu-list copy; only the new virtual
XPAN carries support_edit=false, independent of the legacy skin configuration. This data
flag does not establish that GUI dragging is blocked. There is no migration, transaction or
native void-success assertion to test. Every fault must leave the original result/rows
unchanged. JVM callbacks exercise the actual Java component, not a Python policy.
"""
import argparse
from contextlib import closing
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hook/XpanC17Menu.java"
DECODED = ROOT / "_tmp/decoded-PJX110_17.0.1.201/camera"
ANDROID = Path(os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME") or
               Path.home() / "AppData/Local/Android/Sdk") / "platforms/android-35/android.jar"
XPOSED = ROOT / "_tmp/api-82.jar"
HEADLINE = "position_headline"
PANEL = "position_mode_panel"
COLUMNS = ("mode_name", "mode_id", "position", "rank", "support_edit", "support_front")
SCHEMA = ("create table mode_info(mode_name varchar primary key, mode_id integer, "
          "position varchar, rank integer, support_edit boolean, support_front boolean)")


def smali_method(source, descriptor):
    match = re.search(r"^\.method [^\n]* " + re.escape(descriptor) + r"\n(.*?)^\.end method",
                      source, re.M | re.S)
    if match is None:
        raise AssertionError("missing exact smali descriptor: " + descriptor)
    return match.group(1)


# Standalone fake APIs are compiled in a new temporary directory. Target-class <clinit>
# counters prove install uses Class.forName(false), not getters/constructors/static values.
STUBS = {
    "android/database/Cursor.java": r'''package android.database;
public interface Cursor extends java.io.Closeable {
  int FIELD_TYPE_NULL=0, FIELD_TYPE_INTEGER=1, FIELD_TYPE_FLOAT=2, FIELD_TYPE_STRING=3, FIELD_TYPE_BLOB=4;
  String[] getColumnNames(); int getColumnIndex(String name); int getCount();
  boolean moveToNext(); int getType(int index); String getString(int index); long getLong(int index);
  void close();
}''',
    "android/database/sqlite/SQLiteDatabase.java": r'''package android.database.sqlite;
import android.database.Cursor;
public class SQLiteDatabase {
  public boolean isOpen() { return true; }
  public Cursor rawQuery(String sql, String[] selection) { throw new AssertionError("native rawQuery replayed"); }
  public Cursor query(String table, String[] columns, String selection, String[] args,
      String group, String having, String order) { throw new AssertionError("unimplemented fake snapshot"); }
  public void execSQL(String sql) { throw new AssertionError("adapter wrote SQL"); }
  public void beginTransaction() { throw new AssertionError("adapter started transaction"); }
  public void setTransactionSuccessful() { throw new AssertionError("adapter committed transaction"); }
  public void endTransaction() { throw new AssertionError("adapter ended transaction"); }
  public void setVersion(int value) { throw new AssertionError("adapter changed DB version"); }
  public void close() { throw new AssertionError("adapter closed original connection"); }
}''',
    "android/database/sqlite/SQLiteOpenHelper.java": r'''package android.database.sqlite;
public class SQLiteOpenHelper {
  public SQLiteDatabase getReadableDatabase() { throw new AssertionError("early helper open"); }
  public SQLiteDatabase getWritableDatabase() { throw new AssertionError("early helper writable open"); }
  public void close() { throw new AssertionError("adapter closed original helper"); }
}''',
    "de/robv/android/xposed/XC_MethodHook.java": r'''package de.robv.android.xposed;
import java.lang.reflect.Member;
import java.util.HashMap;
public abstract class XC_MethodHook {
  protected void beforeHookedMethod(MethodHookParam p) throws Throwable {}
  protected void afterHookedMethod(MethodHookParam p) throws Throwable {}
  public final void enter(MethodHookParam p) throws Throwable { beforeHookedMethod(p); }
  public final void exit(MethodHookParam p) throws Throwable { afterHookedMethod(p); }
  public class Unhook {
    private final Member target;
    Unhook(Member target) { this.target=target; }
    public void unhook() {
      if (XposedBridge.failUnhook) throw new IllegalStateException("unhook");
      XposedBridge.callbacks.remove(target);
    }
  }
  public static class MethodHookParam {
    public Member method; public Object thisObject; public Object[] args;
    public Object result; public Throwable error; public int mutations;
    private final HashMap<String,Object> extras=new HashMap<>();
    public void setObjectExtra(String name,Object value) { extras.put(name,value); }
    public Object getObjectExtra(String name) { return extras.get(name); }
    public Object getResult() { return result; }
    public boolean hasThrowable() { return error!=null; }
    public Throwable getThrowable() { return error; }
    public void setResult(Object value) { result=value; error=null; mutations++; }
  }
}''',
    "de/robv/android/xposed/XposedBridge.java": r'''package de.robv.android.xposed;
import java.lang.reflect.Member;
import java.util.LinkedHashMap;
public final class XposedBridge {
  public static final LinkedHashMap<Member,XC_MethodHook> callbacks=new LinkedHashMap<>();
  public static int registrations, failAt;
  public static boolean failUnhook, failLog;
  public static XC_MethodHook.Unhook hookMethod(Member m,XC_MethodHook cb) {
    registrations++;
    if (registrations==failAt) throw new IllegalStateException("registration");
    callbacks.put(m,cb); return cb.new Unhook(m);
  }
  public static void log(String text) { if (failLog) throw new IllegalStateException("log"); }
}''',
    "fixture/Probe.java": r'''package fixture;
public final class Probe {
  public static int operator, helper, mode, config, constructors, xpans, lookups, legacyReads;
  public static int stickerReads, superTextReads, superTextV2Reads;
  public static boolean failMode, corruptMode, failLookup, lookupUnexpected, failConfig, failLegacy;
  public static boolean failSticker, failSuperText, failSuperTextV2;
}''',
    "mp/j.java": r'''package mp;
public final class j extends android.database.sqlite.SQLiteOpenHelper {
  static { fixture.Probe.helper++; }
}''',
    "fa/a.java": r'''package fa;
public final class a {
  static { fixture.Probe.mode++; }
  public final String a,d; public final boolean b,c; public final int e,f;
  public a(String name,boolean edit,String position,int rank,int id,boolean front) {
    fixture.Probe.constructors++;
    if ("xpan".equals(name)) fixture.Probe.xpans++;
    if (fixture.Probe.failMode && "xpan".equals(name)) throw new IllegalStateException("candidate");
    a=name; b=edit; c=front; d=position; e=rank;
    f=fixture.Probe.corruptMode && "xpan".equals(name) ? id+1 : id;
  }
  // Stock equals compares id only. The adapter must use native name lookup, not contains().
  public boolean equals(Object value) { return value instanceof a && ((a)value).f==f; }
  public int hashCode() { return f; }
}''',
    "mp/h.java": r'''package mp;
import java.util.*;
import android.database.sqlite.SQLiteDatabase;
public final class h {
  static { fixture.Probe.operator++; }
  public final j a; public boolean g;
  public h() { a=new j(); g=true; }
  public final ArrayList<fa.a> p(String position,boolean init,boolean ordered) {
    throw new AssertionError("adapter replayed native get-list");
  }
  public static fa.a r(fa.a candidate,List<fa.a> headline,List<fa.a> panel) {
    fixture.Probe.lookups++;
    if (fixture.Probe.failLookup) throw new IllegalStateException("lookup");
    if (fixture.Probe.lookupUnexpected) return candidate;
    for (fa.a row:headline) if (candidate.a.equals(row.a)) return row;
    for (fa.a row:panel) if (candidate.a.equals(row.a)) return row;
    return null;
  }
  public final void j(SQLiteDatabase db,List<fa.a> headline,List<fa.a> panel) {
    throw new AssertionError("generic multi-mode merge invoked");
  }
  public final void a(ArrayList<fa.a> modes,SQLiteDatabase db) {
    throw new AssertionError("native inserts/inserted marker invoked");
  }
}''',
    "com/oplus/camera/configure/CameraConfig.java": r'''package com.oplus.camera.configure;
public final class CameraConfig {
  static { fixture.Probe.config++; }
  public static boolean supported=true, legacy, sticker=true, superText=true, superTextV2;
  public static boolean getConfigBooleanValue(String key) {
    if ("com.oplus.app.feature.sticker.support".equals(key)) {
      fixture.Probe.stickerReads++;
      if (fixture.Probe.failSticker) throw new IllegalStateException("sticker getter");
      return sticker;
    }
    if (!"com.oplus.feature.xpan.mode.support".equals(key)) throw new AssertionError("foreign config read");
    if (fixture.Probe.failConfig) throw new IllegalStateException("config");
    return supported;
  }
  public static boolean d(String key,boolean fallback,boolean front) {
    if (front) throw new AssertionError("front config predicate");
    if ("com.oplus.camera.super.text.mode.support".equals(key)) {
      if (!fallback) throw new AssertionError("superText default must be true");
      fixture.Probe.superTextReads++;
      if (fixture.Probe.failSuperText) throw new IllegalStateException("superText getter");
      return superText;
    }
    if ("com.oplus.feature.super.text.support.v2".equals(key)) {
      if (fallback) throw new AssertionError("superText v2 default must be false");
      fixture.Probe.superTextV2Reads++;
      if (fixture.Probe.failSuperTextV2) throw new IllegalStateException("superText v2 getter");
      return superTextV2;
    }
    if (!"com.oplus.xpan.legacy.ui.style".equals(key) || fallback) throw new AssertionError("legacy ABI");
    fixture.Probe.legacyReads++;
    if (fixture.Probe.failLegacy) throw new IllegalStateException("legacy getter");
    return legacy;
  }
}''',
}

HARNESS = r'''package com.xpanport.hook;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import de.robv.android.xposed.*;
import de.robv.android.xposed.XC_MethodHook.MethodHookParam;
import fixture.Probe;
import java.lang.reflect.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

public final class C17MenuCheck {
  static final String PANEL="position_mode_panel", HEAD="position_headline";
  static final String SQL="select * from mode_info where position = ?";
  static final String[] COLS={"mode_name","mode_id","position","rank","support_edit","support_front"};
  static XpanC17Menu.InstallResult installed;
  static Toggle gate;
  static int checks;
  static void check(boolean ok,String message) { checks++; if (!ok) throw new AssertionError(message); }
  public static final class Toggle implements XpanC17Menu.Gate {
    boolean on=true, fail;
    int allowed=-1;
    public boolean enabled() {
      if (fail) throw new IllegalStateException("gate");
      if (allowed==0) return false;
      if (allowed>0) allowed--;
      return on;
    }
  }
  static final class DataCursor implements Cursor {
    final List<Object[]> rows; final String[] columns;
    int offset=-1, moves, closes, reported=-1, stopAt=-1, throwMove=-1;
    boolean failClose;
    DataCursor(String[] c,List<Object[]> r) { columns=c.clone(); rows=new ArrayList<>(); for (Object[] row:r) rows.add(row.clone()); }
    public String[] getColumnNames() { return columns.clone(); }
    public int getColumnIndex(String name) { for(int i=0;i<columns.length;i++) if(name.equals(columns[i])) return i; return -1; }
    public int getCount() { return reported<0 ? rows.size() : reported; }
    public boolean moveToNext() {
      moves++; if(moves==throwMove) throw new IllegalStateException("cursor read");
      if(stopAt>=0 && offset+1>=stopAt) return false;
      offset++; return offset<rows.size();
    }
    Object value(int i) { if(offset<0 || offset>=rows.size()) throw new AssertionError("bad cursor row"); return rows.get(offset)[i]; }
    public int getType(int i) {
      Object v=value(i); if(v==null)return 0; if(v instanceof byte[])return 4;
      if(v instanceof String)return 3; if(v instanceof Float || v instanceof Double)return 2;
      if(v instanceof Number)return 1; throw new AssertionError("unsupported fixture type");
    }
    public String getString(int i) { Object v=value(i); return v==null ? null : v.toString(); }
    public long getLong(int i) { return ((Number)value(i)).longValue(); }
    public void close() { closes++; if(failClose)throw new IllegalStateException("cursor close"); }
  }
  static final class FakeDb extends SQLiteDatabase {
    final List<Object[]> rows=new ArrayList<>();
    final List<DataCursor> own=new ArrayList<>();
    String[] columns=COLS.clone();
    int queries, nullAt=-1, throwAt=-1, mutateAt=-1, reported=-1, stopAt=-1, throwMove=-1;
    boolean open=true, failClose;
    Runnable duringQuery;
    FakeDb(List<Object[]> source) { for(Object[] row:source)rows.add(row.clone()); }
    public boolean isOpen() { return open; }
    public Cursor query(String table,String[] projection,String selection,String[] args,
        String group,String having,String order) {
      check("mode_info".equals(table) && projection==null && selection==null && args==null
          && group==null && having==null && order==null,"snapshot not exact whole-table query");
      queries++;
      if(queries==mutateAt && !rows.isEmpty()) rows.get(0)[3]=((Number)rows.get(0)[3]).intValue()+1;
      if(duringQuery!=null) { Runnable callback=duringQuery; duringQuery=null; callback.run(); }
      if(queries==throwAt)throw new IllegalStateException("snapshot query");
      if(queries==nullAt)return null;
      DataCursor cursor=new DataCursor(columns,rows);
      cursor.reported=reported; cursor.stopAt=stopAt; cursor.throwMove=throwMove; cursor.failClose=failClose;
      own.add(cursor); return cursor;
    }
    public void close() { throw new AssertionError("native DB closed"); }
  }
  static Object[] row(String name,int id,String position,int rank,String edit,String front) {
    return new Object[]{name,id,position,rank,edit,front};
  }
  static List<Object[]> basic() {
    ArrayList<Object[]> rows=new ArrayList<>();
    rows.add(row("common",6,HEAD,0,"true","true"));
    rows.add(row("more",23,HEAD,Integer.MAX_VALUE,"false","true"));
    // Intentionally not rank-sorted. The module must retain the caller's native ordering.
    rows.add(row("panorama",0,PANEL,14,"false","false"));
    rows.add(row("fastVideo",1,PANEL,-5,"true","true"));
    rows.add(row("slowVideo",2,PANEL,14,"true","false"));
    return rows;
  }
  static boolean bool(Object value) { return Boolean.parseBoolean(String.valueOf(value)); }
  static ArrayList<fa.a> nativeList(FakeDb db,String position) {
    ArrayList<fa.a> result=new ArrayList<>();
    for(Object[] row:db.rows) if(position.equals(row[2])) result.add(new fa.a((String)row[0],bool(row[4]),
        (String)row[2],((Number)row[3]).intValue(),((Number)row[1]).intValue(),bool(row[5])));
    return result;
  }
  static XC_MethodHook callback(String name) {
    for(Map.Entry<Member,XC_MethodHook> entry:XposedBridge.callbacks.entrySet())
      if(entry.getKey().getName().equals(name))return entry.getValue();
    throw new AssertionError("hook missing: "+name);
  }
  static MethodHookParam frame(Object owner,Object... args) {
    MethodHookParam p=new MethodHookParam(); p.thisObject=owner; p.args=args; return p;
  }
  static MethodHookParam enter(Object owner,String position,boolean init,boolean ordered) throws Throwable {
    MethodHookParam p=frame(owner,position,init,ordered); callback("p").enter(p); return p;
  }
  static DataCursor capture(FakeDb db,String sql,String position) throws Throwable {
    DataCursor original=new DataCursor(COLS,Collections.<Object[]>emptyList());
    MethodHookParam q=frame(db,sql,new String[]{position}); q.result=original;
    callback("rawQuery").exit(q);
    check(q.result==original && q.mutations==0 && q.error==null,"raw query result touched");
    check(original.closes==0 && original.moves==0,"borrowed native cursor consumed/closed");
    return original;
  }
  static MethodHookParam run(FakeDb db,ArrayList<?> original,boolean ordered) throws Throwable {
    return run(db,original,ordered,new mp.h());
  }
  static MethodHookParam run(FakeDb db,ArrayList<?> original,boolean ordered,mp.h owner) throws Throwable {
    MethodHookParam p=enter(owner,PANEL,true,ordered);
    DataCursor nativeCursor=capture(db,SQL+(ordered ? " order by rank" : ""),PANEL);
    p.result=original; callback("p").exit(p);
    check(nativeCursor.closes==0 && nativeCursor.moves==0,"adapter took original cursor ownership");
    assertClosed(db); return p;
  }
  static void assertClosed(FakeDb db) { for(DataCursor cursor:db.own)check(cursor.closes==1,"snapshot cursor leak/double close"); }
  static void preserved(MethodHookParam p,Object original) { check(p.result==original && p.mutations==0,"failed view published/changed original"); }
  static fa.a appended(MethodHookParam p,ArrayList<?> original,int rank) {
    check(p.mutations==1 && p.result instanceof ArrayList && p.result!=original,"no list copy published");
    ArrayList<?> copy=(ArrayList<?>)p.result; check(copy.size()==original.size()+1,"not XPAN-only append");
    for(int i=0;i<original.size();i++)check(copy.get(i)==original.get(i),"original order/identity changed");
    fa.a item=(fa.a)copy.get(copy.size()-1);
    check("xpan".equals(item.a) && item.f==28 && PANEL.equals(item.d) && item.e==rank
        && !item.b && !item.c,"new virtual XPAN must carry edit=false/front=false with original id/rank");
    return item;
  }
  static String fingerprint(List<Object[]> rows) { StringBuilder text=new StringBuilder(); for(Object[] row:rows)text.append(Arrays.deepToString(row)); return text.toString(); }
  static void resetFaults() {
    gate.on=true; gate.fail=false; gate.allowed=-1; XposedBridge.failLog=false;
    Probe.failMode=false; Probe.corruptMode=false; Probe.failLookup=false; Probe.lookupUnexpected=false; Probe.failConfig=false; Probe.failLegacy=false;
    Probe.failSticker=false; Probe.failSuperText=false; Probe.failSuperTextV2=false;
    com.oplus.camera.configure.CameraConfig.supported=true; com.oplus.camera.configure.CameraConfig.legacy=false;
    com.oplus.camera.configure.CameraConfig.sticker=true; com.oplus.camera.configure.CameraConfig.superText=true;
    com.oplus.camera.configure.CameraConfig.superTextV2=false;
  }
  static void install() {
    gate=new Toggle();
    check(Probe.operator==0 && Probe.helper==0 && Probe.mode==0 && Probe.config==0,"premature fixture init");
    installed=XpanC17Menu.install(C17MenuCheck.class.getClassLoader(),"7.013.51",gate);
    check(installed.installed && installed.registeredHooks==2 && installed.validatedBindings==14,"exact ABI install failed: "+installed.detail);
    check(Probe.operator==0 && Probe.helper==0 && Probe.mode==0 && Probe.config==0 && Probe.constructors==0,"install initialized target class");
    check(installed.runtimeStatus().appends==0 && installed.runtimeStatus().reads==0,"install claimed runtime effect");
    check(installed.detail.contains("no DB migration") && installed.detail.contains("unverified"),"misleading install evidence");
    int registrations=XposedBridge.registrations;
    check(XpanC17Menu.install(C17MenuCheck.class.getClassLoader(),"7.013.51",gate)==installed,"double install not idempotent");
    check(!XpanC17Menu.install(C17MenuCheck.class.getClassLoader(),"7.013.51",new Toggle()).installed,"Gate rebind allowed");
    check(!XpanC17Menu.install(null,"7.013.51",gate).installed && !XpanC17Menu.install(C17MenuCheck.class.getClassLoader(),"7.013.51",null).installed
        && !XpanC17Menu.install(C17MenuCheck.class.getClassLoader(),"6.020.876",gate).installed,"invalid install accepted");
    check(registrations==XposedBridge.registrations,"double install stacked hooks");
  }
  static void policy() throws Throwable {
    resetFaults(); FakeDb db=new FakeDb(basic()); String before=fingerprint(db.rows);
    ArrayList<fa.a> original=nativeList(db,PANEL); MethodHookParam first=run(db,original,false);
    fa.a candidate=appended(first,original,15);
    check(db.queries==2 && before.equals(fingerprint(db.rows)),"DB changed/inconsistent snapshot count");
    check(Probe.lookups>0 && installed.runtimeStatus().candidateBuilds>0,"native factory/name lookup unused");
    int builds=installed.runtimeStatus().candidateBuilds;
    MethodHookParam again=run(db,original,false); check(appended(again,original,15)==candidate,"same snapshot candidate not reused");
    check(builds==installed.runtimeStatus().candidateBuilds,"one-time candidate repeated");
    MethodHookParam alreadyVirtual=run(db,(ArrayList<?>)first.result,false); preserved(alreadyVirtual,first.result);
    for(boolean legacy:new boolean[]{false,true}) {
      com.oplus.camera.configure.CameraConfig.legacy=legacy;
      int reads=Probe.legacyReads;
      fa.a readonly=appended(run(db,original,false),original,15);
      check(readonly==candidate && !readonly.b,"legacy skin changed virtual XPAN edit data flag");
      check(Probe.legacyReads==reads+1 && com.oplus.camera.configure.CameraConfig.legacy==legacy,
          "native legacy getter removed or skin configuration changed");
    }
    resetFaults();
    for(String position:new String[]{HEAD,PANEL}) for(boolean editable:new boolean[]{false,true}) {
      db=new FakeDb(basic()); db.rows.add(row("xpan",28,position,-7,String.valueOf(editable),"true"));
      original=nativeList(db,PANEL); ArrayList<fa.a> nativeXpan=nativeList(db,position); before=fingerprint(db.rows);
      fa.a stored=nativeXpan.get(nativeXpan.size()-1);
      if(PANEL.equals(position))stored=original.get(original.size()-1);
      preserved(run(db,original,false),original); check(before.equals(fingerprint(db.rows)),"persisted XPAN moved/flags overwritten");
      check(stored.b==editable && stored.c && stored.e==-7 && stored.f==28 && position.equals(stored.d),
          "persisted XPAN edit/front/rank/position changed");
    }
    // Simulate native save of an item after a prior virtual read: a fresh DB snapshot suppresses it.
    db=new FakeDb(basic()); original=nativeList(db,PANEL); appended(run(db,original,false),original,15);
    db.rows.add(row("xpan",28,HEAD,1,"true","false")); original=nativeList(db,PANEL);
    preserved(run(db,original,false),original);
    db=new FakeDb(basic()); db.rows.add(row("foreign",28,HEAD,2,"true","false")); original=nativeList(db,PANEL);
    preserved(run(db,original,false),original);
    db=new FakeDb(basic()); original=nativeList(db,PANEL); Collections.reverse(original);
    appended(run(db,original,true),original,15); // sorted API never reorders the native list on our behalf.
    db=new FakeDb(basic()); db.rows.removeIf(r->PANEL.equals(r[2])); original=nativeList(db,PANEL);
    appended(run(db,original,false),original,0); // Complete empty panel after normal initialization.
    db=new FakeDb(basic()); for(Object[] r:db.rows)if(PANEL.equals(r[2]))r[3]=-2; original=nativeList(db,PANEL);
    appended(run(db,original,false),original,-1);
    db=new FakeDb(basic()); db.rows.get(2)[3]=Integer.MAX_VALUE; original=nativeList(db,PANEL);
    preserved(run(db,original,false),original);
    db=new FakeDb(basic()); com.oplus.camera.configure.CameraConfig.supported=false; original=nativeList(db,PANEL);
    preserved(run(db,original,false),original); resetFaults();
    com.oplus.camera.configure.CameraConfig.sticker=false;
    com.oplus.camera.configure.CameraConfig.superText=false;
    com.oplus.camera.configure.CameraConfig.superTextV2=false;
    db=new FakeDb(basic()); db.rows.add(row("sticker",7,PANEL,40,"true","false"));
    db.rows.add(row("superText",8,PANEL,41,"true","false")); original=nativeList(db,PANEL);
    original.removeIf(r->"sticker".equals(r.a)||"superText".equals(r.a)); appended(run(db,original,false),original,42);
    check(((ArrayList<?>)run(db,original,false).result).size()==original.size()+1,"generic merge added filtered/unrequested modes");
    resetFaults();
    for(boolean supported:new boolean[]{false,true}) {
      db=new FakeDb(basic()); db.rows.add(row("sticker",7,PANEL,40,"true","false")); original=nativeList(db,PANEL);
      original.removeIf(r->"sticker".equals(r.a)); com.oplus.camera.configure.CameraConfig.sticker=supported;
      int reads=Probe.stickerReads, candidateBefore=installed.runtimeStatus().candidateBuilds, appends=installed.runtimeStatus().appends;
      before=fingerprint(db.rows); MethodHookParam result=run(db,original,false);
      check(Probe.stickerReads==reads+1,"sticker native predicate not consulted for missing row");
      if(supported) {
        preserved(result,original);
        check(installed.runtimeStatus().appends==appends && installed.runtimeStatus().candidateBuilds==candidateBefore,
            "supported missing sticker treated as harmless filtering");
      } else appended(result,original,41);
      check(before.equals(fingerprint(db.rows)),"sticker predicate check changed database");
    }
    for(boolean primary:new boolean[]{false,true}) for(boolean v2:new boolean[]{false,true}) {
      resetFaults(); db=new FakeDb(basic()); db.rows.add(row("superText",8,PANEL,41,"true","false"));
      original=nativeList(db,PANEL); original.removeIf(r->"superText".equals(r.a));
      com.oplus.camera.configure.CameraConfig.superText=primary; com.oplus.camera.configure.CameraConfig.superTextV2=v2;
      int reads=Probe.superTextReads, v2Reads=Probe.superTextV2Reads;
      int candidateBefore=installed.runtimeStatus().candidateBuilds, appends=installed.runtimeStatus().appends;
      before=fingerprint(db.rows); MethodHookParam result=run(db,original,false);
      check(Probe.superTextReads==reads+1 && Probe.superTextV2Reads==v2Reads+(primary ? 0 : 1),
          "superText predicates did not use native short-circuit/default ABI");
      if(primary||v2) {
        preserved(result,original);
        check(installed.runtimeStatus().appends==appends && installed.runtimeStatus().candidateBuilds==candidateBefore,
            "supported missing superText treated as harmless filtering");
      } else appended(result,original,42);
      check(before.equals(fingerprint(db.rows)),"superText predicate check changed database");
    }
    for(int fault=0;fault<3;fault++) {
      resetFaults(); String missing=fault==0 ? "sticker" : "superText";
      db=new FakeDb(basic()); db.rows.add(row(missing,fault==0 ? 7 : 8,PANEL,42,"true","false"));
      original=nativeList(db,PANEL); original.removeIf(r->missing.equals(r.a));
      if(fault==0)Probe.failSticker=true;
      if(fault==1)Probe.failSuperText=true;
      if(fault==2) { com.oplus.camera.configure.CameraConfig.superText=false; Probe.failSuperTextV2=true; }
      int appends=installed.runtimeStatus().appends; before=fingerprint(db.rows);
      preserved(run(db,original,false),original);
      check(installed.runtimeStatus().appends==appends && before.equals(fingerprint(db.rows)),
          "unknown native filter predicate allowed append/write");
    }
    resetFaults(); db=new FakeDb(basic()); db.rows.add(row("superText",8,PANEL,41,"true","false"));
    original=nativeList(db,PANEL); original.removeIf(r->"superText".equals(r.a));
    Probe.failSuperTextV2=true; // Default/primary=true must fail closed without calling v2.
    int v2Reads=Probe.superTextV2Reads; preserved(run(db,original,false),original);
    check(Probe.superTextV2Reads==v2Reads,"native primary=true predicate incorrectly evaluated v2");
    resetFaults();
    db=new FakeDb(basic()); original=nativeList(db,PANEL); original.remove(0);
    preserved(run(db,original,false),original); // Any other missing panel row is a failed/partial native read.
    db=new FakeDb(basic()); original=nativeList(db,PANEL); fa.a row=original.get(0);
    original.set(0,new fa.a(row.a,row.b,row.d,(int)row.e+1,row.f,row.c)); preserved(run(db,original,false),original);
    db=new FakeDb(basic()); original=nativeList(db,PANEL); original.add(original.get(0)); preserved(run(db,original,false),original);
    db=new FakeDb(basic()); original=nativeList(db,PANEL);
    fa.a adjusted=original.get(0); original.set(0,new fa.a(adjusted.a,!adjusted.b,adjusted.d,(int)adjusted.e,adjusted.f,!adjusted.c));
    appended(run(db,original,false),original,15); // Original runtime flags survive, no DB flags overwrite.
    for(int count:new int[]{62,63,64}) {
      List<Object[]> rows=new ArrayList<>(); rows.add(row("common",6,HEAD,0,"true","true"));
      for(int i=1;i<count;i++)rows.add(row("mode"+i,100+i,PANEL,i,"true","false"));
      db=new FakeDb(rows); original=nativeList(db,PANEL);
      if(count==62)appended(run(db,original,false),original,62); else preserved(run(db,original,false),original);
    }
    System.out.println("PASS policy/order/persisted/moved/filter/bounds (no DB migration)");
  }
  static void gates() throws Throwable {
    resetFaults(); FakeDb db=new FakeDb(basic()); ArrayList<fa.a> original=nativeList(db,PANEL);
    gate.on=false; preserved(run(db,original,false),original); check(db.queries==0,"Gateoff performed snapshot");
    gate.fail=true; XposedBridge.failLog=true; preserved(run(db,original,false),original);
    check(!installed.runtimeStatus().enabled,"Gate exception runtime enabled"); resetFaults();
    // Exact callbacks see malformed args even with Gate on; they preserve camera result/throwable.
    for(boolean on:new boolean[]{false,true}) {
      gate.on=on;
      MethodHookParam malformed=frame(null); Object result=new Object(); Throwable failure=new Exception("camera");
      malformed.result=result; malformed.error=failure; callback("p").enter(malformed); callback("p").exit(malformed);
      check(malformed.result==result && malformed.error==failure && malformed.mutations==0,"malformed frame changed original");
    }
    resetFaults();
    MethodHookParam beforeOff=enter(new mp.h(),PANEL,true,false); gate.on=false; capture(db,SQL,PANEL);
    beforeOff.result=original; callback("p").exit(beforeOff); preserved(beforeOff,original);
    gate.on=true;
    MethodHookParam skipped=enter(new mp.h(),PANEL,true,false); capture(db,SQL,PANEL);
    gate.on=false; skipped.result=original; callback("p").exit(skipped); preserved(skipped,original);
    // Sweep every possible Gate boundary, including last setResult check. No intermediate list write.
    resetFaults(); int beforeAppends=installed.runtimeStatus().appends;
    int successful=0, refusedAfterRead=0;
    for(int allowed=0;allowed<18;allowed++) {
      db=new FakeDb(basic()); original=nativeList(db,PANEL); gate.allowed=allowed;
      MethodHookParam p=run(db,original,false);
      if(p.mutations==0) { preserved(p,original); if(db.queries>0)refusedAfterRead++; }
      else { appended(p,original,15); successful++; }
      check(fingerprint(db.rows).equals(fingerprint(basic())),"Gate revocation changed DB");
    }
    check(refusedAfterRead>0 && successful>0 && installed.runtimeStatus().appends==beforeAppends+successful,"Gate sweep lacked commit/refusal controls");
    resetFaults(); db=new FakeDb(basic()); original=nativeList(db,PANEL);
    db.duringQuery=()->gate.on=false; preserved(run(db,original,false),original);
    resetFaults(); System.out.println("PASS Gate revocation/malformed/original throwable boundaries");
  }
  static void faults() throws Throwable {
    resetFaults();
    for(int fault=0;fault<18;fault++) {
      FakeDb db=new FakeDb(basic()); ArrayList<?> original=nativeList(db,PANEL);
      switch(fault) {
        case 0:db.nullAt=1;break; case 1:db.throwAt=1;break; case 2:db.nullAt=2;break;
        case 3:db.throwAt=2;break; case 4:db.failClose=true;break; case 5:db.reported=db.rows.size()+1;break;
        case 6:db.stopAt=2;break; case 7:db.throwMove=2;break;
        case 8:db.columns=new String[]{"mode_name","mode_id","position","rank","support_edit"};break;
        case 9:db.columns[5]="support_edit";break; case 10:db.rows.get(0)[2]="unknown_position";break;
        case 11:db.rows.get(0)[4]=null;break; case 12:db.rows.get(0)[1]=2147483648L;break;
        case 13:db.rows.add(db.rows.get(0).clone());break; case 14:db.open=false;break;
        case 15:db.rows.get(0)[4]="1";break; case 16:db.rows.get(0)[3]=1.5;break;
        case 17:db.rows.get(0)[0]="bad\u0001name";break;
      }
      String before=fingerprint(db.rows); preserved(run(db,original,false),original);
      check(before.equals(fingerprint(db.rows)),"snapshot failure wrote rows");
    }
    // Column order may differ; exact names must map before fields are read.
    FakeDb db=new FakeDb(basic()); ArrayList<fa.a> original=nativeList(db,PANEL);
    for(Object[] r:db.rows) { Object tmp=r[0];r[0]=r[3];r[3]=tmp; }
    String tmp=db.columns[0];db.columns[0]=db.columns[3];db.columns[3]=tmp;
    appended(run(db,original,false),original,15);
    // Whole-row drift during preparation fails before publication, not a pretend rollback.
    db=new FakeDb(basic()); original=nativeList(db,PANEL); db.mutateAt=2; preserved(run(db,original,false),original);
    check(((Number)db.rows.get(0)[3]).intValue()==1,"adapter reverted someone else's native edit");
    // Candidate construction/lookup can fail after partial preparation. No publication/success count.
    for(int fault=0;fault<4;fault++) {
      resetFaults(); db=new FakeDb(basic()); db.rows.get(2)[3]=50+fault; original=nativeList(db,PANEL);
      int appends=installed.runtimeStatus().appends;
      if(fault==0)Probe.failMode=true; if(fault==1)Probe.corruptMode=true;
      if(fault==2)Probe.failLookup=true; if(fault==3)Probe.lookupUnexpected=true;
      preserved(run(db,original,false),original);
      check(installed.runtimeStatus().appends==appends,"candidate failure claimed migrated/view success");
      resetFaults(); appended(run(db,original,false),original,51+fault);
    }
    resetFaults(); db=new FakeDb(basic()); original=nativeList(db,PANEL); Probe.failConfig=true;
    preserved(run(db,original,false),original); resetFaults();
    db=new FakeDb(basic()); original=nativeList(db,PANEL); Probe.failLegacy=true;
    preserved(run(db,original,false),original); resetFaults();
    for(Object result:new Object[]{null,new LinkedList<>(),new ArrayList<>(Arrays.asList("bad")),new ArrayList<>(Arrays.asList((Object)null))}) {
      db=new FakeDb(basic()); MethodHookParam p=enter(new mp.h(),PANEL,true,false); capture(db,SQL,PANEL);
      p.result=result; callback("p").exit(p); preserved(p,result); assertClosed(db);
    }
    db=new FakeDb(basic()); original=nativeList(db,PANEL);
    mp.h owner=new mp.h();owner.g=false;preserved(run(db,original,false,owner),original);
    MethodHookParam p=enter(new mp.h(),PANEL,true,false);capture(db,SQL,PANEL);p.result=original;p.error=new Exception("native list");
    callback("p").exit(p);preserved(p,original);check(p.error!=null,"original throwable swallowed");
    for(String query:new String[]{SQL.toUpperCase(),SQL+" order by rank",SQL+" "}) {
      p=enter(new mp.h(),PANEL,true,false);capture(db,query,PANEL);p.result=original;callback("p").exit(p);preserved(p,original);
    }
    p=enter(new mp.h(),PANEL,true,false);capture(db,SQL,HEAD);p.result=original;callback("p").exit(p);preserved(p,original);
    p=enter(new mp.h(),PANEL,true,false);capture(db,SQL,PANEL);capture(db,SQL,PANEL);p.result=original;callback("p").exit(p);preserved(p,original);
    p=enter(new mp.h(),PANEL,true,false);MethodHookParam query=frame(db,SQL,new String[]{PANEL});query.result=null;
    callback("rawQuery").exit(query);p.result=original;callback("p").exit(p);preserved(p,original);
    p=enter(new mp.h(),PANEL,true,false);query=frame(db,SQL,new String[]{PANEL});query.error=new Exception("query");
    callback("rawQuery").exit(query);p.result=original;callback("p").exit(p);preserved(p,original);
    System.out.println("PASS snapshot/schema/null/partial/candidate faults; no false void-success");
  }
  static ThreadLocal<?> reading() throws Exception {
    Field field=XpanC17Menu.InstallResult.class.getDeclaredField("owner");field.setAccessible(true);Object owner=field.get(installed);
    field=XpanC17Menu.class.getDeclaredField("reading");field.setAccessible(true);return (ThreadLocal<?>)field.get(owner);
  }
  static void nested() throws Throwable {
    resetFaults(); FakeDb db=new FakeDb(basic()); ArrayList<fa.a> original=nativeList(db,PANEL);
    MethodHookParam outer=enter(new mp.h(),PANEL,true,false); Object saved=reading().get();
    check(saved!=null,"outer scope absent");
    // Initialization's init=false read must shield an eligible outer scope, not capture its DB.
    MethodHookParam inner=enter(new mp.h(),PANEL,false,false);capture(new FakeDb(basic()),SQL,PANEL);
    inner.result=original;callback("p").exit(inner);preserved(inner,original);check(reading().get()==saved,"init inner did not restore outer");
    gate.on=false;inner=enter(new mp.h(),PANEL,true,false);gate.on=true;capture(new FakeDb(basic()),SQL,PANEL);
    inner.result=original;callback("p").exit(inner);preserved(inner,original);check(reading().get()==saved,"Gateoff inner leaked capture");
    inner=enter(new mp.h(),HEAD,true,false);capture(db,SQL,HEAD);inner.result=original;callback("p").exit(inner);
    preserved(inner,original);check(reading().get()==saved,"headline inner restored wrong scope");
    capture(db,SQL,PANEL);outer.result=original;callback("p").exit(outer);appended(outer,original,15);
    check(reading().get()==null,"outer thread scope leaked");
    // Fully eligible nested call restores outer even when original throws and Gate/log revoke.
    outer=enter(new mp.h(),PANEL,true,false);saved=reading().get();inner=enter(new mp.h(),PANEL,true,false);
    capture(db,SQL,PANEL);inner.result=original;inner.error=new Exception("camera");gate.fail=true;XposedBridge.failLog=true;
    callback("p").exit(inner);preserved(inner,original);check(reading().get()==saved,"exception inner lost outer");
    callback("p").exit(outer);check(reading().get()==null,"revoke/throw cleanup leaked");resetFaults();
    // A reentrant native read during snapshot preparation cannot recursively project or steal DB.
    db=new FakeDb(basic());original=nativeList(db,PANEL);final ArrayList<fa.a> result=original;
    db.duringQuery=()->{
      try {
        Object prior=reading().get();MethodHookParam reentry=enter(new mp.h(),PANEL,true,false);
        capture(new FakeDb(basic()),SQL,PANEL);reentry.result=result;callback("p").exit(reentry);
        preserved(reentry,result);check(reading().get()==prior,"projection reentry lost current scope");
      } catch(Throwable failure) { throw new AssertionError(failure); }
    };
    appended(run(db,original,false),original,15);check(reading().get()==null,"projection frame leaked");
    assertClosed(db);System.out.println("PASS nested initialization/Gate/reentry frames and cursor ownership");
  }
  static void rollback(String kind) throws Throwable {
    Toggle ownGate=new Toggle();XposedBridge.failAt=2;XposedBridge.failUnhook="residual".equals(kind);
    XpanC17Menu.InstallResult result=XpanC17Menu.install(C17MenuCheck.class.getClassLoader(),"7.013.51",ownGate);
    check(!result.installed && !result.runtimeStatus().enabled,"failed registration active");
    check(result.registeredHooks==(XposedBridge.failUnhook ? 1 : 0),"wrong residual handles");
    check(Probe.operator==0 && Probe.mode==0 && Probe.config==0,"failed install initialized target");
    for(XC_MethodHook callback:new ArrayList<>(XposedBridge.callbacks.values())) {
      MethodHookParam p=frame(null);Object original=new Object();p.result=original;
      callback.enter(p);callback.exit(p);preserved(p,original);
    }
    if(XposedBridge.failUnhook) {
      int registrations=XposedBridge.registrations;
      check(XpanC17Menu.install(C17MenuCheck.class.getClassLoader(),"7.013.51",ownGate)==result,"residual registration retried");
      check(registrations==XposedBridge.registrations,"residual hooks stacked");
    } else {
      XposedBridge.failAt=0;
      check(XpanC17Menu.install(C17MenuCheck.class.getClassLoader(),"7.013.51",ownGate).installed,"clean rollback blocked retry");
    }
    System.out.println("PASS registration rollback "+kind);
  }
  static void wrongAbi() {
    XpanC17Menu.InstallResult result=XpanC17Menu.install(C17MenuCheck.class.getClassLoader(),"7.013.51",new Toggle());
    check(!result.installed && XposedBridge.registrations==0,"ABI mismatch partially installed");
    check(Probe.operator==0 && Probe.helper==0 && Probe.mode==0 && Probe.config==0,"bad ABI install initialized target");
    check(!result.runtimeStatus().enabled,"wrong ABI active");System.out.println("PASS wrong ABI resolves before registration");
  }
  // Fixture data was exported from a *new temporary* Python SQLite DB. No device/database path is accepted.
  static List<Object[]> fixture(String path) throws Exception {
    List<Object[]> rows=new ArrayList<>();
    for(String line:Files.readAllLines(Paths.get(path),StandardCharsets.UTF_8)) {
      String[] fields=line.split("\\t",-1);check(fields.length==6,"fixture schema");
      rows.add(row(fields[0],Integer.parseInt(fields[1]),fields[2],Integer.parseInt(fields[3]),fields[4],fields[5]));
    }
    return rows;
  }
  static final class ModeManager {
    String actual="common";
    boolean o0(String name) { check("xpan".equals(name),"menu selected foreign feature");actual=name;return true; }
  }
  static void selection(String path) throws Throwable {
    install();FakeDb db=new FakeDb(fixture(path));ArrayList<fa.a> original=nativeList(db,PANEL);
    MethodHookParam view=run(db,original,false);ArrayList<?> projected=(ArrayList<?>)view.result;
    check(projected.size()==original.size()+1,"fixture menu entry absent");
    fa.a virtual=(fa.a)projected.get(projected.size()-1);
    check(!virtual.b && !virtual.c && PANEL.equals(virtual.d),"new XPAN data flags/More modes position changed");
    // Only fa.a data flags are proved; op.d.b -> mp.k does not forward b. No fake GUI edit gate.
    check(((fa.a)projected.get(0)).b && !((fa.a)projected.get(1)).b,"existing data edit flags changed");
    // Stock op.d.b converts fa.a.f to mp.k.f, click passes id, yp.i.g is a static switch
    // pinned by the Python decoded test. The fake handoff models no by-id DB query.
    ArrayList<Integer> menu=new ArrayList<>();for(Object item:projected)menu.add(((fa.a)item).f);
    int id=menu.get(menu.size()-1);check(id==28,"native menu id changed");
    ModeManager manager=new ModeManager();String mode=id==28 ? "xpan" : "common";
    check(manager.o0(mode) && "xpan".equals(manager.actual),"load -> menu id -> mode name -> native manager failed");
    check(db.queries==2 && installed.runtimeStatus().appends==1,"selector required another DB row lookup");
    check(db.rows.stream().noneMatch(r->"xpan".equals(r[0])),"selection fabricated stored XPAN row");
    System.out.println("PASS isolated load -> native id28/name -> selector handoff (not Android/HAL evidence)");
  }
  public static void main(String[] args) throws Throwable {
    if(args[0].equals("rollback")||args[0].equals("residual"))rollback(args[0]);
    else if(args[0].equals("bad-abi"))wrongAbi();
    else if(args[0].equals("fixture"))selection(args[1]);
    else {
      install();
      if(args[0].equals("policy"))policy();else if(args[0].equals("gates"))gates();
      else if(args[0].equals("faults"))faults();else if(args[0].equals("nested"))nested();
      else throw new AssertionError("unknown suite");
    }
    System.out.println("Checks="+checks+"; no actual DB or device operations");
  }
}'''


class C17MenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.javac = shutil.which("javac")
        cls.java = shutil.which("java")
        if not cls.javac or not cls.java:
            raise RuntimeError("javac and java required for production callback tests")
        cls.temporary = tempfile.TemporaryDirectory(prefix="c17-menu-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.folder = Path(cls.temporary.name)
        cls.classes = cls.compile_fakes(cls.folder / "normal")

    @classmethod
    def compile_fakes(cls, folder, replacement=None):
        sources = []
        content = {**STUBS, "com/xpanport/hook/C17MenuCheck.java": HARNESS}
        if replacement:
            content.update(replacement)
        for name, text in content.items():
            target = folder / "sources" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            sources.append(str(target))
        classes = folder / "classes"
        classes.mkdir(parents=True)
        subprocess.run([cls.javac, "-encoding", "UTF-8", "-source", "8", "-target", "8",
                        "-d", str(classes), str(HOOK), *sources], check=True, cwd=ROOT)
        return classes

    def run_java(self, suite, *args, classes=None):
        subprocess.run([self.java, "-cp", str(classes or self.classes),
                        "com.xpanport.hook.C17MenuCheck", suite, *map(str, args)], check=True, cwd=ROOT)

    def test_exact_decoded_native_menu_abi_and_schema(self):
        operator = (DECODED / "smali_classes7/mp/h.smali").read_text(encoding="utf-8")
        helper = (DECODED / "smali_classes7/mp/j.smali").read_text(encoding="utf-8")
        mode = (DECODED / "smali/fa/a.smali").read_text(encoding="utf-8")
        config = (DECODED / "smali/com/oplus/camera/configure/CameraConfig.smali").read_text(encoding="utf-8")
        for source, descriptors in ((operator, (
                "p(Ljava/lang/String;ZZ)Ljava/util/ArrayList;",
                "r(Lfa/a;Ljava/util/List;Ljava/util/List;)Lfa/a;",
                "a(Ljava/util/ArrayList;Landroid/database/sqlite/SQLiteDatabase;)V",
                "j(Landroid/database/sqlite/SQLiteDatabase;Ljava/util/List;Ljava/util/List;)V")),
                (mode, ("<init>(Ljava/lang/String;ZLjava/lang/String;IIZ)V",)),
                (config, ("getConfigBooleanValue(Ljava/lang/String;)Z", "d(Ljava/lang/String;ZZ)Z"))):
            for descriptor in descriptors:
                with self.subTest(descriptor=descriptor):
                    smali_method(source, descriptor)
        for name, descriptor in {"a": "Ljava/lang/String;", "b": "Z", "c": "Z",
                                 "d": "Ljava/lang/String;", "e": "I", "f": "I"}.items():
            self.assertIn(f".field public final {name}:{descriptor}", mode)
        self.assertIn(".field public final a:Lmp/j;", operator)
        self.assertIn(".field public g:Z", operator)
        self.assertIn(".super Landroid/database/sqlite/SQLiteOpenHelper;", helper)
        self.assertIn(SCHEMA.replace("create table ", "create table if not exists ") + ";", helper)
        constructor = smali_method(mode, "<init>(Ljava/lang/String;ZLjava/lang/String;IIZ)V")
        for param, field in {"p1": "a:Ljava/lang/String;", "p2": "b:Z", "p3": "d:Ljava/lang/String;",
                             "p4": "e:I", "p5": "f:I", "p6": "c:Z"}.items():
            self.assertRegex(constructor, r"iput(?:-object|-boolean)? " + param + r", p0, Lfa/a;->" + re.escape(field))
        reader = smali_method(operator, "p(Ljava/lang/String;ZZ)Ljava/util/ArrayList;")
        for literal in (HEADLINE, PANEL, "select * from mode_info where position = ?",
                        "select * from mode_info where position = ? order by rank"):
            self.assertIn('"' + literal + '"', reader)
        self.assertIn("iget-boolean p2, p0, Lmp/h;->g:Z", reader)
        self.assertIn("Landroid/database/sqlite/SQLiteOpenHelper;->getReadableDatabase()", reader)
        self.assertIn("Ljava/lang/Boolean;->parseBoolean(Ljava/lang/String;)Z", reader)
        self.assertIn("Lmp/h;->s(ILjava/lang/String;)V", reader)  # Don't replay this supposedly read-only API.
        instructions = "\n".join(line.strip() for line in reader.splitlines()
                                 if line.strip() and not line.strip().startswith((".", "#")))
        sticker = instructions.split('const-string v1, "com.oplus.app.feature.sticker.support"', 1)[1].split(":cond_5", 1)[0]
        self.assertIn("invoke-static {v1}, Lcom/oplus/camera/configure/CameraConfig;->getConfigBooleanValue(Ljava/lang/String;)Z", sticker)
        self.assertIn("move-result v1\nif-eqz v1, :cond_4", sticker)
        self.assertIn('query, unsupport the sticker mode.', sticker)
        super_text = instructions.split('const-string v1, "com.oplus.camera.super.text.mode.support"', 1)[1].split("const-string v1, \"com.oplus.feature.tilt.shift.photo.support\"", 1)[0]
        self.assertIn("const/4 v3, 0x1\nconst/4 v8, 0x0\ninvoke-static {v1, v3, v8}, Lcom/oplus/camera/configure/CameraConfig;->d(Ljava/lang/String;ZZ)Z", super_text)
        self.assertIn('move-result v1\nif-nez v1, :cond_6\nconst-string v1, "com.oplus.feature.super.text.support.v2"', super_text)
        self.assertIn("invoke-static {v1, v8, v8}, Lcom/oplus/camera/configure/CameraConfig;->d(Ljava/lang/String;ZZ)Z\nmove-result v1\nif-nez v1, :cond_6", super_text)
        self.assertIn('query, unsupport the super text mode.', super_text)
        merge = smali_method(operator, "j(Landroid/database/sqlite/SQLiteDatabase;Ljava/util/List;Ljava/util/List;)V")
        xpan = merge.split('const-string v4, "com.oplus.feature.xpan.mode.support"', 1)[1].split(":cond_26", 2)[2]
        self.assertIn('const-string v9, "xpan"', merge)
        self.assertIn("const/16 v13, 0x1c", merge)
        self.assertIn("xor-int/lit8 v10, v4, 0x1", merge)
        self.assertIn("Lmp/h;->r(Lfa/a;Ljava/util/List;Ljava/util/List;)Lfa/a;", merge)
        self.assertIn("const-string v4, \"com.oplus.feature.telephoto.mode.support\"", xpan)
        native_insert = smali_method(operator, "a(Ljava/util/ArrayList;Landroid/database/sqlite/SQLiteDatabase;)V")
        self.assertIn(".catch Landroid/database/SQLException;", native_insert)
        self.assertIn("Lcom/oplus/camera/data/DataManager;->g(", native_insert)
        self.assertNotIn("beginTransaction", native_insert)
        source = HOOK.read_text(encoding="utf-8")
        self.assertIn('cls(loader, "mp.h")', source)
        self.assertIn('method(operator, "p", ArrayList.class, false, String.class, boolean.class, boolean.class)', source)
        self.assertIn('method(operator, "r", modeInfo, true, modeInfo, List.class, List.class)', source)

    def test_decoded_load_and_click_use_static_id_name_not_db_row(self):
        data = (DECODED / "smali_classes7/op/d.smali").read_text(encoding="utf-8")
        load = smali_method(data, "b()V")
        self.assertIn('"position_mode_panel"', load)
        self.assertIn("Lmp/h;->p(Ljava/lang/String;ZZ)Ljava/util/ArrayList;", load)
        self.assertIn("Lfa/a;->f:I", load)
        self.assertIn("Lmp/k;-><init>(Landroid/content/Context;II)V", load)
        click = smali_method((DECODED / "smali_classes7/op/e$c.smali").read_text(encoding="utf-8"),
                             "onClick(Landroid/view/View;)V")
        self.assertIn("Lmp/k;->f:I", click)
        selector = smali_method((DECODED / "smali_classes7/jp/o0$f.smali").read_text(encoding="utf-8"), "c(I)V")
        self.assertIn("Lyp/i;->g(I)Ljava/lang/String;", selector)
        self.assertIn("CameraModeChangeEventMessage;-><init>(Ljava/lang/String;)V", selector)
        self.assertIn("Ljp/o0;->Gc(", selector)
        self.assertNotIn("Lmp/h;", selector)
        headline = (DECODED / "smali_classes7/yp/i.smali").read_text(encoding="utf-8")
        mapping = smali_method(headline, "g(I)Ljava/lang/String;")
        switch = re.search(r"\.packed-switch 0x0\s+(.*?)\.end packed-switch", mapping, re.S).group(1)
        labels = re.findall(r":pswitch_\w+", switch)
        label = labels[28]
        self.assertIn(label + '\n    const-string p0, "xpan"', mapping)
        self.assertNotIn("SQLite", mapping)
        self.assertNotIn("Lmp/h;", mapping)

    def test_no_writes_early_open_reset_or_generic_merge(self):
        source = HOOK.read_text(encoding="utf-8")
        self.assertLess(source.index("b.resolve(loader);"), source.index("h.register();"))
        self.assertLess(source.index("h.register();"), source.index("h.enabled = true;"))
        self.assertIn("Class.forName(name, false, loader)", source)
        self.assertIn("db.query(TABLE, null, null, null, null, null, null)", source)
        self.assertIn("try (Cursor cursor", source)
        self.assertIn("p.setResult(copy)", source)
        self.assertIn("boolean edit = false;", source)
        self.assertIn("b.legacy.invoke(null, LEGACY, false, false);", source)
        self.assertIn('method(config, "d", boolean.class, true, String.class, boolean.class, boolean.class)', source)
        self.assertNotIn("boolean edit = !", source)
        self.assertIn("menu item in More modes (support_edit=false)", source)
        self.assertIn("device UI/edit controls unverified", source)
        self.assertNotIn("read-only menu item", source)
        self.assertIn("if (!nativeFiltersMissing(row.name))", source)
        self.assertIn("!((Boolean) b.support.invoke(null, STICKER_SUPPORT))", source)
        self.assertIn("!((Boolean) b.legacy.invoke(null, SUPER_TEXT_SUPPORT, true, false))", source)
        self.assertIn("&& !((Boolean) b.legacy.invoke(null, SUPER_TEXT_V2, false, false))", source)
        self.assertIn("new ArrayList<>(original)", source)
        self.assertEqual(source.count("p.setResult("), 1)
        self.assertEqual(source.count("public static synchronized InstallResult install("), 1)
        for forbidden in ("execSQL(", "rawQuery.invoke", "getReadableDatabase(", "getWritableDatabase(",
                          "SQLiteDatabase.open", "beginTransaction(", "setTransactionSuccessful(",
                          "endTransaction(", "setVersion(", "delete(", "update(", "insert(",
                          "getInstance(", "DataManager;", "SharedPreferences", "FileOutputStream",
                          "setBoolean(", "setInt(", "invokeOriginalMethod", "SQLiteOpenHelper.close",
                          "db.close(", "helper.close(", "method(operator, \"j\"", "method(operator, \"a\"",
                          "p.setThrowable(", "debug.xpan", "DROP TABLE", "reset-setting"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        self.assertIn("frame.projecting", source)
        self.assertIn("if (frame.prior == null) reading.remove(); else reading.set(frame.prior)", source)
        self.assertIn("no DB migration", source)
        self.assertNotIn(b"\r", HOOK.read_bytes())

    def test_compile_against_real_android_and_xposed_api(self):
        self.assertTrue(ANDROID.is_file(), str(ANDROID))
        self.assertTrue(XPOSED.is_file(), str(XPOSED))
        classes = self.folder / "api-compile"
        classes.mkdir()
        subprocess.run([self.javac, "-encoding", "UTF-8", "-source", "8", "-target", "8",
                        "-bootclasspath", str(ANDROID), "-cp", os.pathsep.join(map(str, (ANDROID, XPOSED))),
                        "-d", str(classes), str(HOOK)], check=True, cwd=ROOT)

    def test_production_policy_preserves_rank_order_existing_and_unrequested_modes(self):
        self.run_java("policy")

    def test_production_gate_off_revoke_and_null_frames(self):
        self.run_java("gates")

    def test_production_schema_list_failures_and_partial_native_preparation(self):
        self.run_java("faults")

    def test_production_nested_raw_query_capture_restores_frames(self):
        self.run_java("nested")

    def test_double_install_registration_rollback_and_disabled_residual(self):
        self.run_java("rollback")
        self.run_java("residual")

    def test_abi_failure_before_any_registration_or_target_static_init(self):
        for field, replacement in (("rank", STUBS["fa/a.java"].replace("public final int e,f;", "public final long e; public final int f;")),
                                   ("reader", STUBS["mp/h.java"].replace("public final ArrayList<fa.a> p(", "public final List<fa.a> p("))):
            with self.subTest(abi=field):
                name = "fa/a.java" if field == "rank" else "mp/h.java"
                classes = self.compile_fakes(self.folder / ("bad-" + field), {name: replacement})
                self.run_java("bad-abi", classes=classes)

    def test_temporary_sqlite_fixture_load_to_select_and_storage_unchanged(self):
        # Sole actual SQLite here: a brand new temporary local test fixture, never mode_data.db.
        rows = [("common", 6, HEADLINE, 0, "true", "true"),
                ("more", 23, HEADLINE, 2147483647, "false", "true"),
                ("panorama", 0, PANEL, 12, "true", "false"),
                ("fastVideo", 1, PANEL, -3, "false", "true")]
        database = self.folder / "fixture.sqlite"
        exported = self.folder / "fixture.tsv"
        with closing(sqlite3.connect(database)) as db:
            db.execute(SCHEMA)
            db.executemany("insert into mode_info values (?,?,?,?,?,?)", rows)
            db.execute("pragma user_version=4")
            db.commit()
            before = db.execute("select * from mode_info order by rowid").fetchall()
            columns = tuple(column[1] for column in db.execute("pragma table_info(mode_info)"))
            self.assertEqual(columns, COLUMNS)
            exported.write_text("\n".join("\t".join(map(str, row)) for row in before) + "\n", encoding="utf-8")
            digest_before = database.read_bytes()
            self.run_java("fixture", exported)
            self.assertEqual(db.execute("select * from mode_info order by rowid").fetchall(), before)
            self.assertEqual(db.execute("pragma user_version").fetchone()[0], 4)
            self.assertEqual(database.read_bytes(), digest_before)
            self.assertFalse(any(row[0] == "xpan" for row in before))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decoded-root", type=Path, default=DECODED)
    parser.add_argument("--android-jar", type=Path, default=ANDROID)
    options = parser.parse_args()
    DECODED, ANDROID = options.decoded_root.resolve(), options.android_jar.resolve()
    unittest.main(argv=[__file__], verbosity=2)
