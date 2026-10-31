package de.robv.android.xposed;

import de.robv.android.xposed.callbacks.XC_LoadPackage;

/** 编译期桩（真实实现由 LSPosed 提供）。 */
public interface IXposedHookLoadPackage {
    void handleLoadPackage(XC_LoadPackage.LoadPackageParam lpparam) throws Throwable;
}
