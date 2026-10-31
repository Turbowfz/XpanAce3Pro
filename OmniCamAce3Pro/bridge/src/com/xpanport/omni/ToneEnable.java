package com.xpanport.omni;

import java.lang.reflect.Method;

import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;

/**
 * 哈苏自然影调总开关：com.oplus.natural.tone.support 在配置里不存在 → 应用读默认 false →
 * 影调链（LMT 表 / BasicTone）全程不生效。接管 CameraConfig 的读取，开关打开时回答 true，
 * 相机才会真正应用挂载层里的影调数据（C16 上由 X-Pan 模块的 debug.xpan.tone 负责，这里只管 C17）。
 * C17 读取点：CameraConfig.d(String, Z, Z)Z —— Lut3dDataManager 用它查该键（反编译实锤）。
 * 开关：/sdcard/DCIM/.omnicam_tone（默认关，需重启相机生效）。
 */
final class ToneEnable {

    private static final String KEY = "com.oplus.natural.tone.support";
    private static final String MARKER = "/sdcard/DCIM/.omnicam_tone";

    private ToneEnable() {
    }

    static String install(ClassLoader cl, String ver) throws Throwable {
        if (!"C17".equals(ver)) {
            return "跳过（C16 由 X-Pan 模块的 debug.xpan.tone 负责）";
        }
        Class<?> cc = cl.loadClass("com.oplus.camera.configure.CameraConfig");
        int hooked = 0;
        for (Method m : cc.getDeclaredMethods()) {
            Class<?>[] p = m.getParameterTypes();
            if (p.length != 3 || !String.class.equals(p[0])
                    || !boolean.class.equals(p[1]) || !boolean.class.equals(p[2])) continue;
            if (!boolean.class.equals(m.getReturnType())) continue;
            XposedBridge.hookMethod(m, new XC_MethodHook() {
                @Override
                protected void beforeHookedMethod(MethodHookParam param) {
                    if (KEY.equals(param.args[0]) && Marker.on(MARKER)) param.setResult(Boolean.TRUE);
                }
            });
            hooked++;
        }
        if (hooked == 0) throw new IllegalStateException("CameraConfig.d(String,ZZ) 未找到");
        return "CameraConfig.d 影调钩子 x" + hooked + "（开关 /sdcard/DCIM/.omnicam_tone，改后重启相机）";
    }
}
