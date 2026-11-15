package com.xpanport.hook;

import android.util.Size;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.IdentityHashMap;
import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;

/** C16 getter-only config layer. Caller approves device/profile before installing.
 * Six app keys are receipt overrides; native XPAN gets only its style gate/version.
 * No feature-list injection, config/cache writes or target static Key reads.
 */
public final class XpanC16Config {
    public static final String CAMERA_VERSION = "6.020.876";
    public interface Gate { boolean enabled(); }

    private static final String SUPPORT = "com.oplus.feature.xpan.mode.support";
    private static final String VERSION = "com.oplus.xpan.mode.version";
    private static final String LEGACY = "com.oplus.xpan.legacy.ui.style";
    private static final String PICTURE = "com.oplus.xpan.main.picturesize";
    private static final String PORTRAIT = "com.oplus.xpan.crop.region.main.portrait";
    private static final String LANDSCAPE = "com.oplus.xpan.crop.region.main.landscape";
    private static final int[] PORTRAIT_VALUE = {780, 0, 1512, 4096};
    private static final int[] LANDSCAPE_VALUE = {0, 780, 4096, 1512};
    private static final String SDK_FRAME = "com.xpanport.hook.c16.config.sdk.frame";
    private static final String XPAN_FRAME = "com.xpanport.hook.c16.config.xpan.frame";
    private static final IdentityHashMap<ClassLoader, InstallResult> INSTALLS =
            new IdentityHashMap<ClassLoader, InstallResult>();

    public static final class InstallResult {
        public final boolean installed;
        public final int validatedBindings;
        /** Successful registrations, or disabled residual handles on failure. */
        public final int registeredHooks;
        public final String detail;
        private final XpanC16Config owner;
        private InstallResult(boolean ok, int bindings, int hooks, String detail, XpanC16Config owner) {
            installed = ok; validatedBindings = bindings; registeredHooks = hooks;
            this.detail = detail; this.owner = owner;
        }
    }

    /** Resolve all thirteen methods first; all eleven callbacks stay inactive until commit.
     * Mandatory live Gate is consulted at every setResult, never captured at install.
     * Residual failed unhooks remain inactive and block a second overlapping install.
     */
    public static synchronized InstallResult install(ClassLoader loader, String version, Gate gate) {
        if (loader == null || gate == null || !CAMERA_VERSION.equals(version)) {
            return new InstallResult(false, 0, 0, "refused: exact C16 version, loader and live Gate required", null);
        }
        InstallResult prior = INSTALLS.get(loader);
        if (prior != null) {
            if (prior.owner.gate == gate) return prior;
            return new InstallResult(false, prior.validatedBindings, prior.registeredHooks,
                    "refused: loader already has config hooks bound to another Gate", prior.owner);
        }
        Bindings b = new Bindings();
        XpanC16Config h = null;
        try {
            b.resolve(loader);
            h = new XpanC16Config(b, gate);
            h.register();
            InstallResult result = new InstallResult(true, b.count, h.hooks.size(),
                    "C16 getter ABI installed; six receipt keys; SDK main Size; native XPAN-only style gate=1/version=3; device/HAL unverified", h);
            INSTALLS.put(loader, result);
            h.active = true;
            return result;
        } catch (Throwable failure) {
            int remaining = h == null ? 0 : h.rollback();
            InstallResult result = new InstallResult(false, b.count, remaining,
                    "install failed at " + b.current + ": " + failure + "; remaining hooks=" + remaining + " (inactive)", h);
            if (remaining != 0) INSTALLS.put(loader, result);
            log(result.detail);
            return result;
        }
    }

    private final Bindings b;
    private final Gate gate;
    private final ArrayList<XC_MethodHook.Unhook> hooks = new ArrayList<XC_MethodHook.Unhook>();
    // A mismatched typed Key must not acquire a Size string from its inner raw getter.
    private final ThreadLocal<Integer> sdkPassthrough = new ThreadLocal<Integer>();
    // Permission belongs to the exact native XPAN builder, not its regular-mode parent.
    private final ThreadLocal<Boolean> nativeXpan = new ThreadLocal<Boolean>();
    private volatile boolean active;

    private XpanC16Config(Bindings b, Gate gate) { this.b = b; this.gate = gate; }

    private static Boolean booleanValue(Object key) {
        if (SUPPORT.equals(key)) return Boolean.TRUE;
        if (LEGACY.equals(key)) return Boolean.FALSE;
        return null;
    }
    private static int[] arrayValue(Object key) {
        if (PORTRAIT.equals(key)) return PORTRAIT_VALUE.clone();
        if (LANDSCAPE.equals(key)) return LANDSCAPE_VALUE.clone();
        return null;
    }
    private static Size pictureValue() { return new Size(4096, 1512); }

    private void register() {
        XC_MethodHook bool = new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam p) {
                if (!active) return;
                Boolean value = booleanValue(p.args[0]);
                if (value != null) replace(p, value);
            }
        };
        hook(b.booleanGetter, bool);
        hook(b.booleanDefault, bool);
        hook(b.integerDefault, new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam p) {
                if (active && VERSION.equals(p.args[1])) replace(p, Integer.valueOf(3));
            }
        });
        hook(b.sizeGetter, new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam p) {
                // Original front=true ignores the supplied key and uses high.picturesize_front.
                if (active && PICTURE.equals(p.args[0]) && Boolean.FALSE.equals(p.args[1])) {
                    replace(p, pictureValue());
                }
            }
        });
        XC_MethodHook array = new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam p) {
                if (!active) return;
                int[] value = arrayValue(p.args[0]);
                if (value != null) replace(p, value);
            }
        };
        hook(b.arrayDefault, array);
        hook(b.arrayGetter, array);

        // Original SDK evidence: CameraConfigBase.KEY_XPAN_MAIN_PICTURE_SIZE is Key<Size>.
        // Do not read that static field (would initialize CameraConfigBase), or infer
        // SDK types for other receipt keys. Their app getters are covered above.
        hook(b.sdkTyped, new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam p) {
                if (!active) return;
                try {
                    Object key = p.args[0];
                    if (b.keyClass.isInstance(key) && PICTURE.equals(b.keyName.invoke(key))
                            && b.keyType.invoke(key) == Size.class && replace(p, pictureValue())) return;
                } catch (Throwable failure) {
                    log("SDK Key metadata unavailable; original typed getter preserved: " + failure);
                }
                // Preserve all unsupported typed calls, including same-name String/Size[]
                // keys; their original may call the separately hooked raw overload.
                Integer prior = sdkPassthrough.get();
                p.setObjectExtra(SDK_FRAME, new SdkFrame(prior));
                sdkPassthrough.set(prior == null ? 1 : prior + 1);
            }
            @Override protected void afterHookedMethod(MethodHookParam p) {
                SdkFrame frame = (SdkFrame) p.getObjectExtra(SDK_FRAME);
                if (frame == null) return;
                // Cleanup even if Gate changed or original threw; not a config mutation.
                if (frame.prior == null) sdkPassthrough.remove();
                else sdkPassthrough.set(frame.prior);
            }
        });
        hook(b.sdkString, new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam p) {
                // SDK Size parser splits [Xx], unlike the app's own Size path.
                if (active && sdkPassthrough.get() == null && PICTURE.equals(p.args[0])) {
                    replace(p, "4096x1512");
                }
            }
        });

        hook(b.nativeXpanBuilder, new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam p) {
                if (!active) return;
                p.setObjectExtra(XPAN_FRAME, new XpanFrame(nativeXpan.get()));
                // A disabled nested entry shadows, rather than inherits, its parent.
                nativeXpan.set(Boolean.valueOf(permitted()));
            }
            @Override protected void afterHookedMethod(MethodHookParam p) {
                XpanFrame frame = (XpanFrame) p.getObjectExtra(XPAN_FRAME);
                if (frame == null) return;
                // Restore even after original failure or Gate revocation; no target writes.
                if (frame.prior == null) nativeXpan.remove();
                else nativeXpan.set(frame.prior);
            }
        });
        hook(b.nativeStyle, new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam p) {
                if (active && Boolean.TRUE.equals(nativeXpan.get())) {
                    // CouiUtil accepts Byte("1") > 0. Skip its original so shared
                    // sHasselbladStyle never caches an XPAN-only answer (or blocks on "0").
                    replace(p, Boolean.TRUE);
                }
            }
        });
        hook(b.vendorString, new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam p) {
                if (active && Boolean.TRUE.equals(nativeXpan.get()) && VERSION.equals(p.args[0])) {
                    // CommonUtil reads this direct vendor map, not app CameraConfig.
                    // Leave the raw style key and every unrelated/missing key untouched.
                    replace(p, "3");
                }
            }
        });
    }

    private static final class XpanFrame {
        final Boolean prior;
        XpanFrame(Boolean prior) { this.prior = prior; }
    }

    private static final class SdkFrame {
        final Integer prior;
        SdkFrame(Integer prior) { this.prior = prior; }
    }

    private boolean permitted() {
        if (!active) return false;
        try {
            if (!gate.enabled() || !active) return false;
        } catch (Throwable failure) {
            log("Gate failed; original getter preserved: " + failure);
            return false;
        }
        return true;
    }

    /** One mutation boundary shared by every config override; exceptions fail closed. */
    private boolean replace(XC_MethodHook.MethodHookParam p, Object value) {
        if (!permitted()) return false;
        p.setResult(value);
        return true;
    }

    private void hook(Method method, XC_MethodHook callback) {
        b.current = method.toString();
        XC_MethodHook.Unhook handle = XposedBridge.hookMethod(method, callback);
        if (handle == null) throw new IllegalStateException("registration returned no rollback handle");
        hooks.add(handle);
    }
    private int rollback() {
        active = false;
        for (int i = hooks.size() - 1; i >= 0; i--) {
            try { hooks.get(i).unhook(); hooks.remove(i); }
            catch (Throwable failure) { log("unhook failed; callback stays inactive: " + failure); }
        }
        return hooks.size();
    }
    private static void log(String text) {
        try { XposedBridge.log("[XpanC16Config] " + text); }
        catch (Throwable ignored) { /* Diagnostics never change original config behavior. */ }
    }

    private static final class Bindings {
        int count;
        String current = "version/loader";
        Class<?> keyClass;
        Method booleanGetter, booleanDefault, integerDefault, sizeGetter, arrayDefault, arrayGetter;
        Method sdkTyped, sdkString, keyName, keyType;
        Method nativeXpanBuilder, nativeStyle, vendorString;
        void resolve(ClassLoader loader) throws Exception {
            Class<?> config = cls(loader, "com.oplus.camera.configure.CameraConfig");
            booleanGetter = method(config, "getConfigBooleanValue", boolean.class, true, String.class);
            booleanDefault = method(config, "b", boolean.class, true, String.class, boolean.class);
            integerDefault = method(config, "i", int.class, true, int.class, String.class);
            sizeGetter = method(config, "getSizeConfigValue", Size.class, true, String.class, boolean.class);
            arrayDefault = method(config, "h", int[].class, true, String.class, int[].class);
            arrayGetter = method(config, "getConfigIntArrayValue", int[].class, true, String.class);
            Class<?> sdk = cls(loader, "com.oplus.ocs.camera.producer.info.CameraConfigHelper");
            keyClass = cls(loader, "com.oplus.ocs.camera.common.util.CameraConfigBase$Key");
            keyName = method(keyClass, "getName", String.class, false);
            keyType = method(keyClass, "getType", Class.class, false);
            sdkTyped = method(sdk, "getConfigValue", Object.class, true, keyClass, Object.class);
            sdkString = method(sdk, "getConfigValue", String.class, true, String.class);
            Class<?> groups = cls(loader, "com.oplus.ocs.camera.ipusdk.processunit.filter.list.FilterGroupManager");
            nativeXpanBuilder = declaredMethod(groups, "initHasselbladXpanFilterGroup", void.class,
                    true, Modifier.PRIVATE);
            Class<?> coui = cls(loader, "com.oplus.ocs.camera.ipusdk.processunit.filter.list.CouiUtil");
            nativeStyle = method(coui, "isHasselbladStyle", boolean.class, true);
            Class<?> aps = cls(loader, "com.oplus.ocs.camera.consumer.apsAdapter.adapter.ApsUtils");
            vendorString = method(aps, "getVendorTagConfig", String.class, true, String.class);
        }
        private Class<?> cls(ClassLoader loader, String name) throws ClassNotFoundException {
            current = name;
            return Class.forName(name, false, loader);
        }
        private Method method(Class<?> owner, String name, Class<?> result, boolean statik,
                              Class<?>... params) throws NoSuchMethodException {
            return declaredMethod(owner, name, result, statik, Modifier.PUBLIC, params);
        }
        private Method declaredMethod(Class<?> owner, String name, Class<?> result, boolean statik,
                                      int access, Class<?>... params) throws NoSuchMethodException {
            current = owner.getName() + "." + name;
            Method m = owner.getDeclaredMethod(name, params);
            int flags = m.getModifiers();
            int visibility = flags & (Modifier.PUBLIC | Modifier.PROTECTED | Modifier.PRIVATE);
            if (m.getReturnType() != result || visibility != access
                    || Modifier.isStatic(flags) != statik || Modifier.isAbstract(flags)) {
                throw new NoSuchMethodException("unexpected descriptor/access: " + m);
            }
            count++;
            return m;
        }
    }
}
