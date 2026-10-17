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
 *      预览起不来（快门消失）、模式状态卡死。用 buildStreamSurface 的 thisObject 判断模式。
 *   2) 只改"能确认是预览/拍照"的面。usage 认不出来就**一律不动** ——
 *      曾经默认按预览处理，结果把 4096x3072/4096x1512 的拍照面强改成 2304x1048，
 *      比例从 4:3 / 65:24 变成 2.2:1，画面就不对了。
 *   3) 某个 hook 装失败不能影响其他 hook —— 之前 getSurfaceSize 找不到就 return，
 *      把后面真正管用的模式门和尺寸修正全跳过了。
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
     *  所以 X-Pan 的 0.6× 拍出来是 2.2:1（横向视野仍是完整 16mm 超广角），
     *  1×/2× 主摄不受影响，仍是 4096x1512 的 65:24。可用 debug.xpan.uwcap 覆盖。 */
    private static final String DEFAULT_UW_CAPTURE = "2780x1264";

    /** ★ v1.3-b：HAL 侧拍照流尺寸。
     *  实测（2026-10-16 定论）：HAL 的变焦裁剪**只对宽度 ≤4096 的流生效** ——
     *  8192 宽的流超出传感器活动阵列(4096x3072)，HAL 走"放大"通路、直接不理变焦矩形。
     *  所以要让 28/35/48/85mm 这些焦段**真裁切**，HAL 就必须收 4096x1512 的流。
     *  出片文件大小另由"应用侧尺寸"决定（见 captureSize()）：APS 把 4096x1512 的
     *  HAL 缓冲放大到 8192x3024 —— 这正是系统"高像素"模式 appSurfaceSize 8192x6144 /
     *  halSurfaceSize 4096x3072 的同一套机制（日志实锤）。
     *  两者合起来 = 真裁切 + 24.8MP 文件。可用 debug.xpan.halcap 覆盖。 */
    private static final String DEFAULT_HAL_CAPTURE = "4096x1512";

    /** X-Pan 变焦档（倍率；24mm 主摄 = 1×）。
     *  1 / 1.1667 / 1.4583 / 2 倍 —— 后两个就是 **28mm / 35mm 的等效倍率**
     *  （1.1667 ≈ 28/24、1.4583 ≈ 35/24），倍率档全部实测可用（真裁切）。
     *  变焦条按应用原样显示 ×（不改成 mm）。
     *  ⚠️ 85mm（3.5417×）暂时做不了：这个模式在应用内部有 **2.0× 硬上限**
     *  （实测请求 2.5 / 3.5417 都被钳回 2.0，钳制点在 z1.m 内部、比 fh.g.f 更早），
     *  要突破得再挖那处钳制。变焦条那个 List<Float> 就是这些值。
     *  可用 debug.xpan.zooms 覆盖（逗号分隔的倍率，注意 2.0 以上无效）。 */
    private static final float[] XPAN_ZOOM_POINTS = {1.0f, 1.1667f, 1.4583f, 2.0f};

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

    /** 当前是否正在为 X-Pan 模式构建流（只有这时才改尺寸） */
    private static volatile boolean sXpanActive = false;

    /** X-Pan 会话内最近的变焦请求值（fh.g.f 钳制钩子记录；进模式时复位 1.0）。
     *  ★ v1.2 实测：拍照流 8192 宽超出传感器活动阵列(4096x3072)时，HAL 不理变焦裁剪
     *  —— 2× 预览正常(预览流 2304 在阵列内)但拍出来只有 ~1.12× 视野(CamX currentZoom
     *  实锤，无 2.0)。v1.3 起 2× 不再回退拍照流（构图反正相同，统一出大图），
     *  此字段仅作诊断记录，不再参与尺寸决策。 */
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
    /** HAL 侧拍照流尺寸（见 DEFAULT_HAL_CAPTURE 注释） */
    private static volatile Size sHalCaptureSize = null;

    /** ★ 出片档位：true = 4096x1512（6.2MP 小文件档），false = 8192x3024（24.8MP）。
     *  null = 还没读过（首次从 /sdcard/DCIM/.xpan_smallpic 文件读）。
     *  界面上的切换按钮直接改它（并同步那个文件，重启相机后仍保持）。
     *  注意：HAL 流尺寸在会话建立时就定了，改档位不会立刻换流 —— 所以出片尺寸由
     *  存盘后处理（postProcessInPlace）兜底，点一下下张照片就生效。 */
    private static volatile Boolean sSmallPic = null;

    private static boolean smallPicMode() {
        Boolean b = sSmallPic;
        if (b == null) {
            b = smallPicEnabled();
            sSmallPic = b;
        }
        return b;
    }

    private static void setSmallPicMode(boolean on) {
        sSmallPic = on;
        sCaptureSize = null;            // 尺寸缓存失效：下次建流按新档位
        try {
            java.io.File f = new java.io.File("/storage/emulated/0/DCIM/.xpan_smallpic");
            if (on) {
                if (!f.exists()) f.createNewFile();
            } else if (f.exists()) {
                f.delete();
            }
        } catch (Throwable t) {
            XposedBridge.log(TAG + "写 .xpan_smallpic 失败: " + t);
        }
    }

    private static int targetWidth() {
        return smallPicMode() ? 4096 : 8192;
    }

    private static int targetHeight() {
        return smallPicMode() ? 1512 : 3024;
    }
    private static volatile Size[] sAvail = null;

    @Override
    public void handleLoadPackage(XC_LoadPackage.LoadPackageParam lp) throws Throwable {
        if (!PKG.equals(lp.packageName)) return;
        XposedBridge.log(TAG + "handleLoadPackage pkg=" + lp.packageName
                + " proc=" + lp.processName + " first=" + lp.isFirstApplication);

        // 每个 hook 各自 try/catch：任何一个装不上都不许影响其他（边界 3）
        installModeGate(lp);
        installHalSizeFix(lp);
        installSurfaceSetters(lp);
        installXpanSurfaceSize(lp);
        installTakePictureProbe(lp);
        installXpanUiRestore(lp);
        installXpanZoomUnlock(lp);
        installXpanZoomPointFix(lp);
        installXpanZoomLabelFix(lp);
        installXpanShotZoomWatch(lp);
        installWatermarkCapture(lp);   // 捕获水印参数，裁剪后重画
        installToneEnable(lp);          // 影调引擎开关（哈苏色彩/影调）
        installXpanFeatureInject(lp);
        installXpanSwitchFix(lp);
        installXpanPanelFix(lp);

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
            installToneDiag(lp);      // 影调（BasicTone）链路诊断：只打日志，不改行为
            // 看 FeatureFactory 到底为 X-Pan 模式请求了哪些 feature name
            // （工厂按 featureName.equals("com.oplus.camera.feature.xpan") 才创建 XPanPresenter）
            try {
                Class<?> ff = Class.forName("q7.d0", false, lp.classLoader);
                for (Method m : ff.getDeclaredMethods()) {
                    if (!"b".equals(m.getName()) || m.getParameterTypes().length != 5) continue;
                    XposedBridge.hookMethod(m, new XC_MethodHook() {
                        @Override
                        protected void beforeHookedMethod(MethodHookParam param) {
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
                    XposedBridge.hookMethod(m, new XC_MethodHook() {
                        @Override
                        protected void beforeHookedMethod(MethodHookParam param) {
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
                    XposedBridge.hookMethod(ctor, new XC_MethodHook() {
                        @Override
                        protected void afterHookedMethod(MethodHookParam param) {
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
                XposedBridge.hookMethod(m, new XC_MethodHook() {
                    @Override
                    protected void beforeHookedMethod(MethodHookParam param) {
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
            XposedBridge.hookMethod(set, new XC_MethodHook() {
                @Override
                protected void beforeHookedMethod(MethodHookParam param) {
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
            XposedBridge.hookMethod(build, new XC_MethodHook() {
                @Override
                protected void afterHookedMethod(MethodHookParam param) {
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
            XposedBridge.hookMethod(setEnabled, new XC_MethodHook() {
                @Override
                protected void beforeHookedMethod(MethodHookParam param) {
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
            XC_MethodHook h = new XC_MethodHook() {
                @Override
                protected void beforeHookedMethod(MethodHookParam param) {
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
            XC_MethodHook wake = new XC_MethodHook() {
                @Override
                protected void beforeHookedMethod(MethodHookParam param) {
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
            XposedBridge.hookMethod(hc, new XC_MethodHook() {
                @Override
                protected void beforeHookedMethod(MethodHookParam param) {
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
            XposedBridge.hookMethod(vb, new XC_MethodHook() {
                @Override
                protected void afterHookedMethod(MethodHookParam param) {
                    XposedBridge.log(TAG + "ch.b.b() xpan ui version = " + param.getResult());
                }
            });
            Class<?> xc = Class.forName("zl.q", false, lp.classLoader);
            for (java.lang.reflect.Constructor<?> ctor : xc.getDeclaredConstructors()) {
                XposedBridge.hookMethod(ctor, new XC_MethodHook() {
                    @Override
                    protected void afterHookedMethod(MethodHookParam param) {
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
            XposedBridge.hookMethod(m, new XC_MethodHook() {
                @Override
                protected void afterHookedMethod(MethodHookParam param) {
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
                XposedBridge.hookMethod(m, new XC_MethodHook() {
                    @Override
                    protected void beforeHookedMethod(MethodHookParam param) {
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
                XposedBridge.hookMethod(m, new XC_MethodHook() {
                    @Override
                    protected void beforeHookedMethod(MethodHookParam param) {
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
                    XposedBridge.hookMethod(m, new XC_MethodHook() {
                        @Override
                        protected void afterHookedMethod(MethodHookParam param) {
                            try {
                                XposedBridge.log(TAG + "zoom 请求 fh.g.h(" + param.args[0]
                                        + ") -> 模型 g=" + field(param.thisObject, "g"));
                            } catch (Throwable ignored) {
                            }
                        }
                    });
                    n++;
                } else if ("f".equals(m.getName())) {
                    XposedBridge.hookMethod(m, new XC_MethodHook() {
                        @Override
                        protected void afterHookedMethod(MethodHookParam param) {
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
                XposedBridge.hookMethod(m, new XC_MethodHook() {
                    @Override
                    protected void afterHookedMethod(MethodHookParam param) {
                        try {
                            // 记录真正下发 HAL 的变焦值：b0(arg) 把模型值写进自身 e0 再下发，
                            // after 里 e0 已是新值（比 UI 钳制函数可靠：点按/滑动/复位都走这）
                            Object e0 = field(param.thisObject, "e0");
                            if (sXpanActive && e0 instanceof Number) {
                                float z = ((Number) e0).floatValue();
                                if (z >= 1.0f) sXpanZoomReq = z;
                            }
                            XposedBridge.log(TAG + "zoom 下发 HAL gh.z.b0 e0=" + e0);
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
    private static final float XPAN_MAX_ZOOM = 4.0f;

    private void installXpanZoomUnlock(XC_LoadPackage.LoadPackageParam lp) {
        try {
            Class<?> c = Class.forName("fh.g", false, lp.classLoader);
            int n = 0;
            for (Method m : c.getDeclaredMethods()) {
                Class<?>[] pt = m.getParameterTypes();
                if (!"f".equals(m.getName()) || pt.length != 1 || !float.class.equals(pt[0])) continue;
                XposedBridge.hookMethod(m, new XC_MethodHook() {
                    @Override
                    protected void afterHookedMethod(MethodHookParam param) {
                    try {
                        if (!sXpanActive) return;
                        if (param.getResult() == null) return;
                        float want = (Float) param.args[0];
                        if (want >= 1.0f) sXpanZoomReq = want;   // 记录当前变焦档（大图回退判断用）
                        if (want < 1.0f) return;                 // 0.6× / 坏档位 0.0 都保持原样
                            float got = (Float) param.getResult();
                            if (got >= want) return;                 // 没被钳制，不用管
                            float fixed = Math.min(want, XPAN_MAX_ZOOM);
                            param.setResult(fixed);
                            if ("1".equals(prop("debug.xpan.diag"))) {
                                XposedBridge.log(TAG + "xpan zoom 解锁 " + want + " -> " + fixed);
                            }
                        } catch (Throwable ignored) {
                        }
                    }
                });
                n++;
            }
            XposedBridge.log(TAG + "hook installed OK (xpan zoom unlock x" + n + ")");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! xpan zoom unlock failed: " + t);
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
     * 做法：在档位列表交给变焦条之前，把不要的档位剔掉 ——
     *   1) ≤0 的非法档位（DUAL=0.0 那个渲染成 "0" 的按钮）；
     *   2) <1.0 的档位 = 0.6× 超广角，按用户要求删掉（2026-10-06）：本机超广角的 X-Pan 成像
     *      通路没被 OPPO 调过，出片绿帧/噪声不可靠，X-Pan 只留 1×/2×。
     *   只动值列表、不动 IconType 顺序 —— 按钮文字取的是"值"，
     *   所以剔掉后是 1 | 2，点按映射也不会错位。
     */
    private void installXpanZoomPointFix(XC_LoadPackage.LoadPackageParam lp) {
        try {
            Class<?> c = Class.forName("com.oplus.camera.feature.zoom.view.ZoomSeekBar",
                    false, lp.classLoader);
            int n = 0;
            for (Method m : c.getDeclaredMethods()) {
                if (!"R8".equals(m.getName()) || m.getParameterTypes().length != 18) continue;
                XposedBridge.hookMethod(m, new XC_MethodHook() {
                    @Override
                    protected void beforeHookedMethod(MethodHookParam param) {
                        try {
                            if (!sXpanActive || param.args == null) return;
                            // 注：实测 R8 每次 X-Pan 会话只被调 6 次左右，不是热路径，
                            // 所以这里老实扫一遍参数即可 —— 曾经想"缓存参数下标"提速，
                            // 结果参数顺序不稳定、下标指错列表，0.6× 又冒出来了。
                            for (Object a : param.args) {
                                if (!(a instanceof List)) continue;
                                List<?> list = (List<?>) a;
                                if (looksLikeZoomPointList(list)) applyXpanZoomPoints(list);
                            }
                        } catch (Throwable ignored) {
                        }
                    }
                });
                n++;
            }
            XposedBridge.log(TAG + "hook installed OK (xpan zoom points x" + n + ")");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! xpan zoom point fix failed: " + t);
        }
    }

    /** 这个 List 是不是"变焦档位表"（元素全是 Float）——R8 里还有别的 ArrayList 参数，
     *  靠"元素类型"识别，避免误改 IconType 列表之类的。 */
    private static boolean looksLikeZoomPointList(List<?> list) {
        if (list == null || list.isEmpty()) return false;
        for (Object o : list) {
            if (!(o instanceof Float)) return false;
        }
        return true;
    }

    /**
     * 把 X-Pan 的变焦档位表换成我们要的焦段（24/28/35/48/85mm）。
     * 实测档位表的元素是 Float（倍率），按顺序对应 IconType[ULTRA_WIDE,WIDE,DUAL,TELE,MORE]；
     * 应用自己那份是 [0.6, 1.0, 0.0, 2.0]（0.6 超广角本机成像不可靠、0.0 是坏档位）。
     * 这里整体替换：值变了、数量也变了（4→5），档位文字跟值走。
     * 可用 debug.xpan.zooms=1,2,3 覆盖。
     */
    private static void applyXpanZoomPoints(List<?> list) {
        float[] pts = xpanZoomPoints();
        if (list.size() == pts.length) {
            boolean same = true;
            for (int i = 0; i < pts.length; i++) {
                Object o = list.get(i);
                if (!(o instanceof Float) || Math.abs((Float) o - pts[i]) > 1e-4f) { same = false; break; }
            }
            if (same) return;               // 幂等：已经是我们要的档位就不动
        }
        @SuppressWarnings("unchecked")
        List<Object> l = (List<Object>) list;
        l.clear();
        for (float f : pts) l.add(f);
        XposedBridge.log(TAG + "xpan 变焦档位 = " + describe(list));
    }

    /** X-Pan 的焦段倍率表：debug.xpan.zooms（逗号分隔）优先，否则用内置 XPAN_ZOOM_POINTS */
    private static float[] xpanZoomPoints() {
        String p = prop("debug.xpan.zooms");
        if (p != null && !p.isEmpty()) {
            String[] parts = p.split(",");
            float[] out = new float[parts.length];
            int n = 0;
            for (String s : parts) {
                try {
                    float f = Float.parseFloat(s.trim());
                    if (f >= 1.0f) out[n++] = f;
                } catch (Throwable ignored) {
                }
            }
            if (n > 0) {
                float[] r = new float[n];
                System.arraycopy(out, 0, r, 0, n);
                return r;
            }
        }
        return XPAN_ZOOM_POINTS;
    }

    /* ---------------- 变焦档位的文字：应用是"截断"，我们改成四舍五入 ----------------
     * 反编译实锤：档位文字由 ZoomSeekBar.K8(IconType, LinkedHashMap) 生成，
     * 值取自 map.get(iconType)，最后交给 im.m0.b(F) 格式化 —— 那个函数是**截断**的，
     * 所以 1.1667 会显示成 "1.1"、1.4583 显示成 "1.4"（之前的 34.9mm 也是同一原因）。
     * 这里只改变焦条的档位文字（K8 的返回值），不动全局格式化函数：
     * 1.0 → "1x"，其余四舍五入到 1 位小数、整数不带小数（1.1667→1.2、1.4583→1.5、2.0→2）。
     */
    private void installXpanZoomLabelFix(XC_LoadPackage.LoadPackageParam lp) {
        try {
            Class<?> c = Class.forName("com.oplus.camera.feature.zoom.view.ZoomSeekBar",
                    false, lp.classLoader);
            int n = 0;
            for (Method m : c.getDeclaredMethods()) {
                if (!"K8".equals(m.getName()) || m.getParameterTypes().length != 2) continue;
                XposedBridge.hookMethod(m, new XC_MethodHook() {
                    @Override
                    protected void afterHookedMethod(MethodHookParam param) {
                        try {
                            if (!sXpanActive || param.args == null) return;
                            Object map = param.args[1];
                            if (!(map instanceof java.util.Map)) return;
                            Object v = ((java.util.Map<?, ?>) map).get(param.args[0]);
                            if (!(v instanceof Float)) return;
                            param.setResult(fmtZoomRatio((Float) v));
                        } catch (Throwable ignored) {
                        }
                    }
                });
                n++;
            }
            XposedBridge.log(TAG + "hook installed OK (xpan zoom label x" + n + ")");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! xpan zoom label fix failed: " + t);
        }
    }

    /** 变焦档位文字：1× 写 "1x"，其余四舍五入到 1 位小数、整数不带小数 */
    private static String fmtZoomRatio(float f) {
        if (Math.abs(f - 1.0f) < 1e-3f) return "1x";
        float r = Math.round(f * 10f) / 10f;
        if (Math.abs(r - Math.round(r)) < 1e-3f) return String.valueOf(Math.round(r));
        return String.valueOf(r);
    }

    /* ---------------- 影调引擎开关（哈苏色彩/影调，BasicTone） ----------------
     * 调研 + 实测实锤（2026-10-17）：
     *   影调链的总闸门是 com.oplus.natural.tone.support —— 判定在 wh.t2.K1()
     *   （needBasicToneLutTexture）与 wh.t2.c7()（开时返回 0x4），LUT 由
     *   Lut3dDataManager 从 /odm/etc/camera/basictone/{lmt,odt,vig} 加载，
     *   预览用 GL 的 uBasicToneLut3dTexture 应用。
     *   ★ 本机这个键在任何配置文件里都不存在（一加13 也没有）→ 应用取默认 false
     *     → 影调链全程不生效。这是"缺哈苏影调"的直接原因。
     *   ★ 实测：把它写进 /odm/etc/camera/config/oplus_camera_config **无效** ——
     *     把同文件里另一个被查询的键（watermark.guide.support）从 0 改成 1，
     *     应用照样读 false（见 12_验证/），说明该文件不是这些开关的数据源。
     *   ★ APS 侧那两条 tag（aps.basictone.version / aps.params.basictone.use.8bit）
     *     更不能加：本机 APS 的 JSON 解析器直接 abort（tombstone 实锤，
     *     libAlgoProcess.so APSJsonParser::parseJsonFileParams ← camApsInitEnv），
     *     即本机 APS 不认识它们（硬件/版本差异，与已判死的路线同性质）。
     *   → 唯一可行口子：应用侧把这个键的取值接过来（CameraConfig.b 是它的唯一查询口）。
     * 开关：debug.xpan.tone（默认开；置 0 即回到原厂行为，需重启相机）。
     */
    private static final String TONE_KEY = "com.oplus.natural.tone.support";

    private void installToneEnable(XC_LoadPackage.LoadPackageParam lp) {
        // ★ 用户 2026-10-17 要求"先改回去"：默认**不开**影调（保持原厂行为）。
        //   想再试：setprop debug.xpan.tone 1 后重启相机（照片/人像等模式生效，X-Pan 吃不到）。
        if (!"1".equals(prop("debug.xpan.tone"))) {
            XposedBridge.log(TAG + "note: 影调保持原厂（要开：debug.xpan.tone=1 + 重启相机）");
            return;
        }
        try {
            Class<?> cc = Class.forName("com.oplus.camera.configure.CameraConfig",
                    false, lp.classLoader);
            int n = 0;
            for (Method m : cc.getDeclaredMethods()) {
                Class<?>[] pt = m.getParameterTypes();
                if (!"b".equals(m.getName()) || pt.length != 2
                        || !pt[0].equals(String.class) || !pt[1].equals(boolean.class)) continue;
                XposedBridge.hookMethod(m, new XC_MethodHook() {
                    @Override
                    protected void afterHookedMethod(MethodHookParam param) {
                        try {
                            if (!TONE_KEY.equals(param.args[0])) return;
                            if (Boolean.TRUE.equals(param.getResult())) return;
                            param.setResult(Boolean.TRUE);
                            if ("1".equals(prop("debug.xpan.diag"))) {
                                XposedBridge.log(TAG + "影调开关：natural.tone.support -> true（已接管）");
                            }
                        } catch (Throwable ignored) {
                        }
                    }
                });
                n++;
            }
            XposedBridge.log(TAG + "hook installed OK (影调开关接管 x" + n + ")");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! 影调开关接管失败: " + t);
        }

        // ⚠️ 2026-10-17 实测后**撤掉**了对 u6.o0.D() 的接管（X-Pan 的档位判定）：
        //   接管后 X-Pan 出片与不接管**毫无差异**（同场景各 2 张，亮度差 -0.54、
        //   对比度 +0.58、暗部 0.44 vs 0.46）—— 说明 X-Pan 的成像链走它自己的 APS 通路，
        //   应用侧判定打开也没用；而预览反而可能变化（截到亮度 -19），会造成
        //   "预览和成片不一致"。所以 X-Pan 不加影调，保持所见即所得。
        //   （真要让 X-Pan 吃影调，得在 APS 侧开 basictone —— 本机 APS 解析器
        //    不接受那两个 tag：加进去 camApsInitEnv 直接 abort，见 12_验证/，已判死。）
    }

    /* ---------------- Phase 0 诊断：影调（BasicTone）链路 ----------------
     * 背景（2026-10-17 调研实锤）：影调与滤镜是两条独立通道 —— 影调走 basictone
     *   （/odm/etc/camera/basictone/{lmt,odt,setting,vig}，Lut3dDataManager 加载，
     *   预览 GL 用 uBasicToneLut3dTexture 应用）；开关判定在 wh.t2.K1()
     *   （读 com.oplus.natural.tone.support，默认 false）与 wh.t2.c7()（开时返回 0x4）。
     * 本钩子只打日志，不改行为。setprop debug.xpan.diag 1 后重启相机生效。
     * 目的：看清"应用到底查了哪些影调键、拿到什么值、决策是什么"，再决定加哪条配置。
     */
    private static volatile Object sToneK1Last = null;
    private static volatile Object sToneC7Last = null;

    private void installToneDiag(XC_LoadPackage.LoadPackageParam lp) {
        // 1) CameraConfig.b(String, boolean)：只记影调/哈苏相关键的查询与结果
        try {
            Class<?> cc = Class.forName("com.oplus.camera.configure.CameraConfig", false, lp.classLoader);
            int n = 0;
            for (Method m : cc.getDeclaredMethods()) {
                Class<?>[] pt = m.getParameterTypes();
                if (!"b".equals(m.getName()) || pt.length != 2
                        || !pt[0].equals(String.class) || !pt[1].equals(boolean.class)) continue;
                XposedBridge.hookMethod(m, new XC_MethodHook() {
                    @Override
                    protected void afterHookedMethod(MethodHookParam param) {
                        try {
                            String k = String.valueOf(param.args[0]).toLowerCase();
                            if (!k.contains("tone") && !k.contains("hasselblad")
                                    && !k.contains("basictone")) return;
                            XposedBridge.log(TAG + "影调配置查询 " + param.args[0]
                                    + " -> " + param.getResult() + "（默认 " + param.args[1] + "）");
                        } catch (Throwable ignored) {
                        }
                    }
                });
                n++;
            }
            XposedBridge.log(TAG + "hook installed OK (tone config query x" + n + ")");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! tone config query failed: " + t);
        }

        // 2) wh.t2.K1()Z / c7()I：影调决策（只在值变化时打日志，避免刷屏）
        try {
            Class<?> c = Class.forName("wh.t2", false, lp.classLoader);
            int n = 0;
            for (Method m : c.getDeclaredMethods()) {
                if (m.getParameterTypes().length != 0) continue;
                if ("K1".equals(m.getName())) {
                    XposedBridge.hookMethod(m, new XC_MethodHook() {
                        @Override
                        protected void afterHookedMethod(MethodHookParam param) {
                            try {
                                Object r = param.getResult();
                                if (sToneK1Last != null && sToneK1Last.equals(r)) return;
                                sToneK1Last = r;
                                XposedBridge.log(TAG + "影调决策 K1(needBasicToneLutTexture) = " + r
                                        + " | mode=" + param.thisObject.getClass().getName());
                            } catch (Throwable ignored) {
                            }
                        }
                    });
                    n++;
                } else if ("c7".equals(m.getName())) {
                    XposedBridge.hookMethod(m, new XC_MethodHook() {
                        @Override
                        protected void afterHookedMethod(MethodHookParam param) {
                            try {
                                Object r = param.getResult();
                                if (sToneC7Last != null && sToneC7Last.equals(r)) return;
                                sToneC7Last = r;
                                XposedBridge.log(TAG + "影调决策 c7() = " + r
                                        + " | mode=" + param.thisObject.getClass().getName());
                            } catch (Throwable ignored) {
                            }
                        }
                    });
                    n++;
                }
            }
            XposedBridge.log(TAG + "hook installed OK (tone decision x" + n + ")");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! tone decision failed: " + t);
        }

        // 3) Lut3dDataManager：影调 LUT 环境是否初始化（构造 + 含 initializeBasicToneEnv 日志的方法）
        try {
            Class<?> c = Class.forName("com.oplus.camera.common.utils.Lut3dDataManager",
                    false, lp.classLoader);
            for (java.lang.reflect.Constructor<?> ctor : c.getDeclaredConstructors()) {
                XposedBridge.hookMethod(ctor, new XC_MethodHook() {
                    @Override
                    protected void afterHookedMethod(MethodHookParam param) {
                        XposedBridge.log(TAG + "影调：Lut3dDataManager 已实例化");
                    }
                });
            }
            int n = 0;
            for (Method m : c.getDeclaredMethods()) {
                Class<?>[] pt = m.getParameterTypes();
                if (pt.length != 2 || !pt[0].equals(int.class) || !pt[1].equals(int.class)) continue;
                XposedBridge.hookMethod(m, new XC_MethodHook() {
                    @Override
                    protected void afterHookedMethod(MethodHookParam param) {
                        XposedBridge.log(TAG + "影调：Lut3dDataManager.e(" + param.args[0] + ","
                                + param.args[1] + ") = " + param.getResult());
                    }
                });
                n++;
            }
            XposedBridge.log(TAG + "hook installed OK (tone lut manager x" + n + ")");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! tone lut manager failed: " + t);
        }
    }

    /* ---------------- 实验：把 feature.xpan 注入功能生成列表 ----------------
     * 背景（2026-10-06）：显影动画/双皮肤的总闸门 —— FeatureFactory.b 只有在收到
     *   功能名 "com.oplus.camera.feature.xpan" 时才 new bh.c(XPanPresenter)，
     *   bh.c 构造里又因 ch.b.c()==true 而 new dh.w(XPanViewManagerV3)。
     *   本机 mode=xpan 的功能表里没有这个名字 → 两者从未被创建 → 动画/皮肤/双皮肤全休眠。
     *
     * 做法（hook 路线，不动配置——配置手术已失败 4 次）：
     *   1) getFeatureInfoList(I) 返回的 Set 无条件加进 "com.oplus.camera.feature.xpan"。
     *      这个 Set 只被 x0.e(generateFeatures) 拿去逐个调 FeatureFactory.b，
     *      不影响模式菜单（那走 hasConfigFeature 查 protobuf，不查这个 Set）。
     *   2) FeatureFactory.b 加闸：featureName==xpan 且 modeName!="xpan" 时返回 null
     *      （调用方 i0.accept 对 null 有判空），保证其他模式不受影响。
     *
     * 开关：默认开启（显影动画/双皮肤/专属 UI 都靠它）。setprop debug.xpan.feat 0 重启相机可关。
     * 已知代价：0.6×（超广角）出流会被 presenter 按设备能力改成 2.2:1 —— 已随"删掉 0.6× 档"一并规避，
     * 1×/2× 主摄保持 4096x1512 的 65:24。
     * 验证口径：日志出现 "FeatureFactory feature=com.oplus.camera.feature.xpan | mode=xpan"
     *   和 "bh.c <init>" / "dh.w <init>"，且相机不崩、X-Pan 仍能正常拍照。
     */
    private static final String FEAT_XPAN = "com.oplus.camera.feature.xpan";

    private void installXpanFeatureInject(XC_LoadPackage.LoadPackageParam lp) {
        // 1) 功能名 Set 注入
        try {
            Class<?> ad = Class.forName(
                    "com.oplus.ocs.camera.config.FeatureConfigureAdapter", false, lp.classLoader);
            Method m = ad.getDeclaredMethod("getFeatureInfoList", int.class);
            XposedBridge.hookMethod(m, new XC_MethodHook() {
                @Override
                protected void afterHookedMethod(MethodHookParam param) {
                    try {
                        if ("0".equals(prop("debug.xpan.feat"))) return;   // 默认开，置 0 关
                        Object r = param.getResult();
                        if (r instanceof java.util.Set) {
                            @SuppressWarnings("unchecked")
                            java.util.Set<Object> s = (java.util.Set<Object>) r;
                            s.add(FEAT_XPAN);
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
            XposedBridge.hookMethod(b, new XC_MethodHook() {
                @Override
                protected void afterHookedMethod(MethodHookParam param) {
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
            XposedBridge.log(TAG + "hook installed OK (xpan feature gate)");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! xpan feature gate failed: " + t);
        }
        // 3) 见证：两个目标类是否真的被创建（仅诊断开关打开时打日志，避免常态开销）
        try {
            for (final String cn : new String[]{"bh.c", "dh.w"}) {
                Class<?> c = Class.forName(cn, false, lp.classLoader);
                for (java.lang.reflect.Constructor<?> ctor : c.getDeclaredConstructors()) {
                    XposedBridge.hookMethod(ctor, new XC_MethodHook() {
                        @Override
                        protected void afterHookedMethod(MethodHookParam param) {
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
            XposedBridge.hookMethod(bss, new XC_MethodHook() {
                @Override
                protected void afterHookedMethod(MethodHookParam param) {
                    try {
                        Object thiz = param.thisObject;
                        if (thiz == null || !CLS_XPAN_MODE.equals(thiz.getClass().getName())) return;
                        android.os.Handler h = new android.os.Handler(android.os.Looper.getMainLooper());
                        h.postDelayed(new Runnable() {
                            @Override
                            public void run() {
                                driveXpanContainer();
                            }
                        }, 2000);
                        h.postDelayed(new Runnable() {
                            @Override
                            public void run() {
                                driveXpanContainer();
                            }
                        }, 4500);   // 兜底第二次
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
            XposedBridge.hookMethod(c1, new XC_MethodHook() {
                @Override
                protected void afterHookedMethod(MethodHookParam param) {
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
            XposedBridge.hookMethod(xa, new XC_MethodHook() {
                @Override
                protected void afterHookedMethod(MethodHookParam param) {
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
                XposedBridge.hookMethod(m, new XC_MethodHook() {
                    @Override
                    protected void beforeHookedMethod(MethodHookParam param) {
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
            XposedBridge.hookMethod(ot, new XC_MethodHook() {
                @Override
                protected void beforeHookedMethod(MethodHookParam param) {
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
            XposedBridge.hookMethod(a, new XC_MethodHook() {
                @Override
                protected void beforeHookedMethod(MethodHookParam param) {
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
                XposedBridge.hookMethod(m, new XC_MethodHook() {
                    @Override
                    protected void afterHookedMethod(MethodHookParam param) {
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
                XposedBridge.hookMethod(m, new XC_MethodHook() {
                    @Override
                    protected void beforeHookedMethod(MethodHookParam param) {
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
            XposedBridge.hookMethod(fc, new XC_MethodHook() {
                @Override
                protected void afterHookedMethod(MethodHookParam param) {
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

    /* ---------------- 【已停用】退出清理：靠"功能表重建"信号 ----------------
     * ⚠️ 2026-10-07 实测：这段是"往返切换后界面卡死（除滤镜外全不能点）"的元凶 ——
     *    它在模式名≠xpan 时调 fc() 拆掉 X-Pan 视图，而切换过程中该回调会在"正在回 X-Pan"
     *    的时刻被触发，把刚建好的界面拆了 → 卡死。A/B 对照：加 debug.xpan.noexit=1 跳过它，
     *    往返 3/3 全部正常；不跳过则频繁卡死。故整段停用（函数保留以便日后改造）。
     * 代价：退出 X-Pan 后可能有少量视图残留（原先要修的重叠问题），但那比卡死轻得多。
     * ---------------------------------------------------------------------
     * 实测退出走"轮盘切模式"时：buildStreamSurface 不重跑（模式门不翻转）、fc() 也不调，
     * 于是 X-Pan 的皮肤/面板留在照片模式上重叠。
     * 但模式切换时应用一定会重建功能表：q7.x0.e(Activity, modeName, ...)，modeName 就是新模式名。
     * 只要 modeName 不是 "xpan"，就说明离开了 X-Pan → 延迟卸掉容器视图与面板。
     */
    private void installXpanExitCleanup(XC_LoadPackage.LoadPackageParam lp) {
        try {
            Class<?> x0 = Class.forName("q7.x0", false, lp.classLoader);
            for (Method m : x0.getDeclaredMethods()) {
                if (!"e".equals(m.getName()) || m.getParameterTypes().length != 7) continue;
                XposedBridge.hookMethod(m, new XC_MethodHook() {
                    @Override
                    protected void beforeHookedMethod(MethodHookParam param) {
                        try {
                            // 调试开关：置 1 就完全不清理（用来对照"卡死是否由退出清理引起"）
                            if ("1".equals(prop("debug.xpan.noexit"))) return;
                            String mode = String.valueOf(param.args[1]);
                            if ("xpan".equals(mode)) return;      // 还在 X-Pan
                            if (!sXpanActive) return;             // 本来就不在
                            sXpanActive = false;                  // 模式门没翻转也要清，这里直接置位
                            sDriveDone = true;                    // 别让 xa() 兜底再把它建回来
                            android.os.Handler h = new android.os.Handler(
                                    android.os.Looper.getMainLooper());
                            Runnable r = new Runnable() {
                                @Override
                                public void run() {
                                    cleanupXpanContainer();
                                }
                            };
                            h.postDelayed(r, 300);
                            h.postDelayed(r, 1200);
                            XposedBridge.log(TAG + "离开 X-Pan（新模式=" + mode + "）→ 触发界面清理");
                        } catch (Throwable ignored) {
                        }
                    }
                });
            }
            XposedBridge.log(TAG + "hook installed OK (xpan exit cleanup)");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! xpan exit cleanup failed: " + t);
        }
    }

    /* ---------------- 模式门：判断当前在不在 X-Pan ---------------- */
    private void installModeGate(XC_LoadPackage.LoadPackageParam lp) {
        try {
            Class<?> bm = Class.forName(CLS_BASE_MODE, false, lp.classLoader);
            Class<?> cfg = Class.forName(CLS_DEVICE_CONFIG, false, lp.classLoader);
            // 真实签名（反编译 BaseMode 得到）：buildStreamSurface(SdkCameraDeviceConfig, String) -> LinkedList
            // 之前多写了两个 String 和一个 int → NoSuchMethodException → 标志位永不置位 → 整个覆盖失效
            Method bss = bm.getDeclaredMethod("buildStreamSurface", cfg, String.class);
            XposedBridge.hookMethod(bss, new XC_MethodHook() {
                @Override
                protected void beforeHookedMethod(MethodHookParam param) {
                    try {
                        Object thiz = param.thisObject;
                        boolean isXpan = thiz != null && CLS_XPAN_MODE.equals(thiz.getClass().getName());
                        if (sXpanActive != isXpan) {
                            sXpanActive = isXpan;
                            if (isXpan) sXpanZoomReq = 1.0f;     // 进模式复位变焦档
                            XposedBridge.log(TAG + "xpan stream building = " + isXpan);
                            if (isXpan) {
                                // 进入 X-Pan：复位会话级状态 + 快速补建（滤镜由 wireXpanFilter 负责接回）
                                sDriveDone = false;
                                sDroveThisSession = false;
                                sDriveFails = 0;
                                android.os.Handler h = new android.os.Handler(
                                        android.os.Looper.getMainLooper());
                                h.postDelayed(new Runnable() {
                                    @Override
                                    public void run() {
                                        driveXpanContainer();
                                    }
                                }, 600);
                                h.postDelayed(new Runnable() {
                                    @Override
                                    public void run() {
                                        driveXpanContainer();
                                    }
                                }, 1800);
                                // 自愈：清掉相册里那些指向已删文件的空图（应用此时已完全起来）
                                new Thread(new Runnable() {
                                    @Override
                                    public void run() {
                                        cleanupZombieMediaRows();
                                    }
                                }, "xpan-zombie-clean").start();
                            } else {
                                // 退出 X-Pan：卸掉可能残留的容器视图 + 尺寸按钮，防止与其他模式界面重叠
                                android.os.Handler h = new android.os.Handler(
                                        android.os.Looper.getMainLooper());
                                h.postDelayed(new Runnable() {
                                    @Override
                                    public void run() {
                                        cleanupXpanContainer();
                                    }
                                }, 300);
                                h.postDelayed(new Runnable() {
                                    @Override
                                    public void run() {
                                        cleanupXpanContainer();
                                    }
                                }, 1200);
                            }
                        }
                    } catch (Throwable t) {
                        XposedBridge.log(TAG + "mode flag error: " + t);
                    }
                }
            });
            XposedBridge.log(TAG + "hook installed OK (buildStreamSurface mode gate)");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! buildStreamSurface hook failed: " + t);
        }
    }

    /* ---------------- 真正的修复点：getHalSurfaceSize ---------------- */
    private void installHalSizeFix(XC_LoadPackage.LoadPackageParam lp) {
        try {
            Class<?> sw = Class.forName(CLS_SURFACE_WRAPPER, false, lp.classLoader);
            Method getHal = sw.getDeclaredMethod("getHalSurfaceSize");
            // ImageReader 就是按这个 getter 建的；它在 mHalSurfaceSize==null 时直接回退成 App 尺寸，
            // 所以只改 setHalSurfaceSize 没用，必须拦 getter。
            XposedBridge.hookMethod(getHal, new XC_MethodHook() {
                @Override
                protected void afterHookedMethod(MethodHookParam param) {
                    try {
                        Object r = param.getResult();
                        if (!(r instanceof Size)) return;
                        Size want = targetFor((Size) r, param.thisObject);
                        if (want != null && !want.equals(r)) {
                            XposedBridge.log(TAG + "  halSize fix " + r + " -> " + want);
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
            // 两侧要分别给：HAL 侧小（变焦才裁）、应用侧大（出片才大）
            XposedBridge.hookMethod(setHal, sizeSetterHook(false));
            XposedBridge.hookMethod(setApp, sizeSetterHook(true));
            XposedBridge.log(TAG + "hook installed OK (SurfaceWrapper setters)");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! surface wrapper setter hook failed: " + t);
        }
    }

    private static XC_MethodHook sizeSetterHook(final boolean appSide) {
        return new XC_MethodHook() {
            @Override
            protected void beforeHookedMethod(MethodHookParam param) {
                try {
                    if (param.args == null || param.args.length < 1) return;
                    Object o = param.args[0];
                    if (!(o instanceof Size)) return;
                    Size want = targetFor((Size) o, param.thisObject, appSide);
                    if (want != null && !want.equals(o)) {
                        XposedBridge.log(TAG + "  size fix (" + (appSide ? "app" : "hal")
                                + ") " + o + " -> " + want);
                        param.args[0] = want;
                    }
                } catch (Throwable t) {
                    XposedBridge.log(TAG + "size fix error: " + t);
                }
            }
        };
    }

    /* ---------------- XpanMode.getSurfaceSize（可选，装不上也不影响上面两个） ---------------- */
    private void installXpanSurfaceSize(XC_LoadPackage.LoadPackageParam lp) {
        try {
            Class<?> cls = Class.forName(CLS_XPAN_MODE, false, lp.classLoader);
            Method target = null;
            for (Method m : cls.getDeclaredMethods()) {
                if ("getSurfaceSize".equals(m.getName()) && m.getParameterTypes().length == 5) {
                    target = m;
                    break;
                }
            }
            if (target == null) {
                XposedBridge.log(TAG + "note: XpanMode.getSurfaceSize(5) not found, skip（不影响尺寸修正）");
                return;
            }
            XposedBridge.hookMethod(target, new XC_MethodHook() {
                @Override
                protected void afterHookedMethod(MethodHookParam param) {
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
     * 拍照 → 分两侧（v1.3-b 起）：
     *   HAL 侧 = halCaptureSize()（默认 4096x1512）—— 只有 ≤4096 宽的流 HAL 才执行变焦裁剪，
     *     所以焦段真裁切靠它；
     *   应用侧 = captureSize()（默认 8192x3024）—— 决定出片文件大小，APS 负责放大。
     *   建 .xpan_smallpic 或 debug.xpan.split=0 → 两侧同尺寸（回 v1.2 行为）。
     * 认不出来 / 是视频、缩略图、metadata 等 → 原样不动
     */
    private static Size targetFor(Size s, Object wrapper) {
        return targetFor(s, wrapper, false);
    }

    /** @param appSide true = 应用侧（出片大小）；false = HAL 侧（流尺寸，决定变焦能否裁剪） */
    private static Size targetFor(Size s, Object wrapper, boolean appSide) {
        try {
            if (!sXpanActive) return null;                 // 边界 1：非 X-Pan 模式一律不动
            if (s == null) return null;

            String usage = surfaceUsage(wrapper);
            if (usage == null) return null;                // 认不出来 → 不动
            String u = usage.toLowerCase();

            boolean isPreview = u.contains("preview");
            boolean isPicture = u.contains("picture") || u.contains("snapshot")
                    || u.contains("capture") || u.contains("jpeg") || u.contains("blob");
            if (!isPreview && !isPicture) return null;     // 视频/缩略图/metadata 等 → 不动

            String forced = isPreview ? prop("debug.xpan.preview") : prop("debug.xpan.capture");
            Size want = (forced != null) ? parseSize(forced)
                    : (isPreview ? previewSize()
                                 : (appSide ? captureSize() : halCaptureSize()));
            if (want == null) return null;

            // v1.3：变焦不再回退拍照流。2× 与 1× 构图相同（HAL 对 65:24 流不裁剪，
            //   2026-10-16 定论），既然视野一样，2× 也出 24.8MP 大图（用户选的 C 方案）。
            //   勿再试注入裁剪矩形：4:3 矩形崩 HAL，65:24 矩形出图退 4:3（都实测过）。
            // v1.3-b：改走"HAL 小流 + 应用侧大图"后构图恢复正常（HAL 在 4096 流上会裁），
            //   上面那段"构图相同"的历史结论仅适用于两侧同为大流的旧配置。

            // 超广角的 HAL 流必须用它自己支持的宽度（imx355 流表最大 3200/3216），
            // 否则绿帧/噪声；JPEG 输出仍由应用侧的 4096x1512 负责（APS 缩放）。
            if (!isPreview && isUltraWide(cameraTypeOf(wrapper))) {
                String uw = prop("debug.xpan.uwcap");
                if (uw == null || uw.isEmpty()) uw = DEFAULT_UW_CAPTURE;
                Size uwSize = parseSize(uw);
                if (uwSize != null) want = uwSize;
            }
            return want.equals(s) ? null : want;
        } catch (Throwable t) {
            XposedBridge.log(TAG + "targetFor failed: " + t);
            return null;
        }
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
     * v1.3-b：拍照面按 (HAL 尺寸, 应用尺寸) 分开给 —— 实测 Pair 语义就是
     *   (halSurfaceSize, appSurfaceSize)，出片大小跟第二个元素走。
     */
    @SuppressWarnings({"unchecked", "rawtypes"})
    private void onSurfaceSize(XC_MethodHook.MethodHookParam param) {
        Object[] a = param.args;
        String surfaceType = (a != null && a.length > 1 && a[1] instanceof String) ? (String) a[1] : null;
        String cameraType = (a != null && a.length > 2 && a[2] instanceof String) ? (String) a[2] : null;
        Object orig = param.getResult();

        XposedBridge.log(TAG + "getSurfaceSize surfaceType=" + surfaceType
                + " cameraType=" + cameraType + " orig=" + describe(orig));

        if (!sXpanActive || surfaceType == null || !(orig instanceof List)) return;
        List<Object> list = (List<Object>) orig;
        if (list.isEmpty()) return;

        String st = surfaceType.toLowerCase();
        Size halWant;
        Size appWant;
        if (st.contains("preview")) {
            halWant = previewSize();
            appWant = halWant;
        } else if (st.startsWith("capture") || st.equals("raw_output") || st.equals("raw12_output")) {
            boolean uw = isUltraWide(cameraType);
            halWant = uw ? parseSize(prop("debug.xpan.uwcap") != null
                    ? prop("debug.xpan.uwcap") : DEFAULT_UW_CAPTURE) : halCaptureSize();
            if (halWant == null) halWant = halCaptureSize();
            appWant = captureSize();
            if (uw) appWant = halWant;      // 超广角不拆：两侧都按它自己的流尺寸
        } else {
            return;
        }
        if (halWant == null || appWant == null) return;

        List<Object> out = new ArrayList<>(list.size());
        boolean changed = false;
        for (Object o : list) {
            if (o instanceof Pair) {
                Pair p = (Pair) o;
                if (!halWant.equals(p.first) || !appWant.equals(p.second)) changed = true;
                out.add(new Pair<>(halWant, appWant));
            } else {
                out.add(o);
            }
        }
        if (changed) {
            param.setResult(out);
            XposedBridge.log(TAG + "  -> override [" + surfaceType + "] hal=" + halWant
                    + " app=" + appWant);
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
     * 想回 6.2MP 小文件档：新建 /sdcard/DCIM/.xpan_smallpic（空文件）并重启相机。
     * 有缓存，改动后重启相机进程生效（与 prop 行为一致）。
     */
    private static Size captureSize() {
        if (sCaptureSize != null) return sCaptureSize;
        String p = prop("debug.xpan.capture");
        Size s = parseSize(p);
        if (s == null && !smallPicMode()) {
            s = parseSize(BIG_CAPTURE);
            XposedBridge.log(TAG + "默认大图档: " + BIG_CAPTURE + "（算法放大，细节不增加；"
                    + "建 /sdcard/DCIM/.xpan_smallpic 可回 6.2MP）");
        }
        if (s == null) s = parseSize(DEFAULT_CAPTURE);
        if (s == null) s = new Size(4096, 1512);
        sCaptureSize = s;
        XposedBridge.log(TAG + "captureSize resolved = " + s + "（档位="
                + (smallPicMode() ? "4096 小图" : "8192 大图") + "）");
        return s;
    }

    /**
     * ★ v1.3 退出开关：/sdcard/DCIM/.xpan_smallpic（空文件）存在 → 回 6.2MP（4096x1512）。
     * 默认（无文件）= 8192x3024 大图档。改完重启相机生效。
     * 注意大图档是"算法放大"：像素 ×4、细节不变（本机真细节上限 6.2MP，
     * 见 12_验证/分辨率实验结论.md）。DCIM 为主选，相机的 Android/media 目录做备选
     * （个别系统对 DCIM 的 File API 收紧时仍可用）。
     */
    private static boolean smallPicEnabled() {
        try {
            if (new File("/storage/emulated/0/DCIM/.xpan_smallpic").exists()) return true;
            return new File("/storage/emulated/0/Android/media/com.oneplus.camera/.xpan_smallpic").exists();
        } catch (Throwable t) {
            return false;
        }
    }

    /**
     * ★ HAL 侧拍照流尺寸（默认 4096x1512）。
     * 只有 ≤4096 宽的流 HAL 才执行变焦裁剪 → 这是"焦段真裁切"的前提。
     * debug.xpan.halcap 可覆盖；debug.xpan.split=0 或 .xpan_smallpic 存在时退回 captureSize()
     * （即 v1.2 的"两侧同尺寸"行为）。
     */
    private static Size halCaptureSize() {
        if (sHalCaptureSize != null) return sHalCaptureSize;
        Size s;
        if (!splitEnabled()) {
            s = captureSize();
        } else {
            s = parseSize(prop("debug.xpan.halcap"));
            if (s == null) s = parseSize(DEFAULT_HAL_CAPTURE);
            if (s == null) s = new Size(4096, 1512);
        }
        sHalCaptureSize = s;
        XposedBridge.log(TAG + "halCaptureSize resolved = " + s
                + (splitEnabled() ? "（HAL 小流：变焦裁剪才生效）" : "（split 关闭，与出片同尺寸）"));
        return s;
    }

    /** 是否启用"HAL 小流 + 应用侧大图"拆分。
     *  ⚠️ 2026-10-17 实测结论：**这个方向没用** —— 把 HAL 流改成 4096x1512 后
     *  应用照样下发 zoomRatio=1.4583，出图构图仍与 1× 相同（NCC 0.99），
     *  说明 HAL 对 X-Pan 的 65:24 拍照流**无论宽窄都不裁**（真正原因是应用同时下发
     *  固定的 com.oplus.opx.CropInfo=[0,780,4096,1512]，HAL 只认它）。
     *  所以默认关闭，留着 debug.xpan.split=1 供以后复验。 */
    private static boolean splitEnabled() {
        return "1".equals(prop("debug.xpan.split"));
    }

    /* ---------------- 软件裁切：X-Pan 的"真焦段"就靠这里 ----------------
     * 背景（2026-10-17 实锤，两次独立验证）：
     *   HAL 对 X-Pan 的 65:24 拍照流**不执行变焦裁剪** —— 应用确实下发了
     *   android.control.zoomRatio=1.4583（takePicture 探针），但同一个请求里还带着
     *   固定的 com.oplus.opx.CropInfo=[0,780,4096,1512]，HAL 只认后者。
     *   流宽 4096 / 8192 都试过，构图都不变；改 CropInfo 会让应用退回 4:3（有校验）。
     *   → 想要"28/35/85mm 真的是那个视野"，只能在成片上自己裁。
     * 做法：拍照时记下变焦值；监听 DCIM/Camera 新文件；按中心 1/z 裁剪，
     *   再放大回原尺寸（保住"高像素"档的文件大小），重新编码写回原文件。
     * 代价（如实写在 README）：裁完的真实细节 = 6.2MP / z²，放大是插值。
     * 开关：debug.xpan.swcrop=0 关（默认开）。
     */
    private static volatile float sShotZoom = 1.0f;
    private static volatile long sShotAt = 0L;
    /** 裁完是否还要放大回原尺寸（关掉就是"裁多大存多大"的真实像素档） */
    private static final int SOFT_CROP_DECODE_WIDTH = 4096;   // 源图真细节宽度上限

    private void installXpanShotZoomWatch(XC_LoadPackage.LoadPackageParam lp) {
        try {
            Class<?> c = Class.forName("com.oplus.ocs.camera.producer.device.Camera2Impl",
                    false, lp.classLoader);
            int n = 0;
            for (Method m : c.getDeclaredMethods()) {
                if (!"takePicture".equals(m.getName()) || m.getParameterTypes().length != 5) continue;
                XposedBridge.hookMethod(m, new XC_MethodHook() {
                    @Override
                    protected void beforeHookedMethod(MethodHookParam param) {
                        try {
                            if (!sXpanActive) return;
                            Object z = field(param.args[0], "mZoomRatio");
                            if (z instanceof Number) sShotZoom = ((Number) z).floatValue();
                            sShotAt = android.os.SystemClock.elapsedRealtime();
                            // 每次都起观察线程：要不要处理由 postProcess 自己判断
                            // （1× 且原生尺寸已符合档位时它会直接返回，不动原文件）
                            if (!"0".equals(prop("debug.xpan.swcrop"))) {
                                final java.io.File dir = new java.io.File(DCIM_CAMERA);
                                final java.util.Set<String> before = listNames(dir);
                                final float z2 = sShotZoom;
                                new Thread(new Runnable() {
                                    @Override
                                    public void run() {
                                        watchNewFile(dir, before, z2);
                                    }
                                }, "xpan-softcrop").start();
                            }
                        } catch (Throwable ignored) {
                        }
                    }
                });
                n++;
            }
            XposedBridge.log(TAG + "hook installed OK (shot zoom watch x" + n + ")");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! shot zoom watch failed: " + t);
        }
    }

    private static final String DCIM_CAMERA = "/storage/emulated/0/DCIM/Camera";

    private static java.util.Set<String> listNames(java.io.File d) {
        java.util.Set<String> s = new java.util.HashSet<>();
        try {
            String[] a = d.list();
            if (a != null) {
                for (String x : a) s.add(x);
            }
        } catch (Throwable ignored) {
        }
        return s;
    }

    /** 轮询等新照片落盘（FileObserver 在 /sdcard 这种 FUSE 路径上收不到事件，实测无效），
     *  等文件大小连续两次不变（= 写完了）再动手。 */
    private static void watchNewFile(java.io.File dir, java.util.Set<String> before, float zoom) {
        try {
            for (int i = 0; i < 60; i++) {          // 最多约 30 秒
                Thread.sleep(500);
                String[] a = dir.list();
                if (a == null) continue;
                for (String n : a) {
                    if (before.contains(n)) continue;
                    String low = n.toLowerCase();
                    if (!low.endsWith(".heic") && !low.endsWith(".jpg")
                            && !low.endsWith(".jpeg")) continue;
                    java.io.File f = new java.io.File(dir, n);
                    long s1 = f.length();
                    if (s1 < 20000) continue;
                    Thread.sleep(800);
                    if (f.length() != s1) continue;  // 还在写，下一轮再看
                    softCropInPlace(f, zoom);
                    return;
                }
            }
            XposedBridge.log(TAG + "softcrop: 没等到新文件（zoom=" + zoom + "）");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "watchNewFile failed: " + t);
        }
    }

    /* ---------------- 裁剪后重画哈苏水印 ----------------
     * 背景：水印由应用在存图前**烘焙进画面**，位置在画面最底部（约 93%~99%）。
     *   我们的软件裁剪是中心裁，1.167× 时保留 [7.1%, 92.9%] —— 正好把整条水印裁掉。
     *   （试过用应用的 WatermarkProcessUnit 重放，结果位图是 null：它依赖应用自己的
     *    GL/IPU 上下文，新建/复用实例都试过 → 判死，别再走这条。）
     * 做法：**反解白色水印的 alpha，再用 alpha 合成**。应用是"白色半透明字"叠在画面上
     *   （px = base*(1-a) + 255*a），所以估出背景亮度 base 就能反解 a，再用同一个 a
     *   叠到成片底部 → 字的浓淡/清晰度与原生一致。
     * ★ 关键坑（踩过）：估 base 的窗口半径**必须明显大于笔画宽度** —— 8192 宽下笔画约
     *   40px，一开始用 5px 等于把字自己当背景扣掉 → 又淡又糊。现在用 ~90px。
     * ★ 只对"底部细文字水印"（哈苏 X-Pan 款）做：换其他样式（卡片/边框/带底色）时
     *   白色水印模型不成立，抠出来会乱会糊（用户实测反馈）。按参数里的
     *   mFinalJpegBottomHeight（水印占画面高度比例）判断，超过 12% 就跳过。
     * 开关：debug.xpan.rewatermark=0 关。
     */
    private static volatile Object sWmInitParam = null;
    private static volatile Object sWmProcessParam = null;
    private static volatile Object sWmConfig = null;
    /** 应用实际调用的方法与单元类 —— 必须复用它们（`Class.forName` 用模块的 ClassLoader
     *  会 ClassNotFoundException：这个类在应用的 dex 里，不在我们包里）。 */
    private static volatile java.lang.reflect.Method sWmInitMethod = null;
    private static volatile java.lang.reflect.Method sWmProcMethod = null;
    private static volatile Class<?> sWmUnitCls = null;
    private static volatile Object sWmUnitInst = null;

    /** 捕获水印参数：应用每张照片都会走
     *  `WatermarkProcessUnit.init(WatermarkInitParameter, Bitmap)` 与
     *  `processImage(WatermarkProcessParameter, ProcessConfig)`，
     *  在这里把两个参数对象和"应用实际调用的 Method/类"存下来，
     *  裁剪后就能用同样的参数把水印重画到成片上。 */
    private void installWatermarkCapture(XC_LoadPackage.LoadPackageParam lp) {
        try {
            Class<?> c = Class.forName(
                    "com.oplus.ocs.camera.ipuapi.process.watermark.WatermarkProcessUnit",
                    false, lp.classLoader);
            int n = 0;
            for (Method m : c.getDeclaredMethods()) {
                String name = m.getName();
                Class<?>[] pt = m.getParameterTypes();
                if ("init".equals(name) && pt.length == 2
                        && pt[1].equals(android.graphics.Bitmap.class)) {
                    XposedBridge.hookMethod(m, new XC_MethodHook() {
                        @Override
                        protected void beforeHookedMethod(MethodHookParam param) {
                            sWmInitParam = param.args[0];
                            sWmInitMethod = (java.lang.reflect.Method) param.method;
                            if (param.thisObject != null) {
                                sWmUnitCls = param.thisObject.getClass();
                                sWmUnitInst = param.thisObject;
                            }
                        }
                    });
                    n++;
                } else if ("processImage".equals(name) && pt.length == 2
                        && pt[1].getName().endsWith("ProcessConfig")) {
                    XposedBridge.hookMethod(m, new XC_MethodHook() {
                        @Override
                        protected void beforeHookedMethod(MethodHookParam param) {
                            sWmProcessParam = param.args[0];
                            sWmConfig = param.args[1];
                            sWmProcMethod = (java.lang.reflect.Method) param.method;
                            if (param.thisObject != null) {
                                sWmUnitCls = param.thisObject.getClass();
                                sWmUnitInst = param.thisObject;
                            }
                        }
                    });
                    n++;
                }
            }
            XposedBridge.log(TAG + "hook installed OK (水印参数捕获 x" + n + ")");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! 水印参数捕获失败: " + t);
        }
    }

    private static void stampWatermark(android.graphics.Bitmap dst, String origPath, int origW, int origH) {
        if ("0".equals(prop("debug.xpan.rewatermark"))) return;
        Object procParam = sWmProcessParam;
        if (procParam == null) {
            XposedBridge.log(TAG + "贴水印跳过：还没捕获到水印参数（本会话没走过水印链？）");
            return;
        }
        float wmH = readFloat(procParam, "mFinalJpegBottomHeight");
        int wmType = readInt(procParam, "mWatermarkType");
        if ("1".equals(prop("debug.xpan.diag"))) {
            XposedBridge.log(TAG + "水印参数：type=" + wmType + " bottomHeight=" + wmH);
        }
        if (wmH > 0.12f) {
            XposedBridge.log(TAG + "水印样式不是底部细文字（height=" + wmH + "）→ 跳过，保持原样");
            return;
        }
        try {
            final int W = dst.getWidth(), H = dst.getHeight();
            int bandH = Math.round(origH * 0.16f);        // 底部 16%：水印两行都在这条带里
            if (bandH < 8 || origW < 16) return;
            android.graphics.BitmapRegionDecoder dec =
                    android.graphics.BitmapRegionDecoder.newInstance(origPath, false);
            android.graphics.Bitmap band = dec.decodeRegion(
                    new android.graphics.Rect(0, origH - bandH, origW, origH), null);
            dec.recycle();
            if (band == null) return;
            final int bw = band.getWidth(), bh = band.getHeight();
            int[] src = new int[bw * bh];
            band.getPixels(src, 0, bw, 0, 0, bw, bh);
            band.recycle();

            int[] lum = new int[bw * bh];
            for (int i = 0; i < lum.length; i++) {
                int p = src[i];
                lum[i] = (((p >> 16) & 0xFF) * 77 + ((p >> 8) & 0xFF) * 150 + (p & 0xFF) * 29) >> 8;
            }
            final int R = Math.max(24, Math.round(origW * 0.011f));   // ~90px（远大于笔画宽）
            float[] tmp = new float[lum.length];
            float[] base = new float[lum.length];
            for (int y = 0; y < bh; y++) {                 // 横向滑动均值
                int off = y * bw;
                long sum = 0;
                int lim = Math.min(bw - 1, R);
                for (int x = 0; x <= lim; x++) sum += lum[off + x];
                int cnt = lim + 1;
                for (int x = 0; x < bw; x++) {
                    tmp[off + x] = sum / (float) cnt;
                    int add = x + R + 1, rem = x - R;
                    if (add < bw) { sum += lum[off + add]; cnt++; }
                    if (rem >= 0) { sum -= lum[off + rem]; cnt--; }
                }
            }
            for (int x = 0; x < bw; x++) {                 // 纵向滑动均值
                long sum = 0;
                int lim = Math.min(bh - 1, R);
                for (int y = 0; y <= lim; y++) sum += tmp[y * bw + x];
                int cnt = lim + 1;
                for (int y = 0; y < bh; y++) {
                    base[y * bw + x] = sum / (float) cnt;
                    int add = y + R + 1, rem = y - R;
                    if (add < bh) { sum += tmp[add * bw + x]; cnt++; }
                    if (rem >= 0) { sum -= tmp[rem * bw + x]; cnt--; }
                }
            }
            float[] alpha = new float[lum.length];
            int kept = 0;
            for (int i = 0; i < lum.length; i++) {
                float b = base[i];
                float denom = 255f - b;
                if (denom < 45f) continue;                 // 背景本身很亮 → 分不出水印
                float a = (lum[i] - b) / denom;
                if (a < 0.12f) continue;
                alpha[i] = a > 1f ? 1f : a;
                kept++;
            }
            if (kept > lum.length * 12 / 100) {            // 有效像素超过 12% → 多半不是细文字水印
                XposedBridge.log(TAG + "水印形态异常（有效像素 " + kept + "/" + lum.length
                        + "）→ 跳过合成，保持原样");
                return;
            }
            float sx = W / (float) bw, sy = H / (float) origH;
            int outH = Math.round(bh * sy);
            int dstY0 = H - outH;
            int[] row = new int[W];
            for (int y = 0; y < outH; y++) {
                int oy = Math.min(bh - 1, (int) (y / sy));
                dst.getPixels(row, 0, W, 0, dstY0 + y, W, 1);
                for (int x = 0; x < W; x++) {
                    int ox = Math.min(bw - 1, (int) (x / sx));
                    float a = alpha[oy * bw + ox];
                    if (a <= 0f) continue;
                    int p = row[x];
                    int r = (int) (((p >> 16) & 0xFF) * (1 - a) + 255 * a);
                    int g = (int) (((p >> 8) & 0xFF) * (1 - a) + 255 * a);
                    int b2 = (int) ((p & 0xFF) * (1 - a) + 255 * a);
                    row[x] = 0xFF000000 | (clamp255(r) << 16) | (clamp255(g) << 8) | clamp255(b2);
                }
                dst.setPixels(row, 0, W, 0, dstY0 + y, W, 1);
            }
            XposedBridge.log(TAG + "水印已按 alpha 重合成（band " + bw + "x" + bh + " R=" + R
                    + " 有效像素 " + kept + " → y" + dstY0 + "）");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "水印合成失败（跳过）: " + t);
        }
    }

    private static float readFloat(Object o, String name) {
        try {
            Object v = field(o, name);
            return (v instanceof Number) ? ((Number) v).floatValue() : -1f;
        } catch (Throwable t) {
            return -1f;
        }
    }

    private static int readInt(Object o, String name) {
        try {
            Object v = field(o, name);
            return (v instanceof Number) ? ((Number) v).intValue() : -1;
        } catch (Throwable t) {
            return -1;
        }
    }

    /** 高质量重采样：分离式 Lanczos3（每轴 6 抽头），逐输出像素直接算。
     *  源是裁剪后的**真实像素**（≤4096 宽），目标是 8192×3024 —— 全程只有这一次插值，
     *  比双线性锐利得多，也就不需要再叠锐化（用户明确要求不要锐化）。
     *  权重归一化；索引越界按边界复制。会回收传入的源位图。 */
    private static android.graphics.Bitmap resizeLanczos3(android.graphics.Bitmap src, int tw, int th) {
        final int sw = src.getWidth(), sh = src.getHeight();
        final int T = 6;
        float[] wx = new float[tw * T];
        int[] ix = new int[tw * T];
        float[] wy = new float[th * T];
        int[] iy = new int[th * T];
        lanczosWeights(sw, tw, wx, ix);
        lanczosWeights(sh, th, wy, iy);
        int[] s = new int[sw * sh];
        src.getPixels(s, 0, sw, 0, 0, sw, sh);
        src.recycle();
        android.graphics.Bitmap dst = android.graphics.Bitmap.createBitmap(tw, th,
                android.graphics.Bitmap.Config.ARGB_8888);
        int[] row = new int[tw];
        for (int y = 0; y < th; y++) {
            final int yb = y * T;
            for (int x = 0; x < tw; x++) {
                final int xb = x * T;
                float r = 0f, g = 0f, b = 0f;
                for (int ky = 0; ky < T; ky++) {
                    final float wv = wy[yb + ky];
                    final int rowOff = iy[yb + ky] * sw;
                    for (int kx = 0; kx < T; kx++) {
                        final float w = wv * wx[xb + kx];
                        final int p = s[rowOff + ix[xb + kx]];
                        r += w * ((p >> 16) & 0xFF);
                        g += w * ((p >> 8) & 0xFF);
                        b += w * (p & 0xFF);
                    }
                }
                row[x] = 0xFF000000 | (clamp255((int) (r + 0.5f)) << 16)
                        | (clamp255((int) (g + 0.5f)) << 8) | clamp255((int) (b + 0.5f));
            }
            dst.setPixels(row, 0, tw, 0, y, tw, 1);
        }
        return dst;
    }

    /** Lanczos3 权重表：每个目标像素 6 个抽头（w[i*6+k] / idx[i*6+k]），权重归一化 */
    private static void lanczosWeights(int srcLen, int dstLen, float[] w, int[] idx) {
        final double scale = srcLen / (double) dstLen;
        final double fs = scale < 1.0 ? 1.0 / scale : 1.0;    // 放大时支撑保持 3
        for (int i = 0; i < dstLen; i++) {
            double center = (i + 0.5) * scale - 0.5;
            int left = (int) Math.floor(center - 3.0 * fs);
            double sum = 0;
            for (int k = 0; k < 6; k++) {
                int si = left + k;
                int ci = si < 0 ? 0 : (si > srcLen - 1 ? srcLen - 1 : si);
                double v = lanczosKernel((si - center) / fs);
                w[i * 6 + k] = (float) v;
                idx[i * 6 + k] = ci;
                sum += v;
            }
            if (sum != 0) {
                for (int k = 0; k < 6; k++) w[i * 6 + k] /= (float) sum;
            }
        }
    }

    private static double lanczosKernel(double x) {
        if (x == 0) return 1.0;
        double ax = Math.abs(x);
        if (ax >= 3.0) return 0.0;
        double px = Math.PI * x;
        return (Math.sin(px) / px) * (Math.sin(px / 3.0) / (px / 3.0));
    }

    /* ---------------- 画框水印样式下的裁剪（"画框重建"） ----------------
     * 应用在"X-Pan 画框"样式下出图 = 白框卡片（内嵌照片 + 四角 logo + 底部文字）。
     * 做法：① 按画框几何算出内嵌照片矩形；② 在内嵌矩形里按倍率做中心裁；
     *      ③ 用系统 API 把裁出的区域缩回内嵌尺寸；④ 贴回原图同一位置 ——
     *      白框 / logo / 文字**全部用应用官方渲染的那份像素**，我们只替换框内照片。
     *  → "画框"与"倍率"互不影响，也不需要反解水印 alpha。
     * 几何来源：官方样式文件 /data/data/com.oplus.camera/files/photo_watermark/hassel_style_xpan_1.json
     *   （baseImageSize=291、条带 height=95 → 95/291=32.6% 与实测 32.7% 吻合）。
     *   当前用实测比例兜底：卡片宽 = 内嵌宽×1.0381、卡片高 = 内嵌高×1.6534、内嵌居中。
     * 内存保护：任一步失败（含 OOM）返回 false，调用方跳过处理、保留应用原图。
     */
    private static final float FRAME_W_RATIO = 1.0381f;   // 卡片宽 / 内嵌宽
    private static final float FRAME_H_RATIO = 1.6534f;   // 卡片高 / 内嵌高

    private static boolean frameCropInPlace(java.io.File f, float z, java.io.File tmp) {
        android.graphics.Bitmap full = null, inner = null, innerFull = null, cut2 = null;
        try {
            android.graphics.BitmapFactory.Options b = new android.graphics.BitmapFactory.Options();
            b.inJustDecodeBounds = true;
            android.graphics.BitmapFactory.decodeFile(f.getAbsolutePath(), b);
            final int SW = b.outWidth, SH = b.outHeight;
            if (SW < 64 || SH < 64) return false;

            // ① 低分辨率解码 → 按几何算内嵌矩形（按轴分别除；横竖屏都成立）
            int sample = 1;
            while (SW / (sample * 2) >= 1600) sample *= 2;
            android.graphics.BitmapFactory.Options o = new android.graphics.BitmapFactory.Options();
            o.inSampleSize = sample;
            android.graphics.Bitmap small = android.graphics.BitmapFactory.decodeFile(f.getAbsolutePath(), o);
            if (small == null) return false;
            final int W0 = small.getWidth(), H0 = small.getHeight();
            int[] px = new int[W0 * H0];
            small.getPixels(px, 0, W0, 0, 0, W0, H0);
            small.recycle();

            // ① 内嵌矩形：优先按**官方样式 JSON** 的边距参数算；横竖屏各试一种轴分配，
            //    取通过"65:24 + 采样校验"的那个（竖屏不用手量）。
            android.graphics.Rect innerRect = officialInnerRect(px, W0, H0);
            if (innerRect == null) {
                // 兜底：实测比例（卡片宽 = 内嵌宽×1.0381、卡片高 = 内嵌高×1.6534、居中）
                int w = Math.round(W0 / FRAME_W_RATIO), h = Math.round(H0 / FRAME_H_RATIO);
                android.graphics.Rect r = new android.graphics.Rect((W0 - w) / 2, (H0 - h) / 2,
                        (W0 - w) / 2 + w, (H0 - h) / 2 + h);
                if (marginLooksWhite(px, W0, H0, r.left, r.top, r.width(), r.height())) innerRect = r;
            }
            if (innerRect == null) {
                XposedBridge.log(TAG + "画框几何识别失败 → 放弃（保留应用原图）");
                return false;
            }
            final int iW0 = innerRect.width(), iH0 = innerRect.height();
            final int iL0 = innerRect.left, iT0 = innerRect.top;
            XposedBridge.log(TAG + "画框内嵌识别（低分辨率）：" + iW0 + "x" + iH0 + " @ " + iL0 + "," + iT0);
            final float kx = SW / (float) W0, ky = SH / (float) H0;
            final int iL = Math.round(iL0 * kx), iT = Math.round(iT0 * ky);
            final int iW = Math.round(iW0 * kx), iH = Math.round(iH0 * ky);

            // ②③ 内嵌区域：解出来 → 中心 1/z 裁 → 系统缩放回内嵌尺寸
            int sample2 = 1;
            while (iW / (sample2 * 2) >= SOFT_CROP_DECODE_WIDTH) sample2 *= 2;
            android.graphics.BitmapFactory.Options o3 = new android.graphics.BitmapFactory.Options();
            o3.inSampleSize = sample2;
            o3.inPreferredConfig = android.graphics.Bitmap.Config.ARGB_8888;
            android.graphics.BitmapRegionDecoder rd =
                    android.graphics.BitmapRegionDecoder.newInstance(f.getAbsolutePath(), false);
            innerFull = rd.decodeRegion(new android.graphics.Rect(iL, iT, iL + iW, iT + iH), o3);
            rd.recycle();
            if (innerFull == null) return false;
            final int w2 = innerFull.getWidth(), h2 = innerFull.getHeight();
            final int cw2 = Math.max(16, Math.round(w2 / z)), ch2 = Math.max(16, Math.round(h2 / z));
            cut2 = android.graphics.Bitmap.createBitmap(innerFull, (w2 - cw2) / 2, (h2 - ch2) / 2, cw2, ch2);
            innerFull.recycle();
            innerFull = null;
            inner = android.graphics.Bitmap.createScaledBitmap(cut2, iW, iH, true);
            cut2.recycle();
            cut2 = null;
            if (inner == null) return false;

            // ④ 原图（可变）上把新内嵌画回去 —— 画框部分保持官方像素不变
            full = android.graphics.ImageDecoder.decodeBitmap(
                    android.graphics.ImageDecoder.createSource(f),
                    new android.graphics.ImageDecoder.OnHeaderDecodedListener() {
                        @Override
                        public void onHeaderDecoded(android.graphics.ImageDecoder d,
                                                    android.graphics.ImageDecoder.ImageInfo info,
                                                    android.graphics.ImageDecoder.Source src) {
                            d.setAllocator(android.graphics.ImageDecoder.ALLOCATOR_SOFTWARE);
                            d.setMutableRequired(true);
                        }
                    });
            if (full == null) return false;
            android.graphics.Paint pt = new android.graphics.Paint(android.graphics.Paint.FILTER_BITMAP_FLAG);
            new android.graphics.Canvas(full).drawBitmap(inner, iL, iT, pt);
            inner.recycle();
            inner = null;

            java.io.FileOutputStream fos = new java.io.FileOutputStream(tmp);
            boolean ok = full.compress(android.graphics.Bitmap.CompressFormat.JPEG, 95, fos);
            fos.close();
            full.recycle();
            full = null;
            if (!ok || tmp.length() < 20000) return false;
            XposedBridge.log(TAG + "画框重建完成：内嵌 " + iW + "x" + iH + "（原图 " + SW + "x" + SH
                    + "，裁切区 " + cw2 + "x" + ch2 + " @ " + iL + "," + iT + "）");
            return true;
        } catch (Throwable t) {
            XposedBridge.log(TAG + "画框重建失败（保留应用原图）: " + t);
            return false;
        } finally {
            if (inner != null) inner.recycle();
            if (innerFull != null) innerFull.recycle();
            if (cut2 != null) cut2.recycle();
            if (full != null) full.recycle();
        }
    }

    /** 按官方画框样式 JSON 的边距参数算内嵌矩形。
     *  官方 hassel_style_xpan_1.json：baseImageSize=291、left/rightMargin=15、top/bottomMargin=95
     *  （实测验算：15/291×3024=155.9 ≈ 实测 156；95/291×3024=987 ≈ 实测 988 ✓）。
     *  横竖屏各试一种轴分配（95 在垂直 / 95 在水平），取通过校验的那个 → 竖屏不用手量。 */
    private static android.graphics.Rect officialInnerRect(int[] px, int W0, int H0) {
        int base = 291, ml = 15, mr = 15, mt = 95, mb = 95;
        try {
            java.io.File f = new java.io.File(
                    "/data/data/com.oplus.camera/files/photo_watermark/hassel_style_xpan_1.json");
            if (f.exists() && f.length() < 1 << 20) {
                byte[] buf = new byte[(int) f.length()];
                java.io.FileInputStream in = new java.io.FileInputStream(f);
                int n = 0;
                while (n < buf.length) {
                    int r = in.read(buf, n, buf.length - n);
                    if (r <= 0) break;
                    n += r;
                }
                in.close();
                String js = new String(buf, 0, n, "UTF-8");
                base = jsonNum(js, "baseImageSize", base);
                // size 对象里的四个边距（注意别误取 elements 里的 leftMargin/rightMargin）
                int si = js.indexOf("\"size\"");
                if (si > 0) {
                    String seg = js.substring(si, Math.min(js.length(), si + 300));
                    ml = jsonNum(seg, "leftMargin", ml);
                    mr = jsonNum(seg, "rightMargin", mr);
                    mt = jsonNum(seg, "topMargin", mt);
                    mb = jsonNum(seg, "bottomMargin", mb);
                }
                XposedBridge.log(TAG + "官方画框参数：base=" + base + " l/r=" + ml + "/" + mr
                        + " t/b=" + mt + "/" + mb);
            }
        } catch (Throwable t) {
            XposedBridge.log(TAG + "读官方画框 JSON 失败，用内置默认参数: " + t);
        }
        android.graphics.Rect a = innerFrom(W0, H0, base, ml, mr, mt, mb);   // 横屏语义
        if (a != null && validInner(px, W0, H0, a)) return a;
        android.graphics.Rect b = innerFrom(W0, H0, base, mt, mb, ml, mr);   // 竖屏语义（轴交换）
        if (b != null && validInner(px, W0, H0, b)) return b;
        return null;
    }

    /** 由"卡片尺寸 + 官方边距参数"推内嵌矩形：内嵌高 h 满足 H0 = h×(1+(mT+mB)/base)，
     *  水平边距按 h 折算；位置用 (mL, mT) 折算出。 */
    private static android.graphics.Rect innerFrom(int W0, int H0, int base,
                                                   int mL, int mR, int mT, int mB) {
        try {
            if (base <= 0) return null;
            float h = H0 / (1f + (mT + mB) / (float) base);
            float w = W0 - (mL + mR) / (float) base * h;
            int iw = Math.round(w), ih = Math.round(h);
            int il = Math.round(mL / (float) base * h), it = Math.round(mT / (float) base * h);
            if (iw < 64 || ih < 32 || il < 0 || it < 0 || il + iw > W0 || it + ih > H0) return null;
            return new android.graphics.Rect(il, it, il + iw, it + ih);
        } catch (Throwable t) {
            return null;
        }
    }

    /** 内嵌矩形校验：比例须是 65:24（横竖都认）+ 外侧几乎全白 + 中间有内容 */
    private static boolean validInner(int[] px, int W0, int H0, android.graphics.Rect r) {
        float a = r.width() / (float) r.height();
        boolean ok65 = Math.abs(a - 65f / 24f) / (65f / 24f) <= 0.03f
                || Math.abs(a - 24f / 65f) / (24f / 65f) <= 0.03f;
        return ok65 && marginLooksWhite(px, W0, H0, r.left, r.top, r.width(), r.height());
    }

    /** 从 JSON 文本里取 "key":<number> 的整数值 */
    private static int jsonNum(String js, String key, int def) {
        try {
            int i = js.indexOf("\"" + key + "\"");
            if (i < 0) return def;
            int c = js.indexOf(':', i);
            if (c < 0) return def;
            int e = c + 1;
            while (e < js.length() && (Character.isDigit(js.charAt(e)) || js.charAt(e) == '-'
                    || js.charAt(e) == '.')) e++;
            if (e == c + 1) return def;
            return Math.round(Float.parseFloat(js.substring(c + 1, e)));
        } catch (Throwable t) {
            return def;
        }
    }

    /** 该行是否"几乎全白"（≥99.5% 像素三通道 ≥245）。画框里的 logo/文字只占极小面积，
     *  所以它们所在的行仍算"白"，不会把照片边界判错。 */
    private static boolean rowAlmostWhite(int[] px, int W, int y) {
        int base = y * W, nw = 0;
        for (int x = 0; x < W; x++) {
            int p = px[base + x];
            if (((p >> 16) & 0xFF) < 245 || ((p >> 8) & 0xFF) < 245 || (p & 0xFF) < 245) nw++;
        }
        return nw * 1000 <= W * 5;
    }

    private static boolean colAlmostWhite(int[] px, int W, int H, int x) {
        int nw = 0;
        for (int y = 0; y < H; y++) {
            int p = px[y * W + x];
            if (((p >> 16) & 0xFF) < 245 || ((p >> 8) & 0xFF) < 245 || (p & 0xFF) < 245) nw++;
        }
        return nw * 1000 <= H * 5;
    }

    /** 画框几何校验：内嵌矩形外侧应几乎全白、内侧应有内容（防几何不匹配时瞎裁）。 */
    private static boolean marginLooksWhite(int[] px, int W, int H, int iL, int iT, int iW, int iH) {
        if (iL < 2 || iT < 2 || iW < 64 || iH < 32) return false;
        if (iL + iW > W - 2 || iT + iH > H - 2) return false;
        if (!rowAlmostWhite(px, W, iT - 2) || !rowAlmostWhite(px, W, iT + iH + 1)) return false;
        if (!colAlmostWhite(px, W, H, iL - 2) || !colAlmostWhite(px, W, H, iL + iW + 1)) return false;
        return !rowAlmostWhite(px, W, iT + iH / 2);
    }

    private static int clamp255(int v) {
        return v < 0 ? 0 : (v > 255 ? 255 : v);
    }

    /** 按焦段裁切 + 按档位定尺寸 → 重编码写回原文件。
     *  两个作用：① 真焦段（HAL 不会裁，见上方说明）② 出片尺寸档位（4096 / 8192）。
     *  若两样都不需要（1× 且原生尺寸已经对），直接返回、不碰原文件（保住 HEIC/水印/EXIF）。 */
    private static void softCropInPlace(java.io.File f, float zoom) {
        android.graphics.Bitmap src = null;
        android.graphics.Bitmap cut = null;
        android.graphics.Bitmap out = null;
        java.io.File tmp = new java.io.File(f.getParentFile(), f.getName() + ".crop.tmp");
        try {
            if (!f.exists() || f.length() < 20000) return;
            android.graphics.BitmapFactory.Options b = new android.graphics.BitmapFactory.Options();
            b.inJustDecodeBounds = true;
            android.graphics.BitmapFactory.decodeFile(f.getAbsolutePath(), b);
            final int sw = b.outWidth, sh = b.outHeight;
            if (sw <= 0 || sh <= 0) return;

            final float z = Math.max(1.0f, zoom);
            final int tw = targetWidth(), th = targetHeight();

            // ★★ 画幅保护：**只有源图是 X-Pan 的 65:24 才做裁切**。
            //   实测（2026-10-17）：把水印样式换成"X-Pan 画框"款后，应用出图会变成
            //   8504x5000（1.70:1，四周是白边 + XPAN/HASSELBLAD 字样）——那不是 65:24 的照片，
            //   按 65:24 去中心裁会把画框裁进来再缩放，出图撕裂/白条/字糊（用户报的现象）。
            //   所以比例不对就**整张不处理**，保留应用原图（画框样式照样是好的）。
            float aspect = sw / (float) sh;
            if (Math.abs(aspect - 65f / 24f) / (65f / 24f) > 0.03f) {
                // 不是 65:24：多半是"X-Pan 画框"水印样式（白框卡片）→ 走画框重建：
                // 在官方画框的内嵌照片里按倍率裁、缩回内嵌尺寸、贴回原图，画框部分保持官方像素。
                if (z > 1.05f && !"0".equals(prop("debug.xpan.frame"))
                        && frameCropInPlace(f, z, tmp)) {
                    String fn = f.getName();
                    java.io.File dstF = fn.toLowerCase().endsWith(".heic")
                            ? new java.io.File(f.getParentFile(), fn.substring(0, fn.length() - 5) + ".jpg")
                            : f;
                    if (f.exists() && !f.delete()) {
                        XposedBridge.log(TAG + "画框重建：删原图失败");
                        return;
                    }
                    forgetMediaStoreEntry(f.getAbsolutePath());
                    if (!tmp.renameTo(dstF)) {
                        XposedBridge.log(TAG + "画框重建：改名失败");
                        return;
                    }
                    scanFile(dstF.getAbsolutePath());
                    XposedBridge.log(TAG + "画框重建出片 " + dstF.getName() + "（倍率 " + z + "）");
                    return;
                }
                XposedBridge.log(TAG + "画幅不是 65:24（" + sw + "x" + sh + " = " + aspect
                        + "）→ 跳过裁剪，保留应用原图");
                return;
            }
            if (z < 1.05f && sw == tw) return;      // 无需处理：原生出片已符合档位

            // 源图本身就是"算法放大"的，解到真细节那一档就够，省内存也更快
            int sample = 1;
            while (sw / (sample * 2) >= SOFT_CROP_DECODE_WIDTH) sample *= 2;
            android.graphics.BitmapFactory.Options o = new android.graphics.BitmapFactory.Options();
            o.inSampleSize = sample;
            o.inPreferredConfig = android.graphics.Bitmap.Config.ARGB_8888;
            src = android.graphics.BitmapFactory.decodeFile(f.getAbsolutePath(), o);
            if (src == null) return;

            int w = src.getWidth(), h = src.getHeight();
            int cw = Math.max(16, Math.round(w / z)), ch = Math.max(16, Math.round(h / z));
            cut = android.graphics.Bitmap.createBitmap(src, (w - cw) / 2, (h - ch) / 2, cw, ch);
            // ⚠️ createBitmap 在"裁剪区域 = 整张图"时**直接返回原对象**（createScaledBitmap
            //    同理）—— 所以必须先判同一个对象再回收，否则会把还要用的图 recycle 掉
            //    （实测报 "Can't compress a recycled bitmap"）。
            if (cut != src) src.recycle();
            src = null;

            android.graphics.Bitmap scaled;
            if (cw == tw && ch == th) {
                scaled = cut;
                cut = null;
            } else {
                // ⚠️ 自己写的 Lanczos3 重采样实测出图被拉坏（撕裂/白条，2026-10-17），
                //    已退回系统 createScaledBitmap（双线性）。要上更好的算法得单独调试验证，
                //    不能拿坏图交付。用户要求"不要锐化"已满足（USM 已移除）。
                scaled = android.graphics.Bitmap.createScaledBitmap(cut, tw, th, true);
                if (scaled != cut) cut.recycle();
                cut = null;
            }
            out = scaled;
            // 水印：原图那条在画面最底部、已被中心裁切整条裁掉 → 从原图把水印笔画抠出来贴回底部
            stampWatermark(out, f.getAbsolutePath(), sw, sh);

            boolean ok;
            java.io.FileOutputStream fos = new java.io.FileOutputStream(tmp);
            ok = out.compress(android.graphics.Bitmap.CompressFormat.JPEG, 95, fos);
            fos.close();
            out.recycle();
            out = null;

            if (!ok || tmp.length() < 20000) {
                XposedBridge.log(TAG + "softcrop 编码失败，保留原图 " + f.getName());
                return;
            }
            // 存的是 JPEG（HEIC 编码器是隐藏 API，应用进程里调不动），所以文件名也跟着改成 .jpg
            String name = f.getName();
            java.io.File dst = f;
            if (name.toLowerCase().endsWith(".heic")) {
                dst = new java.io.File(f.getParentFile(),
                        name.substring(0, name.length() - 5) + ".jpg");
            }
            if (f.exists() && !f.delete()) {
                XposedBridge.log(TAG + "softcrop 删原图失败 " + name);
                return;
            }
            // ★ 关键：原文件（.heic）在媒体库里的记录要一起删掉。
            //   不然相册里会多一张指向"已被删文件"的空图（用户看到的"0B 图片"就是它）——
            //   实测：处理过的照片如果媒体库记录先建立，就会留这么一条僵尸记录。
            forgetMediaStoreEntry(f.getAbsolutePath());
            if (!tmp.renameTo(dst)) {
                XposedBridge.log(TAG + "softcrop 改名失败 " + name);
                return;
            }
            XposedBridge.log(TAG + "softcrop OK " + dst.getName() + " zoom=" + z
                    + " " + sw + "x" + sh + " -> 裁 " + cw + "x" + ch + " -> " + tw + "x" + th);
            scanFile(dst.getAbsolutePath());
        } catch (Throwable t) {
            XposedBridge.log(TAG + "softCropInPlace failed: " + t);
        } finally {
            if (src != null) src.recycle();
            if (cut != null) cut.recycle();
            if (out != null) out.recycle();
            if (tmp.exists()) tmp.delete();
        }
    }

    /** 删掉"指向已不存在文件"的媒体库记录（原文件被我们替换掉时用）。
     *  ★ 不删的话相册里会留一张空图 —— 这就是"拍一次多一张 0B 图片"的来源：
     *  我们把 .heic 删掉改成 .jpg，媒体库里那条 .heic 记录就成了僵尸。
     *  （是否出现取决于时序：媒体库记录先建立就留僵尸，后建立就没有。） */
    private static void forgetMediaStoreEntry(String path) {
        try {
            Context ctx = currentContext();
            if (ctx == null) return;
            android.content.ContentResolver cr = ctx.getContentResolver();
            android.database.Cursor c = cr.query(
                    android.provider.MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
                    new String[]{"_id"}, "_data=?", new String[]{path}, null);
            if (c != null) {
                while (c.moveToNext()) {
                    long id = c.getLong(0);
                    int n = cr.delete(android.content.ContentUris.withAppendedId(
                            android.provider.MediaStore.Images.Media.EXTERNAL_CONTENT_URI, id),
                            null, null);
                    XposedBridge.log(TAG + "清理媒体库记录 id=" + id + "（" + n + " 行）" + path);
                }
                c.close();
            }
        } catch (Throwable t) {
            XposedBridge.log(TAG + "forgetMediaStoreEntry failed: " + t);
        }
    }

    /** 启动时自愈：清掉 DCIM/Camera 里"文件已不在"的媒体库记录（老版本留下的空图）。
     *  只动这一个目录、且只删文件确实不存在的记录，避免误伤。 */
    private void cleanupZombieMediaRows() {
        try {
            Context ctx = currentContext();
            if (ctx == null) {
                XposedBridge.log(TAG + "自愈跳过：拿不到 Context");
                return;
            }
            android.content.ContentResolver cr = ctx.getContentResolver();
            android.database.Cursor c = cr.query(
                    android.provider.MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
                    new String[]{"_id", "_data"},
                    "_data LIKE ?", new String[]{DCIM_CAMERA + "/%"}, null);
            if (c == null) return;
            java.util.List<Long> gone = new java.util.ArrayList<>();
            while (c.moveToNext()) {
                long id = c.getLong(0);
                String p = c.getString(1);
                if (p == null || p.contains(".trashed")) continue;
                if (!new java.io.File(p).exists()) gone.add(id);
            }
            c.close();
            for (long id : gone) {
                cr.delete(android.content.ContentUris.withAppendedId(
                        android.provider.MediaStore.Images.Media.EXTERNAL_CONTENT_URI, id),
                        null, null);
            }
            if (!gone.isEmpty()) {
                XposedBridge.log(TAG + "自愈：清理了 " + gone.size() + " 条失效的媒体库记录");
            }
        } catch (Throwable t) {
            XposedBridge.log(TAG + "cleanupZombieMediaRows failed: " + t);
        }
    }

    /** 改完文件通知媒体库重扫（否则图库里尺寸/缩略图还是旧的） */
    private static void scanFile(String path) {
        try {
            Context ctx = currentContext();
            if (ctx == null) return;
            android.media.MediaScannerConnection.scanFile(ctx, new String[]{path}, null, null);
        } catch (Throwable ignored) {
        }
    }

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
