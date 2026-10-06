package de.robv.android.xposed;

import java.lang.reflect.Member;

/**
 * 编译期桩：真正的实现在 LSPosed 运行时里（libxposed）。
 * 这里只需要签名对得上，好让 javac 能编过；打包进 APK 后不会被用到。
 */
public class XposedBridge {

    public static void log(String text) {
        // stub
    }

    public static void log(Throwable t) {
        // stub
    }

    public static XC_MethodHook.Unhook hookMethod(Member hookMethod, XC_MethodHook callback) {
        return null;
    }

    public static void deoptimizeMethod(Member method) {
        // stub
    }
}
