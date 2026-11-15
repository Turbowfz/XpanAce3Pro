package com.xpanport.hook;

import android.util.Size;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.IdentityHashMap;
import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;

/** C17 XPAN geometry getters only. Keep the qualified native crop, not another
 * module's model-specific values; final 8192 output remains in XpanC17Hook.
 * No whole-config replacement, cache writes, static Key reads or filter changes.
 */
public final class XpanC17Config {
    public static final String CAMERA_VERSION = "7.013.51";
    public interface Gate { boolean enabled(); }

    private static final String PICTURE = "com.oplus.xpan.main.picturesize";
    private static final String PORTRAIT = "com.oplus.xpan.crop.region.main.portrait";
    private static final String LANDSCAPE = "com.oplus.xpan.crop.region.main.landscape";
    private static final int[] PORTRAIT_VALUE = {780, 0, 1512, 4096};
    private static final int[] LANDSCAPE_VALUE = {0, 780, 4096, 1512};
    private static final String SDK_FRAME = "com.xpanport.hook.c17.config.sdk.frame";
    private static final String SURFACE_FRAME = "com.xpanport.hook.c17.config.surface.frame";
    private static final IdentityHashMap<ClassLoader, InstallResult> INSTALLS =
            new IdentityHashMap<ClassLoader, InstallResult>();

    public static final class InstallResult {
        public final boolean installed;
        public final int validatedBindings;
        public final int registeredHooks;
        public final String detail;
        private final XpanC17Config owner;
        private InstallResult(boolean ok, int bindings, int hooks, String detail, XpanC17Config owner) {
            installed = ok; validatedBindings = bindings; registeredHooks = hooks;
            this.detail = detail; this.owner = owner;
        }
    }

    /** Resolve ten bindings first; six callbacks remain inactive until commit.
     * The caller's live Gate also requires the matching C17 core to be ready.
     */
    public static synchronized InstallResult install(ClassLoader loader, String version, Gate gate) {
        if (loader == null || gate == null || !CAMERA_VERSION.equals(version)) {
            return new InstallResult(false, 0, 0, "refused: exact C17 version, loader and live Gate required", null);
        }
        InstallResult prior = INSTALLS.get(loader);
        if (prior != null) {
            if (prior.owner.gate == gate) return prior;
            return new InstallResult(false, prior.validatedBindings, prior.registeredHooks,
                    "refused: loader already has config hooks bound to another Gate", prior.owner);
        }
        Bindings b = new Bindings();
        XpanC17Config h = null;
        try {
            b.resolve(loader);
            h = new XpanC17Config(b, gate);
            h.register();
            InstallResult result = new InstallResult(true, b.count, h.hooks.size(),
                    "C17 geometry getter ABI installed; three XPAN keys; native 4096x1512; device/coexistence unverified", h);
            INSTALLS.put(loader, result);
            h.active = true;
            return result;
        } catch (Throwable failure) {
            int remaining = h == null ? 0 : h.rollback();
            InstallResult result = new InstallResult(false, b.count, remaining,
                    "install failed at " + b.current + "; remaining hooks=" + remaining + " (inactive)", h);
            if (remaining != 0) INSTALLS.put(loader, result);
            log(result.detail);
            return result;
        }
    }

    private final Bindings b;
    private final Gate gate;
    private final ArrayList<XC_MethodHook.Unhook> hooks = new ArrayList<XC_MethodHook.Unhook>();
    private final ThreadLocal<Integer> sdkPassthrough = new ThreadLocal<Integer>();
    private final ThreadLocal<Integer> sdkSurfaceDepth = new ThreadLocal<Integer>();
    private boolean uncertainRegistration;
    private volatile boolean active;

    private XpanC17Config(Bindings b, Gate gate) { this.b = b; this.gate = gate; }

    private static int[] arrayValue(Object key) {
        if (PORTRAIT.equals(key)) return PORTRAIT_VALUE.clone();
        if (LANDSCAPE.equals(key)) return LANDSCAPE_VALUE.clone();
        return null;
    }
    private static Size pictureValue() { return new Size(4096, 1512); }

    private void register() {
        hook(b.sizeGetter, new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam p) {
                if (!active || p.args == null || p.args.length != 2) return;
                // Original front=true ignores this key and uses high.picturesize_front.
                if (PICTURE.equals(p.args[0]) && Boolean.FALSE.equals(p.args[1])) replace(p, pictureValue());
            }
        });
        XC_MethodHook array = new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam p) {
                if (!active || p.args == null || p.args.length == 0) return;
                int[] value = arrayValue(p.args[0]);
                if (value != null) replace(p, value);
            }
        };
        hook(b.arrayDefault, array);
        hook(b.arrayGetter, array);

        hook(b.sdkSurface, new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam p) {
                if (!active) return;
                Integer prior = sdkSurfaceDepth.get();
                p.setObjectExtra(SURFACE_FRAME, new SdkFrame(prior));
                sdkSurfaceDepth.set(prior == null ? 1 : prior + 1);
            }
            @Override protected void afterHookedMethod(MethodHookParam p) {
                SdkFrame frame = (SdkFrame) p.getObjectExtra(SURFACE_FRAME);
                if (frame == null) return;
                if (frame.prior == null) sdkSurfaceDepth.remove();
                else sdkSurfaceDepth.set(frame.prior);
            }
        });
        // The same SDK key also fills RAW/DOL Pair.first. All surface reads
        // stay original here; the core's format-qualified stream hooks own sizing.
        hook(b.sdkTyped, new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam p) {
                if (!active || p.args == null || p.args.length != 2) return;
                // Shadow raw overrides before Key metadata reads, including nested calls
                // and unsupported types whose original parser uses the raw overload.
                Object[] args = p.args;
                Object[] snapshot = args.clone();
                Integer prior = sdkPassthrough.get();
                p.setObjectExtra(SDK_FRAME, new SdkFrame(prior));
                sdkPassthrough.set(prior == null ? 1 : prior + 1);
                try {
                    Object key = args[0];
                    if (sdkSurfaceDepth.get() == null && b.keyClass.isInstance(key)
                            && PICTURE.equals(b.keyName.invoke(key)) && b.keyType.invoke(key) == Size.class
                            && permitted() && unchanged(p, args, snapshot) && sdkSurfaceDepth.get() == null
                            && PICTURE.equals(b.keyNameValue.get(key)) && b.keyTypeValue.get(key) == Size.class
                            && unchanged(p, args, snapshot) && sdkSurfaceDepth.get() == null) {
                        p.setResult(pictureValue());
                    }
                } catch (Throwable failure) {
                    log("SDK Key metadata unavailable; original typed getter preserved");
                }
            }
            @Override protected void afterHookedMethod(MethodHookParam p) {
                SdkFrame frame = (SdkFrame) p.getObjectExtra(SDK_FRAME);
                if (frame == null) return;
                // Cleanup is independent of Gate/throwable; never strand an outer scope.
                if (frame.prior == null) sdkPassthrough.remove();
                else sdkPassthrough.set(frame.prior);
            }
        });
        hook(b.sdkString, new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam p) {
                if (active && sdkPassthrough.get() == null && sdkSurfaceDepth.get() == null
                        && p.args != null && p.args.length == 1 && PICTURE.equals(p.args[0])) replace(p, "4096x1512");
            }
        });
    }

    private static final class SdkFrame {
        final Integer prior;
        SdkFrame(Integer prior) { this.prior = prior; }
    }

    private boolean permitted() {
        if (!active) return false;
        try { return gate.enabled() && active; }
        catch (Throwable failure) { return false; }
    }

    private static boolean unchanged(XC_MethodHook.MethodHookParam p, Object[] args, Object[] snapshot) {
        if (p.args != args || args.length != snapshot.length) return false;
        for (int i = 0; i < args.length; i++) if (args[i] != snapshot[i]) return false;
        return true;
    }

    private boolean replace(XC_MethodHook.MethodHookParam p, Object value) {
        Object[] args = p.args, snapshot = args.clone();
        if (!permitted() || !unchanged(p, args, snapshot)) return false;
        if (b.sdkString.equals(p.method) && (sdkPassthrough.get() != null || sdkSurfaceDepth.get() != null)) return false;
        p.setResult(value);
        return true;
    }

    private void hook(Method method, XC_MethodHook callback) {
        b.current = method.toString();
        XC_MethodHook.Unhook handle = XposedBridge.hookMethod(method, callback);
        if (handle == null) {
            uncertainRegistration = true; // no handle cannot prove that registration had no side effects
            throw new IllegalStateException("registration returned no rollback handle");
        }
        hooks.add(handle);
    }
    private int rollback() {
        active = false;
        for (int i = hooks.size() - 1; i >= 0; i--) {
            try { hooks.get(i).unhook(); hooks.remove(i); }
            catch (Throwable failure) { log("unhook failed; callback stays inactive"); }
        }
        return hooks.size() + (uncertainRegistration ? 1 : 0);
    }
    private static void log(String text) {
        try { XposedBridge.log("[XpanC17Config] " + text); }
        catch (Throwable ignored) { /* Diagnostics never change original config behavior. */ }
    }

    private static final class Bindings {
        int count;
        String current = "version/loader";
        Class<?> keyClass;
        Field keyNameValue, keyTypeValue;
        Method sizeGetter, arrayDefault, arrayGetter, sdkTyped, sdkString, keyName, keyType, sdkSurface;
        void resolve(ClassLoader loader) throws Exception {
            Class<?> config = cls(loader, "com.oplus.camera.configure.CameraConfig");
            sizeGetter = method(config, "getSizeConfigValue", Size.class, true, String.class, boolean.class);
            arrayDefault = method(config, "l", int[].class, true, String.class, int[].class);
            arrayGetter = method(config, "getConfigIntArrayValue", int[].class, true, String.class);
            Class<?> sdk = cls(loader, "com.oplus.ocs.camera.producer.info.CameraConfigHelper");
            keyClass = cls(loader, "com.oplus.ocs.camera.common.util.CameraConfigBase$Key");
            keyName = method(keyClass, "getName", String.class, false);
            keyType = method(keyClass, "getType", Class.class, false);
            keyNameValue = keyField(keyClass, "mKeyName", String.class);
            keyTypeValue = keyField(keyClass, "mType", Class.class);
            sdkTyped = method(sdk, "getConfigValue", Object.class, true, keyClass, Object.class);
            sdkString = method(sdk, "getConfigValue", String.class, true, String.class);
            Class<?> xpan = cls(loader, "com.oplus.ocs.camera.producer.mode.XpanMode");
            Class<?> configClass = cls(loader, "com.oplus.ocs.camera.common.parameter.SdkCameraDeviceConfig");
            sdkSurface = method(xpan, "getSurfaceSize", java.util.List.class, false,
                    configClass, String.class, String.class, String.class, int.class);
        }
        private Class<?> cls(ClassLoader loader, String name) throws ClassNotFoundException {
            current = name;
            return Class.forName(name, false, loader);
        }
        private Field keyField(Class<?> owner, String name, Class<?> type) throws NoSuchFieldException {
            current = owner.getName() + "." + name;
            Field f = owner.getDeclaredField(name);
            if (f.getType() != type || f.getModifiers() != Modifier.PRIVATE)
                throw new NoSuchFieldException("unexpected SDK Key field: " + name);
            f.setAccessible(true);
            count++;
            return f;
        }
        private Method method(Class<?> owner, String name, Class<?> result, boolean statik,
                              Class<?>... params) throws NoSuchMethodException {
            current = owner.getName() + "." + name;
            Method m = owner.getDeclaredMethod(name, params);
            int flags = m.getModifiers();
            int visibility = flags & (Modifier.PUBLIC | Modifier.PROTECTED | Modifier.PRIVATE);
            if (m.getReturnType() != result || visibility != Modifier.PUBLIC
                    || Modifier.isStatic(flags) != statik || Modifier.isAbstract(flags)) {
                throw new NoSuchMethodException("unexpected descriptor/access: " + m);
            }
            count++;
            return m;
        }
    }
}
