package com.xpanport.omnicam.hook.features;

import java.io.File;
import java.lang.reflect.Method;

import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;

/**
 * 相机静音：hook 声音管理器 ql/g（OplusPlaySound）的播放入口 g(IZZ)，
 * 开关打开时直接跳过 —— 相机的快门/对焦/倒计时等全部提示音不再播放
 * （系统非静音模式下也能静音，比系统方案彻底）。
 * 开关文件：/sdcard/DCIM/.omnicam_silent（设置应用写入；2 秒缓存，拍照频率下无 I/O 压力）。
 */
public final class SilentCamera {

    private static final String TAG = "OmniCam: SilentCamera";
    private static final String MARKER = "/sdcard/DCIM/.omnicam_silent";
    private static volatile long checkedAt;
    private static volatile boolean muted;

    public String install(ClassLoader cl) throws Throwable {
        Class<?> g = cl.loadClass("ql.g");
        int hooked = 0;
        for (Method m : g.getDeclaredMethods()) {
            if (!"g".equals(m.getName())) continue;
            Class<?>[] pt = m.getParameterTypes();
            if (pt.length != 3 || !int.class.equals(pt[0])
                    || !boolean.class.equals(pt[1]) || !boolean.class.equals(pt[2])) continue;
            XposedBridge.hookMethod(m, new XC_MethodHook() {
                @Override
                protected void beforeHookedMethod(MethodHookParam param) {
                    long now = System.currentTimeMillis();
                    if (now - checkedAt > 2000L) {
                        checkedAt = now;
                        try {
                            muted = new File(MARKER).exists();
                        } catch (Throwable ignored) {
                        }
                    }
                    if (muted) param.setResult(null);
                }
            });
            hooked++;
        }
        if (hooked == 0) throw new IllegalStateException("ql.g.g(IZZ) 未找到");
        return "ql.g.g 静音钩子 x" + hooked;
    }

    static boolean isMuted() {
        long now = System.currentTimeMillis();
        if (now - checkedAt > 2000L) {
            checkedAt = now;
            try {
                muted = new File(MARKER).exists();
            } catch (Throwable ignored) {
            }
        }
        return muted;
    }
}
