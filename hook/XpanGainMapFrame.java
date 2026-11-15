package com.xpanport.hook;

import android.graphics.Bitmap;
import android.util.Size;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.IdentityHashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;
import org.json.JSONObject;
import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;

/** C17 XPAN gain-only drawing parameters; never a buffer/HEIF/ratio patch.
 * Caller must approve the exact camera profile and keep Gate tied to module/core readiness.
 * Only the original stage-3, explicit-Y8, full-content half-scale path is covered.
 * Native fill/copy ignore some errors: PARAMS_APPLIED is NOT pixel or HDR/metadata proof.
 */
public final class XpanGainMapFrame {
    public interface Gate { boolean enabled(); }
    public enum Status { WAITING, PARAMS_APPLIED, SKIPPED, ORIGINAL_FAILED, GEOMETRY_MISMATCH, HOOK_FAILED }
    public static final int Y8 = 0x20203859;
    private static final String VERSION = "7.013.51", EXTRA = "com.xpanport.hook.gain.frame";
    private static final String FRAME_STYLE = "hassel_style_xpan_1";
    private static final String[] KEYS = {"gain_map_width", "gain_map_height", "gain_map",
            "gain_map_format", "image_stage_type"};
    private static final long MAX_BYTES = 128L * 1024 * 1024;
    private static final IdentityHashMap<ClassLoader, InstallResult> INSTALLS =
            new IdentityHashMap<ClassLoader, InstallResult>();

    public static final class InstallResult {
        public final boolean installed;
        public final int registeredHooks;
        public final String detail;
        private final XpanGainMapFrame owner;
        private InstallResult(boolean ok, int hooks, String detail, XpanGainMapFrame owner) {
            installed = ok; registeredHooks = hooks; this.detail = detail; this.owner = owner;
        }
        public RuntimeStatus runtimeStatus() { return new RuntimeStatus(owner); }
    }
    public static final class RuntimeStatus {
        public final boolean enabled, nativePixelsVerified = false;
        public final Status status;
        public final long paramsApplied, originalReturned, skipped, failures;
        public final String detail;
        private RuntimeStatus(XpanGainMapFrame h) {
            enabled = h != null && h.active(); status = h == null ? Status.SKIPPED : h.status;
            paramsApplied = h == null ? 0 : h.applied.get(); originalReturned = h == null ? 0 : h.returned.get();
            skipped = h == null ? 0 : h.skipped.get(); failures = h == null ? 0 : h.failures.get();
            detail = h == null ? "not installed" : h.detail;
        }
    }

    /** Pure geometry, not decoded-photo inference. Known module stream pairs include even alignment. */
    public static final class NativeFramePlan {
        public final boolean supported;
        public final int sourceWidth, sourceHeight, width, height, x, y;
        public final String reason;
        private NativeFramePlan(boolean ok, int sw, int sh, int w, int h, int x, int y, String reason) {
            supported = ok; sourceWidth = sw; sourceHeight = sh; width = w; height = h;
            this.x = x; this.y = y; this.reason = reason;
        }
        public static NativeFramePlan create(int pw, int ph, int cw, int ch, int px, int py,
                                              int sw, int sh, int format, int stage) {
            String why = null;
            if (stage != 3) why = "unsupported original image_stage_type";
            else if (format != Y8) why = "explicit Y8 required; RGB/absent formats are not covered";
            else if (!((pw == 8192 && ph == 3024 && sw == 4096 && sh == 1512)
                    || (pw == 4096 && ph == 1512 && sw == 2048 && sh == 756)))
                why = "RAW_GAIN_CONTAINS_FRAME_OR_UNKNOWN: unverified full-content stream pair";
            else if (cw <= 0 || ch <= 0 || px < 0 || py < 0 || (long) px + pw > cw || (long) py + ph > ch
                    || (long) cw * ch > MAX_BYTES || (long) sw * sh > MAX_BYTES)
                why = "invalid canvas/content ROI or byte limit";
            else if ((long) pw * sh != (long) ph * sw || pw != sw * 2 || ph != sh * 2
                    || ((cw | ch | px | py) & 1) != 0)
                why = "noninteger/two-axis scale";
            else if (((cw / 2 | ch / 2) & 3) != 0 || cw / 2 - sw <= 7 || ch / 2 - sh <= 7)
                why = "original e rounding/nooverlay alignment would alter the canvas";
            return new NativeFramePlan(why == null, sw, sh, cw / 2, ch / 2, px / 2, py / 2,
                    why == null ? "exact full-content half-scale Y8 frame" : why);
        }
        @Override public String toString() {
            return "raw=" + sourceWidth + "x" + sourceHeight + " canvas=" + width + "x" + height
                    + " roi=[" + x + "," + y + "," + ((long) x + sourceWidth) + ","
                    + ((long) y + sourceHeight) + "] " + reason;
        }
    }

    /** All coupled descriptors resolve without class initialization, then register inactive and commit. */
    public static synchronized InstallResult install(ClassLoader loader, String version, Gate gate) {
        if (loader == null || gate == null || !VERSION.equals(version))
            return new InstallResult(false, 0, "refused: exact C17 version/loader and module Gate required", null);
        InstallResult prior = INSTALLS.get(loader);
        if (prior != null) return prior.owner.gate == gate ? prior : new InstallResult(false,
                prior.registeredHooks, "refused: loader already bound to another Gate", prior.owner);
        XpanGainMapFrame h = null;
        try {
            Bindings b = new Bindings(loader);
            h = new XpanGainMapFrame(b, gate);
            h.register(b.w, false); h.register(b.e, true); h.registerClassifier();
            h.ready = true;
            InstallResult r = new InstallResult(true, h.hooks.size(),
                    "C17 XPAN gain parameters/scoped scaling installed; original native lifecycle; device pixels unverified", h);
            INSTALLS.put(loader, r); return r;
        } catch (Throwable t) {
            int remaining = h == null ? 0 : h.rollback();
            InstallResult r = new InstallResult(false, remaining, "install failed: ABI/registration; disabled", h);
            if (remaining != 0) INSTALLS.put(loader, r);
            log(r.detail); return r;
        }
    }

    private final Bindings b;
    private final Gate gate;
    private final ThreadLocal<Scope> scope = new ThreadLocal<Scope>();
    private final ThreadLocal<ScalingCall> scaling = new ThreadLocal<ScalingCall>();
    private final ArrayList<XC_MethodHook.Unhook> hooks = new ArrayList<XC_MethodHook.Unhook>();
    private final AtomicLong applied = new AtomicLong(), returned = new AtomicLong(), skipped = new AtomicLong();
    private final AtomicLong failures = new AtomicLong(), observations = new AtomicLong();
    private volatile boolean ready;
    private volatile Status status = Status.WAITING;
    private volatile String detail = "awaiting runtime candidate; no native pixel/metadata proof";
    private XpanGainMapFrame(Bindings b, Gate gate) { this.b = b; this.gate = gate; }
    private boolean active() { try { return ready && gate.enabled(); } catch (Throwable ignored) { return false; } }
    private void register(Method method, final boolean observeScaling) {
        XC_MethodHook.Unhook hook = XposedBridge.hookMethod(method, new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam p) {
                if (observeScaling) beginScaling(p); else enter(p);
            }
            @Override protected void afterHookedMethod(MethodHookParam p) {
                if (observeScaling) {
                    try { observe(p); }
                    finally { endScaling(p); }
                } else exit(p);
            }
        });
        if (hook == null) throw new IllegalStateException("registration returned no handle");
        hooks.add(hook);
    }
    private void registerClassifier() {
        XC_MethodHook.Unhook hook = XposedBridge.hookMethod(b.aiFrame, new XC_MethodHook() {
            @Override protected void afterHookedMethod(MethodHookParam p) {
                try {
                    Scope f = scope.get();
                    ScalingCall call = scaling.get();
                    if (call != null && call.diagnostic && call.previous == null && call.owner == f
                            && p.args != null && p.args.length == 1 && f != null && p.args[0] == f.lut
                            && diagnosticScaling(call) && active() && diagnosticScaling(call)) {
                        call.branch = p.hasThrowable() ? "original-failed" : Boolean.TRUE.equals(p.getResult()) ? "two-axis"
                                : Boolean.FALSE.equals(p.getResult()) ? "old-bitmap" : "unknown";
                    }
                    if (f == null || !f.standardFrame || call == null || call.owner != f || call.previous != null
                            || call.parameter != f.scaling || f.classifierSeen || p.hasThrowable() || !Boolean.FALSE.equals(p.getResult())
                            || p.args == null || p.args.length != 1 || p.args[0] != f.lut
                            || !active() || !currentScaling(f)) return;
                    f.classifierSeen = true;
                    p.setResult(Boolean.TRUE); // Only the exact gain e call, never global AI support.
                } catch (Throwable t) { diagnosticFailure("gain scaling predicate failed; original predicate retained"); }
            }
        });
        if (hook == null) throw new IllegalStateException("classifier registration returned no handle");
        hooks.add(hook);
    }
    private void beginScaling(XC_MethodHook.MethodHookParam p) {
        try {
            if (!ready) return;
            Scope f = scope.get();
            ScalingCall call = new ScalingCall(scaling.get(), f, p);
            p.setObjectExtra(EXTRA, call); scaling.set(call);
            if (call.previous == null && f != null && f.replacement == null && f.previous == null && f.snapshot != null
                    && "xpan".equals(f.snapshot.mode) && "xpan".equals(f.snapshot.infoMode)
                    && p.thisObject == f.helper && p.args != null && p.args.length == 5
                    && p.args[0] == f.original && p.args[1] == f.tag && p.args[2] instanceof Size
                    && p.args[3] == null && Boolean.TRUE.equals(p.args[4]) && active()
                    && f.snapshot.matches(b, f.original, f.tag, f.map)) {
                call.arguments = p.args; call.targetSize = (Size) p.args[2]; call.diagnostic = true;
            }
            if (call.previous != null || f == null || !f.standardFrame || f.replacement == null || f.scaling != null
                    || p.thisObject != f.helper || p.args == null || p.args.length != 5
                    || p.args[0] != f.replacement || p.args[1] != f.tag || !(p.args[2] instanceof Size)
                    || !new Size(f.plan.sourceWidth, f.plan.sourceHeight).equals(p.args[2])
                    || p.args[3] != null || !Boolean.TRUE.equals(p.args[4]) || !active()) return;
            f.scaling = p;
        } catch (Throwable t) { diagnosticFailure("gain scaling qualification failed; original call retained"); }
    }
    private boolean diagnosticScaling(ScalingCall call) { return diagnosticScaling(call, true); }
    private boolean diagnosticScaling(ScalingCall call, boolean unchangedMaterials) {
        Scope f = call.owner;
        XC_MethodHook.MethodHookParam p = call.parameter;
        try {
            return call.diagnostic && f != null && scope.get() == f && f.replacement == null && f.previous == null
                    && p.thisObject == f.helper && p.args == call.arguments && p.args != null && p.args.length == 5
                    && p.args[0] == f.original && p.args[1] == f.tag && p.args[2] == call.targetSize
                    && p.args[3] == null && Boolean.TRUE.equals(p.args[4]) && f.snapshot != null
                    && (unchangedMaterials ? f.snapshot.matches(b, f.original, f.tag, f.map)
                                           : f.snapshot.sameGeometry(b, f.original, f.tag, f.map));
        } catch (Throwable ignored) { return false; }
    }
    private boolean currentScaling(Scope f) {
        XC_MethodHook.MethodHookParam p = f.scaling;
        return p != null && p.thisObject == f.helper && p.args != null && p.args.length == 5
                && p.args[0] == f.replacement && p.args[1] == f.tag && p.args[3] == null
                && Boolean.TRUE.equals(p.args[4]) && new Size(f.plan.sourceWidth, f.plan.sourceHeight).equals(p.args[2])
                && safeStandardCandidate(f);
    }
    private boolean safeStandardCandidate(Scope f) {
        try {
            Snapshot s = f.snapshot;
            return s != null && s.standardFrame() && s.matches(b, f.original, f.tag, f.map)
                    && b.lut.get(f.tag) == f.lut;
        } catch (Throwable ignored) { return false; }
    }
    private void endScaling(XC_MethodHook.MethodHookParam p) {
        try {
            Object saved = p.getObjectExtra(EXTRA);
            if (!(saved instanceof ScalingCall)) return;
            ScalingCall call = (ScalingCall) saved;
            if (call.owner != null && call.owner.scaling == p) call.owner.scaling = null;
            if (scaling.get() == call) {
                if (call.previous == null) scaling.remove(); else scaling.set(call.previous);
            }
            p.setObjectExtra(EXTRA, null);
        } catch (Throwable ignored) { /* Original exception/result must survive cleanup. */ }
    }
    private int rollback() {
        ready = false;
        for (int i = hooks.size() - 1; i >= 0; i--) {
            try { hooks.get(i).unhook(); hooks.remove(i); } catch (Throwable ignored) { /* Residual hooks inactive. */ }
        }
        return hooks.size();
    }
    private void enter(XC_MethodHook.MethodHookParam p) {
        if (!ready) return;
        try {
            Scope f = new Scope(scope.get(), p.thisObject, p.args);
            p.setObjectExtra(EXTRA, f); scope.set(f);
            if (!active()) return;
            if (f.previous != null) { skip("nested w; unchanged parameters"); return; }
            if (p.args == null || p.args.length != 5 || !b.holder.isInstance(p.args[0])
                    || !(p.args[1] instanceof Map) || !b.tag.isInstance(p.args[2]) || !(p.args[4] instanceof Size)) {
                skip("unsupported/null original w arguments"); return;
            }
            f.original = p.args[0]; f.tag = p.args[2]; f.map = (Map<?, ?>) p.args[1];
            Snapshot s = new Snapshot(b, f.original, f.tag, f.map);
            f.snapshot = s;
            f.lut = s.lut;
            if (!"xpan".equals(s.mode) || !"xpan".equals(s.infoMode) || s.v[4] == null) {
                skip("not exact XPAN frame overlay; " + s.inputEvidence()); return;
            }
            final int sw, sh, format, mapStage;
            try { sw = number(s.raw[0]); sh = number(s.raw[1]); format = number(s.raw[3]); mapStage = number(s.raw[4]); }
            catch (IllegalArgumentException unknown) { skip("unknown SDK geometry/format/stage String; unchanged parameters"); return; }
            if (!(p.args[3] instanceof Integer) || mapStage != ((Integer) p.args[3]).intValue()) {
                skip("original stage/map mismatch"); return;
            }
            final boolean standardCandidate = s.standardStyle();
            final boolean standard = s.standardFrame();
            if (standardCandidate && !standard) {
                skip("standard XPAN source/crop/stage not covered; " + s.geometryEvidence(sw, sh, format, mapStage)); return;
            }
            if (!standard && !Boolean.TRUE.equals(b.aiFrame.invoke(null, s.lut))) {
                skip("not standard XPAN JSON frame or original AI-frame; " + s.geometryEvidence(sw, sh, format, mapStage)); return;
            }
            NativeFramePlan plan = NativeFramePlan.create(s.size.getWidth(), s.size.getHeight(), s.coords[0],
                    s.coords[1], s.coords[2], s.coords[3], sw, sh, format, ((Integer) p.args[3]).intValue());
            if (!plan.supported) { skip(plan + "; " + s.geometryEvidence(sw, sh, format, mapStage)); return; }
            final long source;
            try { source = pointer(s.raw[2]); }
            catch (IllegalArgumentException unknown) { skip("unknown borrowed gain pointer String; unchanged parameters"); return; }
            if (source <= 0 || source == s.primary || (s.oldGain != 0 && source == s.oldGain)
                    || s.primary <= 0 || !s.size.equals(p.args[4]) || b.cropSize.invoke(f.helper, b.g.getInt(f.helper)) != null) {
                skip(plan + "; borrowed pointer collision or original crop/source size not covered"); return;
            }
            Object neutral = b.constructor.newInstance();
            Object info = b.cloneInfo.invoke(s.info);
            if (info == s.info || !b.info.isInstance(info) || b.size.get(info) != s.size || !"xpan".equals(b.infoMode.get(info))
                    || b.q.getLong(neutral) != 0 || b.r.getLong(neutral) != 0 || b.o.get(neutral) != null || b.p.get(neutral) != null) {
                skip("clone has shared metadata/resource ownership; unchanged parameters"); return;
            }
            for (int i = 0; i < b.coords.length; i++) b.coords[i].setInt(neutral, s.coords[i]);
            b.c.set(neutral, s.references == null ? null : s.references.clone()); b.s.set(neutral, info);
            b.v.set(neutral, new Bitmap[6]); b.wBits.set(neutral, new Bitmap[6]); b.x.set(neutral, new Bitmap[6]);
            for (Field metric : b.metrics) metric.set(neutral, new int[6]);
            b.d.setInt(neutral, 0); b.color.set(neutral, "#000000"); b.texture.set(neutral, null);
            // Last qualification precedes the ONE original-argument assignment; never mutate a borrowed owner.
            if ((standard ? !s.standardFrame() : !Boolean.TRUE.equals(b.aiFrame.invoke(null, s.lut)))
                    || b.cropSize.invoke(f.helper, b.g.getInt(f.helper)) != null
                    || !active() || p.args != f.args || p.args[0] != f.original || p.args[1] != f.map || p.args[2] != f.tag
                    || !s.matches(b, f.original, f.tag, f.map) || !s.size.equals(p.args[4])
                    || !(p.args[3] instanceof Integer) || number(s.raw[4]) != ((Integer) p.args[3]).intValue()) {
                skip("gate revoked/stale candidate; unchanged parameters"); return;
            }
            f.replacement = neutral; f.plan = plan; f.standardFrame = standard; f.lut = s.lut; f.snapshot = s;
            p.args[0] = neutral;
            applied.incrementAndGet(); status = Status.PARAMS_APPLIED;
            detail = plan + "; isolated gain drawing parameters; awaiting original w (not pixel proof)";
        } catch (Throwable t) { diagnosticFailure("before w failed; no exception/result/resource replacement"); }
    }
    private void observe(XC_MethodHook.MethodHookParam p) {
        if (!ready) return;
        try {
            Scope f = scope.get();
            ScalingCall call = scaling.get();
            if (call != null && call.parameter == p && call.diagnostic && call.previous == null
                    && diagnosticScaling(call, false) && active() && diagnosticScaling(call, false)) {
                long count = observations.incrementAndGet();
                if (count <= 4 || count % 64 == 0) log("original gain e branch=" + call.branch + " input=" + call.targetSize
                        + " " + (p.hasThrowable() ? "original-failed" : holderEvidence(p.getResult()))
                        + "; originalMaterialsChanged=" + !call.owner.snapshot.matches(b, call.owner.original, call.owner.tag, call.owner.map)
                        + "; observed only, no parameter/result replacement");
            }
            if (f == null || f.replacement == null || p.thisObject != f.helper || p.args == null || p.args.length != 5
                    || p.args[0] != f.replacement || p.args[1] != f.tag || p.args[3] != null || !Boolean.TRUE.equals(p.args[4])) return;
            f.scaled = !p.hasThrowable() && matchesGain(p.getResult(), f);
            f.gain = p.getResult();
        } catch (Throwable t) { diagnosticFailure("original e observation failed; no result/owner replaced"); }
    }
    private String holderEvidence(Object holder) throws Exception {
        if (!b.holder.isInstance(holder)) return "holder=unknown";
        Object info = b.s.get(holder);
        String values = "holder=true type=" + b.type.getInt(holder) + " canvas=" + b.coords[0].getInt(holder)
                + "x" + b.coords[1].getInt(holder) + " placement=" + b.coords[2].getInt(holder) + "," + b.coords[3].getInt(holder);
        if (!b.info.isInstance(info)) return values + " info=unknown";
        return values + " watermarkMode=" + b.watermarkMode.getInt(info) + " stage=" + b.stage.getInt(info)
                + " source=" + b.size.get(info) + " crop=" + b.crop.get(info)
                + " materials=" + materialEvidence(b.v.get(holder)) + "/" + materialEvidence(b.wBits.get(holder))
                + "/" + materialEvidence(b.x.get(holder));
    }
    private static String materialEvidence(Object value) {
        if (!(value instanceof Bitmap[])) return "absent";
        Bitmap[] array = (Bitmap[]) value;
        if (array.length != 6) return "unknown-length";
        int mask = 0;
        for (int i = 0; i < array.length; i++) if (array[i] != null) mask |= 1 << i;
        return "6:" + Integer.toHexString(mask);
    }
    private boolean matchesGain(Object gain, Scope f) throws Exception {
        if (!b.holder.isInstance(gain) || gain == f.original || gain == f.replacement) return false;
        NativeFramePlan n = f.plan;
        Object info = b.s.get(gain);
        return b.coords[0].getInt(gain) == n.width && b.coords[1].getInt(gain) == n.height
                && b.coords[2].getInt(gain) == n.x && b.coords[3].getInt(gain) == n.y && b.info.isInstance(info)
                && info != b.s.get(f.original) && info != b.s.get(f.replacement)
                && new Size(n.sourceWidth, n.sourceHeight).equals(b.size.get(info))
                && empty((Bitmap[]) b.v.get(gain)) && empty((Bitmap[]) b.wBits.get(gain)) && empty((Bitmap[]) b.x.get(gain))
                && b.d.getInt(gain) == 0 && "#000000".equals(b.color.get(gain)) && b.texture.get(gain) == null;
    }
    private void exit(XC_MethodHook.MethodHookParam p) {
        final Scope f;
        try { f = (Scope) p.getObjectExtra(EXTRA); }
        catch (Throwable ignored) { return; }
        if (f == null) return;
        try {
            if (f.replacement != null) {
                if (p.hasThrowable() || !b.holder.isInstance(p.getResult()) || b.q.getLong(p.getResult()) <= 0)
                    failure(f.plan + "; ORIGINAL_FAILED; original exception/native lifecycle retained");
                else if (!f.scaled || p.getResult() != f.gain || !matchesGain(p.getResult(), f)) {
                    failures.incrementAndGet(); status = Status.GEOMETRY_MISMATCH;
                    detail = f.plan + "; original e/w differed AFTER parameters applied; NOT rolled back or pixel-verified";
                    log(detail);
                } else {
                    returned.incrementAndGet(); status = Status.PARAMS_APPLIED;
                    detail = f.plan + "; original e/w geometry confirmed; native pixels/min0/Gamma1/final file unverified";
                    log(detail);
                }
            }
        } catch (Throwable t) { diagnosticFailure("after original w observation failed; original result retained"); }
        finally {
            // Cleanup is unconditional, including Gate revocation and original exceptions. Do not stomp another hook.
            if (p.args == f.args && p.args != null && p.args.length > 0 && p.args[0] == f.replacement) p.args[0] = f.original;
            try { if (f.previous == null) scope.remove(); else scope.set(f.previous); }
            catch (Throwable ignored) { /* Cleanup must not replace the original result. */ }
            try { p.setObjectExtra(EXTRA, null); } catch (Throwable ignored) {}
        }
    }
    private void skip(String why) {
        long count = skipped.incrementAndGet(); status = Status.SKIPPED; detail = why;
        if (count <= 3 || count % 64 == 0) log(why);
    }
    private void failure(String why) { failures.incrementAndGet(); status = Status.ORIGINAL_FAILED; detail = why; log(why); }
    private void diagnosticFailure(String why) { try { failures.incrementAndGet(); status = Status.HOOK_FAILED; detail = why; log(why); } catch (Throwable ignored) { /* Diagnostics never replace original failure. */ } }
    private static void log(String why) { try { XposedBridge.log("[XpanGainMapFrame] " + why); } catch (Throwable ignored) {} }
    private static String numericEvidence(Object value) {
        try { return Integer.toString(number(value)); }
        catch (IllegalArgumentException unknown) { return value == null ? "absent" : "unknown"; }
    }
    private static int number(Object value) { if (!(value instanceof String)) throw new IllegalArgumentException("SDK numeric String required"); return Integer.parseInt((String) value); }
    private static boolean sameStyle(Object before, Object after) {
        return before == after || before instanceof String && before.equals(after);
    }
    private static long pointer(Object value) { if (!(value instanceof String)) throw new IllegalArgumentException("SDK borrowed pointer String required"); return Long.parseLong((String) value); }
    private static boolean empty(Bitmap[] a) { if (a == null || a.length != 6) return false; for (Bitmap v : a) if (v != null) return false; return true; }

    private static final class Scope {
        final Scope previous; final Object helper; final Object[] args;
        Object original, tag, replacement, gain, lut; Map<?, ?> map; NativeFramePlan plan; Snapshot snapshot;
        boolean scaled, standardFrame, classifierSeen; XC_MethodHook.MethodHookParam scaling;
        Scope(Scope p, Object h, Object[] a) { previous = p; helper = h; args = a; }
    }
    private static final class ScalingCall {
        final ScalingCall previous; final Scope owner; final XC_MethodHook.MethodHookParam parameter;
        Object[] arguments; Size targetSize; boolean diagnostic; String branch = "unknown";
        ScalingCall(ScalingCall previous, Scope owner, XC_MethodHook.MethodHookParam parameter) {
            this.previous = previous; this.owner = owner; this.parameter = parameter;
        }
    }
    private static final class Snapshot {
        final Object info, mode, infoMode, lut; final Size size, crop;
        final JSONObject json; final Object style; final int watermarkMode, stage, type;
        final int[] coords, references;
        final Bitmap[] v, w, x; final Object[] arrays, raw; final long primary, oldGain;
        Snapshot(Bindings b, Object holder, Object tag, Map<?, ?> map) throws Exception {
            info = b.s.get(holder); if (!b.info.isInstance(info)) throw new IllegalArgumentException("missing YuvInfo");
            size = (Size) b.size.get(info); if (size == null) throw new IllegalArgumentException("missing content Size");
            crop = (Size) b.crop.get(info); json = (JSONObject) b.json.get(info);
            style = json == null ? null : json.opt("styleId");
            watermarkMode = b.watermarkMode.getInt(info); stage = b.stage.getInt(info); type = b.type.getInt(holder);
            mode = b.mode.get(tag); lut = b.lut.get(tag); infoMode = b.infoMode.get(info);
            coords = new int[4]; for (int i = 0; i < 4; i++) coords[i] = b.coords[i].getInt(holder);
            int[] sourceRefs = (int[]) b.c.get(holder); references = sourceRefs == null ? null : sourceRefs.clone();
            primary = b.q.getLong(holder); oldGain = b.r.getLong(holder);
            arrays = new Object[]{b.v.get(holder), b.wBits.get(holder), b.x.get(holder), b.c.get(holder)};
            for (Object a : new Object[]{arrays[0], arrays[1], arrays[2]})
                if (a == null || ((Bitmap[]) a).length != 6) throw new IllegalArgumentException("unknown material array layout");
            for (Field metric : b.metrics) { int[] a = (int[]) metric.get(holder); if (a == null || a.length != 6) throw new IllegalArgumentException("unknown metric array layout"); }
            v = ((Bitmap[]) arrays[0]).clone(); w = ((Bitmap[]) arrays[1]).clone(); x = ((Bitmap[]) arrays[2]).clone();
            raw = new Object[KEYS.length]; for (int i = 0; i < raw.length; i++) raw[i] = map.get(KEYS[i]);
        }
        String inputEvidence() {
            return "tagXpan=" + "xpan".equals(mode) + " infoXpan=" + "xpan".equals(infoMode)
                    + " overlay=" + (v[4] != null) + " standardStyle=" + FRAME_STYLE.equals(style)
                    + " type=" + type + " watermarkMode=" + watermarkMode + " stage=" + stage
                    + " mapStage=" + numericEvidence(raw[4]) + " format=" + numericEvidence(raw[3])
                    + " raw=" + numericEvidence(raw[0]) + "x" + numericEvidence(raw[1])
                    + " source=" + size + " crop=" + crop + " canvas=" + coords[0] + "x" + coords[1]
                    + " placement=" + coords[2] + "," + coords[3] + " materials=" + materialEvidence(v)
                    + "/" + materialEvidence(w) + "/" + materialEvidence(x) + "; unchanged, ratioMax policy=8x";
        }
        String geometryEvidence(int sw, int sh, int format, int mapStage) {
            return "standardStyle=" + FRAME_STYLE.equals(style) + " type=" + type + " watermarkMode=" + watermarkMode
                    + " stage=" + stage + "/" + mapStage + " format=" + format + " raw=" + sw + "x" + sh
                    + " source=" + size + " crop=" + crop + " canvas=" + coords[0] + "x" + coords[1]
                    + " placement=" + coords[2] + "," + coords[3] + " materials=" + materialEvidence(v)
                    + "/" + materialEvidence(w) + "/" + materialEvidence(x) + "; unchanged, ratioMax policy=8x";
        }
        boolean standardStyle() { return FRAME_STYLE.equals(style); }
        boolean standardFrame() {
            return watermarkMode == 3 && stage == 3 && type == 9 && standardStyle() && size.equals(crop);
        }
        boolean sameGeometry(Bindings b, Object holder, Object tag, Map<?, ?> map) throws Exception {
            if (b.s.get(holder) != info || b.size.get(info) != size || b.crop.get(info) != crop || b.json.get(info) != json
                    || b.watermarkMode.getInt(info) != watermarkMode || b.stage.getInt(info) != stage || b.type.getInt(holder) != type
                    || !sameStyle(style, json == null ? null : json.opt("styleId")) || !"xpan".equals(b.mode.get(tag))
                    || !"xpan".equals(b.infoMode.get(info)) || b.lut.get(tag) != lut
                    || b.q.getLong(holder) != primary || b.r.getLong(holder) != oldGain) return false;
            for (int i = 0; i < 4; i++) if (b.coords[i].getInt(holder) != coords[i]) return false;
            for (int i = 0; i < raw.length; i++) if (map.get(KEYS[i]) != raw[i]) return false;
            return true;
        }
        boolean matches(Bindings b, Object holder, Object tag, Map<?, ?> map) throws Exception {
            if (!sameGeometry(b, holder, tag, map)) return false;
            if (b.v.get(holder) != arrays[0] || b.wBits.get(holder) != arrays[1] || b.x.get(holder) != arrays[2]
                    || b.c.get(holder) != arrays[3] || !Arrays.equals((int[]) arrays[3], references)
                    || !Arrays.equals((Bitmap[]) arrays[0], v) || !Arrays.equals((Bitmap[]) arrays[1], w) || !Arrays.equals((Bitmap[]) arrays[2], x)) return false;
            for (int i = 0; i < raw.length; i++) if (map.get(KEYS[i]) != raw[i]) return false;
            return true;
        }
    }
    private static final class Bindings {
        final Class<?> holder, info, tag;
        final Constructor<?> constructor;
        final Field[] coords, metrics;
        final Field c, s, v, wBits, x, q, r, o, p, d, color, texture, size, crop, json, watermarkMode, stage, type, infoMode, mode, lut, g;
        final Method w, e, cloneInfo, aiFrame, cropSize;
        Bindings(ClassLoader loader) throws Exception {
            Class<?> helper = cls(loader, "ol.q0"), model = cls(loader, "zk.c");
            holder = cls(loader, "ys.c"); info = cls(loader, "ys.g"); tag = cls(loader, "com.oplus.camera.device.CameraRequestTag");
            constructor = holder.getDeclaredConstructor(); if (!Modifier.isPublic(constructor.getModifiers())) throw new NoSuchMethodException("holder constructor access");
            coords = new Field[]{field(holder, "h", int.class), field(holder, "i", int.class), field(holder, "j", int.class), field(holder, "k", int.class)};
            metrics = new Field[]{field(holder, "m", int[].class), field(holder, "n", int[].class), field(holder, "z", int[].class), field(holder, "A", int[].class)};
            c = field(holder, "c", int[].class); s = field(holder, "s", info); v = field(holder, "v", Bitmap[].class);
            wBits = field(holder, "w", Bitmap[].class); x = field(holder, "x", Bitmap[].class);
            q = field(holder, "q", long.class); r = field(holder, "r", long.class); o = field(holder, "o", Bitmap.class); p = field(holder, "p", Bitmap.class);
            d = field(holder, "D", int.class); color = field(holder, "E", String.class); texture = field(holder, "F", String.class);
            size = field(info, "p", Size.class); crop = field(info, "k", Size.class); json = field(info, "z0", JSONObject.class);
            watermarkMode = field(info, "y", int.class); stage = field(info, "Z", int.class); type = field(holder, "N", int.class);
            infoMode = field(info, "e", String.class);
            mode = field(tag, "C", String.class); lut = field(tag, "q", String.class); g = field(helper, "g", int.class);
            w = method(helper, "w", holder, false, false, holder, Map.class, tag, int.class, Size.class);
            e = method(helper, "e", holder, false, false, holder, tag, Size.class, Bitmap.class, boolean.class);
            cropSize = method(helper, "h", Size.class, false, false, int.class);
            cloneInfo = method(info, "clone", Object.class, false, false); aiFrame = method(model, "e", boolean.class, true, false, String.class);
            Class<?> assembler = cls(loader, "com.oplus.ocs.ipu.watermark.masterwatermark.MasterWatermarkAssembler");
            method(assembler, "assemble", void.class, true, false, holder, int.class, Object.class, boolean.class, int.class);
            method(assembler, "hardwareBufferAllocate", long.class, true, true, int.class, int.class, int.class);
            method(assembler, "fillBufferWithColor", int.class, true, true, long.class, String.class);
            method(assembler, "copyBufferToCoord", int.class, true, true, long.class, long.class, int.class, int.class);
            method(assembler, "releaseHardwareBuffer", int.class, true, true, long.class);
        }
    }
    private static Class<?> cls(ClassLoader loader, String name) throws ClassNotFoundException { return Class.forName(name, false, loader); }
    private static Field field(Class<?> owner, String name, Class<?> type) throws Exception {
        Field f = owner.getDeclaredField(name); int flags = f.getModifiers();
        if (f.getType() != type || !Modifier.isPublic(flags) || Modifier.isStatic(flags) || Modifier.isFinal(flags)) throw new NoSuchFieldException(name + " descriptor/access");
        return f;
    }
    private static Method method(Class<?> owner, String name, Class<?> result, boolean isStatic, boolean isNative, Class<?>... args) throws Exception {
        Method m = owner.getDeclaredMethod(name, args); int flags = m.getModifiers();
        if (m.getReturnType() != result || !Modifier.isPublic(flags) || Modifier.isStatic(flags) != isStatic
                || Modifier.isNative(flags) != isNative) throw new NoSuchMethodException(name + " descriptor/access");
        return m;
    }
}
