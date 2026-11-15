package com.xpanport.hook;

import android.content.ContentValues;
import android.hardware.HardwareBuffer;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.IdentityHashMap;
import java.util.concurrent.atomic.AtomicLong;
import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;

/** Before original save/update, not a writer-argument or post-file patch.
 * Caller must first approve CameraProfile (PJX110 + exact app version).
 * Only complete encoded HEIC byte owners are covered; hardware/native and later
 * tmap creation paths remain unverified, especially for C17. No I/O/decoding.
 */
public final class XdrBeforeSave {
    public interface Gate { boolean enabled(); }
    private static final IdentityHashMap<ClassLoader, InstallResult> INSTALLS =
            new IdentityHashMap<ClassLoader, InstallResult>();

    public static final class InstallResult {
        public final boolean installed;
        public final int registeredHooks;
        public final String detail;
        private final XdrBeforeSave owner;
        private InstallResult(boolean installed, int hooks, String detail, XdrBeforeSave owner) {
            this.installed = installed; this.registeredHooks = hooks;
            this.detail = detail; this.owner = owner;
        }
        public RuntimeStatus runtimeStatus() { return new RuntimeStatus(owner); }
    }

    public static final class RuntimeStatus {
        public final boolean enabled;
        public final long checked, patched, already, unsupported, noTmap, failures;
        public final String detail;
        private RuntimeStatus(XdrBeforeSave h) {
            enabled = h != null && h.active();
            checked = h == null ? 0 : h.checked.get();
            patched = h == null ? 0 : h.patched.get();
            already = h == null ? 0 : h.already.get();
            unsupported = h == null ? 0 : h.unsupported.get();
            noTmap = h == null ? 0 : h.noTmap.get();
            failures = h == null ? 0 : h.failures.get();
            detail = h == null ? "not installed" : h.lastDetail;
        }
    }

    /** Resolve the whole coupled ABI before registering; inactive until all succeed. */
    public static InstallResult install(ClassLoader loader, String version) {
        return install(loader, version, null);
    }

    public static synchronized InstallResult install(ClassLoader loader, String version, Gate gate) {
        boolean c16 = "6.020.876".equals(version);
        if (loader == null || (!c16 && !"7.013.51".equals(version)))
            return new InstallResult(false, 0, "refused: exact approved app version/loader required", null);
        InstallResult prior = INSTALLS.get(loader);
        if (prior != null) return prior.owner.version.equals(version) ? prior
                : new InstallResult(false, prior.registeredHooks, "refused: loader already bound to another version", prior.owner);
        XdrBeforeSave h = null;
        try {
            Class<?> picture = Class.forName("com.oplus.camera.picturestore.CameraPicture", false, loader);
            Class<?> storage = Class.forName(c16 ? "ui.t" : "gn.r", false, loader);
            Field bytes = field(picture, c16 ? "h" : "i", byte[].class, true);
            Field hardware = field(picture, c16 ? "i" : "k", HardwareBuffer.class, false);
            Field format = field(picture, c16 ? "l" : "n", String.class, false);
            Method save = method(storage, "a", picture, picture);
            Method update = method(storage, c16 ? "P" : "c0", picture, picture, ContentValues.class);
            h = new XdrBeforeSave(picture, bytes, hardware, format, version, gate);
            h.register(save); h.register(update);
            h.enabled = true;
            InstallResult result = new InstallResult(true, h.hooks.size(),
                    version + " before-save ABI installed; complete encoded bytes only; device/native paths unverified", h);
            INSTALLS.put(loader, result);
            return result;
        } catch (Throwable failure) {
            int remaining = h == null ? 0 : h.rollback();
            String detail = "install failed: " + failure + "; remaining hooks=" + remaining + " (disabled)";
            InstallResult result = new InstallResult(false, remaining, detail, h);
            if (remaining != 0) INSTALLS.put(loader, result);
            log(detail);
            return result;
        }
    }

    private static Field field(Class<?> owner, String name, Class<?> type, boolean writable) throws Exception {
        Field f = owner.getDeclaredField(name);
        int m = f.getModifiers();
        if (f.getType() != type || !Modifier.isPublic(m) || Modifier.isStatic(m)
                || (writable && Modifier.isFinal(m))) throw new NoSuchFieldException(name + " descriptor/access");
        return f;
    }
    private static Method method(Class<?> owner, String name, Class<?> result, Class<?>... args) throws Exception {
        Method m = owner.getDeclaredMethod(name, args);
        if (m.getReturnType() != result || !Modifier.isPublic(m.getModifiers())
                || !Modifier.isStatic(m.getModifiers())) throw new NoSuchMethodException(name + " descriptor/access");
        return m;
    }

    private final Class<?> picture;
    private final Field bytes, hardware, format;
    private final String version;
    private final Gate gate;
    private final ArrayList<XC_MethodHook.Unhook> hooks = new ArrayList<XC_MethodHook.Unhook>();
    private final AtomicLong checked = new AtomicLong(), patched = new AtomicLong(), already = new AtomicLong();
    private final AtomicLong unsupported = new AtomicLong(), noTmap = new AtomicLong(), failures = new AtomicLong();
    private volatile boolean enabled;
    private volatile String lastDetail = "awaiting original save/update buffer";

    private XdrBeforeSave(Class<?> picture, Field bytes, Field hardware, Field format, String version,
                          Gate gate) {
        this.picture = picture; this.bytes = bytes; this.hardware = hardware;
        this.format = format; this.version = version; this.gate = gate;
    }
    private void register(final Method method) {
        XC_MethodHook.Unhook hook = XposedBridge.hookMethod(method, new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam p) {
                if (!enabled) return;
                try { patchOwner(p.args[0], method.getName()); }
                catch (Throwable failure) {
                    try { failures.incrementAndGet(); reject(method.getName() + " hook exception: " + failure); }
                    catch (Throwable ignored) { /* Even diagnostic allocation/failure must not escape. */ }
                }
            }
        });
        if (hook == null) throw new IllegalStateException("registration returned no handle");
        hooks.add(hook);
    }
    private int rollback() {
        enabled = false;
        for (int i = hooks.size() - 1; i >= 0; i--) {
            try { hooks.get(i).unhook(); hooks.remove(i); }
            catch (Throwable ignored) { /* Residual callbacks stay disabled. */ }
        }
        return hooks.size();
    }

    private boolean active() {
        try { return enabled && (gate == null || gate.enabled()); }
        catch (Throwable failure) { return false; }
    }
    private void patchOwner(Object owner, String entry) throws Throwable {
        if (gate != null && !gate.enabled()) return;
        checked.incrementAndGet();
        if (!picture.isInstance(owner)) { reject(entry + " unsupported/null owner"); return; }
        synchronized (owner) {
            // Do not inspect/close even an already-closed HardwareBuffer.
            if (hardware.get(owner) != null) { reject(entry + " hardware buffer: not covered"); return; }
            Object kind = format.get(owner);
            if (!"heic_8bits".equals(kind) && !"heic_10bits".equals(kind)) {
                reject(entry + " unsupported format"); return;
            }
            byte[] input = (byte[]) bytes.get(owner);
            XdrRatioPatch.Result r = XdrRatioPatch.raiseRatioMax(input, 8.0);
            String evidence = entry + " format=" + kind + " bytes=" + (input == null ? 0 : input.length)
                    + " status=" + r.status + " items=" + r.itemCount + " tmap=" + r.tmapItemCount
                    + " xmp=" + r.xmpItemCount + " log2=" + r.log2RatioMax() + " " + r.reason;
            if (input == null || input.length == 0) {
                evidence += "; encoded owner unavailable before original update/save";
                if ("7.013.51".equals(version)) {
                    try { evidence += "; heifProcessInAps=" + picture.getField("j0").getBoolean(owner); }
                    catch (Throwable ignored) { evidence += "; heifProcessInAps=unknown"; }
                }
            }
            if (r.status == XdrRatioPatch.Status.ALREADY_SUFFICIENT) {
                already.incrementAndGet(); lastDetail = evidence; return;
            }
            if (r.status != XdrRatioPatch.Status.PATCHED || !r.changed()) {
                boolean firstNoTmap = r.status == XdrRatioPatch.Status.NO_TMAP && noTmap.incrementAndGet() == 1;
                reject(evidence + "; unchanged", firstNoTmap); return;
            }
            byte[] copy = r.bytes(); // Pure parser clones only after complete-container validation.
            if (gate != null && !gate.enabled()) { lastDetail = entry + " gate revoked; owner unchanged"; return; }
            if (input == null || copy == null || copy == input || copy.length != input.length
                    || bytes.get(owner) != input || hardware.get(owner) != null || !kind.equals(format.get(owner))) {
                reject(evidence + "; stale owner or invalid clone, unchanged"); return;
            }
            bytes.set(owner, copy); // One assignment: both original write and later EXIF reload see it.
            patched.incrementAndGet(); lastDetail = evidence;
            log(version + " " + evidence + "; owner assigned before original (not final-file proof)");
        }
    }
    private void reject(String reason) { reject(reason, false); }
    private void reject(String reason, boolean firstNoTmap) {
        long n = unsupported.incrementAndGet();
        lastDetail = reason;
        if (n <= 3 || firstNoTmap || n % 64 == 0)
            log(version + " " + reason + "; checked=" + checked.get() + " patched=" + patched.get()
                    + " already=" + already.get() + " unsupported=" + n + " noTmap=" + noTmap.get()
                    + " failures=" + failures.get());
    }
    private static void log(String message) {
        try { XposedBridge.log("[XdrBeforeSave] " + message); }
        catch (Throwable ignored) { /* Logging must never affect the original save. */ }
    }
}
