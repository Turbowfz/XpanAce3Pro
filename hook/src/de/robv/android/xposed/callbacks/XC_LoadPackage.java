package de.robv.android.xposed.callbacks;

/** 编译期桩（真实实现由 LSPosed 提供）。 */
public class XC_LoadPackage {

    public static class LoadPackageParam {
        public String packageName;
        public String processName;
        public ClassLoader classLoader;
        public boolean isFirstApplication;
    }
}
