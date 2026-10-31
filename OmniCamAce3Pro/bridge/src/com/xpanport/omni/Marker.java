package com.xpanport.omni;

import java.io.File;
import java.util.HashMap;
import java.util.Map;

/**
 * 开关文件（存在=开）。2 秒缓存：拍照频率下无 I/O 压力，改动最多 2 秒生效。
 * 开关面：KernelSU 模块卡的 WebUI（webroot/index.html）、adb touch/rm、文件管理器皆可。
 */
final class Marker {

    private static final Map<String, Long> AT = new HashMap<>();
    private static final Map<String, Boolean> VAL = new HashMap<>();

    private Marker() {
    }

    static boolean on(String path) {
        long now = System.currentTimeMillis();
        Long t = AT.get(path);
        if (t == null || now - t > 2000L) {
            boolean v;
            try {
                v = new File(path).exists();
            } catch (Throwable ignored) {
                v = false;
            }
            AT.put(path, now);
            VAL.put(path, v);
        }
        Boolean b = VAL.get(path);
        return b != null && b;
    }
}
