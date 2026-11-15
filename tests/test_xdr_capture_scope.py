"""Offline fresh-capture POLICY STUDY, not a working capture-scope hook.

The Java model lives under tests/support, outside the release hook/*.java build.
Fake evidence exercises its fail-closed policy only. Optional local smali checks
record verified fragments and why dispatch/helper callbacks are not freshness
proof; they are deliberately NOT an operative ABI/mapping contract.
No device, install, original photo I/O, main hook edits, or full-suite invocation.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "tests/support/XdrCaptureScopePolicy.java"
C16 = ROOT.parent / "XpanPort/11_反编译/usdk_smali/smali"
C17 = ROOT / "_tmp/decoded-PJX110_17.0.1.201/sdk/smali"
SDK = "com/oplus/ocs/camera/"
REQUEST = "Landroid/hardware/camera2/CaptureRequest;"
TAG = "Lcom/oplus/ocs/camera/common/util/CameraRequestTag;"
APS = "Lcom/oplus/ocs/camera/consumer/apsAdapter/adapter/"
SCENARIOS = (
    "fresh_and_updates", "order_and_context", "replay", "collisions",
    "recovery_and_reprocess", "failures", "gate", "timebounds",
    "bounded_refs", "config_and_cross_policy", "reentrancy", "concurrency",
)


def body(source, declaration):
    """Read one exact observed smali method, never infer behavior from its name."""
    marker = ".method " + declaration + "\n"
    if marker not in source:
        raise AssertionError("original fragment missing: " + declaration)
    return source.split(marker, 1)[1].split(".end method", 1)[0]


class CaptureScopeStudyTests(unittest.TestCase):
    def test_study_is_outside_release_and_has_no_operative_adapter(self):
        source = POLICY.read_text(encoding="utf-8")
        self.assertIn("TEST SUPPORT ONLY", source)
        self.assertIn("package com.xpanport.hook.study;", source)
        self.assertIn("final class XdrCaptureScopePolicy", source)
        self.assertIn("Passing these tests does not establish camera/XDR coverage", source)
        self.assertFalse((ROOT / "hook/XdrCaptureScope.java").exists())
        build = (ROOT / "tools/build_hook.py").read_text(encoding="utf-8")
        self.assertIn('sources = sorted(HOOK.glob("*.java"))', build)
        self.assertNotIn(POLICY, list((ROOT / "hook").glob("*.java")))
        for name in ("XpanHook.java", "XdrBeforeSave.java"):
            self.assertNotIn("XdrCaptureScope", (ROOT / "hook" / name).read_text(encoding="utf-8"))
        for forbidden in ("import android.", "de.robv.", "Class.forName(", "hookMethod(",
                          "InstallResult", "setResult(", "setThrowable(", "java.io.",
                          "java.nio.", "System.currentTimeMillis", "System.load", "Thread(",
                          "ThreadLocal", "->", ".hashCode()", ".equals(", "getDeclaredFields("):
            self.assertNotIn(forbidden, source)

    def test_local_original_fragments_are_not_a_scope_contract(self):
        required = ("producer/device/Camera2Impl.smali", "producer/device/Camera2Impl$CaptureCallback.smali",
                    "consumer/ConsumerImpl.smali", "consumer/ApsProcessor.smali",
                    "consumer/apsAdapter/APSClient.smali")
        missing = [str(root / SDK / name) for root in (C16, C17) for name in required
                   if not (root / SDK / name).is_file()]
        if missing:
            self.skipTest("optional original SDK fragments unavailable; no mapping verified: " + missing[0])
        for root in (C16, C17):
            with self.subTest(sdk=str(root)):
                files = {name: (root / SDK / name).read_text(encoding="utf-8") for name in required}
                impl = files[required[0]]
                callback = body(files[required[1]], "public onCaptureStarted("
                                "Landroid/hardware/camera2/CameraCaptureSession;" + REQUEST + "JJ)V")
                self.assertIn("{p2}, " + REQUEST + "->getTag()Ljava/lang/Object;", callback)
                self.assertIn("move-object v1, p2", callback)
                self.assertIn("move-wide v2, p3", callback)
                self.assertIn("move-wide v4, p5", callback)
                self.assertIn("CameraPictureCallbackAdapter;->onCaptureStarted(" + REQUEST + "JJ)V", callback)
                consumer = body(files[required[2]], "private onPictureMetaStart(" + REQUEST + "J)V")
                self.assertIn(REQUEST + "->hashCode()I", consumer)
                self.assertIn("IProcessor;->onPictureMetaStart(" + TAG + "IJ)V", consumer)
                processor = files[required[3]]
                start = body(processor, "public onPictureMetaStart(" + TAG + "IJ)V")
                self.assertIn("iput-wide p3, p0, Lcom/oplus/ocs/camera/consumer/ApsProcessor;->mCaptureMergeIdentity:J", start)
                image_meta = body(processor, "public addPictureMeta(" + TAG
                                  + "Landroid/media/ImageReader;Landroid/media/Image;Ljava/lang/String;"
                                  "Lcom/oplus/ocs/camera/common/surface/SurfaceKey;)V")
                self.assertIn("mCaptureRequestMap:Ljava/util/Map;", image_meta)
                self.assertIn("IConsumer;->onCaptureStarted(" + REQUEST + "JI)V", image_meta)
                # Recovery/reprocess can use the SAME Camera2 capture callback helper.
                reprocess = body(impl, "public reprocessImage(Landroid/media/Image;"
                                 "Landroid/hardware/camera2/TotalCaptureResult;Landroid/graphics/Rect;"
                                 + TAG + "Lcom/oplus/ocs/camera/metadata/parameter/Parameter;Landroid/os/Handler;)V")
                self.assertIn("mPictureCallback:Lcom/oplus/ocs/camera/producer/device/Camera2Impl$CaptureCallback;", reprocess)
                self.assertIn("CameraCaptureSession;->capture(" + REQUEST, reprocess)
                take = body(impl, "public takePicture(" + TAG
                            + "Lcom/oplus/ocs/camera/metadata/parameter/Parameter;Landroid/os/Handler;ZZ)V")
                self.assertRegex(take, r"invoke-static \{\}, Ljava/lang/System;->currentTimeMillis\(\)J\s+"
                                 r"move-result-wide (v\d+)\s+iput-wide \1, v\d+, "
                                 + re.escape(TAG + "->mCaptureStartedTimestamp:J"))
                client = files[required[4]]
                self.assertRegex(client, r'const-string v4, "resultinfo_identity"[\s\S]{0,650}'
                                 r'Ljava/lang/Long;->parseLong\(Ljava/lang/String;\)J[\s\S]{0,120}'
                                 + re.escape(APS + "ApsResult;->mIdentity:J"))
                meta = body(processor, "public addPictureMeta(" + TAG + APS + "ApsCaptureResult;Z)V")
                self.assertIn("KEY_MERGE_IDENTITY:", meta)
                if root == C17:
                    merge = body(processor, "private getMergeIdentity(I)J")
                    self.assertIn("mRequestIdToMergeIdentity:Ljava/util/Map;", merge)
                    self.assertIn("mCaptureMergeIdentity:J", merge)  # Global fallback is not request proof.
                    self.assertIn(";->getMergeIdentity(I)J", meta)
                else:
                    self.assertIn("mCaptureMergeIdentity:J", meta)

    def test_java8_fake_evidence_policy(self):
        javac, java = shutil.which("javac"), shutil.which("java")
        self.assertIsNotNone(javac, "Java compiler required for policy study")
        self.assertIsNotNone(java, "Java runtime required for policy study")
        # Only authored test support is compiled/run; no original SDK code executes.
        with tempfile.TemporaryDirectory(prefix="xdr-capture-scope-study-") as temporary:
            temporary = Path(temporary)
            harness = temporary / "CaptureScopeCheck.java"
            harness.write_text(HARNESS, encoding="utf-8")
            classes = temporary / "classes"
            classes.mkdir()
            compiled = subprocess.run(
                [javac, "-encoding", "UTF-8", "--release", "8", "-Xlint:all", "-Werror",
                 "-d", str(classes), str(POLICY), str(harness)],
                cwd=ROOT, timeout=60, capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(0, compiled.returncode, compiled.stdout + compiled.stderr)
            for scenario in SCENARIOS:
                with self.subTest(scenario=scenario):
                    completed = subprocess.run(
                        [java, "-cp", str(classes), "com.xpanport.hook.study.CaptureScopeCheck", scenario],
                        cwd=ROOT, timeout=30, capture_output=True, text=True, encoding="utf-8", errors="replace")
                    self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
                    self.assertIn("PASS " + scenario + "; policy study only, NOT a camera hook", completed.stdout)
                    print(completed.stdout.strip())


HARNESS = r'''package com.xpanport.hook.study;
import java.lang.ref.WeakReference;
import java.lang.reflect.Field;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicReference;

public final class CaptureScopeCheck {
  static class Identity {
    public int hashCode() { throw new AssertionError("must not hash request/owner"); }
    public boolean equals(Object o) { throw new AssertionError("must not compare by value"); }
  }
  static final class Owner extends Identity {
    long identity; final byte[] untouched = {1, 2, 3};
    Owner(long identity) { this.identity = identity; }
  }
  static final class Gate implements XdrCaptureScopePolicy.Gate {
    boolean allow = true, fail; Runnable during;
    public boolean enabled() {
      if (during != null) during.run();
      if (fail) throw new AssertionError("gate failed");
      return allow;
    }
  }
  static final class Clock implements XdrCaptureScopePolicy.Clock {
    long time = 5000; boolean fail; Runnable during;
    public long elapsedMillis() {
      if (during != null) during.run();
      if (fail) throw new AssertionError("clock failed");
      return time;
    }
  }
  static final class E {
    final Gate gate = new Gate(); final Clock clock = new Clock();
    final Identity device = new Identity(), session = new Identity(), request = new Identity();
    final XdrCaptureScopePolicy p; XdrCaptureScopePolicy.Token token;
    final Owner owner = new Owner(7);
    E() { this(1000, 16); }
    E(long ttl, int capacity) { p = new XdrCaptureScopePolicy(gate, clock, ttl, capacity); }
    void pending() { token = p.newRequest(device, session, request, false); check(token != null, "pending registration"); }
    void start() { check(p.captureStarted(token, device, session, request, 99, 0), "real start evidence"); }
    void link() { check(p.linkApsIdentity(token, device, session, request, 99, 0, 7), "exact bridge evidence"); }
    void bind() { check(p.bindOwner(token, owner, 7), "owner provenance evidence"); }
    void ready() { pending(); start(); link(); bind(); }
    boolean eligible() { return p.ownerEligible(owner, owner.identity); }
  }
  static void check(boolean value, String why) { if (!value) throw new AssertionError(why); }
  static Object field(Object value, String name) throws Exception {
    Field f = value.getClass().getDeclaredField(name); f.setAccessible(true); return f.get(value);
  }
  static void clear(Object value, String name) throws Exception {
    ((WeakReference<?>) field(value, name)).clear(); // Deterministic GC simulation, never clear a strong ref.
  }
  static void noStrong(XdrCaptureScopePolicy.Token t) throws Exception {
    for (String name : new String[]{"strongDevice", "strongSession", "strongRequest"})
      check(field(t, name) == null, "strong reference outlived fixed TTL/bind/revocation");
  }
  static void fresh() {
    E e = new E(); byte[] original = e.owner.untouched;
    e.pending(); check(!e.eligible(), "request/dispatch alone is not proof");
    e.start(); check(!e.eligible(), "callback alone is not proof");
    e.link(); check(!e.eligible(), "APS numeric identity alone is not owner proof");
    e.bind();
    for (int i = 0; i < 64; i++) check(e.eligible(), "own save/storage updates must remain eligible");
    check(!e.p.ownerEligible(new Owner(7), 7), "duplicate/recovered owner borrowed numeric identity");
    check(!e.p.ownerEligible(null, 7), "null owner");
    check(e.eligible(), "unknown owner lookup revoked rightful shot");
    check(original == e.owner.untouched && original[0] == 1 && original[2] == 3, "policy touched image bytes");
  }
  static void order() {
    E noToken = new E();
    check(!noToken.p.captureStarted(null, noToken.device, noToken.session, noToken.request, 99, 0), "callback minted token");
    check(noToken.p.retainedCount() == 0, "unknown callback registered evidence");
    E early = new E(); early.pending();
    check(!early.p.linkApsIdentity(early.token, early.device, early.session, early.request, 99, 0, 7), "APS before start");
    check(!early.p.captureStarted(early.token, early.device, early.session, early.request, 99, 0), "out-of-order resurrected");
    E owner = new E(); owner.pending(); owner.start();
    check(!owner.p.bindOwner(owner.token, owner.owner, 7), "owner before APS bridge");
    for (int i = 0; i < 3; i++) {
      E e = new E(); e.pending();
      check(!e.p.captureStarted(e.token, i == 0 ? new Identity() : e.device,
          i == 1 ? new Identity() : e.session, i == 2 ? new Identity() : e.request, 99, 0), "wrong context accepted");
      check(!e.p.captureStarted(e.token, e.device, e.session, e.request, 99, 0), "contradiction resurrected");
    }
    for (long[] tuple : new long[][]{{0, 0}, {-1, 0}, {99, -1}}) {
      E e = new E(); e.pending();
      check(!e.p.captureStarted(e.token, e.device, e.session, e.request, tuple[0], tuple[1]), "invalid sensor tuple");
    }
    for (long[] tuple : new long[][]{{98, 0, 7}, {99, 1, 7}, {99, 0, 0}, {99, 0, -1}}) {
      E e = new E(); e.pending(); e.start();
      check(!e.p.linkApsIdentity(e.token, e.device, e.session, e.request, tuple[0], tuple[1], tuple[2]), "APS tuple/identity mismatch");
      check(!e.p.bindOwner(e.token, e.owner, 7), "mismatch stayed eligible");
    }
    E nil = new E(); nil.pending(); nil.start(); nil.link();
    check(!nil.p.bindOwner(nil.token, null, 7), "null bound owner");
  }
  static void replay() {
    E request = new E(); request.ready();
    check(request.p.newRequest(new Identity(), new Identity(), request.request, false) == null, "same request replay");
    check(!request.eligible(), "request collision kept first owner eligible");
    E start = new E(); start.ready();
    check(!start.p.captureStarted(start.token, start.device, start.session, start.request, 99, 0), "duplicate start");
    check(!start.eligible(), "duplicate start kept shot");
    E aps = new E(); aps.ready();
    check(!aps.p.linkApsIdentity(aps.token, aps.device, aps.session, aps.request, 99, 0, 7), "duplicate APS evidence");
    check(!aps.eligible(), "duplicate APS kept shot");
    E owner = new E(); owner.ready();
    check(!owner.p.bindOwner(owner.token, new Owner(7), 7), "duplicate owner binding");
    check(!owner.eligible(), "duplicate owner kept shot");
    E expired = new E(); expired.ready(); expired.clock.time += 1000;
    check(!expired.eligible(), "expired shot");
    check(expired.p.newRequest(expired.device, expired.session, expired.request, false) == null, "expired request replay");
    check(!expired.p.bindOwner(expired.token, expired.owner, 7), "expired token revived");
  }
  static void collisions() {
    E tuple = new E(); tuple.ready(); Identity another = new Identity();
    XdrCaptureScopePolicy.Token t = tuple.p.newRequest(tuple.device, tuple.session, another, false);
    check(!tuple.p.captureStarted(t, tuple.device, tuple.session, another, 99, 0), "same sensor frame collision");
    check(!tuple.eligible(), "sensor collision kept old owner");
    E aps = new E(); aps.ready(); Identity d = new Identity(), s = new Identity(), r = new Identity();
    t = aps.p.newRequest(d, s, r, false);
    check(aps.p.captureStarted(t, d, s, r, 199, 12), "separate camera start");
    check(!aps.p.linkApsIdentity(t, d, s, r, 199, 12, 7), "global APS collision hidden by camera/mode");
    check(!aps.eligible(), "APS collision kept old owner");
    E owner = new E(); owner.ready(); r = new Identity();
    t = owner.p.newRequest(owner.device, owner.session, r, false);
    check(owner.p.captureStarted(t, owner.device, owner.session, r, 100, 1), "second start");
    check(owner.p.linkApsIdentity(t, owner.device, owner.session, r, 100, 1, 8), "second bridge");
    check(!owner.p.bindOwner(t, owner.owner, 8) && !owner.eligible(), "same owner assigned to different shots");
    E separate = new E(); separate.ready(); r = new Identity(); s = new Identity();
    t = separate.p.newRequest(separate.device, s, r, false);
    check(separate.p.captureStarted(t, separate.device, s, r, 99, 0), "frame numbers need session context");
    check(separate.p.linkApsIdentity(t, separate.device, s, r, 99, 0, 8), "independent identity");
    Owner own = new Owner(8); check(separate.p.bindOwner(t, own, 8), "independent owner");
    check(separate.eligible() && separate.p.ownerEligible(own, 8), "noncolliding concurrent shots");
  }
  static void recovery() {
    E e = new E();
    check(e.p.newRequest(e.device, e.session, e.request, true) == null, "reprocess is not fresh request evidence");
    check(!e.p.captureStarted(null, e.device, e.session, e.request, Long.MAX_VALUE, 123), "old helper callback minted proof");
    check(!e.p.ownerEligible(new Owner(Long.MAX_VALUE), Long.MAX_VALUE), "large numeric timestamp proves nothing");
    e.ready();
    Owner restored = new Owner(7);
    check(!e.p.ownerEligible(restored, 7), "recovered same identity borrowed fresh token");
    check(e.eligible(), "genuine owner should still qualify");
    e.owner.identity = 8;
    check(!e.eligible(), "changed owner identity accepted");
    e.owner.identity = 7;
    check(!e.eligible(), "stale owner identity reverted and revived");
  }
  static void failures() {
    for (int stage = 0; stage < 4; stage++) {
      E e = new E(); e.pending();
      if (stage >= 1) e.start(); if (stage >= 2) e.link(); if (stage >= 3) e.bind();
      check(e.p.captureFailed(e.token, e.device, e.session, e.request), "matching failure not recorded");
      check(!e.eligible(), "failed capture stayed eligible");
      check(!e.p.captureStarted(e.token, e.device, e.session, e.request, 99, 0), "failure resurrected");
    }
    E wrong = new E(); wrong.ready();
    check(!wrong.p.captureFailed(wrong.token, wrong.device, wrong.session, new Identity()), "failure context mismatch");
    check(!wrong.eligible(), "contradictory failure kept local token");
  }
  static void gates() throws Exception {
    for (int stage = 0; stage < 4; stage++) {
      E e = new E(); e.pending();
      if (stage >= 1) e.start(); if (stage >= 2) e.link(); if (stage >= 3) e.bind();
      e.gate.allow = false;
      check(!e.eligible() && e.p.revoked(), "gate off/default-on"); noStrong(e.token);
      e.gate.allow = true;
      check(e.p.newRequest(e.device, e.session, new Identity(), false) == null, "revoked policy adopted new install");
      check(!e.p.bindOwner(e.token, e.owner, 7), "revoked token revived");
    }
    E off = new E(); off.gate.allow = false;
    check(off.p.newRequest(off.device, off.session, off.request, false) == null && off.p.revoked(), "registration gate off");
    E fail = new E(); fail.ready(); fail.gate.fail = true;
    check(!fail.eligible() && fail.p.revoked(), "gate exception defaulted on");
  }
  static void times() throws Exception {
    E e = new E(); e.ready();
    e.clock.time = 5999; check(e.eligible(), "early expiration");
    for (int i = 0; i < 64; i++) check(e.eligible(), "updates consumed token");
    e.clock.time = 6000; check(!e.eligible(), "TTL boundary or updates renewed time"); noStrong(e.token);
    E pending = new E(); pending.pending(); pending.clock.time += 1000;
    check(!pending.p.captureStarted(pending.token, pending.device, pending.session, pending.request, 99, 0), "late start revived request");
    E rollback = new E(); rollback.ready(); rollback.clock.time--;
    check(!rollback.eligible() && rollback.p.revoked(), "clock rollback/default time");
    E negative = new E(); negative.clock.time = -1;
    check(negative.p.newRequest(negative.device, negative.session, negative.request, false) == null
        && negative.p.revoked(), "negative clock defaulted");
    E fail = new E(); fail.ready(); fail.clock.fail = true;
    check(!fail.eligible() && fail.p.revoked(), "clock exception defaulted"); noStrong(fail.token);
    E huge = new E(); huge.clock.time = Long.MAX_VALUE - 999; huge.ready();
    huge.clock.time = Long.MAX_VALUE; check(huge.eligible(), "overflowing deadline");
    E jump = new E(); jump.ready(); jump.clock.time = Long.MAX_VALUE;
    check(!jump.eligible(), "forward jump kept TTL alive");
    E zero = new E(1, 1); zero.clock.time = 0; zero.ready(); check(zero.eligible(), "monotonic zero valid");
    zero.clock.time = 1; check(!zero.eligible(), "minimum TTL");
  }
  static void refs() throws Exception {
    E e = new E(1000, 2); e.ready(); noStrong(e.token);
    Identity req = new Identity();
    XdrCaptureScopePolicy.Token t = e.p.newRequest(e.device, e.session, req, false);
    check(field(t, "strongRequest") == req, "pending exact request not strongly held");
    check(e.p.retainedCount() == 2, "bound count");
    check(e.p.newRequest(e.device, e.session, new Identity(), false) == null, "capacity overflow");
    check(e.eligible(), "saturation evicted genuine shot");
    e.clock.time += 1000; check(!e.eligible(), "TTL"); noStrong(e.token); noStrong(t);
    check(e.p.retainedCount() == 2, "live weak tombstones evicted");
    clear(t, "request"); check(e.p.retainedCount() == 1, "dead collected request not pruned");
    clear(e.token, "request"); check(e.p.retainedCount() == 1, "owner replay tombstone prematurely pruned");
    clear(e.token, "owner"); check(e.p.retainedCount() == 0, "fully collected evidence not pruned");
    check(e.p.newRequest(e.device, e.session, new Identity(), false) != null, "bounded slot not reusable after GC");
    check(!e.p.bindOwner(e.token, e.owner, 7), "external dead token revived after pruning");
  }
  static void config() {
    final Gate gate = new Gate(); final Clock clock = new Clock();
    for (long ttl : new long[]{0, -1, XdrCaptureScopePolicy.MAX_TTL_MILLIS + 1, Long.MAX_VALUE}) {
      try { new XdrCaptureScopePolicy(gate, clock, ttl, 1); throw new AssertionError("invalid TTL"); }
      catch (IllegalArgumentException expected) { }
    }
    for (int n : new int[]{0, -1, XdrCaptureScopePolicy.MAX_ENTRIES + 1, Integer.MAX_VALUE}) {
      try { new XdrCaptureScopePolicy(gate, clock, 1, n); throw new AssertionError("invalid capacity"); }
      catch (IllegalArgumentException expected) { }
    }
    try { new XdrCaptureScopePolicy(null, clock, 1, 1); throw new AssertionError("null gate"); }
    catch (IllegalArgumentException expected) { }
    try { new XdrCaptureScopePolicy(gate, null, 1, 1); throw new AssertionError("null clock"); }
    catch (IllegalArgumentException expected) { }
    E a = new E(), b = new E(); a.ready(); b.pending();
    check(!b.p.captureStarted(a.token, a.device, a.session, a.request, 99, 0), "foreign token");
    check(!b.p.bindOwner(a.token, a.owner, 7), "foreign owner binding");
    check(!b.p.captureFailed(a.token, a.device, a.session, a.request), "foreign failure");
    check(a.eligible(), "foreign policy invalidated original token");
    check(b.p.newRequest(null, b.session, new Identity(), false) == null, "null device");
    check(b.p.newRequest(b.device, null, new Identity(), false) == null, "null session");
    check(b.p.newRequest(b.device, b.session, null, false) == null, "null request");
  }
  static void reentrancy() {
    final E a = new E(); a.ready();
    a.gate.during = new Runnable() { public void run() { check(!a.eligible(), "reentrant gate must fail closed"); } };
    check(!a.eligible() && a.p.revoked(), "reentrant gate revived outer operation");
    final E b = new E(); b.ready();
    b.clock.during = new Runnable() { public void run() { check(!b.eligible(), "reentrant clock must fail closed"); } };
    check(!b.eligible() && b.p.revoked(), "reentrant clock revived outer operation");
  }
  static void concurrency() throws Exception {
    final E e = new E(); final int n = 8;
    final XdrCaptureScopePolicy.Token[] tokens = new XdrCaptureScopePolicy.Token[n];
    final CountDownLatch go = new CountDownLatch(1); final AtomicReference<Throwable> error = new AtomicReference<Throwable>();
    Thread[] workers = new Thread[n];
    for (int i = 0; i < n; i++) {
      final int slot = i;
      workers[i] = new Thread(new Runnable() { public void run() {
        try { go.await(); tokens[slot] = e.p.newRequest(e.device, e.session, e.request, false); }
        catch (Throwable failure) { error.compareAndSet(null, failure); }
      } }); workers[i].start();
    }
    go.countDown(); for (Thread w : workers) w.join();
    if (error.get() != null) throw new AssertionError(error.get());
    int minted = 0;
    for (XdrCaptureScopePolicy.Token t : tokens) if (t != null) {
      minted++; check(!e.p.captureStarted(t, e.device, e.session, e.request, 99, 0), "racing duplicate registered freshness");
    }
    check(minted == 1 && e.p.retainedCount() == 1 && !e.eligible(), "concurrent identity registration unbounded");
  }
  public static void main(String[] args) throws Exception {
    String s = args[0];
    if ("fresh_and_updates".equals(s)) fresh();
    else if ("order_and_context".equals(s)) order();
    else if ("replay".equals(s)) replay();
    else if ("collisions".equals(s)) collisions();
    else if ("recovery_and_reprocess".equals(s)) recovery();
    else if ("failures".equals(s)) failures();
    else if ("gate".equals(s)) gates();
    else if ("timebounds".equals(s)) times();
    else if ("bounded_refs".equals(s)) refs();
    else if ("config_and_cross_policy".equals(s)) config();
    else if ("reentrancy".equals(s)) reentrancy();
    else if ("concurrency".equals(s)) concurrency();
    else throw new AssertionError("unknown study scenario");
    System.out.println("PASS " + s + "; policy study only, NOT a camera hook");
  }
}'''


if __name__ == "__main__":
    unittest.main(verbosity=2)
