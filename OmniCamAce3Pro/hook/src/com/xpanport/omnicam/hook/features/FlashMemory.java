package com.xpanport.omnicam.hook.features;

import java.io.File;
import java.util.HashMap;
import java.util.Map;

import de.robv.android.xposed.XC_MethodHook;
import java.lang.reflect.Method;

import de.robv.android.xposed.XposedBridge;

/**
 * 按模式记忆闪光灯（Muse 同名功能）：每个拍照模式各自记住自己的闪光灯设置。
 * 挂点：BaseMode.buildStreamSurface —— 每次会话构建必然发生，thisObject 的类名即模式标识；
 * 模式切换时把闪光灯键的当前值存进旧模式的槽位，再写回新模式槽位的值。
 * 读写走应用自己的 DataManager（内存缓存与通知一致）；存档持久化在相机自己的 files 目录。
 * 键名：pref_camera_flashmode_key（String，on/off/auto，117 处引用已核对）。
 */
public final class FlashMemory {

    private static final String TAG = "OmniCam: FlashMemory";
    private static final String KEY_NAME = "pref_camera_flashmode_key";
    private static final String KEEP = "/data/user/0/com.oplus.camera/files/omnicam_flash_keep.txt";
    private static final String BASE_MODE = "com.oplus.ocs.camera.producer.mode.BaseMode";

    private static String lastMode;                 // 上一个模式的标识
    private static final Map<String, String> saved = new HashMap<>();
    private static boolean dmReady;
    private static Object dm;
    private static Object flashKey;
    private static Method dmGet, dmSet;

    public String install(ClassLoader cl) throws Throwable {
        Class<?> base = cl.loadClass(BASE_MODE);
        Class<?> cfg = cl.loadClass("com.oplus.ocs.camera.common.parameter.SdkCameraDeviceConfig");
        Method bss = base.getDeclaredMethod("buildStreamSurface", cfg, String.class);
        XposedBridge.hookMethod(bss, new XC_MethodHook() {
            @Override
            protected void beforeHookedMethod(MethodHookParam param) {
                try {
                    Object thiz = param.thisObject;
                    if (thiz == null) return;
                    String mode = thiz.getClass().getName()
                            .replace("com.oplus.ocs.camera.producer.mode.", "");
                    if (mode.equals(lastMode)) return;      // 同模式多次建流不重算
                    if (!initDm(cl)) return;
                    String cur = readFlash();
                    if (lastMode != null && cur != null) {
                        saved.put(lastMode, cur);            // 旧模式存档
                    }
                    String want = saved.get(mode);
                    if (want != null && !want.equals(cur) && !cur.isEmpty()) {
                        writeFlash(want);                    // 新模式恢复
                    }
                    lastMode = mode;
                    XposedBridge.log(TAG + "模式=" + mode + " 闪光灯=" + cur
                            + (want != null ? " → 恢复 " + want : "（该模式首次记录）"));
                } catch (Throwable t) {
                    XposedBridge.log(TAG + "flash swap 失败: " + t);
                }
            }
        });
        return "BaseMode.buildStreamSurface 模式闪光灯记忆";
    }

    private static boolean initDm(ClassLoader cl) {
        if (dmReady && dm != null) return true;
        try {
            Class<?> dk = cl.loadClass("com.oplus.camera.data.DataKey");
            Class<?> fac = cl.loadClass("androidx.preference.e");
            Method f = fac.getMethod("f", Class.class, String.class);
            flashKey = f.invoke(null, String.class, KEY_NAME);
            Class<?> dmc = cl.loadClass("com.oplus.camera.data.DataManager");
            dmGet = dmc.getMethod("c", dk);
            dmSet = dmc.getMethod("g", dk, Object.class);
            dm = dmc.getMethod("getInstance").invoke(null);
            dmReady = dm != null;
        } catch (Throwable t) {
            XposedBridge.log(TAG + "DataManager 初始化失败: " + t);
        }
        return dmReady;
    }

    private static String readFlash() {
        try {
            Object v = dmGet.invoke(dm, flashKey);
            return v == null ? "" : String.valueOf(v);
        } catch (Throwable t) {
            return "";
        }
    }

    private static void writeFlash(String v) {
        try {
            dmSet.invoke(dm, flashKey, v);
        } catch (Throwable ignored) {
        }
    }
}
