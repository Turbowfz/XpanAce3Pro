package com.xpanport.hook;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.IdentityHashMap;
import java.util.concurrent.atomic.AtomicLong;
import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;

/** C17 opt-in capture evidence only. No bytes/fields/results are changed or retained.
 * A container header is a clue, never complete HEIC, capture ownership or 8x proof.
 */
public final class XpanApsCaptureTrace {
    public interface Gate { boolean enabled(); }
    private static final IdentityHashMap<ClassLoader, InstallResult> INSTALLS =
            new IdentityHashMap<ClassLoader, InstallResult>();

    public static final class InstallResult {
        public final boolean installed;
        public final int registeredHooks;
        public final String detail;
        private final XpanApsCaptureTrace owner;
        private InstallResult(boolean ok, int hooks, String detail, XpanApsCaptureTrace owner) {
            installed = ok; registeredHooks = hooks; this.detail = detail; this.owner = owner;
        }
    }

    public static synchronized InstallResult install(ClassLoader loader, String version, Gate gate) {
        if (loader == null || gate == null || !"7.013.51".equals(version))
            return new InstallResult(false, 0, "refused: exact C17 loader/version and live Gate required", null);
        InstallResult prior = INSTALLS.get(loader);
        if (prior != null) {
            if (prior.owner.gate == gate) return prior;
            return new InstallResult(false, prior.registeredHooks, "refused: trace bound to another Gate", prior.owner);
        }
        XpanApsCaptureTrace h = null;
        try {
            Bindings b = new Bindings();
            b.resolve(loader);
            h = new XpanApsCaptureTrace(b, gate);
            final XpanApsCaptureTrace trace = h;
            XC_MethodHook.Unhook handle = XposedBridge.hookMethod(b.received, new XC_MethodHook() {
                @Override protected void beforeHookedMethod(MethodHookParam p) {
                    try { trace.observe(p); }
                    catch (Throwable failure) { /* Evidence cannot alter the original capture. */ }
                }
            });
            if (handle == null) {
                InstallResult result = new InstallResult(false, 1,
                        "trace registration returned no handle; uncertain callback stays inactive", h);
                INSTALLS.put(loader, result);
                return result;
            }
            InstallResult result = new InstallResult(true, 1,
                    "C17 APS capture trace installed; opt-in, read-only, no encoded ownership proof", h);
            INSTALLS.put(loader, result);
            h.ready = true;
            return result;
        } catch (Throwable failure) {
            return new InstallResult(false, 0, "trace ABI/registration refused; original capture unchanged", h);
        }
    }

    private final Bindings b;
    private final Gate gate;
    private final AtomicLong seen = new AtomicLong();
    private volatile boolean ready;

    private XpanApsCaptureTrace(Bindings b, Gate gate) { this.b = b; this.gate = gate; }

    private boolean enabled() {
        try { return ready && gate.enabled() && ready; }
        catch (Throwable failure) { return false; }
    }

    private void observe(XC_MethodHook.MethodHookParam p) throws IllegalAccessException {
        if (!enabled() || p.args == null || p.args.length != 1 || !b.result.isInstance(p.args[0])) return;
        Object[] args = p.args;
        Object owner = args[0];
        Object mode = b.mode.get(owner);
        int width = b.width.getInt(owner), height = b.height.getInt(owner), format = b.format.getInt(owner);
        boolean inAps = b.inAps.getBoolean(owner);
        byte[] bytes = (byte[]) b.bytes.get(owner);
        long header = prefix(bytes, 0), tail = prefix(bytes, 8);
        String kind = headerKind(bytes);
        long n = seen.incrementAndGet();
        if (n > 4 && n % 64 != 0) return;
        if (!enabled() || p.args != args || args[0] != owner || b.mode.get(owner) != mode
                || b.width.getInt(owner) != width || b.height.getInt(owner) != height
                || b.format.getInt(owner) != format || b.inAps.getBoolean(owner) != inAps
                || b.bytes.get(owner) != bytes || prefix(bytes, 0) != header || prefix(bytes, 8) != tail) return;
        log("seen=" + n + " mode=" + modeLabel(mode) + " size=" + width + "x" + height
                + " bufferType=" + format + " heifInAps=" + inAps
                + " copyBytes=" + (bytes == null ? -1 : bytes.length) + " header=" + kind
                + "; before original, read-only; not complete HEIC/capture ownership proof");
    }

    private static String modeLabel(Object mode) {
        if (mode == null) return "unknown";
        if ("xpan".equals(mode)) return "xpan";
        if ("common".equals(mode)) return "common";
        return "other";
    }

    /** At most twelve fixed header bytes, never a pointer, image dump or full-array scan. */
    private static long prefix(byte[] bytes, int start) {
        if (bytes == null) return 0;
        long value = 0;
        for (int i = start; i < Math.min(bytes.length, start == 0 ? 8 : 12); i++)
            value = (value << 8) | (bytes[i] & 255L);
        return value;
    }
    private static String headerKind(byte[] bytes) {
        if (bytes == null) return "absent";
        if (bytes.length >= 12 && bytes[4] == 'f' && bytes[5] == 't'
                && bytes[6] == 'y' && bytes[7] == 'p') {
            long size = ((bytes[0] & 255L) << 24) | ((bytes[1] & 255L) << 16)
                    | ((bytes[2] & 255L) << 8) | (bytes[3] & 255L);
            if (size >= 12 && size <= bytes.length) return "bmff-ftyp";
        }
        if (bytes.length >= 3 && (bytes[0] & 255) == 255 && (bytes[1] & 255) == 216
                && (bytes[2] & 255) == 255) return "jpeg-prefix";
        return "unknown";
    }
    private static void log(String text) {
        try { XposedBridge.log("[XpanApsCaptureTrace] " + text); }
        catch (Throwable failure) { /* Never format a failing callback or logger exception. */ }
    }

    private static final class Bindings {
        Class<?> result;
        Method received;
        Field mode, width, height, format, inAps, bytes;
        void resolve(ClassLoader loader) throws Exception {
            result = Class.forName("com.oplus.ocs.camera.consumer.apsAdapter.adapter.ApsResult", false, loader);
            Class<?> processor = Class.forName("com.oplus.ocs.camera.consumer.ApsProcessor", false, loader);
            mode = field(result, "mCaptureMode", String.class);
            width = field(result, "mWidth", int.class);
            height = field(result, "mHeight", int.class);
            format = field(result, "mBufferType", int.class);
            inAps = field(result, "mbHeifProcessInAps", boolean.class);
            bytes = field(result, "mCopyBuffer", byte[].class);
            received = processor.getDeclaredMethod("onHeicReceived", result);
            if (received.getReturnType() != void.class || received.getModifiers() != Modifier.PUBLIC)
                throw new NoSuchMethodException("unexpected capture callback ABI");
        }
        private Field field(Class<?> owner, String name, Class<?> type) throws NoSuchFieldException {
            Field f = owner.getDeclaredField(name);
            if (f.getType() != type || f.getModifiers() != Modifier.PUBLIC)
                throw new NoSuchFieldException("unexpected capture result ABI");
            return f;
        }
    }
}
