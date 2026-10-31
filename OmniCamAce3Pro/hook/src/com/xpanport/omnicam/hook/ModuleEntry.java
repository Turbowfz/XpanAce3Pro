package com.xpanport.omnicam.hook;

import java.util.ArrayList;
import java.util.List;

import de.robv.android.xposed.IXposedHookLoadPackage;
import de.robv.android.xposed.callbacks.XC_LoadPackage;
import de.robv.android.xposed.XposedBridge;

/**
 * OmniCam Ace 3Pro 钩子入口（独立于 X-Pan 的 XpanHook）。
 * 逐个装载功能钩子；新适配的功能往 FEATURES 加一行即可。
 * 当前首批功能在移植矩阵定稿后逐个补入（见 OmniCamAce3Pro/移植矩阵.md）。
 */
public final class ModuleEntry implements IXposedHookLoadPackage {
    private static final String TAG = "OmniCam: ";
    private static final String CAM = "com.oplus.camera";

    private interface Feature {
        /** 安装钩子，返回描述串；抛异常视为该功能失败（不影响其他功能）。 */
        String install(ClassLoader cl) throws Throwable;
    }

    private static final List<Feature> FEATURES = new ArrayList<>();
    static {
        FEATURES.add(new Feature() {
            @Override
            public String install(ClassLoader cl) throws Throwable {
                return new com.xpanport.omnicam.hook.features.SilentCamera().install(cl);
            }
        });
        FEATURES.add(new Feature() {
            @Override
            public String install(ClassLoader cl) throws Throwable {
                return new com.xpanport.omnicam.hook.features.FlashMemory().install(cl);
            }
        });
    }

    @Override
    public void handleLoadPackage(XC_LoadPackage.LoadPackageParam lpp) {
        if (!CAM.equals(lpp.packageName)) return;
        XposedBridge.log(TAG + "handleLoadPackage, features=" + FEATURES.size());
        for (Feature f : FEATURES) {
            try {
                XposedBridge.log(TAG + "installed: " + f.install(lpp.classLoader));
            } catch (Throwable t) {
                XposedBridge.log(TAG + "install 失败: " + t);
            }
        }
    }
}
