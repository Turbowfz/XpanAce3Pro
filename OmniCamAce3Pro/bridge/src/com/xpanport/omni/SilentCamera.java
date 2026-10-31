package com.xpanport.omni;

import java.io.File;
import java.lang.reflect.Method;

import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;

/**
 * 相机静音：hook 声音管理器的播放入口 (IZZ)V，开关打开时直接跳过 ——
 * 快门/对焦/倒计时等全部提示音不播（系统非静音模式也静，比系统方案彻底）。
 * C16 锚点 ql.g.g(IZZ)V、C17 锚点 gq.e.j(IZZ)V：方法体里都是 SoundPool.load 相机音效，签名一致。
 * 开关：/sdcard/DCIM/.omnicam_silent。
 */
final class SilentCamera {

    private SilentCamera() {
    }

    static String install(final ClassLoader cl, String ver) throws Throwable {
        final String cls;
        final String mname;
        if ("C17".equals(ver)) {
            cls = "gq.e";
            mname = "j";
        } else {
            cls = "ql.g";
            mname = "g";
        }
        Class<?> g = cl.loadClass(cls);
        int hooked = 0;
        for (Method m : g.getDeclaredMethods()) {
            if (!mname.equals(m.getName())) continue;
            Class<?>[] pt = m.getParameterTypes();
            if (pt.length != 3 || !int.class.equals(pt[0])
                    || !boolean.class.equals(pt[1]) || !boolean.class.equals(pt[2])) continue;
            XposedBridge.hookMethod(m, new XC_MethodHook() {
                @Override
                protected void beforeHookedMethod(MethodHookParam param) {
                    if (Marker.on("/sdcard/DCIM/.omnicam_silent")) param.setResult(null);
                }
            });
            hooked++;
        }
        if (hooked == 0) throw new IllegalStateException(cls + "." + mname + "(IZZ) 未找到");
        return cls + "." + mname + " 静音钩子 x" + hooked + "（开关 /sdcard/DCIM/.omnicam_silent）";
    }

    static boolean isMuted() {
        return new File("/sdcard/DCIM/.omnicam_silent").exists();
    }
}
