package com.xpanport.omni;

import java.io.File;
import java.io.FileOutputStream;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.HashMap;
import java.util.Map;

import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;

/**
 * 按模式记忆闪光灯：每个拍照模式各自记住自己的闪光灯设置。
 * 读写走应用自己的 DataManager（内存缓存与界面通知一致）；键 pref_camera_flashmode_key（String on/off/auto）。
 *
 * C16（6.020.876）：挂 BaseMode.buildStreamSurface —— 每次会话构建必然发生，thisObject 类名即模式。
 * C17（7.012.64）：挂 flash.m1.onModeSwitchStageChanged —— 事件 d=from 模式、e=to 模式
 *   （方法体日志串 "from: d, to: e" 实锤）；应用在方法体内调 C4 给新模式上默认值，所以
 *   before 存旧模式的值、after 用新模式槽位的值盖回去（我们的写入在应用默认之后，必然赢）。
 *   同一次切换会触发多个 stage（b 字段），用"当前值归属 owner"防错记：
 *   恢复过之后 DM 里的值已属于新模式，再来的 stage 不能把它存进旧模式槽。
 * 内存映射按相机进程存活（与 C16 版一致）；调试存档写 files/omnicam_flash_keep_c17.txt。
 */
final class FlashMemory {

    private static final String KEY_NAME = "pref_camera_flashmode_key";
    private static final String KEEP = "/data/user/0/com.oplus.camera/files/omnicam_flash_keep_c17.txt";
    private static final String C17_EVENT =
            "com.oplus.camera.protocal.event.message.modeProtocol.EventMessageModeSwitcherStage";

    private static final Map<String, String> saved = new HashMap<>();
    private static String owner;            // 当前 DataManager 里的闪光灯值属于哪个模式
    private static boolean dmReady;
    private static Object dm;
    private static Object flashKey;
    private static Method dmGet, dmSet;

    private FlashMemory() {
    }

    static String install(final ClassLoader cl, String ver) throws Throwable {
        if ("C17".equals(ver)) return installC17(cl);
        return installC16(cl);
    }

    // ---------- C17：事件驱动 ----------

    private static String installC17(final ClassLoader cl) throws Throwable {
        Class<?> m1 = cl.loadClass("com.oplus.camera.feature.basic.flash.m1");

        // DataKey：构造完成后抓字段 D0（7.012.64 的 flash 键就存在这里）
        for (final Constructor<?> c : m1.getDeclaredConstructors()) {
            XposedBridge.hookMethod(c, new XC_MethodHook() {
                @Override
                protected void afterHookedMethod(MethodHookParam param) {
                    if (flashKey != null) return;
                    try {
                        Object k = fieldOf(param.thisObject, "D0");
                        if (k != null) {
                            flashKey = k;
                            initDm(cl);
                            XposedBridge.log(Bridge.TAG + " 闪光灯 DataKey 已捕获（m1.D0）");
                        }
                    } catch (Throwable t) {
                        XposedBridge.log(Bridge.TAG + " 捕获 D0 失败: " + t);
                    }
                }
            });
        }

        Class<?> ev = cl.loadClass(C17_EVENT);
        Method handler = m1.getDeclaredMethod("onModeSwitchStageChanged", ev);
        XposedBridge.hookMethod(handler, new XC_MethodHook() {
            @Override
            protected void beforeHookedMethod(MethodHookParam param) {
                try {
                    if (!initDm(cl)) return;
                    String from = modeField(param.args[0], "d");
                    String to = modeField(param.args[0], "e");
                    if (from == null || to == null || from.equals(to)) return;
                    if (to.equals(owner)) return;       // 我们刚恢复过，值已属于新模式
                    String cur = readFlash();
                    if (cur != null && !cur.isEmpty()) {
                        saved.put(from, cur);
                        persist();
                        XposedBridge.log(Bridge.TAG + " 闪光灯[" + from + "]=" + cur);
                    }
                } catch (Throwable t) {
                    XposedBridge.log(Bridge.TAG + " 存档失败: " + t);
                }
            }

            @Override
            protected void afterHookedMethod(MethodHookParam param) {
                try {
                    if (!dmReady) return;
                    String to = modeField(param.args[0], "e");
                    if (to == null) return;
                    String want = saved.get(to);
                    if (want != null) writeFlash(want);
                    owner = to;                          // 应用默认值（若有）也算新模式所有
                } catch (Throwable t) {
                    XposedBridge.log(Bridge.TAG + " 恢复失败: " + t);
                }
            }
        });
        restore();
        return "m1.onModeSwitchStageChanged 模式闪光灯记忆（C17）";
    }

    private static Object fieldOf(Object obj, String name) throws Throwable {
        Class<?> c = obj.getClass();
        while (c != null) {
            try {
                Field f = c.getDeclaredField(name);
                f.setAccessible(true);
                return f.get(obj);
            } catch (NoSuchFieldException ignored) {
                c = c.getSuperclass();
            }
        }
        return null;
    }

    private static String modeField(Object event, String name) {
        try {
            Field f = event.getClass().getField(name);
            Object v = f.get(event);
            return v == null ? null : String.valueOf(v);
        } catch (Throwable t) {
            return null;
        }
    }

    // ---------- C16：建流驱动（原 6.020.876 版逻辑） ----------

    private static String installC16(final ClassLoader cl) throws Throwable {
        final ClassLoader fcl = cl;
        Class<?> base = cl.loadClass("com.oplus.ocs.camera.producer.mode.BaseMode");
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
                    if (mode.equals(owner)) return;      // 同模式多次建流不重算
                    if (!initDm(fcl)) return;
                    String cur = readFlash();
                    if (owner != null && cur != null && !cur.isEmpty()) {
                        saved.put(owner, cur);           // 旧模式存档
                        persist();
                    }
                    String want = saved.get(mode);
                    if (want != null && !want.equals(cur) && cur != null && !cur.isEmpty()) {
                        writeFlash(want);                // 新模式恢复
                    }
                    owner = mode;
                    XposedBridge.log(Bridge.TAG + " 闪光灯[" + mode + "]=" + cur
                            + (want != null ? " → 恢复 " + want : "（首次记录）"));
                } catch (Throwable t) {
                    XposedBridge.log(Bridge.TAG + " flash swap 失败: " + t);
                }
            }
        });
        return "BaseMode.buildStreamSurface 模式闪光灯记忆（C16）";
    }

    // ---------- DataManager 与存档 ----------

    private static boolean initDm(ClassLoader cl) {
        if (dmReady && dm != null) return true;
        try {
            Class<?> dk = cl.loadClass("com.oplus.camera.data.DataKey");
            Class<?> dmc = cl.loadClass("com.oplus.camera.data.DataManager");
            dmGet = dmc.getMethod("c", dk);
            dmSet = dmc.getMethod("g", dk, Object.class);
            dm = dmc.getMethod("getInstance").invoke(null);
            if (flashKey == null) {
                // 兜底：应用自己的 DataKey 工厂（C16 实证；C17 若字段没抓到也试试）
                try {
                    Class<?> fac = cl.loadClass("androidx.preference.e");
                    Method f = fac.getMethod("f", Class.class, String.class);
                    flashKey = f.invoke(null, String.class, KEY_NAME);
                } catch (Throwable ignored) {
                }
            }
            dmReady = dm != null && flashKey != null;
        } catch (Throwable t) {
            XposedBridge.log(Bridge.TAG + " DataManager 初始化失败: " + t);
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

    private static void persist() {
        try {
            StringBuilder sb = new StringBuilder();
            for (Map.Entry<String, String> e : saved.entrySet()) {
                sb.append(e.getKey()).append('=').append(e.getValue()).append('\n');
            }
            File f = new File(KEEP);
            File dir = f.getParentFile();
            if (dir != null && !dir.isDirectory()) dir.mkdirs();
            FileOutputStream out = new FileOutputStream(f);
            out.write(sb.toString().getBytes("UTF-8"));
            out.close();
        } catch (Throwable ignored) {
        }
    }

    private static void restore() {
        try {
            File f = new File(KEEP);
            if (!f.isFile()) return;
            byte[] b = new byte[(int) f.length()];
            java.io.FileInputStream in = new java.io.FileInputStream(f);
            int n = in.read(b);
            in.close();
            if (n <= 0) return;
            for (String line : new String(b, "UTF-8").split("\n")) {
                int i = line.indexOf('=');
                if (i > 0 && i < line.length() - 1) saved.put(line.substring(0, i), line.substring(i + 1));
            }
        } catch (Throwable ignored) {
        }
    }
}
