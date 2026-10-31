package com.xpanport.omni;

import de.robv.android.xposed.IXposedHookLoadPackage;
import de.robv.android.xposed.XposedBridge;
import de.robv.android.xposed.callbacks.XC_LoadPackage;

/**
 * OmniCam Ace3Pro 补全钩子：并入 Muse 应用（assets/xposed_init 第二入口）。
 *
 * 相机混淆名跨版本全变，锚点探测决定走哪一套：
 *   C16 = 6.020.876（ColorOS 16）：声音 ql.g.g(IZZ)V、建流 BaseMode.buildStreamSurface
 *   C17 = 7.012.64（ColorOS 17）：声音 gq.e.j(IZZ)V、切模式 flash.m1.onModeSwitchStageChanged
 * 两代混淆名都在 7.012.64 反编译里逐个核实过（见 OmniCamAce3Pro/移植矩阵 与记忆）。
 *
 * 每个功能独立 try/catch：一个失败不影响其余，诊断看 LSPosed 日志（tag OmniCam+）。
 */
public final class Bridge implements IXposedHookLoadPackage {

    public static final String TAG = "OmniCam+";
    public static final String CAMERA = "com.oplus.camera";

    @Override
    public void handleLoadPackage(XC_LoadPackage.LoadPackageParam lpp) {
        if (!CAMERA.equals(lpp.packageName)) return;
        ClassLoader cl = lpp.classLoader;
        String ver = probe(cl);
        XposedBridge.log(TAG + " 相机代际=" + ver + "（按声音锚点判定）");

        try {
            XposedBridge.log(TAG + " 静音: " + SilentCamera.install(cl, ver));
        } catch (Throwable t) {
            XposedBridge.log(TAG + " 静音失败: " + t);
        }
        try {
            XposedBridge.log(TAG + " 闪光灯记忆: " + FlashMemory.install(cl, ver));
        } catch (Throwable t) {
            XposedBridge.log(TAG + " 闪光灯记忆失败: " + t);
        }
        try {
            XposedBridge.log(TAG + " 影调开关: " + ToneEnable.install(cl, ver));
        } catch (Throwable t) {
            XposedBridge.log(TAG + " 影调开关失败: " + t);
        }
    }

    /** 用"带 (IZZ)V 方法的类"本身当版本探针，比读 versionName 稳（不怕包名变化）。 */
    private static String probe(ClassLoader cl) {
        if (hasSoundAnchor(cl, "gq.e", "j")) return "C17";
        if (hasSoundAnchor(cl, "ql.g", "g")) return "C16";
        return "unknown";
    }

    private static boolean hasSoundAnchor(ClassLoader cl, String cls, String mname) {
        try {
            Class<?> c = cl.loadClass(cls);
            for (java.lang.reflect.Method m : c.getDeclaredMethods()) {
                if (!mname.equals(m.getName())) continue;
                Class<?>[] p = m.getParameterTypes();
                if (p.length == 3 && p[0] == int.class
                        && p[1] == boolean.class && p[2] == boolean.class) return true;
            }
        } catch (Throwable ignored) {
        }
        return false;
    }
}
