package com.xpanport.hook;

import android.content.Context;
import android.graphics.ImageFormat;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.params.StreamConfigurationMap;
import android.util.Size;
import android.util.Pair;

import java.io.File;
import java.lang.reflect.Method;
import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.util.ArrayList;
import java.util.List;

import de.robv.android.xposed.IXposedHookLoadPackage;
import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;
import de.robv.android.xposed.callbacks.XC_LoadPackage;

/**
 * X-Pan 移植：把 X-Pan 模式申请的出流尺寸换成"本机 HAL 真支持"的组合。
 *
 * 背景（实机取证）：
 *   X-Pan 会话原本要申请 1920x864(预览) + 4096x1512(拍照)，本机相机 HAL 不认这组，
 *   回 "Set of requested inputs/outputs not supported by HAL"，会话失败、预览黑屏
 *   （error code 10002, stream surface error）。
 *
 * 做法（实机试出来的能跑组合）：
 *   预览 2304x1048  +  拍照 4096x1512   ← 4096x1512 就是 65:24，拍出来即标准宽幅
 *
 * ★ 三条必须守住的边界（都是踩过坑换来的）：
 *   1) 只在 X-Pan 模式改尺寸。其他模式一律放行 —— 否则切回普通模式会预览格式不符（果冻）、
     *      预览起不来（快门消失）、模式状态卡死。建流使用独立 scope，提交状态读实际模式实例。
 *   2) 只改"能确认是预览/拍照"的面。usage 认不出来就**一律不动** ——
     *      RAW、PRIVATE 或未知格式不改；usage 单独不能区分 RAW 与 YUV，必须同时核对格式。
     *      不能把其他流强改为预览，破坏画面比例。
 *   3) 某个 hook 装失败不能影响其他 hook —— 之前 getSurfaceSize 找不到就 return，
     *      耦合成员应先成组验证；C17 路线注册失败撤回，不能以某个单钩子成功冒充完整功能。
 *
 * 另外还修了"进一次 X-Pan 只能拍一张"（实机取证 2026-10-05）：
 *   X-Pan 的界面容器 zl.q(XpanUIContainer) 在版本号==3 时确实被创建了，
 *   但应用从不驱动它 —— ic()/o4()/q8()/k() 整个会话 0 次调用，Q 停在构造初值 -1；
 *   而 zl.q.g()/c1() 都是"Q==-1 就跳过 ic()"，于是 ic() 永远不执行。
 *   拍照开始时 CameraControlUI.A(false,false)/c4(false,false) 禁用快门，
 *   本该由 ic() 里 Q==0 分支执行的 A(1,0)/c4(1,0) 恢复调用永远不来 → 快门永久失效。
 *   现在抓住 A/c4 被调成"禁用"的时机，等拍照结束后用应用自己的逻辑恢复
 *   （先试 hc(0)+ic()，容器视图未初始化会 NPE，则直接 c4(true,false)+A(true,false)）。
 */
public class XpanHook implements IXposedHookLoadPackage {

    private static final String TAG = "XpanPort: ";

    /** ★ 热路径日志开关（性能）：每帧/每次请求都打的那种日志是实打实的开销
     *  （logcat 写入 + 字符串拼接，实测一次拍照会话能刷几千行）。一律走 dlog()，
     *  默认不打；排查时 setprop debug.xpan.diag 1 再开。 */
    private static volatile boolean sDiag = false;

    private static void dlog(String msg) {
        if (sDiag) XposedBridge.log(TAG + msg);
    }

    private static final String PKG = "com.oplus.camera";

    private static final String CLS_XPAN_MODE = "com.oplus.ocs.camera.producer.mode.XpanMode";
    private static final String CLS_BASE_MODE = "com.oplus.ocs.camera.producer.mode.BaseMode";
    private static final String CLS_SURFACE_WRAPPER = "com.oplus.ocs.camera.common.surface.SurfaceWrapper";
    private static final String CLS_DEVICE_CONFIG = "com.oplus.ocs.camera.common.parameter.SdkCameraDeviceConfig";

    private static final String DEFAULT_PREVIEW = "2304x1048";
    private static final String DEFAULT_CAPTURE = "4096x1512";
    /** v1.3 起的默认拍照档。实测定论（12_验证/分辨率实验结论.md）：
     *  HAL 对 >4096 宽的流一律从 12.5MP 四合一读出后放大，8192x3024 是 24.8MP 的
     *  "算法放大"图 —— 像素 x4、细节不增加，看图能放大更多、文件更大。
     *  连拍 3 张实测稳定。系统"高像素"模式也是同级别算法重构，真细节上限就是 6.2MP。 */
    private static final String BIG_CAPTURE = "8192x3024";
    /** 超广角（imx355）HAL 流的 X-Pan 尺寸。实测 2026-10-06 五种尺寸的结论：
     *  4096x1512 → 整片绿帧（宽度超出 imx355 上限，HAL 不填缓冲，YUV 全 0 就是绿）；
     *  3264x1205 / 3216x1440 / 2520x1080 → 雪花噪声（表外或非 2.2:1 通路）；
     *  3200x1181 → 快门静默失败，根本不出片；
     *  2780x1264 (2.199) → ✔ 唯一能出真实照片的宽幅档 —— 超广角的 ISP 只调通了
     *  2.2:1 电影比例这一条路，硬件上出不了 65:24（imx355 是 8MP，流表最宽 3216）。
     *  历史实验曾开放超广角；当前变焦条和内部请求仅允许主摄 1×，此档不再对用户开放。 */
    private static final String DEFAULT_UW_CAPTURE = "2780x1264";

    /** rear_main=主摄；rear_wide=超广角（本机命名，实测日志里 0.6× 时出现） */
    private static boolean isUltraWide(String cameraType) {
        String c = cameraType.toLowerCase();
        return c.contains("wide") && !c.contains("main");
    }

    private static String cameraTypeOf(Object wrapper) {
        if (wrapper == null) return null;
        try {
            Method m = wrapper.getClass().getMethod("getCameraType");
            Object t = m.invoke(wrapper);
            return (t instanceof String) ? (String) t : null;
        } catch (Throwable t) {
            return null;
        }
    }

    /** 已从 ModeManager 实际运行实例见证的模式，不是建流/请求模式。 */
    private static volatile boolean sXpanActive = false;
    /** SDK 建流期间的嵌套线程作用域；after 即使 gateoff 也必须归还。 */
    private static final ThreadLocal<Boolean> sXpanBuilding = new ThreadLocal<>();
    private static final String MODE_FRAME = "com.xpanport.hook.mode.transaction";
    private static final String BUILD_FRAME = "com.xpanport.hook.mode.build";

    private static final class BuildScope {
        final Boolean prior;
        BuildScope(Boolean prior) { this.prior = prior; }
    }

    /** 实际倍率仅用于诊断；X-Pan 不按倍率改变拍照尺寸。 */
    private static volatile float sXpanZoomReq = 1.0f;

    /** XPanPresenter(bh.c) 实例 —— FeatureFactory.b 的返回值，滤镜重新装配必须用它 */
    private static volatile Object sPresenter = null;
    /** 当次 X-Pan 的 Activity 与 ui.a（重放视图装配要用最新实例，旧实例会把预览绑错） */
    private static volatile Object sAct = null;
    private static volatile Object sUi = null;

    /** 是否已经排了一次"拍完恢复 UI"的任务（避免重复排队） */
    private static volatile boolean sRestorePending = false;

    private static volatile Size sPreviewSize = null;
    private static volatile Size sCaptureSize = null;
    private static volatile Size[] sAvail = null;
    private static volatile CameraProfile sProfile;
    private static volatile ModuleGate sModuleGate;
    private static volatile boolean sC17CoreReady;
    private boolean initialized;
    private XdrBeforeSave.InstallResult xdrProcessor;
    private XpanC17Menu.InstallResult c17Menu;
    private XpanC17Ui.InstallResult c17Ui;
    private XpanGainMapFrame.InstallResult gainMapFrame;

    private static boolean moduleEnabled() {
        ModuleGate gate = sModuleGate;
        return gate != null && gate.enabled();
    }

    /** C16 callbacks share the same fail-closed gate; attach itself is not gated. */
    private abstract static class QualifiedHook extends XC_MethodHook {
        @Override
        protected final void beforeHookedMethod(MethodHookParam param) throws Throwable {
            if (moduleEnabled()) beforeQualified(param);
        }

        @Override
        protected final void afterHookedMethod(MethodHookParam param) throws Throwable {
            if (moduleEnabled()) afterQualified(param);
        }

        protected void beforeQualified(MethodHookParam param) throws Throwable { }
        protected void afterQualified(MethodHookParam param) throws Throwable { }
    }

    @Override
    public void handleLoadPackage(final XC_LoadPackage.LoadPackageParam lp) throws Throwable {
        if (!PKG.equals(lp.packageName) || !PKG.equals(lp.processName)) return;
        Method attach = android.app.Application.class.getDeclaredMethod("attach", Context.class);
        XposedBridge.hookMethod(attach, new XC_MethodHook() {
            @Override
            protected void afterHookedMethod(MethodHookParam param) {
                try {
                    initialize(lp, (Context) param.args[0]);
                } catch (Throwable t) {
                    XposedBridge.log(TAG + "camera profile installation failed: " + t);
                }
            }
        });
    }

    private synchronized void initialize(XC_LoadPackage.LoadPackageParam lp, Context context)
            throws Throwable {
        if (initialized) return;
        CameraProfile profile = CameraProfile.read(context);
        if (profile.generation == CameraProfile.Generation.UNSUPPORTED) {
            XposedBridge.log(TAG + "unsupported camera build: " + profile.cameraVersion);
            return;
        }
        sProfile = profile;
        sModuleGate = ModuleGate.create(context, profile);
        if (!sModuleGate.verified()) {
            XposedBridge.log(TAG + "module qualification refused: " + sModuleGate.detail());
            return;
        }
        if (profile.generation == CameraProfile.Generation.C17) {
            sC17CoreReady = false;
            XpanC17Config.InstallResult config = XpanC17Config.install(
                    lp.classLoader, profile.cameraVersion, new XpanC17Config.Gate() {
                @Override public boolean enabled() { return sC17CoreReady && moduleEnabled(); }
            });
            XposedBridge.log(TAG + "C17 geometry config installation: " + config.detail);
            if (!config.installed) return;
            c17Menu = XpanC17Menu.install(lp.classLoader, profile.cameraVersion, new XpanC17Menu.Gate() {
                @Override public boolean enabled() { return sC17CoreReady && moduleEnabled(); }
            });
            XposedBridge.log(TAG + "C17 menu installation: " + c17Menu.detail);
            if (!c17Menu.installed) return;
            c17Ui = XpanC17Ui.install(lp.classLoader, profile.cameraVersion, new XpanC17Ui.Gate() {
                @Override public boolean enabled() { return sC17CoreReady && moduleEnabled(); }
            });
            XposedBridge.log(TAG + "C17 UI guard installation: " + c17Ui.detail);
            installWatermarkIsolation(lp);
            XpanC17Hook.InstallResult result = XpanC17Hook.install(
                    lp.classLoader, profile.cameraVersion, new XpanC17Hook.Callbacks() {
                @Override
                public Object beforeModeChange(Object manager, String oldMode, String requestedMode) {
                    return wmBeforeModeChange(oldMode, requestedMode);
                }

                @Override
                public void afterModeChange(Object manager, String oldMode, String requestedMode,
                                            String actualMode, Object token, Throwable originalFailure) {
                    wmAfterModeChange(oldMode, actualMode, token);
                    if (c17Ui != null) c17Ui.onActualMode(manager, oldMode, actualMode);
                    if (originalFailure != null) {
                        XposedBridge.log(TAG + "C17 original mode switch failed: " + originalFailure);
                    }
                }
            }, new XpanC17Hook.Gate() {
                @Override public boolean enabled() { return moduleEnabled(); }
            });
            initialized = result.installed;
            sC17CoreReady = result.installed;
            if (result.installed) {
                installXdrProcessor(lp.classLoader, context);
                if ("1".equals(prop("debug.xpan.apstrace"))) {
                    XpanApsCaptureTrace.InstallResult trace = XpanApsCaptureTrace.install(
                            lp.classLoader, profile.cameraVersion, new XpanApsCaptureTrace.Gate() {
                        @Override public boolean enabled() {
                            return sC17CoreReady && moduleEnabled() && "1".equals(prop("debug.xpan.apstrace"));
                        }
                    });
                    XposedBridge.log(TAG + "APS capture evidence: " + trace.detail);
                }
                gainMapFrame = XpanGainMapFrame.install(lp.classLoader, profile.cameraVersion,
                        new XpanGainMapFrame.Gate() {
                    @Override public boolean enabled() {
                        return sC17CoreReady && moduleEnabled() && !"0".equals(prop("debug.xpan.proxdr"));
                    }
                });
                XposedBridge.log(TAG + "XPAN gain-frame parameter installation: " + gainMapFrame.detail);
            }
            XposedBridge.log(TAG + "C17 installation: " + result.detail);
            return;
        }
        XpanC16Config.InstallResult config = XpanC16Config.install(
                lp.classLoader, profile.cameraVersion, new XpanC16Config.Gate() {
            @Override public boolean enabled() { return moduleEnabled(); }
        });
        XposedBridge.log(TAG + "C16 config installation: " + config.detail);
        if (!config.installed || !installModeGate(lp)) return;
        initialized = true;
        XposedBridge.log(TAG + "camera profile=" + profile.generation
                + " version=" + profile.cameraVersion + " proc=" + lp.processName);

        // 每个 hook 各自 try/catch：任何一个装不上都不许影响其他（边界 3）
        installHalSizeFix(lp);
        installSurfaceSetters(lp);
        installXpanSurfaceSize(lp);
        installTakePictureProbe(lp);
        installXdrProcessor(lp.classLoader, context);
        installXpanUiRestore(lp);
        installXpanZoomUnlock(lp);
        installXpanZoomPointFix(lp);
        installXpanFeatureInject(lp);
        installXpanSwitchFix(lp);
        installXpanPanelFix(lp);
        installWatermarkIsolation(lp);

        sDiag = "1".equals(prop("debug.xpan.diag"));
        // 下面两个是排查用的取证 hook，日志量大，默认关闭。
        // 需要复现问题时：setprop debug.xpan.diag 1，重启相机即可。
        // installXpanContainerWake 也放这里：它只是把休眠的 XpanUIContainer 状态推回 0，
        // 但因为应用从不激活这个容器（XPanPresenter/XPanViewManagerV3 都没被创建），
        // 推回去也不会让 X-Pan 专属 UI / 显影动画出现，反而多改一处应用状态，所以默认不开。
        if ("1".equals(prop("debug.xpan.diag"))) {
            installXpanContainerWake(lp);
            installShutterWatch(lp);
            installRequestProbe(lp);
            installXpanUiStateWatch(lp);
            installXpanContainerTrace(lp, "zl.q");
            installXpanContainerTrace(lp, "bh.c");
            installXpanContainerTrace(lp, "dh.w");
            installZoomTrace(lp);
            installFilterTrace(lp);
            // 看 FeatureFactory 到底为 X-Pan 模式请求了哪些 feature name
            // （工厂按 featureName.equals("com.oplus.camera.feature.xpan") 才创建 XPanPresenter）
            try {
                Class<?> ff = Class.forName("q7.d0", false, lp.classLoader);
                for (Method m : ff.getDeclaredMethods()) {
                    if (!"b".equals(m.getName()) || m.getParameterTypes().length != 5) continue;
                    XposedBridge.hookMethod(m, new QualifiedHook() {
                        @Override
                        protected void beforeQualified(MethodHookParam param) {
                            try {
                                XposedBridge.log(TAG + "FeatureFactory feature=" + param.args[0]
                                        + " | mode=" + param.args[1] + " | id=" + param.args[2]);
                            } catch (Throwable ignored) {
                            }
                        }
                    });
                }
                XposedBridge.log(TAG + "hook installed OK (feature factory)");
            } catch (Throwable t) {
                XposedBridge.log(TAG + "!! feature factory watch failed: " + t);
            }
            XposedBridge.log(TAG + "诊断 hook 已开启（debug.xpan.diag=1）");
        }
    }

    /** 只在原保存流程之前修改完整 HEIC 内存副本，不额外写文件。 */
    private void installXdrProcessor(ClassLoader loader, Context context) {
        if (xdrProcessor != null || sProfile == null) return;
        xdrProcessor = XdrBeforeSave.install(loader, sProfile.cameraVersion, new XdrBeforeSave.Gate() {
            @Override
            public boolean enabled() {
                ModuleGate gate = sModuleGate;
                return gate != null && gate.verified() && !"0".equals(prop("debug.xpan.proxdr"));
            }
        });
        XposedBridge.log(TAG + "XDR before-save installation: " + xdrProcessor.detail);
    }

    /** 诊断：把某个类的所有方法都挂上，每个方法只记第一次调用。
     *  目的：搞清应用到底驱动了容器的哪些生命周期方法（决定显影动画/双皮肤能不能补上）。 */
    private static final java.util.HashSet<String> sSeen = new java.util.HashSet<>();

    private void installXpanContainerTrace(XC_LoadPackage.LoadPackageParam lp, String clsName) {
        try {
            Class<?> c = Class.forName(clsName, false, lp.classLoader);
            int n = 0;
            for (Method m : c.getDeclaredMethods()) {
                if (m.isSynthetic() || m.isBridge()) continue;
                final String sig = clsName + "." + m.getName() + "/" + m.getParameterTypes().length;
                try {
                    XposedBridge.hookMethod(m, new QualifiedHook() {
                        @Override
                        protected void beforeQualified(MethodHookParam param) {
                            try {
                                if (!sSeen.add(sig)) return;      // 每个方法只记一次
                                StringBuilder sb = new StringBuilder();
                                if (param.args != null) {
                                    for (Object a : param.args) sb.append(a).append(' ');
                                }
                                XposedBridge.log(TAG + sig + " (" + sb.toString().trim() + ")");
                            } catch (Throwable ignored) {
                            }
                        }
                    });
                    n++;
                } catch (Throwable ignored) {
                }
            }
            XposedBridge.log(TAG + "hook installed OK (trace " + clsName + ", " + n + " methods)");
            // 构造函数不在 getDeclaredMethods 里，单独挂（判断这个类到底有没有被实例化）
            for (java.lang.reflect.Constructor<?> ctor : c.getDeclaredConstructors()) {
                final String csig = clsName + ".<init>/" + ctor.getParameterTypes().length;
                try {
                    XposedBridge.hookMethod(ctor, new QualifiedHook() {
                        @Override
                        protected void afterQualified(MethodHookParam param) {
                            try {
                                if (sSeen.add(csig)) XposedBridge.log(TAG + csig + " ← 已实例化");
                            } catch (Throwable ignored) {
                            }
                        }
                    });
                } catch (Throwable ignored) {
                }
            }
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! trace " + clsName + " failed: " + t);
        }
    }

    /* ---------------- 诊断：抓"谁把快门禁用了" ----------------
     * 现象：X-Pan 拍完第一张后，shutter_button 变 enabled=false，整个控制区失效，
     *       预览流其实还健康（dumpsys media.camera 里 2304x1048 一直在出帧）。
     * 这里在 View.setEnabled(false) 被调用且目标类名含 ShutterButton 时打调用栈，
     * 直接定位到禁用快门的那行代码。
     */
    /** 诊断：看应用往 CaptureRequest 里设了哪些变焦/裁剪相关键（去重+限量，防刷屏）。
     *  用途：确认 2× 时拍照请求里 SCALER_CROP_REGION / com.oplus.original.zoomRatio 的真实值。 */
    private static final java.util.Set<String> sProbeSeen = new java.util.HashSet<>();

    /** 诊断: 拍照时刻的 CameraRequestTag.mZoomRatio(拍照链路真正用的变焦值)。 */
    private void installTakePictureProbe(XC_LoadPackage.LoadPackageParam lp) {
        try {
            Class<?> c = Class.forName("com.oplus.ocs.camera.producer.device.Camera2Impl",
                    false, lp.classLoader);
            int n = 0;
            for (Method m : c.getDeclaredMethods()) {
                if (!"takePicture".equals(m.getName()) || m.getParameterTypes().length != 5) continue;
                XposedBridge.hookMethod(m, new QualifiedHook() {
                    @Override
                    protected void beforeQualified(MethodHookParam param) {
                        try {
                            if (!sXpanActive) return;
                            Object tag = param.args[0];
                            XposedBridge.log(TAG + "takePicture tag.mZoomRatio="
                                    + field(tag, "mZoomRatio") + " sXpanZoomReq=" + sXpanZoomReq
                                    + " tag.mXpanMfllOutputSize=" + field(tag, "mXpanMfllOutputSize"));
                        } catch (Throwable ignored) {
                        }
                    }
                });
                n++;
            }
            XposedBridge.log(TAG + "hook installed OK (takePicture probe x" + n + ")");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! takePicture probe failed: " + t);
        }
    }

    private static String fmtValue(Object v) {
        if (v == null) return "null";
        if (v instanceof int[]) return java.util.Arrays.toString((int[]) v);
        if (v instanceof android.graphics.Rect) return "Rect" + v;
        return String.valueOf(v);
    }

    private void installRequestProbe(XC_LoadPackage.LoadPackageParam lp) {
        try {
            Class<?> cls = Class.forName("android.hardware.camera2.CaptureRequest$Builder",
                    false, lp.classLoader);
            Method set = cls.getDeclaredMethod("set",
                    android.hardware.camera2.CaptureRequest.Key.class, Object.class);
            XposedBridge.hookMethod(set, new QualifiedHook() {
                @Override
                protected void beforeQualified(MethodHookParam param) {
                    try {
                        if (!sXpanActive) return;
                        String name = String.valueOf(param.args[0]);
                        String ln = name.toLowerCase();
                        if (!ln.contains("crop") && !ln.contains("zoom")
                                && !ln.contains("only.") && !ln.contains("bpc")) return;
                        String sig = name + " = " + fmtValue(param.args[1]);
                        synchronized (sProbeSeen) {
                            if (sProbeSeen.size() >= 300 || !sProbeSeen.add(sig)) return;
                        }
                        XposedBridge.log(TAG + "req.set " + sig);
                    } catch (Throwable ignored) {
                    }
                }
            });
            // build() 之后把请求里所有变焦/裁剪相关的最终值打出来（含框架补的 SCALER_CROP_REGION）
            Method build = cls.getDeclaredMethod("build");
            XposedBridge.hookMethod(build, new QualifiedHook() {
                @Override
                protected void afterQualified(MethodHookParam param) {
                    try {
                        if (!sXpanActive) return;
                        Object req = param.getResult();
                        if (req == null) return;
                        Method getKeys = req.getClass().getMethod("getKeys");
                        Object keys = getKeys.invoke(req);
                        if (!(keys instanceof java.util.Set)) return;
                        StringBuilder sb = new StringBuilder();
                        for (Object k : (java.util.Set<?>) keys) {
                            String kn = String.valueOf(k).toLowerCase();
                            if (!kn.contains("crop") && !kn.contains("zoom")) continue;
                            Method get = req.getClass().getMethod("get",
                                    android.hardware.camera2.CaptureRequest.Key.class);
                            Object v = get.invoke(req, k);
                            sb.append(String.valueOf(k)).append('=').append(fmtValue(v)).append(" | ");
                        }
                        if (sb.length() == 0) return;
                        String sig = "BUILT " + sb;
                        synchronized (sProbeSeen) {
                            if (sProbeSeen.size() >= 400 || !sProbeSeen.add(sig)) return;
                        }
                        XposedBridge.log(TAG + "req.build " + sig);
                    } catch (Throwable ignored) {
                    }
                }
            });
            XposedBridge.log(TAG + "hook installed OK (request probe)");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! request probe failed: " + t);
        }
    }

    private void installShutterWatch(XC_LoadPackage.LoadPackageParam lp) {
        try {
            Class<?> view = Class.forName("android.view.View", false, lp.classLoader);
            Method setEnabled = view.getDeclaredMethod("setEnabled", boolean.class);
            XposedBridge.hookMethod(setEnabled, new QualifiedHook() {
                @Override
                protected void beforeQualified(MethodHookParam param) {
                    try {
                        Object en = (param.args != null && param.args.length > 0) ? param.args[0] : null;
                        if (!Boolean.FALSE.equals(en)) return;
                        Object v = param.thisObject;
                        if (v == null) return;
                        String cn = v.getClass().getName();
                        if (!cn.contains("ShutterButton")) return;
                        XposedBridge.log(TAG + "!! setEnabled(false) on " + cn + "\n"
                                + android.util.Log.getStackTraceString(new Throwable()));
                    } catch (Throwable ignored) {
                    }
                }
            });
            XposedBridge.log(TAG + "hook installed OK (shutter watch)");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! shutter watch failed: " + t);
        }
    }

    /** 把 X-Pan 界面状态推回常态并刷新（走应用自己的 hc(0)+ic()） */
    private static void restoreXpanUi(Object ui, Method mA, Method mC4) {
        try {
            if (!sXpanActive) return;
            Object container = null;
            try {
                Object b = field(ui, "H");                 // CameraControlUI.H : com.oplus.camera.b
                if (b != null) {
                    Object k0 = b.getClass().getMethod("M").invoke(b);   // -> nk.k0 (extends nk.u1)
                    if (k0 != null) container = field(k0, "p");          // u1.p : zl.q (XpanUIContainer)
                }
            } catch (Throwable t) {
                XposedBridge.log(TAG + "  restore: container lookup failed: " + t);
            }
            if (container != null) {
                try {
                    container.getClass().getDeclaredMethod("hc", int.class).invoke(container, 0);
                    container.getClass().getDeclaredMethod("ic").invoke(container);
                    XposedBridge.log(TAG + "  已把 XpanUI 状态推回 0 并 ic()");
                    return;
                } catch (Throwable t) {
                    Throwable cause = (t.getCause() != null) ? t.getCause() : t;
                    XposedBridge.log(TAG + "  hc(0)+ic() 失败(" + cause + ")，回退到直接启用");
                    try {
                        container.getClass().getDeclaredMethod("hc", int.class).invoke(container, -1);
                    } catch (Throwable ignored) {
                    }
                }
            }
            // 直接启用快门：c4 管 MainShutterButton，A 管 ShutterButton
            mC4.invoke(ui, true, false);
            mA.invoke(ui, true, false);
            XposedBridge.log(TAG + "  已直接启用快门 c4(true,false)+A(true,false)");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "restoreXpanUi failed: " + t);
        }
    }

    /** 沿继承链找字段（smali 里多是 public，但稳妥起见都试） */
    private static Object field(Object o, String name) {
        for (Class<?> c = o.getClass(); c != null; c = c.getSuperclass()) {
            try {
                java.lang.reflect.Field f = c.getDeclaredField(name);
                f.setAccessible(true);
                return f.get(o);
            } catch (Throwable ignored) {
            }
        }
        return null;
    }

    /* ---------------- 修复：拍完一张后把 X-Pan 界面状态机推回常态 ----------------
     * 病因（实机取证，2026-10-05）：
     *   X-Pan 界面容器 zl.q 创建了（ch.b.c() 返回 true，版本号 3），但应用从不驱动它 ——
     *   ic()/o4()/q8()/k()/L1()/e7()/fc() 整个会话 0 次调用，Q 一直停在构造函数初值 -1。
     *   而 zl.q.g()/c1() 都是"Q==-1 就跳过 ic()"，所以 ic() 永远不跑。
     *   拍照开始时 CameraControlUI.A(false,false)/c4(false,false) 把快门禁掉，
     *   本该由 ic() 里 Q==0 分支执行的 A(1,0)/c4(1,0) 恢复调用永远不来，
     *   快门就永久停在 enabled=false（设置齿轮、切镜头一起失效），
     *   于是"进一次 X-Pan 只能拍一张"。显影动画缺失也是同一处休眠导致。
     */
    private void installXpanUiRestore(XC_LoadPackage.LoadPackageParam lp) {
        try {
            Class<?> c = Class.forName("com.oplus.camera.ui.control.CameraControlUI", false, lp.classLoader);
            Method mA = null, mC4 = null;
            for (Method m : c.getDeclaredMethods()) {
                Class<?>[] pt = m.getParameterTypes();
                if (pt.length != 2 || !boolean.class.equals(pt[0]) || !boolean.class.equals(pt[1])) continue;
                if ("A".equals(m.getName())) mA = m;
                if ("c4".equals(m.getName())) mC4 = m;
            }
            if (mA == null || mC4 == null) {
                XposedBridge.log(TAG + "note: A/c4 未找到，跳过 UI 恢复");
                return;
            }
            final Method fA = mA, fC4 = mC4;
            XC_MethodHook h = new QualifiedHook() {
                @Override
                protected void beforeQualified(MethodHookParam param) {
                    try {
                        if (param.thisObject != null) sControlUI = param.thisObject;  // 供 switch fix 使用
                        if (!sXpanActive) return;
                        if (!Boolean.FALSE.equals(param.args[0])) return;   // 只关心"禁用"
                        // 注入开启时 XPanPresenter 自己在管 UI（含动画期间的刻意禁用），
                        // 旧补丁若再强制启用会跟它打架 → 界面闪/状态错乱。只有容器休眠
                        // （feat=0 关闭注入）的旧路径才需要这个补丁兜底。
                        if (!"0".equals(prop("debug.xpan.feat"))) return;
                        if (sRestorePending) return;
                        sRestorePending = true;
                        final Object ui = param.thisObject;
                        new android.os.Handler(android.os.Looper.getMainLooper()).postDelayed(
                                new Runnable() {
                                    @Override
                                    public void run() {
                                        if (!moduleEnabled()) return;
                                        sRestorePending = false;
                                        restoreXpanUi(ui, fA, fC4);
                                    }
                                }, 2500);
                    } catch (Throwable ignored) {
                    }
                }
            };
            XposedBridge.hookMethod(mA, h);
            XposedBridge.hookMethod(mC4, h);
            XposedBridge.log(TAG + "hook installed OK (xpan ui restore)");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! xpan ui restore failed: " + t);
        }
    }

    /* ---------------- 补齐：唤醒休眠的 X-Pan 界面容器（显影动画 / 双皮肤的前提） ----------------
     * zl.q(XpanUIContainer) 的 c1()/g()/K9() 都有同一个守卫"Q==-1 就直接 return"，
     * 而 Q 一直停在构造函数初值 -1，于是应用自己的 X-Pan 界面初始化永远不会执行：
     *   c1() 里的 bc(resources, screen) 建视图 + ic() 刷 UI 都被这道守卫挡掉，
     *   所以既没有显影动画（动画视图是 null），界面也不是一加13 那套 X-Pan UI。
     * Q 是 public 非 final，直接推成常态值，让应用自己的流程接管。
     *   debug.xpan.uistate 可覆盖初值（0=普通 X-Pan UI，6=哈苏实体皮肤态），默认 0。
     */
    private void installXpanContainerWake(XC_LoadPackage.LoadPackageParam lp) {
        try {
            Class<?> c = Class.forName("zl.q", false, lp.classLoader);
            final java.lang.reflect.Field fQ = c.getDeclaredField("Q");
            fQ.setAccessible(true);
            int want = 0;
            String forced = prop("debug.xpan.uistate");
            if (forced != null) {
                try {
                    want = Integer.parseInt(forced.trim());
                } catch (Throwable ignored) {
                }
            }
            final int init = want;
            final Class<?> screenCls = Class.forName("com.oplus.camera.common.screen.a", false, lp.classLoader);
            // 只在 c1() 里唤醒：c1() 才是"建视图(bc) + 刷UI(ic)"的入口。
            // g() 会比 c1() 早被调用，在 g 里唤醒必然 ic() 撞 null 视图 → 相机崩在 onResume。
            XC_MethodHook wake = new QualifiedHook() {
                @Override
                protected void beforeQualified(MethodHookParam param) {
                    try {
                        Object c = param.thisObject;
                        if (fQ.getInt(c) != -1) return;
                        // 视图还没建（l = X-Pan 自己的 ShutterButton 为 null）就先补建，
                        // 否则 ic() 会拿 null 视图调 setForceHide → NPE。
                        if (field(c, "l") == null) {
                            Object act = field(c, "f");                 // zl.q.f : Activity
                            Object res = act.getClass().getMethod("getResources").invoke(act);
                            Method bc = c.getClass().getDeclaredMethod("bc",
                                    android.content.res.Resources.class, screenCls);
                            bc.invoke(c, res, param.args[0]);            // 复用 c1 传进来的 screen 对象
                            XposedBridge.log(TAG + "  已补建 XpanUI 视图");
                        }
                        fQ.setInt(c, init);
                        XposedBridge.log(TAG + "  XpanUI 状态 -1 -> " + init + "（唤醒界面容器）");
                    } catch (Throwable t) {
                        Throwable cause = (t.getCause() != null) ? t.getCause() : t;
                        XposedBridge.log(TAG + "  唤醒失败，放弃: " + cause);
                    }
                }
            };
            int n = 0;
            for (Method m : c.getDeclaredMethods()) {
                if (!"c1".equals(m.getName())) continue;
                try {
                    XposedBridge.hookMethod(m, wake);
                    n++;
                } catch (Throwable ignored) {
                }
            }
            XposedBridge.log(TAG + "hook installed OK (container wake, " + n + " hooks, init=" + init + ")");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! container wake failed: " + t);
        }
    }

    /* ---------------- 诊断：X-Pan 界面状态机（zl.q = XpanUIContainer） ----------------
     * 快门能不能被重新启用，取决于 zl.q.Q：
     *   CameraControlUI.A()/c4() → Gc() → zl.q.xa() → Q ∈ {1,2,6,7} 时**直接跳过**，
     *   于是拍照后想恢复快门的那次调用被吞掉，快门永远停在 enabled=false。
     * 这里把每次状态切换连同调用栈打出来，定位是谁把它推进了 {1,2,6,7} 且不再回 0。
     */
    private void installXpanUiStateWatch(XC_LoadPackage.LoadPackageParam lp) {
        try {
            Class<?> c = Class.forName("zl.q", false, lp.classLoader);
            Method hc = c.getDeclaredMethod("hc", int.class);
            XposedBridge.hookMethod(hc, new QualifiedHook() {
                @Override
                protected void beforeQualified(MethodHookParam param) {
                    try {
                        Object st = (param.args != null && param.args.length > 0) ? param.args[0] : null;
                        XposedBridge.log(TAG + "XpanUI state -> " + st + "\n"
                                + android.util.Log.getStackTraceString(new Throwable()));
                    } catch (Throwable ignored) {
                    }
                }
            });
            XposedBridge.log(TAG + "hook installed OK (xpan ui state)");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! xpan ui state watch failed: " + t);
        }

        // ch.b.b() = X-Pan 界面版本号；ch.b.c() = (版本==3) 才创建 XpanUIContainer。
        // 版本取自 com.oplus.xpan.mode.version（默认 2）；若 legacy.ui.style 为 true 则强制 1。
        try {
            Class<?> cb = Class.forName("ch.b", false, lp.classLoader);
            Method vb = cb.getDeclaredMethod("b");
            XposedBridge.hookMethod(vb, new QualifiedHook() {
                @Override
                protected void afterQualified(MethodHookParam param) {
                    XposedBridge.log(TAG + "ch.b.b() xpan ui version = " + param.getResult());
                }
            });
            Class<?> xc = Class.forName("zl.q", false, lp.classLoader);
            for (java.lang.reflect.Constructor<?> ctor : xc.getDeclaredConstructors()) {
                XposedBridge.hookMethod(ctor, new QualifiedHook() {
                    @Override
                    protected void afterQualified(MethodHookParam param) {
                        XposedBridge.log(TAG + "XpanUIContainer 已创建");
                    }
                });
            }
            XposedBridge.log(TAG + "hook installed OK (xpan version watch)");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! xpan version watch failed: " + t);
        }

        // 把 X-Pan 界面这条链上所有关键判定都打出来，一次拿全证据。
        watchVoid(lp, "ch.b", "c", "ch.b.c() xpan ui container?");
        watchVoid(lp, "zl.q", "xa", "zl.q.xa()");
        watchVoid(lp, "zl.q", "ic", "zl.q.ic()");
        watchVoid(lp, "zl.q", "o4", "zl.q.o4() [捕获入口?]");
        watchVoid(lp, "zl.q", "q8", "zl.q.q8()");
        watchVoid(lp, "zl.q", "k", "zl.q.k()");
        watchVoid(lp, "zl.q", "g", "zl.q.g() [Q==-1则跳过ic]");
        watchVoid(lp, "zl.q", "L1", "zl.q.L1()");
        watchVoid(lp, "zl.q", "e7", "zl.q.e7()");
        watchVoid(lp, "zl.q", "fc", "zl.q.fc()");
        watchArgs(lp, "com.oplus.camera.ui.control.CameraControlUI", "A", 2, "ControlUI.A");
        watchArgs(lp, "com.oplus.camera.ui.control.CameraControlUI", "c4", 2, "ControlUI.c4");
    }

    /** 监控一个无参方法，把返回值打出来（用于看 X-Pan 界面链路的各种判定） */
    private void watchVoid(XC_LoadPackage.LoadPackageParam lp, String cls, String name, String label) {
        try {
            Class<?> c = Class.forName(cls, false, lp.classLoader);
            Method m = c.getDeclaredMethod(name);
            XposedBridge.hookMethod(m, new QualifiedHook() {
                @Override
                protected void afterQualified(MethodHookParam param) {
                    try {
                        XposedBridge.log(TAG + label + " -> " + param.getResult());
                    } catch (Throwable ignored) {
                    }
                }
            });
        } catch (Throwable t) {
            XposedBridge.log(TAG + "watch " + label + " failed: " + t);
        }
    }

    /** 监控一个带 n 个参数的方法，把入参打出来（用于看快门使能调用） */
    private void watchArgs(XC_LoadPackage.LoadPackageParam lp, String cls, String name,
                           int argc, String label) {
        try {
            Class<?> c = Class.forName(cls, false, lp.classLoader);
            for (Method m : c.getDeclaredMethods()) {
                if (!name.equals(m.getName()) || m.getParameterTypes().length != argc) continue;
                XposedBridge.hookMethod(m, new QualifiedHook() {
                    @Override
                    protected void beforeQualified(MethodHookParam param) {
                        try {
                            StringBuilder sb = new StringBuilder();
                            for (Object a : param.args) sb.append(a).append(' ');
                            XposedBridge.log(TAG + label + "(" + sb.toString().trim() + ")");
                        } catch (Throwable ignored) {
                        }
                    }
                });
            }
        } catch (Throwable t) {
            XposedBridge.log(TAG + "watch " + label + " failed: " + t);
        }
    }

    /* ---------------- 诊断：变焦链路追踪（X-Pan 变焦不生效） ----------------
     * 现象（实机取证 2026-10-06）：
     *   X-Pan 模式点变焦条的 2× 按钮，应用日志显示确实请求了 2.0
     *   （OCAM_ZoomUIManager: changeZoom value 2.0 / OCAM_ZoomPresenter: change zoom 2.0），
     *   但写进 HAL 的参数仍是 com.oplus.original.zoomRatio = 1.0，
     *   CamX 侧 currentZoom 也恒为 1.000000，拍出来等效焦距 24mm（=1×）而不是 48mm。
     *   对照：普通照片模式点 2× 正常出 48mm。所以断点在"请求 → 模型 → HAL"这条链上。
     *
     * 链路（反编译确认）：
     *   z1.m(FFZZZZ) 收到请求 → fh.g.h(F) 把请求值经 f() 钳制后写进 fh.g.g
     *   → gh.z.b0(p5.vg) 读 fh.g.g 写进自身 e0 → e0 作为 com.oplus.original.zoomRatio 下发
     * 这三处各打一行，就能看出值是在哪一步被改成 1.0 的。
     */
    private void installZoomTrace(XC_LoadPackage.LoadPackageParam lp) {
        // 0) 对照钩子：z1.m 是应用日志 "CameraTest change zoom" 的出处，确定会被调用。
        //    它出得来就说明钩子机制没问题，出不来就是装载环节的问题。
        try {
            Class<?> z1 = Class.forName("com.oplus.camera.feature.zoom.ui.z1", false, lp.classLoader);
            int n = 0;
            for (Method m : z1.getDeclaredMethods()) {
                if (!"m".equals(m.getName()) || m.getParameterTypes().length != 6) continue;
                XposedBridge.hookMethod(m, new QualifiedHook() {
                    @Override
                    protected void beforeQualified(MethodHookParam param) {
                        try {
                            XposedBridge.log(TAG + "zoom z1.m 收到请求 value=" + param.args[0]
                                    + " endValue=" + param.args[1]);
                        } catch (Throwable ignored) {
                        }
                    }
                });
                n++;
            }
            XposedBridge.log(TAG + "hook installed OK (zoom z1.m x" + n + ")");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! zoom z1 watch failed: " + t);
        }
        try {
            Class<?> c = Class.forName("fh.g", false, lp.classLoader);
            StringBuilder sb = new StringBuilder();
            for (Method m : c.getDeclaredMethods()) sb.append(m.getName()).append('/')
                    .append(m.getParameterTypes().length).append(' ');
            XposedBridge.log(TAG + "fh.g 方法清单: " + sb);
            int n = 0;
            for (Method m : c.getDeclaredMethods()) {
                Class<?>[] pt = m.getParameterTypes();
                if (pt.length != 1 || !float.class.equals(pt[0])) continue;
                if ("h".equals(m.getName())) {
                    XposedBridge.hookMethod(m, new QualifiedHook() {
                        @Override
                        protected void afterQualified(MethodHookParam param) {
                            try {
                                XposedBridge.log(TAG + "zoom 请求 fh.g.h(" + param.args[0]
                                        + ") -> 模型 g=" + field(param.thisObject, "g"));
                            } catch (Throwable ignored) {
                            }
                        }
                    });
                    n++;
                } else if ("f".equals(m.getName())) {
                    XposedBridge.hookMethod(m, new QualifiedHook() {
                        @Override
                        protected void afterQualified(MethodHookParam param) {
                            try {
                                XposedBridge.log(TAG + "zoom 钳制 fh.g.f(" + param.args[0]
                                        + ") -> " + param.getResult());
                            } catch (Throwable ignored) {
                            }
                        }
                    });
                    n++;
                }
            }
            XposedBridge.log(TAG + "hook installed OK (zoom model x" + n + ")");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! zoom model watch failed: " + t);
        }
        try {
            Class<?> c = Class.forName("gh.z", false, lp.classLoader);
            int n = 0;
            for (Method m : c.getDeclaredMethods()) {
                if (!"b0".equals(m.getName()) || m.getParameterTypes().length != 1) continue;
                XposedBridge.hookMethod(m, new QualifiedHook() {
                    @Override
                    protected void afterQualified(MethodHookParam param) {
                        try {
                            // 记录真正下发 HAL 的变焦值：b0(arg) 把模型值写进自身 e0 再下发，
                            // after 里 e0 已是新值（比 UI 钳制函数可靠：点按/滑动/复位都走这）
                            Object e0 = field(param.thisObject, "e0");
                            if (sXpanActive && e0 instanceof Number) {
                                float z = ((Number) e0).floatValue();
                                if (z >= 1.0f) sXpanZoomReq = z;
                            }
                            dlog("zoom 下发 HAL gh.z.b0 e0=" + e0);
                        } catch (Throwable ignored) {
                        }
                    }
                });
                n++;
            }
            XposedBridge.log(TAG + "hook installed OK (zoom apply x" + n + ")");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! zoom apply watch failed: " + t);
        }
    }

    /* ---------------- 修复：X-Pan 变焦被锁死在 1× ----------------
     * 病因（实机取证 2026-10-06，链路钩子输出）：
     *   点变焦条 2× → z1.m 确实收到 2.0 → fh.g.f(2.0) 返回 1.0 → 模型 g=1.0
     *   → 下发给 HAL 的 com.oplus.original.zoomRatio 还是 1.0，CamX currentZoom 恒 1.000000，
     *   拍出来等效焦距 24mm（1×）而不是 48mm。
     *   fh.g.f(F) 是变焦钳制函数：当模式配置 m7.c 的 j()/u/s/J()/A/U 六个标志全为假时，
     *   它走"该模式没有变焦能力"的分支 —— 只有 (int)请求值 == 1 才放行，其余一律返回 1.0。
     *   X-Pan 模式恰好是这种没声明变焦能力的配置，所以 1× 以上全被吃掉。
     *   （普通照片模式那几个标志为真，所以同一套代码在普通模式 2× 正常出 48mm。）
     *
     * 做法：只在 X-Pan 会话里、且应用请求的是 1× 以上时，把钳制结果改回请求值。
     *   小于 1× 的请求不动 —— 0.6× 靠切换超广角镜头实现，不经过这个函数；
     *   变焦条上那个值为 0.0 的坏档位也落在这边，会保持原来的 1.0，不会把画面搞黑。
     */
    /* ==================================================================================
     *  水印按模式隔离：X-Pan 的水印设置与拍照等其他模式各记各的
     * ----------------------------------------------------------------------------------
     * 为什么需要（2026-10-18 反编译实锤）：应用把水印设置存在**全局键**里，键名里没有模式维度 ——
     *   pref_ai_master_watermark_photo_style_id（用哪款水印；X-Pan 画框 = hassel_style_xpan_1）
     *   pref_ai_master_watermark_photo_open_state（AI 大师水印开关）
     *   pref_watermark_function_key（普通水印开关）
     *   pref_hasselblad_watermark_function_key（哈苏水印开关）
     * 这四个键在 C16 c6.d 是 N0 / K0 / l1 / q1，在 C17 m9.d 是 t1 / m1 / f2 / k2。
     * 只延迟调用各版本的全局工厂 f / b0，不提前读取这些静态字段。
     * 于是"在 X-Pan 里换了水印，回拍照模式也跟着变"——应用侧没有模式维度，只能我们补。
     *
     * 做法：**值交换**（键名一个都不改）
     *   进 X-Pan：先把当前值（= 其他模式的值）抄下来；有上次存的 X-Pan 值就写回去；
     *             没有（首次进）就什么都不写 —— 即"首次进 X-Pan 沿用拍照模式的水印"。
     *   出 X-Pan：把当前值存成 X-Pan 的值，再把其他模式那份写回去。
     * 为什么不用"改键名"：那样应用的观察者/界面刷新挂在原键上，改了键名就收不到通知，界面不刷新。
     * 存档：/data/data/com.oplus.camera/files/xpan_watermark_keep.txt（我们自己的文件，不碰应用 MMKV）。
     * 读写走应用自己的 DataManager.c()/g()；缺失值只走已核实的 f() 单键删除再验 contains/readback。
     * f() 不像 g() 显式派发 DataRepoProxy 的更新通知，不能把存储恢复冒充 UI 已同步。
     * 开关：debug.xpan.wmper=0 关闭（默认开）。
     */
    private static final String[] WM_FIELD = {
            "N0", "K0", "l1", "q1",
    };
    private static final String[] WM_NAME = {
            "pref_ai_master_watermark_photo_style_id",
            "pref_ai_master_watermark_photo_open_state",
            "pref_watermark_function_key",
            "pref_hasselblad_watermark_function_key",
    };
    private static final String WM_KEEP =
            "/data/data/com.oplus.camera/files/xpan_watermark_keep.txt";

    /** 四个键的值类型（从 c6.d 的静态构造里读出来的：v2=String、v3=Boolean） */
    private static final Class<?>[] WM_TYPE = {
            String.class, Boolean.class, String.class, String.class,
    };
    private static final WatermarkState sWatermark = new WatermarkState(WM_TYPE,
            new WatermarkState.Store() {
                @Override public Object read(int index) throws Throwable { return wmRead(index); }
                @Override public void write(int index, Object value) throws Throwable { wmWrite(index, value); }
                @Override public boolean canRemove(int index) throws Throwable { return wmCanRemove(index); }
                @Override public void remove(int index) throws Throwable { wmRemove(index); }
            }, new WatermarkState.Gate() {
                @Override public boolean enabled() { return moduleEnabled(); }
            });

    /** Published only after every coupled read/write/remove/presence binding and key is verified. */
    private static final class WmAbi {
        final ClassLoader loader;
        final CameraProfile.Generation generation;
        final Object dm;
        final Method get, set, remove, uri, contains;
        final Object[] keys, stores;
        final android.net.Uri[] uris;

        WmAbi(ClassLoader loader, CameraProfile.Generation generation, Object dm,
              Method get, Method set, Method remove, Method uri, Method contains,
              Object[] keys, Object[] stores, android.net.Uri[] uris) {
            this.loader = loader;
            this.generation = generation;
            this.dm = dm;
            this.get = get;
            this.set = set;
            this.remove = remove;
            this.uri = uri;
            this.contains = contains;
            this.keys = keys;
            this.stores = stores;
            this.uris = uris;
        }

        void checkKey(int i) throws Throwable {
            if (i < 0 || i >= keys.length || !uris[i].equals(uri.invoke(keys[i])))
                throw new IllegalStateException("watermark key URI changed");
        }
    }

    private static volatile WmAbi sWmAbi;
    private static ClassLoader sClsLoader;

    private void installWatermarkIsolation(XC_LoadPackage.LoadPackageParam lp) {
        try {
            sClsLoader = lp.classLoader;
            wmLoad();
            XposedBridge.log(TAG + "watermark transaction prepared; verified missing-key reset ABI required; runtime unverified");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! 水印隔离安装失败: " + t);
        }
    }

    private static Method wmStaticMethod(Class<?> owner, String name, Class<?> result, Class<?>... args)
            throws Throwable {
        Method m = owner.getDeclaredMethod(name, args);
        if (!Modifier.isPublic(m.getModifiers()) || !Modifier.isStatic(m.getModifiers())
                || m.getReturnType() != result)
            throw new NoSuchMethodException("watermark static ABI mismatch");
        return m;
    }

    /** Lazy at mode switch, never read c6.d/m9.d statics in handleLoadPackage.
     * C16 f / C17 b0 are GLOBAL factories; C17 M0 is per-camera and must not be used here.
     * f(DataKey) -> h7.f/va.d.c(Uri) -> i7.b/wa.b.b(fragment), apply(): real single-key removal.
     * Raw c(DataKey) checks contains before typed reads with null default. A present-null or
     * unknown URI is refused; we never turn Boolean absence into the setter's false default.
     */
    private static synchronized WmAbi wmInitAbi() throws Throwable {
        if (sClsLoader == null || sProfile == null) throw new IllegalStateException("watermark profile unavailable");
        WmAbi ready = sWmAbi;
        if (ready != null) {
            if (ready.loader != sClsLoader || ready.generation != sProfile.generation)
                throw new IllegalStateException("watermark version changed");
            return ready;
        }
        final String factoryName, repoName, storageName;
        if (sProfile.generation == CameraProfile.Generation.C16) {
            factoryName = "f"; repoName = "i7.a"; storageName = "i7.b";
        } else if (sProfile.generation == CameraProfile.Generation.C17) {
            factoryName = "b0"; repoName = "wa.a"; storageName = "wa.b";
        } else throw new IllegalStateException("unknown watermark reset ABI");
        Class<?> dk = Class.forName("com.oplus.camera.data.DataKey", false, sClsLoader);
        Class<?> fac = Class.forName("androidx.preference.e", false, sClsLoader);
        Class<?> dmClass = Class.forName("com.oplus.camera.data.DataManager", false, sClsLoader);
        Class<?> repoClass = Class.forName(repoName, false, sClsLoader);
        Class<?> holder = Class.forName(repoName + "$a", false, sClsLoader);
        Class<?> storage = Class.forName(storageName, false, sClsLoader);
        Method factory = wmStaticMethod(fac, factoryName, dk, Class.class, String.class);
        Method instance = wmStaticMethod(dmClass, "getInstance", dmClass);
        Method get = modeMethod(dmClass, "c", Object.class, dk);
        Method set = modeMethod(dmClass, "g", void.class, dk, Object.class);
        Method remove = modeMethod(dmClass, "f", void.class, dk);
        Method uri = modeMethod(dk, "h", android.net.Uri.class);
        Field type = modeField(dk, "g", Class.class, Modifier.FINAL);
        Method route = modeMethod(repoClass, "a", storage, android.net.Uri.class);
        Method contains = modeMethod(storage, "contains", boolean.class, String.class);
        Field repoInstance = holder.getDeclaredField("a");
        if (repoInstance.getType() != repoClass || !Modifier.isPublic(repoInstance.getModifiers())
                || !Modifier.isStatic(repoInstance.getModifiers()) || !Modifier.isFinal(repoInstance.getModifiers()))
            throw new NoSuchFieldException("watermark repo singleton ABI mismatch");
        Object[] keys = new Object[WM_NAME.length], stores = new Object[WM_NAME.length];
        android.net.Uri[] uris = new android.net.Uri[WM_NAME.length];
        Object dm = instance.invoke(null), repo = repoInstance.get(null);
        if (dm == null || repo == null) throw new IllegalStateException("watermark storage unavailable");
        for (int i = 0; i < keys.length; i++) {
            keys[i] = factory.invoke(null, WM_TYPE[i], WM_NAME[i]);
            if (!dk.isInstance(keys[i]) || type.get(keys[i]) != WM_TYPE[i])
                throw new IllegalStateException("watermark DataKey type mismatch");
            uris[i] = (android.net.Uri) uri.invoke(keys[i]);
            android.net.Uri u = uris[i];
            if (u == null || !("sp".equals(u.getScheme()) || "mmkv".equals(u.getScheme()))
                    || !"com.oplus.camera".equals(u.getAuthority())
                    || !"/com.oplus.camera_preferences".equals(u.getPath())
                    || !WM_NAME[i].equals(u.getFragment())
                    || !WM_TYPE[i].getName().equals(u.getQueryParameter("class_type")))
                throw new IllegalStateException("unknown watermark key namespace/reset path");
            stores[i] = route.invoke(repo, u);
            // Only the traced global SP/MMKV implementations, not a guessed memory/per-camera key.
            String expected = storageName.substring(0, storageName.lastIndexOf('.') + 1)
                    + ("sp".equals(u.getScheme()) ? "h" : "d");
            if (stores[i] == null || !expected.equals(stores[i].getClass().getName()))
                throw new IllegalStateException("unknown watermark backing store");
        }
        ready = new WmAbi(sClsLoader, sProfile.generation, dm, get, set, remove, uri, contains,
                keys, stores, uris);
        sWmAbi = ready; // no partial key/method publication, including when any lookup throws
        return ready;
    }

    /** Reflection failures propagate; null is accepted only as verified raw key absence. */
    private static Object wmRead(int i) throws Throwable {
        WmAbi abi = wmInitAbi();
        abi.checkKey(i);
        Object value = abi.get.invoke(abi.dm, abi.keys[i]);
        boolean present = (Boolean) abi.contains.invoke(abi.stores[i], WM_NAME[i]);
        if (present != (value != null) || (value != null && !WM_TYPE[i].isInstance(value)))
            throw new IllegalStateException("ambiguous watermark presence/value " + i);
        return value;
    }

    private static void wmWrite(int i, Object value) throws Throwable {
        if (i < 0 || i >= WM_TYPE.length || value == null || !WM_TYPE[i].isInstance(value))
            throw new IllegalArgumentException("untyped watermark value");
        WmAbi abi = wmInitAbi();
        abi.checkKey(i);
        abi.set.invoke(abi.dm, abi.keys[i], value);
    }

    private static boolean wmCanRemove(int i) throws Throwable {
        wmInitAbi().checkKey(i);
        return true; // all coupled ABI and exact global routes resolved before the first write
    }

    private static void wmRemove(int i) throws Throwable {
        WmAbi abi = wmInitAbi();
        abi.checkKey(i);
        abi.remove.invoke(abi.dm, abi.keys[i]); // never g(key, null): Boolean would become false
        if (wmRead(i) != null) throw new IllegalStateException("watermark removal readback mismatch " + i);
    }

    private static WatermarkState.Transition wmBeforeModeChange(String oldMode, String requestedMode) {
        WatermarkState.Transition t = sWatermark.begin(oldMode, requestedMode,
                moduleEnabled() && !"0".equals(prop("debug.xpan.wmper")));
        if (!t.staged && !"no swap".equals(t.detail)) XposedBridge.log(TAG + "watermark: " + t.detail);
        return t;
    }

    private void wmAfterModeChange(String oldMode, String actualMode, Object token) {
        if (!(token instanceof WatermarkState.Transition)) return;
        WatermarkState.Transition t = (WatermarkState.Transition) token;
        boolean committed = sWatermark.finish(t, actualMode);
        if (t.staged) XposedBridge.log(TAG + "watermark: " + t.detail);
        // Archive only the successful exit, never speculative writes or failed cleanup.
        if (committed && !"xpan".equals(actualMode)) wmSave();
        if (actualMode != null && sWatermark.current(t) && moduleEnabled())
            onActualMode(oldMode, actualMode, t.epoch);
        // No delayed watermark writes: value equality is not proof of a clobber's source.
    }

    private static String wmDump(Object[] a) {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < a.length; i++) {
            if (i > 0) sb.append(", ");
            sb.append(WM_NAME[i].replace("pref_", "")).append('=').append(a[i]);
        }
        return sb.append(']').toString();
    }

    private static void wmSave() {
        try {
            String archive = sWatermark.archive();
            java.io.FileOutputStream fos = new java.io.FileOutputStream(WM_KEEP);
            fos.write(archive.getBytes("UTF-8"));
            fos.close();
        } catch (Throwable t) {
            XposedBridge.log(TAG + "水印隔离存档失败: " + t);
        }
    }

    private static void wmLoad() {
        try {
            java.io.File f = new java.io.File(WM_KEEP);
            if (!f.exists()) return;
            long length = f.length();
            if (!f.isFile() || length < 0 || length > 4096) return;
            byte[] buf = new byte[(int) length];
            java.io.FileInputStream in = new java.io.FileInputStream(f);
            int n = 0;
            while (n < buf.length) {
                int r = in.read(buf, n, buf.length - n);
                if (r <= 0) break;
                n += r;
            }
            in.close();
            if (n != buf.length || !sWatermark.loadArchive(new String(buf, 0, n, "UTF-8")))
                XposedBridge.log(TAG + "watermark archive refused; previous snapshot retained");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "水印隔离读档失败: " + t);
        }
    }


    /** X-Pan 仅允许 1×，不写请求文件，不启动换配置/切模式流程。 */
    private void installXpanZoomUnlock(XC_LoadPackage.LoadPackageParam lp) {
        try {
            Class<?> model = Class.forName("fh.g", false, lp.classLoader);
            Method clamp = model.getDeclaredMethod("f", float.class);
            if (clamp.getReturnType() != float.class) {
                throw new NoSuchMethodException("fh.g.f(float) must return float");
            }
            XposedBridge.hookMethod(clamp, new QualifiedHook() {
                @Override
                protected void beforeQualified(MethodHookParam param) {
                    if (!sXpanActive) return;
                    sXpanZoomReq = 1.0f;
                    param.setResult(1.0f);
                }
            });
            XposedBridge.log(TAG + "hook installed OK (xpan 1x lock)");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! xpan 1x lock failed: " + t);
        }
    }

    /* ---------------- 修复：X-Pan 变焦条多出来的坏档位（显示成 "0"） ----------------
     * 病因（实机取证 2026-10-06）：
     *   变焦条日志 initZoomPointMap clickValues: [0.6, 1.0, 0.0, 2.0]
     *   → mZoomPointMap {ULTRA_WIDE=0.6, WIDE=1.0, DUAL=0.0, TELE=2.0}
     *   第 3 档 DUAL 的值来自 y5.a$b 的静态初始化：
     *       CameraConfig.e("com.oplus.dual.zoom.value")
     *   而这个键在本机任何相机配置里都不存在（只在 APK 里作为字符串出现），
     *   于是取到默认 0.0 → 按钮文字渲染成 "0"、点了也不生效。
     *   普通照片模式的档位列表只有 3 项（0.6/1/2），所以那边正常。
     *
     * 当前策略：只保留 1×，仅复制并过滤已核对的点按档位和变焦点列表。
     *   非 X-Pan 和其他 Float 列表均保持原样；不触发换带、裁剪或切模式。
     */
    private void installXpanZoomPointFix(XC_LoadPackage.LoadPackageParam lp) {
        try {
            Class<?> c = Class.forName("com.oplus.camera.feature.zoom.view.ZoomSeekBar",
                    false, lp.classLoader);
            int n = 0;
            for (Method m : c.getDeclaredMethods()) {
                if (!"R8".equals(m.getName()) || m.getParameterTypes().length != 18) continue;
                XposedBridge.hookMethod(m, new QualifiedHook() {
                    @Override
                    protected void beforeQualified(MethodHookParam param) {
                        try {
                            if (!sXpanActive || param.args == null) return;
                            // C16 R8 的第 5、6 个参数才是点按档位与变焦点；其他 Float 列表保持原样。
                            for (int index : new int[]{4, 5}) {
                                Object value = param.args[index];
                                if (!(value instanceof List)) continue;
                                List<?> list = (List<?>) value;
                                if (hasBadZoomPoint(list)) {
                                    List<Object> copy = new ArrayList<Object>(list);
                                    filterBadZoomPoints(copy);
                                    if (!copy.isEmpty()) param.args[index] = new ArrayList<Object>(copy);
                                }
                            }
                        } catch (Throwable ignored) {
                        }
                    }
                });
                n++;
            }
            XposedBridge.log(TAG + "hook installed OK (xpan zoom point fix x" + n + ")");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! xpan zoom point fix failed: " + t);
        }
    }

    /** 档位列表里是否还有需要剔除的档位（≤0 非法 / <1.0 的 0.6× 超广角） */
    private static boolean hasBadZoomPoint(List<?> list) {
        for (int i = list.size() - 1; i >= 0; i--) {
            Object v = list.get(i);
            if (!(v instanceof Float)) continue;
            float f = (Float) v;
            if (f < 0.99f || f > 1.01f) return true;
        }
        return false;
    }

    private static void filterBadZoomPoints(List<?> list) {
        for (int i = list.size() - 1; i >= 0; i--) {
            Object v = list.get(i);
            if (!(v instanceof Float)) continue;
            float f = (Float) v;
            if (f >= 0.99f && f <= 1.01f) continue;   // 只保留 1×
            list.remove(i);
            XposedBridge.log(TAG + "xpan 变焦条剔掉档位 " + v
                    + (f <= 0f ? "（非法）"
                       : f < 1f ? "（0.6× 超广角，成像不可靠）"
                       : "（2× 长焦，本机无长焦且 HAL 不裁 65:24 流）"));
        }
    }

    /* ---------------- 实验：把 feature.xpan 注入功能生成列表 ----------------
     * 背景（2026-10-06）：显影动画/双皮肤的总闸门 —— FeatureFactory.b 只有在收到
     *   功能名 "com.oplus.camera.feature.xpan" 时才 new bh.c(XPanPresenter)，
     *   bh.c 构造里又因 ch.b.c()==true 而 new dh.w(XPanViewManagerV3)。
     *   本机 mode=xpan 的功能表里没有这个名字 → 两者从未被创建 → 动画/皮肤/双皮肤全休眠。
     *
     * 做法（hook 路线，不动配置——配置手术已失败 4 次）：
     *   1) getFeatureInfoList(I) 只在已核对的 X-Pan 功能生成 scope 中复制加此名字。
     *      不改原 Set 缓存，也不影响通过 hasConfigFeature 查 protobuf 的模式菜单。
     *   2) FeatureFactory.b 加闸：featureName==xpan 且 modeName!="xpan" 时返回 null
     *      （调用方 i0.accept 对 null 有判空），保证其他模式不受影响。
     *
     * 开关：默认开启（显影动画/双皮肤/专属 UI 都靠它）。setprop debug.xpan.feat 0 重启相机可关。
     * 主摄只留 1×，默认申请 8192x3024；不能把 presenter 创建日志当成界面或成像验收。
     * 验证口径：日志出现 "FeatureFactory feature=com.oplus.camera.feature.xpan | mode=xpan"
     *   和 "bh.c <init>" / "dh.w <init>"，且相机不崩、X-Pan 仍能正常拍照。
     */
    private static final String FEAT_XPAN = "com.oplus.camera.feature.xpan";
    private static volatile boolean sFeatureGuardReady;
    private static final String GENERATION_FRAME = "com.xpanport.hook.mode.generation";
    private static final ThreadLocal<FeatureGeneration> sFeatureGeneration = new ThreadLocal<>();
    private static volatile Method sC16SurfaceFormat;

    private static final class FeatureGeneration {
        final String mode;
        final int mask;
        FeatureGeneration(String mode, int mask) { this.mode = mode; this.mask = mask; }
    }
    private static final class GenerationScope {
        final FeatureGeneration prior;
        GenerationScope(FeatureGeneration prior) { this.prior = prior; }
    }

    private void installXpanFeatureInject(XC_LoadPackage.LoadPackageParam lp) {
        // 1) 功能名 Set 注入
        try {
            Class<?> ad = Class.forName(
                    "com.oplus.ocs.camera.config.FeatureConfigureAdapter", false, lp.classLoader);
            Method m = ad.getDeclaredMethod("getFeatureInfoList", int.class);
            XposedBridge.hookMethod(m, new QualifiedHook() {
                @Override
                protected void afterQualified(MethodHookParam param) {
                    try {
                        if (!sFeatureGuardReady || param.hasThrowable() || "0".equals(prop("debug.xpan.feat"))) return;
                        FeatureGeneration generation = sFeatureGeneration.get();
                        if (generation == null || !"xpan".equals(generation.mode)
                                || generation.mask != (Integer) param.args[0]) return;
                        Object r = param.getResult();
                        if (r instanceof java.util.Set) {
                            for (Object value : (java.util.Set<?>) r) if (!(value instanceof String)) return;
                            java.util.Set<Object> copy = new java.util.LinkedHashSet<>((java.util.Set<?>) r);
                            copy.add(FEAT_XPAN);
                            param.setResult(copy);
                        }
                    } catch (Throwable ignored) {
                    }
                }
            });
            XposedBridge.log(TAG + "hook installed OK (xpan feature inject)");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! xpan feature inject failed: " + t);
        }
        // 2) 工厂闸：只许 xpan 模式创建 XPanPresenter；★ 顺手把 presenter 实例存下来
        //    （后面"滤镜重新装配"必须要它，而应用没有任何全局注册表能拿到它）
        try {
            Class<?> d0 = Class.forName("q7.d0", false, lp.classLoader);
            Class<?> ui = Class.forName("com.oplus.camera.protocal.ui.a", false, lp.classLoader);
            Method b = d0.getDeclaredMethod("b", String.class, String.class,
                    int.class, android.app.Activity.class, ui);
            XposedBridge.hookMethod(b, new QualifiedHook() {
                @Override
                protected void beforeQualified(MethodHookParam param) {
                    if (FEAT_XPAN.equals(param.args[0]) && !"xpan".equals(param.args[1])) {
                        param.setResult(null);
                    }
                }

                @Override
                protected void afterQualified(MethodHookParam param) {
                    try {
                        if (!FEAT_XPAN.equals(param.args[0])) return;
                        String mode = String.valueOf(param.args[1]);
                        Object r = param.getResult();
                        if ("xpan".equals(mode) && r != null) {
                            sPresenter = r;    // bh.c(XPanPresenter) 实例
                            // ★ 同时记下"当次"的 Activity 与 ui.a：
                            //   dh/w.h 会从 ui.a 里取当前容器(ui.a.ra())并给预览层绑屏幕对象，
                            //   重放时若用旧实例会把预览绑到过期视图上 → 预览卡住（实测 2026-10-07）。
                            sAct = param.args[3];
                            sUi = param.args[4];
                            XposedBridge.log(TAG + "FeatureFactory 放行: xpan | presenter/act/ui 已捕获");
                            return;
                        }
                        if (r != null) {
                            param.setResult(null);   // 其他模式装作不支持
                            XposedBridge.log(TAG + "FeatureFactory 拦截: xpan 功能请求自 mode="
                                    + mode + "（已返回 null）");
                        }
                    } catch (Throwable ignored) {
                    }
                }
            });
            sFeatureGuardReady = true;
            XposedBridge.log(TAG + "hook installed OK (xpan feature gate)");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! xpan feature gate failed: " + t);
        }
        // 3) 见证：两个目标类是否真的被创建（仅诊断开关打开时打日志，避免常态开销）
        try {
            for (final String cn : new String[]{"bh.c", "dh.w"}) {
                Class<?> c = Class.forName(cn, false, lp.classLoader);
                for (java.lang.reflect.Constructor<?> ctor : c.getDeclaredConstructors()) {
                    XposedBridge.hookMethod(ctor, new QualifiedHook() {
                        @Override
                        protected void afterQualified(MethodHookParam param) {
                            if ("1".equals(prop("debug.xpan.diag"))) {
                                XposedBridge.log(TAG + cn + " <init> ← 已实例化");
                            }
                        }
                    });
                }
            }
            XposedBridge.log(TAG + "hook installed OK (xpan presenter witness)");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! xpan presenter witness failed: " + t);
        }
    }

    /* ---------------- 修复：模式切换进入 X-Pan 时容器休眠（快门/皮肤/动画全缺失） ----------------
     * 病因（2026-10-06 复现实锤）：
     *   长按图标直开 X-Pan → 容器 zl.q 走 c1(屏幕) → ec+bc(建视图)+ic(刷UI)，实体皮肤完整。
     *   照片模式 → 更多 → XPAN 切换进入 → 应用只调 fc()(卸视图) 和 xa()，从不调 c1：
     *   容器 Q 停在 -1，视图根本没建 → 快门/皮肤/动画全没有（xa() 恒 false 也证明 Q∈{1,2,6,7} 不成立）。
     *
     * 做法：模式门翻到 X-Pan 后延迟 2 秒（UI 稳定后）在主线程检查容器：
     *   Q==-1（确认休眠）才动手 —— 从 k0 的字段 z 拿屏幕对象(nk.u1.z:Lbk/g)、
     *   从容器字段 f 拿 Activity 取 Resources，替应用补一次 ec+bc+hc+ic：
     *   b5()==true（皮肤开）推 Q=6（实体皮肤态），否则 Q=0（普通 X-Pan UI）。
     * 直开路径容器已初始化（Q!=-1）→ 本修复自动空转，不干预。
     */
    private static volatile Object sControlUI = null;

    private void installXpanSwitchFix(XC_LoadPackage.LoadPackageParam lp) {
        try {
            Class<?> bm = Class.forName(CLS_BASE_MODE, false, lp.classLoader);
            Class<?> cfg = Class.forName(CLS_DEVICE_CONFIG, false, lp.classLoader);
            Method bss = bm.getDeclaredMethod("buildStreamSurface", cfg, String.class);
            XposedBridge.hookMethod(bss, new QualifiedHook() {
                @Override
                protected void afterQualified(MethodHookParam param) {
                    try {
                        Object thiz = param.thisObject;
                        if (thiz == null || !CLS_XPAN_MODE.equals(thiz.getClass().getName())) return;
                        final long epoch = sWatermark.epoch();
                        if (param.hasThrowable()) return;
                        android.os.Handler h = new android.os.Handler(android.os.Looper.getMainLooper());
                        h.postDelayed(new Runnable() {
                            @Override
                            public void run() {
                                if (!moduleEnabled() || !sWatermark.current(epoch)) return;
                                driveXpanContainer();
                            }
                        }, 2000);
                        h.postDelayed(new Runnable() {
                            @Override
                            public void run() {
                                if (!moduleEnabled() || !sWatermark.current(epoch)) return;
                                driveXpanContainer();
                            }
                        }, 4500);   // 同一实际模式 epoch 才允许兜底
                    } catch (Throwable ignored) {
                    }
                }
            });
            XposedBridge.log(TAG + "hook installed OK (xpan switch fix)");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! xpan switch fix failed: " + t);
        }
        // 覆盖"应用自己初始化"的路径：应用调 c1(屏幕) 会重建视图 —— 重建后的滤镜视图
        // 是新实例，应用的滤镜装配如果发生在重建之前就落空了。c1 一跑完就重新装配一次
        // （幂等 setter，重复无害）。这样无论"应用先建/我补建"谁先谁后，滤镜都能接上。
        try {
            Class<?> zlq = Class.forName("zl.q", false, lp.classLoader);
            Class<?> scr = Class.forName("bk.g", false, lp.classLoader);
            Method c1 = zlq.getDeclaredMethod("c1", scr);
            XposedBridge.hookMethod(c1, new QualifiedHook() {
                @Override
                protected void afterQualified(MethodHookParam param) {
                    try {
                        if (!sXpanActive || sPresenter == null) return;
                        wireXpanFilter(param.thisObject);
                    } catch (Throwable ignored) {
                    }
                }
            });
            XposedBridge.log(TAG + "hook installed OK (xpan c1 rewire)");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! xpan c1 rewire failed: " + t);
        }
        // 持久兜底：切换路径下应用会反复重置容器（实测状态 6→7→-1→…，甚至重建实例），
        // 定时驱动修完一版会被再次重置覆盖。xa() 是应用频繁查询的状态口 ——
        // 只要 X-Pan 会话里 Q 仍停在 -1（从未初始化），就补一次初始化，直到成功为止。
        try {
            Class<?> zlq = Class.forName("zl.q", false, lp.classLoader);
            final java.lang.reflect.Field fQ = zlq.getDeclaredField("Q");
            fQ.setAccessible(true);
            Method xa = zlq.getDeclaredMethod("xa");
            XposedBridge.hookMethod(xa, new QualifiedHook() {
                @Override
                protected void afterQualified(MethodHookParam param) {
                    try {
                        // 性能：这是热路径（应用每帧/每次状态查询都会调 xa()）。
                        // 成功后立刻短路，之后只剩一次布尔判断，不再做任何反射。
                        if (sDriveDone) return;
                        if (!sXpanActive) return;
                        if (!Boolean.FALSE.equals(param.getResult())) return;   // 已可见，无需管
                        if (fQ.getInt(param.thisObject) != -1) return;          // 非"从未初始化"
                        if (sDrivePending) return;                              // 已排队
                        sDrivePending = true;
                        // 退避：首次尽快（150ms，让边框别"慢半拍"），连续失败再拉长
                        long delay = Math.min(150L * (1 + sDriveFails), 3000L);
                        new android.os.Handler(android.os.Looper.getMainLooper())
                                .postDelayed(new Runnable() {
                                    @Override
                                    public void run() {
                                        if (!moduleEnabled()) return;
                                        sDrivePending = false;
                                        driveXpanContainer();   // 失败则下次 xa() 再触发
                                    }
                                }, 400);
                    } catch (Throwable ignored) {
                    }
                }
            });
            XposedBridge.log(TAG + "hook installed OK (xpan switch revive)");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! xpan switch revive failed: " + t);
        }
    }

    private static volatile boolean sDrivePending = false;

    /** 本次 X-Pan 会话是否已经成功补建过容器视图（成功后 xa() 热路径不再做任何反射）。 */
    private static volatile boolean sDriveDone = false;

    /** 连续失败次数，用于退避（避免在热路径上高频重试反射）。 */
    private static volatile int sDriveFails = 0;

    /** ★ 本次 X-Pan 会话是否已经补建过：严格一次性。
     *  实测（2026-10-07 用户截图）：补建在同一次会话里跑了多次 → 叠出多套界面视图 →
     *  最上面那套吃掉所有触摸（除滤镜，它在新套里）、预览来自已被摘掉的那套所以冻住。
     *  所以补建只允许做一次，做完就彻底停手。 */
    private static volatile boolean sDroveThisSession = false;

    /* ---------------- 修复：补建后重新装配滤镜（列表 + 点击监听） ----------------
     * 应用的滤镜装配（setXpanFilterList / setFilterViewListener）发生在视图管理器初始化时，
     * 针对的是"当时的视图实例"。我们的补建如果重建了视图（新实例），或者时序赶在装配之后，
     * 新视图就是"没有滤镜列表、没有点击监听"的空壳 —— 表现为滤镜徽标点不开。
     *
     * 这里照抄应用自己的装配三步（见反编译 dh/w 的视图装配代码）：
     *   filterView.setXpanFilterList(presenter.m0())      滤镜列表
     *   filterView.j(presenter.w0(), false)               当前选中项
     *   filterView.setFilterViewListener(new dh.u(dh/w))  点击回调
     * presenter 用 FeatureFactory.b 的返回值（installXpanFeatureInject 已捕获）。
     * setXxx 都是幂等 setter，重复调用无害。
     */
    private void wireXpanFilter(Object container) {
        try {
            Object presenter = sPresenter;
            if (presenter == null) return;
            Object w = field(presenter, "F");             // dh/p (运行时是 dh/w = XPanViewManagerV3)
            if (w == null) return;
            Object fv = field(container, "B");            // XpanPhysicalFilterView
            if (fv == null) return;
            Class<?> dhu = Class.forName("dh.u", false, container.getClass().getClassLoader());
            // setFilterViewListener 的参数类型是内部接口 XpanPhysicalFilterView$d，
            // 不能拿实现类 dh.u 去 getMethod（会 NoSuchMethod）
            Class<?> dIface = Class.forName(
                    "com.oplus.camera.feature.xpan.view.widget.XpanPhysicalFilterView$d",
                    false, container.getClass().getClassLoader());
            Object listener = dhu.getConstructors()[0].newInstance(w);
            Object list = presenter.getClass().getMethod("m0").invoke(presenter);
            Object cur = presenter.getClass().getMethod("w0").invoke(presenter);
            fvm(fv, "setXpanFilterList", java.util.List.class).invoke(fv, list);
            // ★ 第二个参数 = "是否刷新显示"（2026-10-07 反编译实证：j(name,false) 只设内部字段 o，
            //   直接 return；j(name,true) 才会把徽标小条滚到该滤镜并换胶片图案）。
            //   之前传 false，往返重建后徽标会停在上一个图案上（实测：实际是复古、徽标显示 Original）。
            //   注意：true 路径会按名字在列表里找下标再取列表项，名字为空时会下标越界 —— 先判空。
            if (cur != null) {
                fvm(fv, "j", String.class, boolean.class).invoke(fv, cur, Boolean.TRUE);
                if ("1".equals(prop("debug.xpan.diag"))) {
                    XposedBridge.log(TAG + "滤镜徽标刷新：cur=" + cur);
                }
            }
            fvm(fv, "setFilterViewListener", dIface).invoke(fv, listener);
            // ★ 关键修复（2026-10-07 探针实证）：点击展开作用的是 dh/w.Y 指向的面板实例，
            //   而补建重建视图后容器里挂载的是新实例（zl.q.C）—— 两者不一致时，
            //   点击作用在"没挂载的旧实例"上，面板永远打不开
            //   （日志：Y=1feab7f/attached=false  C=7a3c984/attached=true 【不同实例!】）。
            //   这里把容器里的面板同步给监听器持有的字段。
            try {
                Object cPanel = field(container, "C");
                if (cPanel != null && cPanel != field(w, "Y")) {
                    java.lang.reflect.Field fY = w.getClass().getField("Y");
                    fY.set(w, cPanel);
                    // 面板换了实例后，必须给它重新装列表（setXpanFilterList 内部会建 adapter 并
                    // setAdapter —— 否则面板里是空的，实测：展开后只有一个空框、没有滤镜条目）
                    Object list3 = presenter.getClass().getMethod("m0").invoke(presenter);
                    fvm(cPanel, "setXpanFilterList", java.util.List.class).invoke(cPanel, list3);
                    Object cur3 = presenter.getClass().getMethod("w0").invoke(presenter);
                    fvm(cPanel, "b1", String.class, boolean.class).invoke(cPanel, cur3, Boolean.FALSE);
                    // ★ 面板内部的"选中回调列表"也必须装（应用装配时往面板的 v 列表加了一个 dh/v）：
                    //   条目点击 → 面板内部 → 遍历 v 列表通知 → dh/v → dh/w → 应用滤镜。
                    //   新实例的 v 列表是空的，不装的话点条目毫无反应（实测：选中框不动、filter_type 不变）。
                    try {
                        Class<?> dv = Class.forName("dh.v", false,
                                container.getClass().getClassLoader());
                        Object cb = dv.getConstructors()[0].newInstance(w);
                        Object vList = field(cPanel, "v");
                        if (vList instanceof java.util.List) {
                            @SuppressWarnings("unchecked")
                            java.util.List<Object> vl = (java.util.List<Object>) vList;
                            vl.clear();
                            vl.add(cb);
                            if ("1".equals(prop("debug.xpan.diag"))) {
                                XposedBridge.log(TAG + "面板选中回调已装（v 列表）");
                            }
                        }
                    } catch (Throwable ignored) {
                    }
                    if ("1".equals(prop("debug.xpan.diag"))) {
                        XposedBridge.log(TAG + "面板实例已同步 + 列表已装（"
                                + ((java.util.List<?>) list3).size() + " 项）");
                    }
                }
            } catch (Throwable t) {
                XposedBridge.log(TAG + "面板实例同步失败: " + t);
            }
            // 面板(xpan_textured_filter_panel)的挂载/位置交给应用自己的装配（重放 dh/w.h），
            // 这里绝不再手工 addView —— 手工挂会让面板常驻屏幕下半部盖住快门/变焦（实测教训）。
            if ("1".equals(prop("debug.xpan.diag"))) {
                XposedBridge.log(TAG + "filter 重装配完成（列表 "
                        + ((java.util.List<?>) list).size() + " 项）");
            }
            // ★ 滤镜条滚动位置对齐（2026-10-07 实证）：往返切换后条子会停在中途
            //   （探针：scrollX=631，而选中项应在 735 —— 非档位值，是滚动动画被打断留下的），
            //   表现为"橙色选中框悬在两格之间"。这里在重建后把选中项重新对到中间框下；
            //   幂等：已经对准时位移为 0。
            try {
                recenterFilterStrip(field(container, "C"));
            } catch (Throwable ignored) {
            }
        } catch (Throwable t) {
            Throwable cause = (t.getCause() != null) ? t.getCause() : t;
            XposedBridge.log(TAG + "filter 重装配失败: " + cause);
        }
    }

    /**
     * 把滤镜条的"选中项"重新对到中间的高亮框下（展开/重建后调用）。
     *
     * 背景（2026-10-07 探针实证）：面板布局里 xpan_filter_panel_item_selected_frame 是
     * 一个固定在整条滤镜带正中间的视图，应用靠"把选中项滚到中间"来对齐它。往返切换/补建
     * 重建后应用自己这次滚动会算错（少算半个条目宽，实测落在 scrollX=632，正确是 735 ——
     * 表现为"橙色选中框悬在两格之间"），而且它可能在我们的修正之后又把条子挪回去。
     * 因此这里不是修一次，而是在展开后的一小段时间内反复核验、不对就修（幂等）。
     *
     * @param panel 面板实例（com.oplus.camera.feature.xpan.view.widget.c，即 zl.q.C）
     */
    private static final long[] RECENTER_AT_MS = {0, 150, 300, 500, 800, 1200, 1800};

    private static void recenterFilterStrip(Object panel) {
        if (panel == null) return;
        for (int i = 0; i < RECENTER_AT_MS.length; i++) {
            try {
                if (!(panel instanceof android.view.View)) return;
                final Object p = panel;
                final int attempt = i;
                ((android.view.View) panel).postDelayed(new Runnable() {
                    @Override
                    public void run() {
                                        if (!moduleEnabled()) return;
                        recenterFilterStripOnce(p, attempt);
                    }
                }, RECENTER_AT_MS[i]);
            } catch (Throwable ignored) {
            }
        }
    }

    /** 单次核验：条子静止时，把选中项的中心对到条子中心（已对准时 delta=0，零动作） */
    private static void recenterFilterStripOnce(Object panel, int attempt) {
        final boolean diag = "1".equals(prop("debug.xpan.diag"));
        try {
            if (!(panel instanceof android.view.View)) return;
            android.view.View pv = (android.view.View) panel;
            if (!pv.isAttachedToWindow()) return;          // 面板已摘（切走模式）→ 本次核验作废
            Object stripObj = field(panel, "d");           // HighlightRecyclerView(滤镜条)
            if (!(stripObj instanceof android.view.View)) return;
            android.view.View strip = (android.view.View) stripObj;
            Object idxObj = field(panel, "o");             // 选中项下标
            if (!(idxObj instanceof Integer)) return;
            int idx = (Integer) idxObj;
            if (idx < 0 || strip.getWidth() <= 0) return;
            // 只在条子静止时校正：用户拖动/惯性滚动中不抢（0=IDLE）
            int state = (Integer) strip.getClass().getMethod("getScrollState").invoke(strip);
            if (state != 0) {
                if (diag) XposedBridge.log(TAG + "滤镜条核验[" + attempt + "] 跳过（滚动中 state=" + state + "）");
                return;
            }
            Object lm = strip.getClass().getMethod("getLayoutManager").invoke(strip);
            if (lm == null) return;
            Object child = lm.getClass().getMethod("findViewByPosition", int.class).invoke(lm, idx);
            if (!(child instanceof android.view.View)) return;
            android.view.View cv = (android.view.View) child;
            int childCenter = cv.getLeft() + cv.getWidth() / 2;
            int stripCenter = strip.getWidth() / 2;
            int delta = childCenter - stripCenter;
            if (delta != 0) {
                try {
                    strip.getClass().getMethod("stopScroll").invoke(strip);
                } catch (Throwable ignored) {
                }
                strip.scrollBy(delta, 0);
            }
            if (diag) {
                XposedBridge.log(TAG + "滤镜条核验[" + attempt + "] idx=" + idx
                        + " childCenter=" + childCenter + " stripCenter=" + stripCenter
                        + " delta=" + delta + (delta != 0 ? "（已修正）" : "（已对准）"));
            }
        } catch (Throwable t) {
            if (diag) XposedBridge.log(TAG + "滤镜条核验[" + attempt + "] 失败: " + t);
        }
    }

    /** 容器里的 X-Pan 视图是否至少有一个已经挂在窗口上 */
    private static boolean anyContainerViewAttached(Object container) {
        for (String f : new String[]{"B", "A", "o", "n", "m", "l", "k"}) {
            Object v = field(container, f);
            if (v instanceof android.view.View && ((android.view.View) v).getParent() != null) {
                return true;
            }
        }
        return false;
    }

    private static java.lang.reflect.Method fvm(Object o, String name, Class<?>... types)
            throws NoSuchMethodException {
        java.lang.reflect.Method m = o.getClass().getMethod(name, types);
        m.setAccessible(true);
        return m;
    }

    /** 诊断：把一个 View 描述成 "id/挂载状态/可见性" */
    private static String describeView(Object v) {
        if (v == null) return "null";
        try {
            android.view.View view = (android.view.View) v;
            return Integer.toHexString(System.identityHashCode(v))
                    + "/attached=" + (view.getParent() != null)
                    + "/vis=" + view.getVisibility();
        } catch (Throwable t) {
            return Integer.toHexString(System.identityHashCode(v)) + "/(非View)";
        }
    }

    /* ---------------- 修复：退出 X-Pan 后容器视图残留（与其他模式界面重叠） ----------------
     * 病因（2026-10-06）：切换路径下应用的进出清理不对称 —— 进入靠我补建（ec+bc+o4+hc+ic），
     *   但退出时应用不一定会调 fc()(卸视图)，尤其容器是被我强制初始化的情况，
     *   于是 X-Pan 的机身框/刻度/快门等视图留在窗口根布局上，与照片等其他模式界面重叠。
     * 做法：模式门翻出 X-Pan 时延迟调 fc() 卸视图 + hc(-1) 复位休眠（与应用自己的退出行为一致，
     *   下次进入我的驱动会重新补建）。调两次防时序缝隙。
     */
    private boolean cleanupXpanContainer() {
        try {
            if (sXpanActive) return true;   // 又回到 X-Pan 了：不能清
            Object ui = sControlUI;
            if (ui == null) return false;
            Object b = field(ui, "H");
            if (b == null) return false;
            Object k0 = b.getClass().getMethod("M").invoke(b);
            if (k0 == null) return false;
            Object container = field(k0, "p");
            if (container == null) return false;
            container.getClass().getDeclaredMethod("fc").invoke(container);
            // fc() 内部会跳过面板（id 0x7f090795），它是常驻视图 —— 退出时必须自己摘掉，
            // 否则 X-Pan 面板会留在照片等模式上（实测残留，界面重叠）。
            Object panel = field(container, "C");
            if (panel instanceof android.view.View) {
                android.view.View pv = (android.view.View) panel;
                if (pv.getParent() instanceof android.view.ViewGroup) {
                    ((android.view.ViewGroup) pv.getParent()).removeView(pv);
                }
            }
            java.lang.reflect.Field fQ = container.getClass().getDeclaredField("Q");
            fQ.setAccessible(true);
            if (fQ.getInt(container) != -1) {
                fQ.setInt(container, -1);
                XposedBridge.log(TAG + "exit fix: 已卸载 XpanUI 视图并复位休眠");
            }
            return true;
        } catch (Throwable t) {
            Throwable cause = (t.getCause() != null) ? t.getCause() : t;
            XposedBridge.log(TAG + "exit fix 失败: " + cause);
            return true;
        }
    }

    /** 替应用补一次容器初始化。只在 Q==-1（休眠）时动手；返回 false 表示容器还没就绪。
     *
     *  ⚠️ 时序说明：v2.9 曾加 2.6s 宽限期"让应用先初始化"，结果往返路径边框反而更慢
     *  且滤镜依旧打不开（应用在往返路径根本不会自己初始化，等了也白等）。
     *  现在的策略：快速补建（600ms，边框不慢）+ 补建后用 presenter 重新装配滤镜
     *  （wireXpanFilter）+ 挂 zl.q.c1 后置装配兜底 —— 谁后执行谁都能把滤镜接上。
     */
    private boolean driveXpanContainer() {
        try {
            if (!sXpanActive) return true;   // 已退出 X-Pan：绝不能再把视图建上去（会与照片等界面重叠）
            // 调试开关：置 1 就完全不补建（用来对照"卡死是否由补建引起"）
            if ("1".equals(prop("debug.xpan.nodrive"))) return true;
            Object ui = sControlUI;
            if (ui == null) return false;
            Object b = field(ui, "H");                    // CameraControlUI.H : com.oplus.camera.b
            if (b == null) return false;
            Object k0 = b.getClass().getMethod("M").invoke(b);   // -> nk.k0 (extends nk.u1)
            if (k0 == null) return false;
            Object container = field(k0, "p");            // u1.p : zl.q (XpanUIContainer)
            if (container == null) return false;
            Class<?> zlq = container.getClass();
            java.lang.reflect.Field fQ = zlq.getDeclaredField("Q");
            fQ.setAccessible(true);
            if (fQ.getInt(container) != -1) {
                sDroveThisSession = true;     // 应用自己初始化好了 → 本会话不再插手
                return true;
            }
            if ("1".equals(prop("debug.xpan.diag"))) {
                XposedBridge.log(TAG + "drive: 开始补建（Q=-1）");
            }
            Object screen = field(k0, "z");               // nk.u1.z : bk.g (当前屏幕对象)
            Object act = field(container, "f");           // zl.q.f : Activity
            if (screen == null || act == null) {
                XposedBridge.log(TAG + "switch fix: screen=" + (screen != null)
                        + " activity=" + (act != null) + "，本次跳过");
                return false;
            }
            Object res = act.getClass().getMethod("getResources").invoke(act);
            Class<?> screenCls = Class.forName("com.oplus.camera.common.screen.a", false,
                    container.getClass().getClassLoader());
            // ★ 走应用自己的完整初始化：先把 Q 从 -1 放开（c1 的守卫是"Q==-1 就 return"），
            //   再调 c1(屏幕) —— 它内部 ec+bc+ic+gc 的顺序、参数都是应用自己要的。
            //   之前我手工拼 ec+bc+o4+ic，建出来的视图虽然显示，但触摸路由是坏的
            //   （用户复现：除了滤镜，快门/变焦/设置/模式轮盘全点不了）。
            // ★ 视图已存在就不要重建（这是实测出来的关键，别删）：
            //   调 bc() 重建会造出新实例，实测重建后 o4() 反而挂不上它们
            //   （日志：B/A/C attached=false，界面半死、点击全无响应）。
            //   跳过重建 + 照常 o4() 才是能用的组合（与 v1.0 行为一致）。
            boolean viewsExist = field(container, "B") != null;
            if (!viewsExist) {
                java.lang.reflect.Method ec = zlq.getDeclaredMethod("ec", screenCls);
                ec.setAccessible(true);
                ec.invoke(container, screen);
                java.lang.reflect.Method bc = zlq.getDeclaredMethod("bc",
                        android.content.res.Resources.class, screenCls);
                bc.setAccessible(true);
                bc.invoke(container, res, screen);
            } else if ("1".equals(prop("debug.xpan.diag"))) {
                XposedBridge.log(TAG + "drive: 视图已存在，跳过重建");
            }
            // ★ o4() = addViewToRoot：bc() 只是把视图建进缓存 map，真正挂到窗口根布局靠 o4。
            //   但 o4 内部第一步 root = nk.u1.W3()，为 null 就直接 return 什么都不挂
            //   （smali 里那句 "addViewToRoot, root is null"）。所以先确认根视图就绪。
            Object rootView = null;
            try {
                rootView = k0.getClass().getSuperclass().getMethod("W3").invoke(k0);
            } catch (Throwable ignored) {
            }
            if (rootView == null) {
                if ("1".equals(prop("debug.xpan.diag"))) {
                    XposedBridge.log(TAG + "drive: 根视图未就绪，延后重试");
                }
                return false;      // 交给 xa() 兜底稍后再试
            }
            java.lang.reflect.Method o4 = zlq.getDeclaredMethod("o4");
            o4.setAccessible(true);
            o4.invoke(container);
            boolean skin = (Boolean) zlq.getDeclaredMethod("b5").invoke(container);
            fQ.setInt(container, skin ? 6 : 0);
            zlq.getDeclaredMethod("ic").invoke(container);
            // ★ 撤掉"重放应用装配流程(dh/w.h)"（v1.1 引入的实验性改动）：
            //   用户实测从 v1.1 起变成"只要照片→X-Pan 就整个卡住、除滤镜外全不能点"。
            //   重放会重跑应用整套 findViewById 与视图装配，把已绑好的触摸/预览关系打乱，
            //   风险远大于收益。退回只补滤镜列表与监听的手工三步，不碰其它视图。
            wireXpanFilter(container);
            if ("1".equals(prop("debug.xpan.diag"))) {
                XposedBridge.log(TAG + "drive: 挂载检查 B(滤镜视图)=" + describeView(field(container, "B"))
                        + " C(滤镜面板)=" + describeView(field(container, "C"))
                        + " A(快门)=" + describeView(field(container, "A"))
                        + " o(曝光轮)=" + describeView(field(container, "o")));
            }
            sDriveDone = true;      // 成功：xa() 热路径从此短路，不再做反射
            sDroveThisSession = true;   // ★ 本会话补建完成，之后绝不再补建（防叠视图）
            sDriveFails = 0;
            XposedBridge.log(TAG + "switch fix: 已补建 XpanUI 视图并推状态 "
                    + (skin ? 6 : 0) + "（模式切换休眠修复）");
            return true;
        } catch (Throwable t) {
            Throwable cause = (t.getCause() != null) ? t.getCause() : t;
            sDriveFails++;          // 退避用：连续失败就拉长重试间隔
            XposedBridge.log(TAG + "switch fix 失败(第" + sDriveFails + "次): " + cause);
            return sDriveFails >= 12;   // 失败太多次就放弃（避免热路径上一直反射）
        }
    }

    /* ---------------- 诊断：滤镜视图装配时序 ----------------
     * 目的：搞清"应用给滤镜视图注册列表/监听"和"我补建视图"谁先谁后。
     * 用户复现路径（杀进程→X-Pan→照片→X-Pan）后滤镜徽标点不开，
     * 怀疑应用装配时视图还不存在（findViewById 落空）→ 视图建好后没人再注册。
     */
    private void installFilterTrace(XC_LoadPackage.LoadPackageParam lp) {
        try {
            Class<?> fv = Class.forName(
                    "com.oplus.camera.feature.xpan.view.widget.XpanPhysicalFilterView",
                    false, lp.classLoader);
            for (Method m : fv.getDeclaredMethods()) {
                final String nm = m.getName();
                if (!"setXpanFilterList".equals(nm) && !"setFilterViewListener".equals(nm)
                        && !"performClick".equals(nm) && !"n".equals(nm)) continue;
                XposedBridge.hookMethod(m, new QualifiedHook() {
                    @Override
                    protected void beforeQualified(MethodHookParam param) {
                        try {
                            StringBuilder sb = new StringBuilder();
                            if (param.args != null) {
                                for (Object a : param.args) {
                                    String s = String.valueOf(a);
                                    sb.append(s.length() > 60 ? s.substring(0, 60) + "..." : s)
                                            .append(' ');
                                }
                            }
                            XposedBridge.log(TAG + "filterView." + nm + "(" + sb.toString().trim()
                                    + ") 视图=" + Integer.toHexString(
                                    System.identityHashCode(param.thisObject)));
                        } catch (Throwable ignored) {
                        }
                    }
                });
            }
            XposedBridge.log(TAG + "hook installed OK (filter trace)");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! filter trace failed: " + t);
        }
        // 真正的触摸探针：滤镜条的触摸由内部 OnTouchListener(XpanPhysicalFilterView$a) 处理，
        // 不走 performClick。挂它才能判断"点击到底有没有到达视图"。
        try {
            Class<?> l = Class.forName(
                    "com.oplus.camera.feature.xpan.view.widget.XpanPhysicalFilterView$a",
                    false, lp.classLoader);
            Method ot = l.getDeclaredMethod("onTouch", android.view.View.class,
                    android.view.MotionEvent.class);
            XposedBridge.hookMethod(ot, new QualifiedHook() {
                @Override
                protected void beforeQualified(MethodHookParam param) {
                    try {
                        android.view.MotionEvent ev = (android.view.MotionEvent) param.args[1];
                        if (ev != null && ev.getAction() == android.view.MotionEvent.ACTION_UP) {
                            XposedBridge.log(TAG + "filterTouch ACTION_UP 到达滤镜视图");
                        }
                    } catch (Throwable ignored) {
                    }
                }
            });
            XposedBridge.log(TAG + "hook installed OK (filter touch trace)");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! filter touch trace failed: " + t);
        }
        // 监听器链路：轻点 → XpanPhysicalFilterView$d.a(滤镜名) → dh/u.a() → dh/w.Y(滤镜面板) 决定是否展开。
        // 挂 dh/u.a() 看点击到底走到哪、以及面板对象 Y 是否为 null（为 null 则点击静默无效）。
        try {
            Class<?> u = Class.forName("dh.u", false, lp.classLoader);
            Method a = u.getDeclaredMethod("a", String.class);
            XposedBridge.hookMethod(a, new QualifiedHook() {
                @Override
                protected void beforeQualified(MethodHookParam param) {
                    try {
                        Object w = field(param.thisObject, "a");      // dh.u.a : dh/w
                        Object yPanel = (w == null) ? null : field(w, "Y");
                        Object cPanel = null;
                        try {
                            Object ui = sControlUI;
                            Object b = (ui == null) ? null : field(ui, "H");
                            Object k0 = (b == null) ? null : b.getClass().getMethod("M").invoke(b);
                            Object container = (k0 == null) ? null : field(k0, "p");
                            cPanel = (container == null) ? null : field(container, "C");
                        } catch (Throwable ignored) {
                        }
                        String ys = (yPanel == null) ? "null" : Integer.toHexString(
                                System.identityHashCode(yPanel)) + "/attached="
                                + (((android.view.View) yPanel).getParent() != null);
                        String cs = (cPanel == null) ? "null" : Integer.toHexString(
                                System.identityHashCode(cPanel)) + "/attached="
                                + (((android.view.View) cPanel).getParent() != null);
                        XposedBridge.log(TAG + "filterListener.a(" + param.args[0] + ") Y=" + ys
                                + " C=" + cs + (yPanel == cPanel ? " 【同一个】" : " 【不同实例!】"));
                    } catch (Throwable t) {
                        XposedBridge.log(TAG + "filterListener 探针异常: " + t);
                    }
                }
            });
            XposedBridge.log(TAG + "hook installed OK (filter listener trace)");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! filter listener trace failed: " + t);
        }
    }

    /* ---------------- 修复：滤镜面板视图脱管（往返切换后徽标点不开） ----------------
     * 实测（2026-10-07）：往返切换后徽标点击能走到面板的展开逻辑 c1(true)，但面板视图
     * (xpan_textured_filter_panel，widget c，1264x729) 已经被应用的清理摘下来（attached=false）——
     * 对脱管视图做展开等于什么都没发生。
     * 修法：挂住面板的 c1/w0/n1，执行前发现脱管就先挂回根布局（滤镜视图所在的根），
     * 再让应用自己的展开逻辑跑。wireXpanFilter 装配滤镜时也会做同样的事。
     */
    private void installXpanPanelFix(XC_LoadPackage.LoadPackageParam lp) {
        // ★ 捕获应用自己的"X-Pan 视图管理器初始化"调用参数：dh/w.h(Activity, ui.a, dh/p$f, bh/c$b)。
        //   这个方法里才做了完整装配（滤镜列表/监听、面板、曝光轮…），
        //   往返路径下它的效果被容器重置冲掉，光靠我手工补几步是不够的 —— 需要在补建后重放它。
        try {
            Class<?> w = Class.forName("dh.w", false, lp.classLoader);
            for (Method m : w.getDeclaredMethods()) {
                if (!"h".equals(m.getName()) || m.getParameterTypes().length != 4) continue;
                XposedBridge.hookMethod(m, new QualifiedHook() {
                    @Override
                    protected void afterQualified(MethodHookParam param) {
                        try {
                            sViewSetup = new Object[]{param.thisObject,
                                    param.args[0], param.args[1], param.args[2], param.args[3]};
                            if ("1".equals(prop("debug.xpan.diag"))) {
                                XposedBridge.log(TAG + "已捕获 X-Pan 视图装配参数");
                            }
                        } catch (Throwable ignored) {
                        }
                    }
                });
            }
            XposedBridge.log(TAG + "hook installed OK (view setup capture)");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! view setup capture failed: " + t);
        }
        try {
            Class<?> c = Class.forName(
                    "com.oplus.camera.feature.xpan.view.widget.c", false, lp.classLoader);
            for (Method m : c.getDeclaredMethods()) {
                final String nm = m.getName();
                if (!"c1".equals(nm) && !"w0".equals(nm) && !"n1".equals(nm)) continue;
                XposedBridge.hookMethod(m, new QualifiedHook() {
                    @Override
                    protected void beforeQualified(MethodHookParam param) {
                        try {
                            android.view.View v = (android.view.View) param.thisObject;
                            XposedBridge.log(TAG + "面板." + nm + "("
                                    + (param.args != null && param.args.length > 0 ? param.args[0] : "")
                                    + ") 实例=" + Integer.toHexString(System.identityHashCode(v))
                                    + " attached=" + (v.getParent() != null)
                                    + " vis=" + v.getVisibility()
                                    + " 尺寸=" + v.getWidth() + "x" + v.getHeight());
                            if (v.getParent() != null) {
                                // 面板还挂着也不代表没问题：往返后滤镜条可能停在中途
                                // （选中框悬在两格之间）—— 展开/转屏时顺手把选中项对回中间；
                                // 幂等：已对准时什么都不做
                                if (sXpanActive && ("c1".equals(nm) || "n1".equals(nm))) {
                                    recenterFilterStrip(param.thisObject);
                                }
                                return;      // 还挂着，不用重建
                            }
                            if (!sXpanActive) return;               // 不在 X-Pan 就不管
                            // 面板脱管：只补滤镜列表与监听（绝不重放整套装配、也不手工 addView
                            // —— 前者打乱触摸/预览绑定、后者会盖住快门变焦，都是实测踩过的坑）
                            Object ui = sControlUI;
                            if (ui == null) return;
                            Object b = field(ui, "H");
                            if (b == null) return;
                            Object k0 = b.getClass().getMethod("M").invoke(b);
                            if (k0 == null) return;
                            Object container = field(k0, "p");
                            if (container != null) wireXpanFilter(container);
                        } catch (Throwable ignored) {
                        }
                    }
                });
            }
            XposedBridge.log(TAG + "hook installed OK (xpan panel fix)");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! xpan panel fix failed: " + t);
        }
        // 退出清理：应用自己卸 X-Pan 视图时调 zl.q.fc()，但 fc() 内部会"跳过面板"，
        // 于是面板留在屏幕上跟照片等模式重叠。挂在 fc() 上，它一卸就把面板也摘掉 ——
        // 不依赖模式门（实测退出走"轮盘切模式"时 buildStreamSurface 不会重跑，模式门不翻转）。
        try {
            Class<?> zlq = Class.forName("zl.q", false, lp.classLoader);
            Method fc = zlq.getDeclaredMethod("fc");
            XposedBridge.hookMethod(fc, new QualifiedHook() {
                @Override
                protected void afterQualified(MethodHookParam param) {
                    try {
                        Object panel = field(param.thisObject, "C");
                        if (panel instanceof android.view.View) {
                            android.view.View pv = (android.view.View) panel;
                            if (pv.getParent() instanceof android.view.ViewGroup) {
                                ((android.view.ViewGroup) pv.getParent()).removeView(pv);
                                if ("1".equals(prop("debug.xpan.diag"))) {
                                    XposedBridge.log(TAG + "面板已随 fc() 摘除");
                                }
                            }
                        }
                    } catch (Throwable ignored) {
                    }
                }
            });
            XposedBridge.log(TAG + "hook installed OK (panel detach on fc)");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! panel detach on fc failed: " + t);
        }
    }

    /** 重放应用自己的 X-Pan 视图装配（dh/w.h），用当前 presenter 替换第三参数 */
    private static volatile Object[] sViewSetup = null;
    /** 重放防重入：重放会装配面板，面板装配又可能触发重放 → 无限递归 */
    private static volatile boolean sReplaying = false;

    private void replayViewSetup() {
        if (sReplaying) return;
        sReplaying = true;
        try {
            Object[] a = sViewSetup;
            if (a == null) return;
            Object w = a[0];
            if (w == null) return;
            // ★ 必须用"当次"的 Activity / ui.a / presenter：
            //   h() 会从 ui.a 取当前容器、并给预览层绑屏幕对象。用上一次会话的旧实例
            //   会把预览绑到过期视图上 → 预览只出一帧就卡住（实测 2026-10-07 用户复现）。
            Object act = (sAct != null) ? sAct : a[1];
            Object ui = (sUi != null) ? sUi : a[2];
            Object pres = (sPresenter != null) ? sPresenter : a[3];
            if (act == null || ui == null || pres == null) {
                XposedBridge.log(TAG + "重放跳过：关键对象缺失");
                return;
            }
            ClassLoader cl = w.getClass().getClassLoader();
            Class<?> actCls = Class.forName("android.app.Activity", false, cl);
            Class<?> uiCls = Class.forName("com.oplus.camera.protocal.ui.a", false, cl);
            Class<?> pfCls = Class.forName("dh.p$f", false, cl);
            Class<?> bCls = Class.forName("bh.c$b", false, cl);
            Method h = w.getClass().getMethod("h", actCls, uiCls, pfCls, bCls);
            h.setAccessible(true);
            h.invoke(w, act, ui, pres, a[4]);
            if ("1".equals(prop("debug.xpan.diag"))) {
                XposedBridge.log(TAG + "已重放 X-Pan 视图装配（dh.w.h，用当次对象）");
            }
        } catch (Throwable t) {
            Throwable cause = (t.getCause() != null) ? t.getCause() : t;
            XposedBridge.log(TAG + "视图装配重放失败: " + cause);
        } finally {
            sReplaying = false;
        }
    }

    /** 面板视图脱管时重新挂回根布局（通过容器里还挂着的视图找到根；沿用面板自身 LayoutParams） */
    private boolean reattachPanelView(android.view.View panel) {
        try {
            if (panel.getParent() != null) return true;     // 已挂载
            if (!sXpanActive) return false;
            Object ui = sControlUI;
            if (ui == null) return false;
            Object b = field(ui, "H");
            if (b == null) return false;
            Object k0 = b.getClass().getMethod("M").invoke(b);
            if (k0 == null) return false;
            Object container = field(k0, "p");
            if (container == null) return false;
            // 容器里任意一个还挂载着的 X-Pan 视图，用它的根当挂载点
            android.view.ViewGroup root = null;
            for (String f : new String[]{"B", "A", "o", "n"}) {
                Object v = field(container, f);
                if (v instanceof android.view.View && ((android.view.View) v).getParent() != null) {
                    root = (android.view.ViewGroup) ((android.view.View) v).getRootView();
                    break;
                }
            }
            if (root == null) return false;
            root.addView(panel);
            panel.bringToFront();          // 提到最前：否则会被上层的控制面板挡住触摸
            panel.requestLayout();
            if ("1".equals(prop("debug.xpan.diag"))) {
                XposedBridge.log(TAG + "面板已重新挂回根布局");
            }
            return true;
        } catch (Throwable t) {
            Throwable cause = (t.getCause() != null) ? t.getCause() : t;
            XposedBridge.log(TAG + "面板重挂失败: " + cause);
            return true;   // 失败不再重试
        }
    }

    /* Requested feature rebuilds are not exits. UI cleanup is driven by actual mode only. */
    private void onActualMode(String oldMode, String actualMode, final long epoch) {
        boolean isXpan = "xpan".equals(actualMode);
        boolean changed = sXpanActive != isXpan;
        sXpanActive = isXpan;
        if (!changed || sProfile == null || sProfile.generation != CameraProfile.Generation.C16) return;
        if (isXpan) {
            sXpanZoomReq = 1.0f;
            sDriveDone = false;
            sDroveThisSession = false;
            sDriveFails = 0;
        }
        final boolean entering = isXpan;
        Runnable task = new Runnable() {
            @Override public void run() {
                if (!moduleEnabled() || !sWatermark.current(epoch) || sXpanActive != entering) return;
                if (entering) driveXpanContainer(); else cleanupXpanContainer();
            }
        };
        android.os.Handler h = new android.os.Handler(android.os.Looper.getMainLooper());
        h.postDelayed(task, isXpan ? 600L : 300L);
        h.postDelayed(task, isXpan ? 1800L : 1200L);
        XposedBridge.log(TAG + "actual mode " + oldMode + " -> " + actualMode);
    }

    private static Field modeField(Class<?> owner, String name, Class<?> type, int required)
            throws Throwable {
        Field f = owner.getDeclaredField(name);
        if (f.getType() != type || Modifier.isStatic(f.getModifiers())
                || (f.getModifiers() & required) != required)
            throw new NoSuchFieldException(owner.getName() + "." + name + " ABI mismatch");
        f.setAccessible(true);
        return f;
    }

    private static Method modeMethod(Class<?> owner, String name, Class<?> result, Class<?>... args)
            throws Throwable {
        Method m = owner.getDeclaredMethod(name, args);
        if (Modifier.isStatic(m.getModifiers()) || m.getReturnType() != result)
            throw new NoSuchMethodException(owner.getName() + "." + name + " ABI mismatch");
        m.setAccessible(true);
        return m;
    }

    private static String c16ActualMode(Object manager, Field holderField, Field runningField,
                                        Field nameField) throws Throwable {
        Object holder = holderField.get(manager);
        if (holder == null) return null;
        Object running = runningField.get(holder);
        if (running == null) return null;
        Object name = nameField.get(running);
        if (name == null) return null;
        if (!(name instanceof String)) throw new IllegalStateException("untyped actual mode");
        return (String) name;
    }

    /* C16 core bindings are all resolved before hooks become active. */
    private boolean installModeGate(XC_LoadPackage.LoadPackageParam lp) {
        final List<XC_MethodHook.Unhook> installed = new ArrayList<>();
        final boolean[] ready = {false};
        try {
            Class<?> manager = Class.forName("wh.q3", false, lp.classLoader);
            Class<?> holder = Class.forName("wh.w4", false, lp.classLoader);
            Class<?> actualBase = Class.forName("com.oplus.camera.module.BaseMode", false, lp.classLoader);
            final Field holderField = modeField(manager, "a", holder, 0);
            final Field runningField = modeField(holder, "a", actualBase, Modifier.VOLATILE);
            final Field nameField = modeField(actualBase, "a0", String.class, Modifier.FINAL);
            Method change = modeMethod(manager, "d0", boolean.class, String.class, boolean.class, boolean.class);
            Class<?> bm = Class.forName(CLS_BASE_MODE, false, lp.classLoader);
            Class<?> cfg = Class.forName(CLS_DEVICE_CONFIG, false, lp.classLoader);
            Method bss = modeMethod(bm, "buildStreamSurface", java.util.LinkedList.class, cfg, String.class);
            final Method format = modeMethod(bm, "getSurfaceFormatBySurfaceType", int.class,
                    String.class, String.class, String.class);
            Method generate = modeMethod(Class.forName("q7.x0", false, lp.classLoader), "e", void.class,
                    android.app.Activity.class, String.class, int.class, int.class,
                    Class.forName("com.oplus.camera.filter.IEffectProcessor", false, lp.classLoader), actualBase,
                    Class.forName("nk.b", false, lp.classLoader));
            installed.add(XposedBridge.hookMethod(change, new XC_MethodHook() {
                @Override protected void beforeHookedMethod(MethodHookParam param) {
                    if (!ready[0] || !moduleEnabled()) return;
                    try {
                        String old = c16ActualMode(param.thisObject, holderField, runningField, nameField);
                        param.setObjectExtra(MODE_FRAME, wmBeforeModeChange(old, (String) param.args[0]));
                    } catch (Throwable failure) {
                        XposedBridge.log(TAG + "C16 actual-mode before read failed; original preserved: " + failure);
                    }
                }
                @Override protected void afterHookedMethod(MethodHookParam param) {
                    Object token = param.getObjectExtra(MODE_FRAME);
                    if (!(token instanceof WatermarkState.Transition)) return;
                    String actual = null;
                    try { actual = c16ActualMode(param.thisObject, holderField, runningField, nameField); }
                    catch (Throwable failure) {
                        XposedBridge.log(TAG + "C16 actual-mode after read failed; direct live rollback: " + failure);
                    }
                    // Delivered despite gateoff/throwable: only this token's cleanup is allowed.
                    wmAfterModeChange(((WatermarkState.Transition) token).oldMode, actual, token);
                }
            }));
            installed.add(XposedBridge.hookMethod(bss, new XC_MethodHook() {
                @Override protected void beforeHookedMethod(MethodHookParam param) {
                    if (!ready[0] || !moduleEnabled()) return;
                    param.setObjectExtra(BUILD_FRAME, new BuildScope(sXpanBuilding.get()));
                    sXpanBuilding.set(param.thisObject != null
                            && CLS_XPAN_MODE.equals(param.thisObject.getClass().getName()));
                }
                @Override protected void afterHookedMethod(MethodHookParam param) {
                    Object frame = param.getObjectExtra(BUILD_FRAME);
                    if (!(frame instanceof BuildScope)) return;
                    Boolean prior = ((BuildScope) frame).prior;
                    if (prior == null) sXpanBuilding.remove(); else sXpanBuilding.set(prior);
                }
            }));
            installed.add(XposedBridge.hookMethod(generate, new XC_MethodHook() {
                @Override protected void beforeHookedMethod(MethodHookParam param) {
                    if (!ready[0] || !moduleEnabled()) return;
                    param.setObjectExtra(GENERATION_FRAME, new GenerationScope(sFeatureGeneration.get()));
                    sFeatureGeneration.set(new FeatureGeneration((String) param.args[1], (Integer) param.args[3]));
                }
                @Override protected void afterHookedMethod(MethodHookParam param) {
                    Object frame = param.getObjectExtra(GENERATION_FRAME);
                    if (!(frame instanceof GenerationScope)) return;
                    FeatureGeneration prior = ((GenerationScope) frame).prior;
                    if (prior == null) sFeatureGeneration.remove(); else sFeatureGeneration.set(prior);
                }
            }));
            sC16SurfaceFormat = format;
            ready[0] = true;
            XposedBridge.log(TAG + "C16 exact mode/build/generation ABI installed; runtime unverified");
            return true;
        } catch (Throwable failure) {
            ready[0] = false;
            for (int i = installed.size() - 1; i >= 0; i--) {
                try { installed.get(i).unhook(); } catch (Throwable ignored) { }
            }
            XposedBridge.log(TAG + "C16 core binding/registration refused: " + failure);
            return false;
        }
    }

    /* ---------------- 真正的修复点：getHalSurfaceSize ---------------- */
    private void installHalSizeFix(XC_LoadPackage.LoadPackageParam lp) {
        try {
            Class<?> sw = Class.forName(CLS_SURFACE_WRAPPER, false, lp.classLoader);
            Method getHal = sw.getDeclaredMethod("getHalSurfaceSize");
            // ImageReader 就是按这个 getter 建的；它在 mHalSurfaceSize==null 时直接回退成 App 尺寸，
            // 所以只改 setHalSurfaceSize 没用，必须拦 getter。
            XposedBridge.hookMethod(getHal, new QualifiedHook() {
                @Override
                protected void afterQualified(MethodHookParam param) {
                    try {
                        Object r = param.getResult();
                        if (!(r instanceof Size)) return;
                        Size want = targetFor((Size) r, param.thisObject);
                        if (want != null && !want.equals(r)) {
                            dlog("  halSize fix " + r + " -> " + want);
                            param.setResult(want);
                        }
                    } catch (Throwable t) {
                        XposedBridge.log(TAG + "halSize fix error: " + t);
                    }
                }
            });
            XposedBridge.log(TAG + "hook installed OK (getHalSurfaceSize)");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! getHalSurfaceSize hook failed: " + t);
        }
    }

    /* ---------------- 顺手拦 setter（有些路径显式设置） ---------------- */
    private void installSurfaceSetters(XC_LoadPackage.LoadPackageParam lp) {
        try {
            Class<?> sw = Class.forName(CLS_SURFACE_WRAPPER, false, lp.classLoader);
            Method setHal = sw.getDeclaredMethod("setHalSurfaceSize", Size.class);
            Method setApp = sw.getDeclaredMethod("setAppSurfaceSize", Size.class);
            XC_MethodHook fix = new QualifiedHook() {
                @Override
                protected void beforeQualified(MethodHookParam param) {
                    try {
                        if (param.args == null || param.args.length < 1) return;
                        Object o = param.args[0];
                        if (!(o instanceof Size)) return;
                        Size want = targetFor((Size) o, param.thisObject);
                        if (want != null && !want.equals(o)) {
                            XposedBridge.log(TAG + "  size fix " + o + " -> " + want);
                            param.args[0] = want;
                        }
                    } catch (Throwable t) {
                        XposedBridge.log(TAG + "size fix error: " + t);
                    }
                }
            };
            XposedBridge.hookMethod(setHal, fix);
            XposedBridge.hookMethod(setApp, fix);
            XposedBridge.log(TAG + "hook installed OK (SurfaceWrapper setters)");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! surface wrapper setter hook failed: " + t);
        }
    }

    /* ---------------- XpanMode.getSurfaceSize（可选，装不上也不影响上面两个） ---------------- */
    private void installXpanSurfaceSize(XC_LoadPackage.LoadPackageParam lp) {
        try {
            Class<?> cls = Class.forName(CLS_XPAN_MODE, false, lp.classLoader);
            Method target = modeMethod(cls, "getSurfaceSize", List.class,
                    Class.forName(CLS_DEVICE_CONFIG, false, lp.classLoader),
                    String.class, String.class, String.class, int.class);
            XposedBridge.hookMethod(target, new QualifiedHook() {
                @Override
                protected void afterQualified(MethodHookParam param) {
                    try {
                        onSurfaceSize(param);
                    } catch (Throwable t) {
                        XposedBridge.log(TAG + "afterHooked error: " + t);
                    }
                }
            });
            XposedBridge.log(TAG + "hook installed OK (getSurfaceSize)");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! getSurfaceSize hook failed: " + t);
        }
    }

    /**
     * 算出这个面该用哪个尺寸；**返回 null 表示不要动**。
     * 预览 → previewSize()（2304x1048，可被 debug.xpan.preview 覆盖）
     * 拍照 → captureSize()（默认 8192x3024 高像素；调试 prop debug.xpan.capture 可覆盖）
     *   app/HAL 两侧同尺寸 —— 大图档就是让 HAL 出 8192x3024 的放大流
     *   （2026-10-10 实测：HAL 收下 >4096 宽的流，内部从 12.5MP 读出放大，连拍稳定）。
     *   曾试过"app 侧大图 + HAL 侧真读出、借 APS mfll/upscale 放大"的 C 路线：
     *   本机 APS 没有 xpan 的放大算法组件，algo 配置开启后 APS 初始化卡死、快门被禁
     *   （isApsCaptureAlgoInitializing=true, not allow take picture），已判死，勿再试。
     * 认不出来 / 是视频、缩略图、metadata 等 → 原样不动
     */
    private static Size targetFor(Size s, Object wrapper) {
        try {
            if (!xpanSizeScope() || s == null || !"rear_main".equals(cameraTypeOf(wrapper))) return null;
            String usage = surfaceUsage(wrapper);
            boolean isPreview = "surface_key_preview".equals(usage)
                    || "surface_key_preview_frame".equals(usage);
            boolean isPicture = "surface_key_picture".equals(usage);
            if (!isPreview && !isPicture) return null;
            Method getter = wrapper.getClass().getMethod("getFormat");
            if (getter.getReturnType() != int.class || Modifier.isStatic(getter.getModifiers())) return null;
            int format = (Integer) getter.invoke(wrapper);
            if (!safeC16Format(isPreview, format)) return null;
            String forced = isPreview ? prop("debug.xpan.preview") : prop("debug.xpan.capture");
            Size want = (forced != null) ? parseSize(forced)
                    : (isPreview ? previewSize() : captureSize());
            if (want == null) return null;
            return want.equals(s) ? null : want;
        } catch (Throwable t) {
            XposedBridge.log(TAG + "targetFor failed: " + t);
            return null;
        }
    }

    private static boolean xpanSizeScope() {
        Boolean building = sXpanBuilding.get();
        return building == null ? sXpanActive : building;
    }

    private static boolean safeC16Format(boolean preview, int format) {
        return format == ImageFormat.YUV_420_888 || (!preview
                && (format == ImageFormat.JPEG || format == ImageFormat.HEIC));
    }

    /** 取 SurfaceWrapper.getSurfaceUsage()；拿不到返回 null（表示"说不清"） */
    private static String surfaceUsage(Object wrapper) {
        if (wrapper == null) return null;
        try {
            Method m = wrapper.getClass().getMethod("getSurfaceUsage");
            Object u = m.invoke(wrapper);
            return (u instanceof String) ? (String) u : null;
        } catch (Throwable t) {
            return null;
        }
    }

    /**
     * XpanMode.getSurfaceSize 的兜底修正。
     * 注意：保留原列表结构，只替换里面的尺寸 —— 之前直接 new 一个单元素列表，
     * 万一 X-Pan 需要多流就会把其余流丢掉。
     */
    @SuppressWarnings({"unchecked", "rawtypes"})
    private void onSurfaceSize(XC_MethodHook.MethodHookParam param) {
        Object[] a = param.args;
        String surfaceType = (a != null && a.length > 1 && a[1] instanceof String) ? (String) a[1] : null;
        String cameraType = (a != null && a.length > 2 && a[2] instanceof String) ? (String) a[2] : null;
        Object orig = param.getResult();

        dlog("getSurfaceSize surfaceType=" + surfaceType
                + " cameraType=" + cameraType + " orig=" + describe(orig));

        if (!xpanSizeScope() || param.hasThrowable() || !(orig instanceof List)
                || !"rear_main".equals(cameraType) || a.length != 5 || !"rear_main".equals(a[3])) return;
        List<Object> list = (List<Object>) orig;
        if (list.isEmpty()) return;
        boolean preview = "preview".equals(surfaceType) || "preview_frame".equals(surfaceType);
        if (!preview && !"capture".equals(surfaceType) && !"capture_yuv".equals(surfaceType)) return;
        try {
            Method formatMethod = sC16SurfaceFormat;
            if (formatMethod == null) return;
            int format = (Integer) formatMethod.invoke(param.thisObject, surfaceType, cameraType, a[3]);
            if (!safeC16Format(preview, format)) return;
        } catch (Throwable failure) { return; }
        Size want = preview ? previewSize() : captureSize();
        if (want == null) return;

        List<Object> out = new ArrayList<>(list.size());
        boolean changed = false;
        for (Object o : list) {
            if (o instanceof Pair) {
                Pair p = (Pair) o;
                if (!want.equals(p.first) || !want.equals(p.second)) changed = true;
                out.add(new Pair<>(want, want));
            } else {
                out.add(o);
            }
        }
        if (changed) {
            param.setResult(out);
            dlog("  -> override [" + surfaceType + "] = " + want);
        }
    }

    private static String describe(Object o) {
        if (o == null) return "null";
        if (o instanceof List) {
            StringBuilder sb = new StringBuilder("[");
            for (Object x : (List<?>) o) sb.append(x).append(' ');
            return sb.append(']').toString();
        }
        return String.valueOf(o);
    }

    /** X-Pan 预览尺寸（实机验证过的组合，可用 debug.xpan.preview 覆盖） */
    private static Size previewSize() {
        if (sPreviewSize != null) return sPreviewSize;
        String p = prop("debug.xpan.preview");
        Size s = parseSize(p != null ? p : DEFAULT_PREVIEW);
        if (s == null) s = new Size(2304, 1048);
        sPreviewSize = s;
        XposedBridge.log(TAG + "previewSize resolved = " + s);
        return s;
    }

    /**
     * X-Pan 拍照尺寸，优先级：debug.xpan.capture > 默认档 > DEFAULT_CAPTURE。
     * v1.3 起默认 = BIG_CAPTURE（8192x3024，24.8MP 算法放大）；
     * 想看 6.2MP 真实像素档只走 prop：setprop debug.xpan.capture 4096x1512。
     * 有缓存，改动后重启相机进程生效（与 prop 行为一致）。
     */
    private static Size captureSize() {
        if (sCaptureSize != null) return sCaptureSize;
        String p = prop("debug.xpan.capture");
        Size s = parseSize(p);
        // ★ 默认 8192x3024（24.8MP 高像素档）。想看 6.2MP 真实像素档（无重影）只走调试 prop：
        //   setprop debug.xpan.capture 4096x1512，改完重启相机。
        if (s == null) s = parseSize(BIG_CAPTURE);
        if (s == null) s = new Size(8192, 3024);
        sCaptureSize = s;
        XposedBridge.log(TAG + "captureSize resolved = " + s);
        return s;
    }

    /** 解析调试用宽×高；读取失败时由调用方使用已定义的默认档。 */
    private static Size parseSize(String s) {
        try {
            String[] p = s.trim().toLowerCase().split("[x*]");
            if (p.length != 2) return null;
            return new Size(Integer.parseInt(p[0].trim()), Integer.parseInt(p[1].trim()));
        } catch (Throwable t) {
            return null;
        }
    }

    /** 读系统属性（SystemProperties 是隐藏类，走反射） */
    private static String prop(String key) {
        try {
            Class<?> sp = Class.forName("android.os.SystemProperties");
            Method get = sp.getDeclaredMethod("get", String.class, String.class);
            get.setAccessible(true);
            Object v = get.invoke(null, key, "");
            if (v instanceof String && !((String) v).isEmpty()) return (String) v;
        } catch (Throwable ignored) { }
        return null;
    }

    /** 本机后摄 HAL 支持的 YUV 输出尺寸（缓存，仅用于日志/排查） */
    @SuppressWarnings("unused")
    private static Size[] availableSizes() {
        if (sAvail != null) return sAvail;
        try {
            Context ctx = currentContext();
            if (ctx == null) return null;
            CameraManager cm = (CameraManager) ctx.getSystemService(Context.CAMERA_SERVICE);
            if (cm == null) return null;
            for (String id : cm.getCameraIdList()) {
                CameraCharacteristics cc = cm.getCameraCharacteristics(id);
                Integer facing = cc.get(CameraCharacteristics.LENS_FACING);
                if (facing == null || facing != CameraCharacteristics.LENS_FACING_BACK) continue;
                StreamConfigurationMap map = cc.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);
                if (map == null) continue;
                Size[] sizes = map.getOutputSizes(ImageFormat.YUV_420_888);
                if (sizes != null && sizes.length > 0) {
                    sAvail = sizes;
                    StringBuilder sb = new StringBuilder();
                    for (Size x : sizes) sb.append(x).append(' ');
                    XposedBridge.log(TAG + "available sizes(cam " + id + "): " + sb);
                    return sizes;
                }
            }
        } catch (Throwable t) {
            XposedBridge.log(TAG + "availableSizes failed: " + t);
        }
        return null;
    }

    /** 相机进程里拿一个 Context（ActivityThread.currentApplication 优先） */
    private static Context currentContext() {
        try {
            Class<?> at = Class.forName("android.app.ActivityThread");
            Object app = null;
            try {
                Method m = at.getDeclaredMethod("currentApplication");
                m.setAccessible(true);
                app = m.invoke(null);
            } catch (Throwable ignored) { }
            if (app instanceof Context) return (Context) app;
            Method cur = at.getDeclaredMethod("currentActivityThread");
            cur.setAccessible(true);
            Object thread = cur.invoke(null);
            if (thread != null) {
                Method gc = at.getDeclaredMethod("getSystemContext");
                gc.setAccessible(true);
                Object c = gc.invoke(thread);
                if (c instanceof Context) return (Context) c;
            }
        } catch (Throwable t) {
            XposedBridge.log(TAG + "currentContext failed: " + t);
        }
        return null;
    }

}
