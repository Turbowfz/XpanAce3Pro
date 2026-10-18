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

    /** ★ 热路径日志开关（性能）：每帧/每次请求都打的那种日志是实打实的开销
     *  （logcat 写入 + 字符串拼接，实测能刷出几十万行、明显拖慢预览）。
     *  这些一律走 dlog()，默认不打；排查时 setprop debug.xpan.diag 1 再开。 */
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
     *  所以 X-Pan 的 0.6× 拍出来是 2.2:1（横向视野仍是完整 16mm 超广角），
     *  1×/2× 主摄不受影响，仍是 4096x1512 的 65:24。可用 debug.xpan.uwcap 覆盖。 */
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

    /** 当前是否正在为 X-Pan 模式构建流（只有这时才改尺寸） */
    private static volatile boolean sXpanActive = false;

    /** X-Pan 会话内最近的变焦请求值（fh.g.f 钳制钩子记录；进模式时复位 1.0）。
     *  ★ v1.2 实测：拍照流 8192 宽超出传感器活动阵列(4096x3072)时，HAL 不理变焦裁剪
     *  —— 2× 预览正常(预览流 2304 在阵列内)但拍出来只有 ~1.12× 视野(CamX currentZoom
     *  实锤，无 2.0)。所以变焦 ≠1× 时拍照流回退 4096x1512（v1.1 验证过 2× 正常；
     *  2× 下真实细节本来就只有 ~2048 宽，不吃亏），1× 才用 8192x3024 大图。 */
    private static volatile float sXpanZoomReq = 1.0f;
    /** 进模式后的这段毫秒内忽略倍率请求（那是应用恢复上次倍率，不是用户点的） */

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
        installXpanUiRestore(lp);
        installXpanZoomUnlock(lp);
        installXpanZoomPointFix(lp);
        installXpanFeatureInject(lp);
        installXpanSwitchFix(lp);
        installXpanPanelFix(lp);
        installWatermarkIsolation(lp);
        installModeSwitchHelper(lp);
        installAlgoQueryTrace(lp);
        installRequestTraceAll(lp);
        sDiag = "1".equals(prop("debug.xpan.diag"));
        installProxdrPost(lp);

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
                                && !ln.contains("jpeg") && !ln.contains("raw")) return;
                        Object v = param.args[1];
                        String sig = name + " = " + (v instanceof android.graphics.Rect
                                ? "Rect" + v : String.valueOf(v));
                        synchronized (sProbeSeen) {
                            if (sProbeSeen.size() >= 200 || !sProbeSeen.add(sig)) return;
                        }
                        XposedBridge.log(TAG + "req.set " + sig);
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

    /** 沿继承链写字段 */
    private static void setField(Object o, String name, Object v) {
        for (Class<?> c = o.getClass(); c != null; c = c.getSuperclass()) {
            try {
                java.lang.reflect.Field f = c.getDeclaredField(name);
                f.setAccessible(true);
                f.set(o, v);
                return;
            } catch (Throwable ignored) {
            }
        }
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
    private static final float XPAN_MAX_ZOOM = 3.0f;

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
                        if (want < 1.0f) return;                 // 0.6× / 坏档位 0.0 都保持原样
                        // ★ 应用自己的倍率**钉在 1×**。为什么：放开让它变 2× 会被应用持久化，
                        //   而且 X-Pan 的变焦条点 "1×"/拖条都退不回来（实测卡在 2× 出不来）。
                        //   用户意图从**请求值**读：点 2× 当开关（在 1×/2× 之间切），点 1× 回 1×。
                        //   出片尺寸（2× 时 4096）跟的是 sXpanZoomReq，不是应用的倍率。
                        if (want >= 1.0f) sXpanZoomReq = want;   // 记录当前变焦档（拍照流尺寸/出片尺寸用）
                            float got = (Float) param.getResult();
                            if (got >= want) return;                 // 没被钳制，不用管
                            float fixed = Math.min(want, XPAN_MAX_ZOOM);
                            param.setResult(fixed);
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
                                if (hasBadZoomPoint(list)) filterBadZoomPoints(list);
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
            if (v instanceof Float && ((Float) v) < 1.0f) return true;
        }
        return false;
    }

    private static void filterBadZoomPoints(List<?> list) {
        for (int i = list.size() - 1; i >= 0; i--) {
            Object v = list.get(i);
            if (!(v instanceof Float)) continue;
            float f = (Float) v;
            if (f >= 1.0f) continue;   // 保留 1× 及以上
            list.remove(i);
            XposedBridge.log(TAG + "xpan 变焦条剔掉档位 " + v
                    + (f <= 0f ? "（非法）" : "（0.6× 超广角，按要求删除）"));
        }
    }

    /* ==================================================================================
     *  水印按模式隔离：X-Pan 的水印设置与拍照等其他模式各记各的
     * ----------------------------------------------------------------------------------
     * 为什么需要（2026-10-18 反编译实锤）：应用把水印设置存在**全局键**里，键名里没有模式维度 ——
     *   pref_ai_master_watermark_photo_style_id（用哪款水印；X-Pan 画框 = hassel_style_xpan_1）
     *   pref_ai_master_watermark_photo_open_state（AI 大师水印开关）
     *   pref_watermark_function_key（普通水印开关）
     *   pref_hasselblad_watermark_function_key（哈苏水印开关）
     * 这四个键的 DataKey 分别是 c6.d 里的静态字段 N0 / K0 / l1 / q1。
     * 于是"在 X-Pan 里换了水印，回拍照模式也跟着变"——应用侧没有模式维度，只能我们补。
     *
     * 做法：**值交换**（键名一个都不改）
     *   进 X-Pan：先把当前值（= 其他模式的值）抄下来；有上次存的 X-Pan 值就写回去；
     *             没有（首次进）就什么都不写 —— 即"首次进 X-Pan 沿用拍照模式的水印"。
     *   出 X-Pan：把当前值存成 X-Pan 的值，再把其他模式那份写回去。
     * 为什么不用"改键名"：那样应用的观察者/界面刷新挂在原键上，改了键名就收不到通知，界面不刷新。
     * 存档：/data/data/com.oplus.camera/files/xpan_watermark_keep.txt（我们自己的文件，不碰应用 MMKV）。
     * 读写走应用自己的 DataManager.c()/g()，内存缓存与观察者通知都跟着走。
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
    private static final Object[] sWmKey = new Object[WM_FIELD.length];    // 应用自己的 DataKey
    private static final Object[] sWmOther = new Object[WM_FIELD.length];  // 其他模式当前值
    private static final Object[] sWmMine = new Object[WM_FIELD.length];   // X-Pan 上次的值
    private static volatile boolean sWmInXpan = false;
    private static final String WM_KEEP =
            "/data/data/com.oplus.camera/files/xpan_watermark_keep.txt";

    /** 四个键的值类型（从 c6.d 的静态构造里读出来的：v2=String、v3=Boolean） */
    private static final Class<?>[] WM_TYPE = {
            String.class, Boolean.class, String.class, String.class,
    };
    private static volatile boolean sWmDiag = false;

    private static String wmUriOf(int i) {
        try {
            Object dk = sWmKey[i];
            if (dk == null) return "null";
            return String.valueOf(dk.getClass().getMethod("h").invoke(dk));
        } catch (Throwable t) {
            return "err:" + t;
        }
    }

    private static Object sDm;                 // DataManager 实例
    private static Method sDmGet, sDmSet, sDkFactory;
    private static Class<?> sDkCls;
    private static ClassLoader sClsLoader;

    private void installWatermarkIsolation(XC_LoadPackage.LoadPackageParam lp) {
        try {
            sClsLoader = lp.classLoader;
            wmLoad();
            XposedBridge.log(TAG + "hook installed OK (水印按模式隔离 x" + WM_FIELD.length + ")");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! 水印隔离安装失败: " + t);
        }
    }

    /** 造 DataKey：走应用自己的工厂 androidx.preference.e.f(Class, String)。
     *  ⚠️ 不用 c6.d 的静态字段（N0/K0/l1/q1）——实测在 handleLoadPackage 里取它会卡住
     *  （那会触发 c6.d 的静态初始化，而那时应用还没起来）。所以**延迟到切模式时才造键**。 */
    private static boolean wmInitKeys() {
        try {
            if (sWmKey[0] != null) return true;
            if (sClsLoader == null) return false;
            sDkCls = Class.forName("com.oplus.camera.data.DataKey", false, sClsLoader);
            Class<?> fac = Class.forName("androidx.preference.e", false, sClsLoader);
            sDkFactory = fac.getMethod("f", Class.class, String.class);
            for (int i = 0; i < WM_FIELD.length; i++) {
                sWmKey[i] = sDkFactory.invoke(null, WM_TYPE[i], WM_NAME[i]);
            }
            return sWmKey[0] != null;
        } catch (Throwable t) {
            XposedBridge.log(TAG + "水印隔离造键失败: " + t);
            return false;
        }
    }

    /** DataManager 懒加载：拿不到就整段放弃（绝不影响正常拍照） */
    private static boolean wmInitDm() {
        try {
            if (sDm != null) return true;
            if (sClsLoader == null) return false;
            // ⚠️ 必须用应用的类加载器：用模块自己的会 ClassNotFoundException（实测踩过）
            Class<?> dm = Class.forName("com.oplus.camera.data.DataManager", false, sClsLoader);
            sDmGet = dm.getMethod("c", sDkCls);
            sDmSet = dm.getMethod("g", sDkCls, Object.class);
            sDm = dm.getMethod("getInstance").invoke(null);
            return sDm != null;
        } catch (Throwable t) {
            XposedBridge.log(TAG + "水印隔离取 DataManager 失败: " + t);
            return false;
        }
    }

    private static Object wmRead(int i) {
        try {
            if (!wmInitKeys() || !wmInitDm()) return null;
            Object v = sDmGet.invoke(sDm, sWmKey[i]);
            if (v == null && !sWmDiag) {
                sWmDiag = true;
                XposedBridge.log(TAG + "水印隔离诊断：键 " + WM_NAME[i] + " 读到 null，uri=" + wmUriOf(i));
            }
            return v;
        } catch (Throwable t) {
            XposedBridge.log(TAG + "水印隔离读失败（" + WM_NAME[i] + "）: " + t);
            return null;
        }
    }

    private static void wmWrite(int i, Object v) {
        try {
            if (v == null || !wmInitKeys() || !wmInitDm()) return;
            sDmSet.invoke(sDm, sWmKey[i], v);
        } catch (Throwable t) {
            XposedBridge.log(TAG + "水印隔离写入失败（" + WM_NAME[i] + "）: " + t);
        }
    }

    /** 进 X-Pan：抄下其他模式的水印值；有上次 X-Pan 的值就写回去（首次进什么都不写 = 沿用拍照模式） */
    private static void wmEnterXpan() {
        if (sWmInXpan || "0".equals(prop("debug.xpan.wmper"))) return;
        sWmInXpan = true;
        try {
            for (int i = 0; i < WM_FIELD.length; i++) {
                Object cur = wmRead(i);
                sWmOther[i] = cur;
                Object mine = sWmMine[i];
                if (mine != null && !mine.equals(cur)) wmWrite(i, mine);
            }
            XposedBridge.log(TAG + "水印隔离：进 X-Pan，其他模式=" + wmDump(sWmOther)
                    + "，X-Pan 用=" + wmDump(sWmMine));
            // ★ 兜底复查：应用有可能是在"关设置页/切模式"之后才把水印值写下去，压过上面这次切换
            //   （用户反馈"水印控制没有隔离"）。延迟 1.5 秒再查一次，发现不是 X-Pan 那份就补写。
            final Object[] mine = sWmMine.clone();
            new android.os.Handler(android.os.Looper.getMainLooper()).postDelayed(new Runnable() {
                @Override
                public void run() {
                    try {
                        if (!sWmInXpan) return;
                        for (int i = 0; i < WM_FIELD.length; i++) {
                            Object want = mine[i];
                            if (want == null) continue;
                            Object cur = wmRead(i);
                            if (!want.equals(cur)) {
                                wmWrite(i, want);
                                XposedBridge.log(TAG + "水印隔离复查：补写 " + WM_NAME[i] + " → " + want);
                            }
                        }
                    } catch (Throwable ignored) {
                    }
                }
            }, 1500L);
        } catch (Throwable t) {
            XposedBridge.log(TAG + "水印隔离进模式失败: " + t);
        }
    }

    /** 出 X-Pan：把当前值存成 X-Pan 的值，再把其他模式那份写回去 */
    private static void wmExitXpan() {
        if (!sWmInXpan) return;
        sWmInXpan = false;
        try {
            for (int i = 0; i < WM_FIELD.length; i++) {
                Object cur = wmRead(i);
                if (cur != null) sWmMine[i] = cur;
                Object other = sWmOther[i];
                if (other != null && !other.equals(cur)) wmWrite(i, other);
                sWmOther[i] = null;
            }
            wmSave();
            XposedBridge.log(TAG + "水印隔离：出 X-Pan，X-Pan 存下=" + wmDump(sWmMine));
            // 兜底复查：出模式后也延迟查一次，别让 X-Pan 那份漏到其他模式（用户反馈"没有隔离"）
            final Object[] other = sWmOther.clone();
            new android.os.Handler(android.os.Looper.getMainLooper()).postDelayed(new Runnable() {
                @Override
                public void run() {
                    try {
                        if (sWmInXpan) return;
                        for (int i = 0; i < WM_FIELD.length; i++) {
                            Object want = other[i];
                            if (want == null) continue;
                            Object cur = wmRead(i);
                            if (!want.equals(cur)) {
                                wmWrite(i, want);
                                XposedBridge.log(TAG + "水印隔离复查：补写回 " + WM_NAME[i] + " → " + want);
                            }
                        }
                    } catch (Throwable ignored) {
                    }
                }
            }, 1500L);
        } catch (Throwable t) {
            XposedBridge.log(TAG + "水印隔离出模式失败: " + t);
        }
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
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < WM_FIELD.length; i++) {
                Object v = sWmMine[i];
                if (v == null) continue;
                sb.append(i).append('\t').append(v instanceof Boolean ? "b" : "s").append('\t')
                        .append(v).append('\n');
            }
            java.io.FileOutputStream fos = new java.io.FileOutputStream(WM_KEEP);
            fos.write(sb.toString().getBytes("UTF-8"));
            fos.close();
        } catch (Throwable t) {
            XposedBridge.log(TAG + "水印隔离存档失败: " + t);
        }
    }

    private static void wmLoad() {
        try {
            java.io.File f = new java.io.File(WM_KEEP);
            if (!f.exists()) return;
            byte[] buf = new byte[(int) f.length()];
            java.io.FileInputStream in = new java.io.FileInputStream(f);
            int n = 0;
            while (n < buf.length) {
                int r = in.read(buf, n, buf.length - n);
                if (r <= 0) break;
                n += r;
            }
            in.close();
            for (String line : new String(buf, 0, n, "UTF-8").split("\n")) {
                String[] p = line.split("\t");
                if (p.length != 3) continue;
                int i = Integer.parseInt(p[0].trim());
                if (i < 0 || i >= WM_FIELD.length) continue;
                sWmMine[i] = "b".equals(p[1]) ? (Object) Boolean.valueOf(p[2]) : (Object) p[2];
            }
        } catch (Throwable t) {
            XposedBridge.log(TAG + "水印隔离读档失败: " + t);
        }
    }

    /* ==================================================================================
     *  X-Pan 出片后处理：ProXDR 增益图调整（2026-10-18，用户要求）
     * ----------------------------------------------------------------------------------
     * ① ratioMax 提到 8 倍 → ProXDR 高光更亮
     * ② 修"水印条 ProXDR 不均匀"：增益图在水印条上是"白底有提升、文字笔画没有"（导出增益图
     *    看过，白底被赋了很高的值 → 发光；文字是 0）→ 把水印条那几行的增益图图块引用
     *    全部改成指向增益图里最平最暗的一块，整条水印就均匀了。
     *
     * 做法：**纯元数据 / 引用改写，不重编码、不动像素**（SDR 底图、EXIF、ICC 一个字节都不动）——
     *   ① 增益图强度参数在 HEIF 的 `tmap` 项载荷里（62 字节）：ratio 是 log2 值，
     *      定点编码 = log2 × 2^21，大端 4 字节；同文件 XMP 里还有 GainMapMax / HDRCapacityMax
     *      两个定宽字符串，一起改保持一致。
     *   ② 增益图网格的载荷在 meta/idat 里（iloc 的 construction_method=1），内容是
     *      "行数-1, 列数-1, 输出宽, 输出高, 图块id×N" —— 长度不变，可以纯原位改。
     * 开关：debug.xpan.proxdr=0 关闭；debug.xpan.proxdr.ratio=8 指定目标倍率（默认 8）
     */
    private static final String PX_TMAP_TYPE = "tmap";

    /** 解析出来的 HEIF 结构（只读，够用就行） */
    private static final class Hx {
        byte[] d;
        java.util.HashMap<Integer, String> types = new java.util.HashMap<>();
        java.util.HashMap<Integer, int[]> ext = new java.util.HashMap<>();   // id -> {cm, off, len}
        java.util.HashMap<Integer, int[]> extField = new java.util.HashMap<>(); // id -> {offPos, offSz, lenPos, lenSz}
        java.util.HashMap<Integer, int[]> dimg = new java.util.HashMap<>();  // from -> ids
        java.util.HashMap<Integer, int[]> dimgPos = new java.util.HashMap<>(); // from -> {idsPos, count}
        int pitm = -1;
        int idatData = -1;                                                   // idat 载荷起点
    }

    /** 基图网格声明的输出宽高（水印条不算在内；解码高度比它大 = 有水印条）。
     *  宽高比还能认模式：65:24 宽幅（≈2.71）就是 X-Pan 出片。 */
    private static int[] hxBaseGridOutWH(Hx h) {
        try {
            int[] gex = h.ext.get(h.pitm);
            if (gex == null || gex[0] != 1 || h.idatData < 0) return new int[]{-1, -1};
            int pay = h.idatData + gex[1];
            return new int[]{hxBe16(h.d, pay + 4), hxBe16(h.d, pay + 6)};
        } catch (Throwable t) {
            return new int[]{-1, -1};
        }
    }

    private static void hxBe32Put(byte[] b, int o, int v) {
        b[o] = (byte) (v >>> 24);
        b[o + 1] = (byte) (v >>> 16);
        b[o + 2] = (byte) (v >>> 8);
        b[o + 3] = (byte) v;
    }

    /** 找 iprp/ipco 里"面积最大"的 ispe（= 主图的声明输出尺寸），返回 {盒位置, 宽, 高}。
     *  用途：应用把 X-Pan 的声明尺寸虚标成 8192x3528，而实际编码只有 4096x1764（网格真实尺寸）
     *  —— 2× 出片时把声明尺寸改回真实值，出片就是原生 4096 像素（不是放大出来的）。 */
    private static int[] hxMainIspe(Hx h) {
        try {
            byte[] d = h.d;
            int[] meta = null;
            for (int[] b : hxKids(d, 0, d.length)) {
                if (hxType(d, b[0]).equals("meta")) {
                    meta = b;
                    break;
                }
            }
            if (meta == null) return null;
            int ms = meta[0] + meta[2] + 4, me = meta[0] + meta[1];
            int[] iprp = hxFind(d, ms, me, "iprp");      // iprp/ipco 都是普通盒（子盒从 +8 起）
            if (iprp == null) return null;
            int[] ipco = hxFind(d, iprp[0] + 8, iprp[0] + iprp[1], "ipco");
            if (ipco == null) return null;
            int best = -1;
            long bestArea = -1;
            for (int[] b : hxKids(d, ipco[0] + 8, ipco[0] + ipco[1])) {
                if (!hxType(d, b[0]).equals("ispe")) continue;
                int w = hxBe32(d, b[0] + 12), hh = hxBe32(d, b[0] + 16);
                long area = (long) w * hh;
                if (area > bestArea) {
                    bestArea = area;
                    best = b[0];
                }
            }
            if (best < 0) return null;
            return new int[]{best, hxBe32(d, best + 12), hxBe32(d, best + 16)};
        } catch (Throwable t) {
            return null;
        }
    }

    /** 找**与主图网格尺寸一致**的那个 ispe —— 解码器实际用的就是它。
     *  ⚠️ 文件里往往有两个尺寸 ispe：一个是应用写的"目标尺寸"（例如 8192x3024），
     *  另一个才是主图项真正关联的（= 网格的编码尺寸，例如 4096x1512）。
     *  实测：改"最大那个"没用（解码器不认），必须改跟网格尺寸一致的那个。 */
    private static int[] hxIspeOfSize(Hx h, int w, int hh) {
        try {
            byte[] d = h.d;
            int[] meta = null;
            for (int[] b : hxKids(d, 0, d.length)) {
                if (hxType(d, b[0]).equals("meta")) {
                    meta = b;
                    break;
                }
            }
            if (meta == null) return null;
            int ms = meta[0] + meta[2] + 4, me = meta[0] + meta[1];
            int[] iprp = hxFind(d, ms, me, "iprp");
            if (iprp == null) return null;
            int[] ipco = hxFind(d, iprp[0] + 8, iprp[0] + iprp[1], "ipco");
            if (ipco == null) return null;
            for (int[] b : hxKids(d, ipco[0] + 8, ipco[0] + ipco[1])) {
                if (!hxType(d, b[0]).equals("ispe")) continue;
                int ww = hxBe32(d, b[0] + 12), hh2 = hxBe32(d, b[0] + 16);
                if (ww == w && hh2 == hh) return new int[]{b[0], ww, hh2};
            }
            return hxMainIspe(h);      // 兜底：找不到就退回"最大那个"
        } catch (Throwable t) {
            return null;
        }
    }

    private static int hxBe32(byte[] b, int o) {
        return ((b[o] & 255) << 24) | ((b[o + 1] & 255) << 16) | ((b[o + 2] & 255) << 8) | (b[o + 3] & 255);
    }

    private static int hxBe16(byte[] b, int o) {
        return ((b[o] & 255) << 8) | (b[o + 1] & 255);
    }

    /** 在 [start,end) 里遍历直接子 box，回填到 list：{off, size, hdr}；name 为 null 表示全都要 */
    private static java.util.List<int[]> hxKids(byte[] d, int start, int end) {
        java.util.List<int[]> out = new java.util.ArrayList<>();
        int o = start;
        while (o + 8 <= end) {
            long sz = hxBe32(d, o) & 0xFFFFFFFFL;
            int hdr = 8;
            if (sz == 1) {
                sz = 0;
                for (int i = 0; i < 8; i++) sz = (sz << 8) | (d[o + 8 + i] & 255);
                hdr = 16;
            } else if (sz == 0) {
                sz = end - o;
            }
            if (sz < hdr || o + sz > end) break;
            out.add(new int[]{o, (int) sz, hdr});
            o += (int) sz;
        }
        return out;
    }

    private static int[] hxFind(byte[] d, int start, int end, String name) {
        for (int[] b : hxKids(d, start, end)) {
            String t = new String(d, b[0] + 4, 4, java.nio.charset.StandardCharsets.ISO_8859_1);
            if (t.equals(name)) return b;
        }
        return null;
    }

    private static String hxType(byte[] d, int off) {
        return new String(d, off + 4, 4, java.nio.charset.StandardCharsets.ISO_8859_1);
    }

    private static Hx hxParse(byte[] d) {
        try {
            Hx h = new Hx();
            h.d = d;
            int[] meta = null, mdat = null;
            for (int[] b : hxKids(d, 0, d.length)) {
                String t = hxType(d, b[0]);
                if (t.equals("meta")) meta = b;
                else if (t.equals("mdat")) mdat = b;
            }
            if (meta == null || mdat == null) return null;
            int ms = meta[0] + meta[2] + 4, me = meta[0] + meta[1];
            int[] iinf = hxFind(d, ms, me, "iinf");
            int[] iloc = hxFind(d, ms, me, "iloc");
            int[] iref = hxFind(d, ms, me, "iref");
            int[] pitm = hxFind(d, ms, me, "pitm");
            int[] idat = hxFind(d, ms, me, "idat");
            if (iinf == null || iloc == null || pitm == null) return null;
            if (idat != null) h.idatData = idat[0] + idat[2];
            // iinf: id -> type
            int p = iinf[0] + 12;
            int cnt = hxBe16(d, p);
            p += 2;
            for (int i = 0; i < cnt; i++) {
                int isz = hxBe32(d, p);
                h.types.put(hxBe16(d, p + 12), new String(d, p + 16, 4,
                        java.nio.charset.StandardCharsets.ISO_8859_1));
                p += isz;
            }
            // pitm
            h.pitm = (d[pitm[0] + 8] == 0) ? hxBe16(d, pitm[0] + 12) : hxBe32(d, pitm[0] + 12);
            // iref dimg
            if (iref != null) {
                for (int[] b : hxKids(d, iref[0] + iref[2] + 4, iref[0] + iref[1])) {
                    if (!hxType(d, b[0]).equals("dimg")) continue;
                    int frm = hxBe16(d, b[0] + 8), n = hxBe16(d, b[0] + 10);
                    int[] ids = new int[n];
                    for (int i = 0; i < n; i++) ids[i] = hxBe16(d, b[0] + 12 + 2 * i);
                    h.dimg.put(frm, ids);
                    h.dimgPos.put(frm, new int[]{b[0] + 12, n});
                }
            }
            // iloc
            int ver = d[iloc[0] + 8];
            int q = iloc[0] + 12;
            int b1 = d[q] & 255, b2 = d[q + 1] & 255;
            q += 2;
            int n2 = (ver < 2) ? hxBe16(d, q) : hxBe32(d, q);
            q += (ver < 2) ? 2 : 4;
            int offsz = b1 >> 4, lensz = b1 & 15, basesz = b2 >> 4, idxsz = b2 & 15;
            for (int i = 0; i < n2; i++) {
                int id = (ver < 2) ? hxBe16(d, q) : hxBe32(d, q);
                q += (ver < 2) ? 2 : 4;
                int cm = 0;
                if (ver == 1 || ver == 2) {
                    cm = hxBe16(d, q);
                    q += 2;
                }
                q += 2;
                int base = 0;
                for (int k = 0; k < basesz; k++) base = (base << 8) | (d[q + k] & 255);
                q += basesz;
                int ec = hxBe16(d, q);
                q += 2;
                int off = 0, len = 0;
                for (int e = 0; e < ec; e++) {
                    if (idxsz > 0) q += idxsz;
                    int opos = q, o1 = 0;
                    for (int k = 0; k < offsz; k++) o1 = (o1 << 8) | (d[q + k] & 255);
                    q += offsz;
                    int lpos = q, l1 = 0;
                    for (int k = 0; k < lensz; k++) l1 = (l1 << 8) | (d[q + k] & 255);
                    q += lensz;
                    if (e == 0) {
                        off = base + o1;
                        len = l1;
                        if (cm == 0 && offsz > 0 && lensz > 0) {
                            h.extField.put(id, new int[]{opos, offsz, lpos, lensz});
                        }
                    }
                }
                h.ext.put(id, new int[]{cm, off, len});
            }
            return h;
        } catch (Throwable t) {
            XposedBridge.log(TAG + "HEIF 解析失败: " + t);
            return null;
        }
    }

    /** ① 改 ratioMax：tmap 项载荷里的定点值 + XMP 里的同名字符串。
     *  值 = log2(ratio) 的定点编码（×2^21），大端 4 字节；载荷里出现两处（base/alt）。
     *  不硬编码偏移：用解码器读到的当前 ratioMax 算出字节，在载荷里精确匹配替换。 */
    private static boolean pxPatchRatioMax(Hx h, float ratio, float curRatio, StringBuilder log) {
        try {
            int[] ex = null;
            for (java.util.Map.Entry<Integer, String> e : h.types.entrySet()) {
                if (PX_TMAP_TYPE.equals(e.getValue())) {
                    ex = h.ext.get(e.getKey());
                    break;
                }
            }
            if (ex == null || ex[0] != 0) {
                log.append("没找到 tmap 项载荷; ");
                return false;
            }
            int off = ex[1], len = ex[2];
            if (len < 20 || len > 4096) {
                log.append("tmap 载荷长度异常 ").append(len).append("; ");
                return false;
            }
            double log2Now = Math.log(curRatio > 1.0f ? curRatio : 4.956563f) / Math.log(2.0);
            double log2New = Math.log(ratio) / Math.log(2.0);
            // 定点标度按文件自己探测：实测同一台相机 X-Pan 出片是 ×2^21、拍照模式出片是 ×2^23
            // （设备上跑两个变体验证过：×2^23 那版解码器读出来正好 8.0，×2^21 那版读出来 1.68）
            int scaleBits = 0, nowFixed = 0;
            for (int k : new int[]{21, 23, 22}) {
                int v = (int) Math.round(log2Now * (1 << k));
                if (v <= 0) continue;
                byte[] p = new byte[]{(byte) (v >> 24), (byte) (v >> 16), (byte) (v >> 8), (byte) v};
                int at = hxIndexOfFrom(h.d, p, off);
                if (at >= 0 && at + 4 <= off + len) {
                    scaleBits = k;
                    nowFixed = v;
                    break;
                }
            }
            if (scaleBits == 0) {
                log.append("tmap 里没匹配到 ratioMax 字节（当前 ").append(curRatio).append("）; ");
                return false;
            }
            int newFixed = (int) Math.round(log2New * (1 << scaleBits));
            byte[] pat = new byte[]{(byte) (nowFixed >> 24), (byte) (nowFixed >> 16),
                    (byte) (nowFixed >> 8), (byte) nowFixed};
            byte[] rep = new byte[]{(byte) (newFixed >> 24), (byte) (newFixed >> 16),
                    (byte) (newFixed >> 8), (byte) newFixed};
            int patched = 0;
            for (int i = 0; i + 4 <= len; i++) {
                boolean hit = true;
                for (int k = 0; k < 4; k++) {
                    if (h.d[off + i + k] != pat[k]) {
                        hit = false;
                        break;
                    }
                }
                if (!hit) continue;
                System.arraycopy(rep, 0, h.d, off + i, 4);
                patched++;
            }
            if (patched == 0) {
                log.append("tmap 里没匹配到 ratioMax 字节（当前 ").append(curRatio).append("）; ");
                return false;
            }
            log.append("ratioMax ").append(curRatio).append("→").append(ratio)
                    .append("（定点 ×2^").append(scaleBits).append("，改了 ").append(patched)
                    .append(" 处）; ");
            // XMP 里的两个同名字符串（"%.6f" 定宽，只在长度一致时原位替换，宁可不改也不写坏）
            String val = String.format(java.util.Locale.US, "%.6f", log2New);
            String valOld = String.format(java.util.Locale.US, "%.6f", log2Now);
            int xmpChanged = 0;
            for (String key : new String[]{"GainMapMax=\"", "HDRCapacityMax=\""}) {
                byte[] kb = key.getBytes(java.nio.charset.StandardCharsets.ISO_8859_1);
                byte[] ob = valOld.getBytes(java.nio.charset.StandardCharsets.ISO_8859_1);
                byte[] nb = val.getBytes(java.nio.charset.StandardCharsets.ISO_8859_1);
                int i = hxIndexOf(h.d, kb);
                while (i >= 0) {
                    int q = i + kb.length;
                    if (ob.length == nb.length && q + ob.length <= h.d.length) {
                        boolean same = true;
                        for (int k = 0; k < ob.length; k++) if (h.d[q + k] != ob[k]) same = false;
                        if (same) {
                            System.arraycopy(nb, 0, h.d, q, nb.length);
                            xmpChanged++;
                        }
                    }
                    i = hxIndexOfFrom(h.d, kb, q);
                }
            }
            if (xmpChanged > 0) log.append("XMP 字符串改了 ").append(xmpChanged).append(" 处; ");
            return true;
        } catch (Throwable t) {
            log.append("改 ratioMax 失败: ").append(t).append("; ");
            return false;
        }
    }

    private static int hxIndexOf(byte[] d, byte[] pat) {
        return hxIndexOfFrom(d, pat, 0);
    }

    private static int hxIndexOfFrom(byte[] d, byte[] pat, int from) {
        outer:
        for (int i = Math.max(0, from); i + pat.length <= d.length; i++) {
            for (int k = 0; k < pat.length; k++) {
                if (d[i + k] != pat[k]) continue outer;
            }
            return i;
        }
        return -1;
    }

    /** 出片后处理（读文件 → 打补丁 → 写回）。返回 true = 已经写回。
     *  ⚠️ 相机是"先写一版 SDR、过一会儿整份重写成带增益图的最终版"的：
     *  抓到第一版时容器里**没有 tmap 项**，这时不能动它（返回 false，等它重写）。 */
    private static boolean pxPostProcess(java.io.File f, float zoom) {
        StringBuilder log = new StringBuilder();
        try {
            if (f == null || !f.exists() || f.length() < 20000) return true;   // 没得处理，别再重试
            if (!isHeifLike(f)) {
                log.append("不是 HEIF，跳过; ");
                return true;
            }
            byte[] data = pxReadAll(f);
            long len0 = (data != null) ? data.length : -1;
            Hx h = (data != null) ? hxParse(data) : null;
            if (h == null) {
                XposedBridge.log(TAG + "ProXDR 后处理：容器读不全（可能还在写），稍后重试");
                return false;
            }
            boolean hasTmap = false;
            for (String t : h.types.values()) if (PX_TMAP_TYPE.equals(t)) { hasTmap = true; break; }
            if (!hasTmap) {
                XposedBridge.log(TAG + "ProXDR 后处理：这一版还没有增益图（相机第一版），等它重写");
                return false;
            }
            float ratio = 8.0f;
            try {
                String p = prop("debug.xpan.proxdr.ratio");
                if (p != null && p.length() > 0) ratio = Float.parseFloat(p.trim());
            } catch (Throwable ignored) {
            }
            // ★ 不再解 getGainmapContents()：那张 bitmap 是 4096x1512(≈24MB)，而它的唯一用途
            //   （压平增益图）已经删掉了 —— 现在只需要 Gainmap 对象上的 ratioMax。省内存也省时间。
            int decHeight = 0;
            float curRatio = 4.956563f;
            final int[] realH = new int[1];
            try {
                android.graphics.Bitmap b = android.graphics.ImageDecoder.decodeBitmap(
                        android.graphics.ImageDecoder.createSource(f),
                        new android.graphics.ImageDecoder.OnHeaderDecodedListener() {
                            @Override
                            public void onHeaderDecoded(android.graphics.ImageDecoder d,
                                                        android.graphics.ImageDecoder.ImageInfo info,
                                                        android.graphics.ImageDecoder.Source src) {
                                d.setAllocator(android.graphics.ImageDecoder.ALLOCATOR_SOFTWARE);
                                // 全尺寸解码 8192x3528 要 ~100MB，相机进程吃不消；
                                // 主体图缩到 1/4，尺寸从 header 拿，增益图按自己的尺寸解
                                try {
                                    android.util.Size sz = info.getSize();
                                    if (sz != null && sz.getWidth() > 0) {
                                        realH[0] = sz.getHeight();
                                        if (sz.getWidth() > 2048) {
                                            d.setTargetSize(sz.getWidth() / 4, sz.getHeight() / 4);
                                        }
                                    }
                                } catch (Throwable ignored) {
                                }
                            }
                        });
                if (b != null) {
                    decHeight = realH[0] > 0 ? realH[0] : b.getHeight();
                    android.graphics.Gainmap gm = b.getGainmap();
                    if (gm != null) {
                        float[] rm = gm.getRatioMax();
                        if (rm != null && rm.length > 0 && rm[0] > 1.0f) curRatio = rm[0];
                    }
                }
            } catch (Throwable t) {
                log.append("读增益图失败: ").append(t).append("; ");
            }
            int[] gwh0 = hxBaseGridOutWH(h);
            boolean xpanShot0 = gwh0[0] > 0 && gwh0[1] > 0 && gwh0[0] >= gwh0[1] * 2;
            // ★ 出片尺寸（用户要求：1× = 8192、2× = 4096），自己写"声明尺寸"。
            //   背景：X-Pan 拍照流默认 4096x1512（干净通路，见 captureSize 注释 —— 8192 那条
            //   全分辨率 remosaic 通路自带 2~3 像素双线重影）。而应用会把"声明尺寸"跟着拍照流走，
            //   于是 1× 也变成 4096。这里改回来：
            //     1× → 网格的 2 倍（8192 宽，等同应用原来的口径，解码器放大 2 倍输出）
            //     2× → 网格本身（4096 宽，原生像素，不放大）
            //   网格真实尺寸 gwh0 就是编码尺寸（4096x1512 或 4096x1764），所以两者都不损失真实细节。
            if (xpanShot0 && gwh0[0] > 0 && gwh0[1] > 0) {
                int[] ispe = hxIspeOfSize(h, gwh0[0], gwh0[1]);
                if (ispe != null && ispe[1] > 0 && ispe[2] > 0 && zoom > 1.5f) {
                    boolean x2 = true;
                    int wantW = gwh0[0];
                    int wantH = gwh0[1];
                    if (ispe[1] != wantW || ispe[2] != wantH) {
                        hxBe32Put(h.d, ispe[0] + 12, wantW);
                        hxBe32Put(h.d, ispe[0] + 16, wantH);
                        log.append("出片尺寸 ").append(ispe[1]).append("x").append(ispe[2])
                                .append(" → ").append(wantW).append("x").append(wantH)
                                .append(x2 ? "（2× 原生像素）; " : "（1× 网格×2）; ");
                    }
                }
            }
            // ★ ratioMax：**X-Pan 用系统默认值**（用户 2026-10-18 明确要求，不提 8 倍）；
            //   其它模式按用户之前的要求提到 8 倍（只升不降）。
            if (xpanShot0) {
                log.append("X-Pan：ratioMax 保持系统默认 ").append(curRatio).append("; ");
            } else if (ratio > curRatio + 0.01f) {
                pxPatchRatioMax(h, ratio, curRatio, log);
            } else {
                log.append("ratioMax 已是 ").append(curRatio).append("，不降; ");
            }
            int[] gwh = hxBaseGridOutWH(h);
            int outW = gwh[0], outH = gwh[1];
            long lenNow = f.length();
            if (lenNow != len0) {
                log.append("文件长度变了（").append(len0).append("→").append(lenNow).append("），放弃写回; ");
                XposedBridge.log(TAG + "ProXDR 后处理（未写回）: " + log);
                return false;
            }
            boolean ok = pxWriteBack(f, data, log);
            XposedBridge.log(TAG + "ProXDR 后处理" + (ok ? "完成" : "未写回")
                    + "（倍率 " + zoom + "）: " + log);
            return ok;
        } catch (Throwable t) {
            XposedBridge.log(TAG + "ProXDR 后处理失败（保留原图）: " + t);
            return false;
        }
    }

    private static byte[] pxReadAll(java.io.File f) {
        try {
            int len = (int) f.length();
            if (len <= 0 || len > (1 << 28)) return null;
            byte[] b = new byte[len];
            java.io.FileInputStream in = new java.io.FileInputStream(f);
            int n = 0;
            while (n < len) {
                int r = in.read(b, n, len - n);
                if (r <= 0) break;
                n += r;
            }
            in.close();
            return (n == len) ? b : null;
        } catch (Throwable t) {
            return null;
        }
    }

    /** 写回：先写一份临时文件把数据落全，再**就地覆盖**原文件。返回 true = 已写回。
     *  ⚠️ 不用"改名替换"：相机可能还握着原文件的句柄继续写，改名会让它的后续写入落到
     *  已删除的 inode 上，成片就丢掉后半段（实测踩过，一张 13MB 的照片变成 2.9MB）。
     *  就地写最坏情况是相机随后自己重写、我们的改动被覆盖 —— 照片本身还是完好的。 */
    private static boolean pxWriteBack(java.io.File f, byte[] data, StringBuilder log) {
        java.io.File tmp = new java.io.File(f.getParentFile(), f.getName() + ".xptmp");
        try {
            java.io.FileOutputStream fos = new java.io.FileOutputStream(tmp);
            fos.write(data);
            fos.flush();
            fos.close();
            if (tmp.length() != data.length) {
                log.append("临时文件长度不对，放弃写回; ");
                tmp.delete();
                return false;
            }
        } catch (Throwable t) {
            log.append("写临时文件失败（").append(t).append("），放弃写回; ");
            try {
                tmp.delete();
            } catch (Throwable ignored) {
            }
            return false;
        }
        try {
            java.io.FileOutputStream fos = new java.io.FileOutputStream(f);
            fos.write(data);
            fos.flush();
            fos.close();
        } catch (Throwable t) {
            log.append("就地写回失败: ").append(t).append("; ");
            try {
                tmp.delete();
            } catch (Throwable ignored) {
            }
            return false;
        }
        try {
            tmp.delete();
        } catch (Throwable ignored) {
        }
        return true;
    }

    private static boolean isHeifLike(java.io.File f) {
        try {
            byte[] b = new byte[12];
            java.io.FileInputStream in = new java.io.FileInputStream(f);
            int n = in.read(b);
            in.close();
            return n == 12 && b[4] == 'f' && b[5] == 't' && b[6] == 'y' && b[7] == 'p';
        } catch (Throwable t) {
            return false;
        }
    }

    /* ---------------- 出片后处理触发：hook 拍照 + 轮询 DCIM 找新文件 ----------------
     * 全局生效（所有模式）：ratioMax 提升对每一张出片都做；
     * 水印条压平只在宽幅（X-Pan 65:24）出片上做，普通照片不动它的图块引用。 */
    private static final String DCIM_CAMERA = "/storage/emulated/0/DCIM/Camera";

    private void installProxdrPost(XC_LoadPackage.LoadPackageParam lp) {
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
                            if ("0".equals(prop("debug.xpan.proxdr"))) return;
                            float z = 1.0f;
                            Object zz = field(param.args[0], "mZoomRatio");
                            if (zz instanceof Number) z = ((Number) zz).floatValue();
                            final float zf = z;
                            final java.io.File dir = new java.io.File(DCIM_CAMERA);
                            final java.util.Set<String> before = pxListNames(dir);
                            new Thread(new Runnable() {
                                @Override
                                public void run() {
                                    pxWatch(dir, before, zf);
                                }
                            }, "xpan-proxdr").start();
                        } catch (Throwable ignored) {
                        }
                    }
                });
                n++;
            }
            XposedBridge.log(TAG + "hook installed OK (ProXDR 后处理 x" + n + ")");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! ProXDR 后处理安装失败: " + t);
        }
    }

    private static java.util.Set<String> pxListNames(java.io.File d) {
        java.util.Set<String> s = new java.util.HashSet<>();
        try {
            String[] a = d.list();
            if (a != null) java.util.Collections.addAll(s, a);
        } catch (Throwable ignored) {
        }
        return s;
    }

    /** 每个新文件的处理状态：{上次看到的长度, 长度不变的次数, 是否已写回, 失败次数} */
    private static final java.util.HashMap<String, long[]> sPxState = new java.util.HashMap<>();

    /** 等新照片落盘（FileObserver 在 /sdcard 上收不到事件，只能轮询）。
     *  相机是分阶段写同一个文件的（先写一版没增益图的、再整份重写成最终版），所以：
     *  - 长度连续 2 秒不变才动手；
     *  - 处理失败（还没写完 / 还没 tmap）就清零计数，过一会儿再来；
     *  - 已经写回过的文件不再碰，但**长度又变了**说明相机重写了，就重新处理一遍。 */
    private static void pxWatch(java.io.File dir, java.util.Set<String> before, float zoom) {
        try {
            int idle = 0;
            for (int i = 0; i < 240; i++) {
                Thread.sleep(500);
                String[] a = dir.list();
                if (a == null) continue;
                boolean busy = false;
                for (String nm : a) {
                    if (before.contains(nm)) continue;
                    String low = nm.toLowerCase();
                    if (!low.endsWith(".heic") && !low.endsWith(".heif")) continue;
                    java.io.File f = new java.io.File(dir, nm);
                    long len = f.length();
                    if (len < 20000) continue;
                    busy = true;
                    boolean go;
                    synchronized (sPxState) {
                        if (sPxState.size() > 200) sPxState.clear();   // 防慢性增长（一张照片一条）
                        long[] st = sPxState.get(nm);
                        if (st == null) {
                            st = new long[]{len, 0, 0, 0};
                            sPxState.put(nm, st);
                        }
                        if (st[0] != len) {                 // 长度又变了 → 相机在重写，重新计时
                            st[0] = len;
                            st[1] = 0;
                            st[2] = 0;
                            st[3] = 0;
                        } else {
                            st[1]++;
                        }
                        go = (st[1] >= 4 && st[2] == 0);
                    }
                    if (!go) continue;
                    boolean ok = pxPostProcess(f, zoom);
                    synchronized (sPxState) {
                        long[] st = sPxState.get(nm);
                        if (st != null) {
                            st[0] = f.length();
                            st[2] = ok ? 1 : 0;
                            st[1] = ok ? st[1] : 0;         // 没写回就等下一轮稳定计数
                        }
                    }
                }
                if (busy) idle = 0;
                else if (++idle >= 12) return;               // 连续 6 秒没新照片就收工
            }
        } catch (Throwable t) {
            XposedBridge.log(TAG + "ProXDR 后处理轮询失败: " + t);
        }
    }

    /* ---------------- 诊断：APS 算法支持查询（debug.xpan.algotrace=1 才装） ----------------
     * 用途：看应用"认为"某个模式支持哪些 APS 算法 —— 比如 X-Pan 的 HDR 能力就是
     *   yh/l2.getSupportFunction("pref_camera_hdr_mode_key") = w5.a.d("xpan",id,"aps_algo_hdr")
     *   || w5.a.d("xpan",id,"aps_algo_merge_hdr")（反编译实锤）。
     * 只记含 hdr 的查询（量极小），不刷屏。加自动 HDR 配置时用它验证"门"有没有翻转。
     */
    private void installAlgoQueryTrace(XC_LoadPackage.LoadPackageParam lp) {
        if (!"1".equals(prop("debug.xpan.algotrace"))) return;
        try {
            Class<?> c = Class.forName("w5.a", false, lp.classLoader);
            int n = 0;
            for (Method m : c.getDeclaredMethods()) {
                if (!"d".equals(m.getName())) continue;
                Class<?>[] pt = m.getParameterTypes();
                if (pt.length != 3 || pt[0] != String.class || pt[1] != int.class
                        || pt[2] != String.class) continue;
                XposedBridge.hookMethod(m, new XC_MethodHook() {
                    @Override
                    protected void afterHookedMethod(MethodHookParam param) {
                        try {
                            String algo = String.valueOf(param.args[2]);
                            if (algo.toLowerCase().indexOf("hdr") < 0) return;
                            XposedBridge.log(TAG + "algoQuery mode=" + param.args[0]
                                    + " cam=" + param.args[1] + " algo=" + algo
                                    + " -> " + param.getResult());
                        } catch (Throwable ignored) {
                        }
                    }
                });
                n++;
            }
            XposedBridge.log(TAG + "hook installed OK (算法查询探针 x" + n + ")");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! 算法查询探针失败: " + t);
        }
    }

    /* ---------------- 自动 HDR：给 X-Pan 补上应用侧的两道闸 ----------------
     * 背景（反编译 h9/m.b0 实锤，2026-10-18）：应用给 HAL 下发的
     *   com.oplus.capture.hdr.support 由三道闸串起来 ——
     *   ① v2(mode) → A2(mode)：模式能力链（W0() + n8.c.c(mode) …）
     *   ② E2() = getSupportFunction("func_support_preview_hdr")：预览 HDR 能力
     *   ③ CameraConfig.b("com.oplus.camera.capture.hdr.support", false)：配置开关
     * X-Pan 不是本机发布的模式，APS 配置里也没有 HDR 算法 → 三道闸全 false。
     * ★ 实测结论（2026-10-18）：**这条路走不通，默认关闭**（要试就 setprop debug.xpan.autohdr 1）。
     *   ① 闸①(A2) 内部先看 W0()=UI 容器状态（EventMessageUIContainerContext.c==1），X-Pan 下为 false
     *      → 整条 && 链短路，闸②③ 根本不被求值；
     *   ② 强行接管 A2 后：闸②(func_support_preview_hdr) 仍未被调用，capture.hdr.support 仍是 false；
     *      而且 auto.hdr.enable 从 [1] 变 [0]、出片亮度整体 +9（曝光也漂了）—— 说明这些接管
     *      扰动了应用自己的拍照决策 ✗。
     *   ③ 更关键：应用是通过请求里的 **com.oplus.aps.feature.type（int[] 位图）**告诉 APS 跑哪些算法；
     *      X-Pan 下这个位图不含 HDR 位（能力链判定为"不支持"）→ 光在 APS 算法开关里加 HDR 条目
     *      （实测能让应用的能力查询 aps_algo_turbo_hdr 翻成 true）也**不会真的触发 HDR** ✗。
     *   → 要真给 X-Pan 上 HDR，得同时谎报一整套全局能力（UI 容器状态、模式能力、预览 HDR、配置开关）
     *     并猜 APS 的 feature 位图，风险大、下游未验证；按"宁可如实记录也不硬凑"的原则不做。
     *      开关：debug.xpan.autohdr=1 才启用（默认关，模块行为与 v1.2 完全一致）。
     */
    private static final String CFG_CAP_HDR = "com.oplus.camera.capture.hdr.support";
    private static final String CFG_PREV_HDR = "com.oplus.camera.preview.hdr.support";
    private static final String FUNC_PREV_HDR = "func_support_preview_hdr";
    private static volatile boolean sHdrGateLogged = false;

    /* ---------------- 诊断：拍照请求参数全量探针（debug.xpan.reqtrace=1） ----------------
     * 用途：看应用到底往 HAL 下发了哪些参数（尤其 HDR 相关的厂商 tag），
     *   用来判定"自动 HDR 没效果"是卡在应用侧没请求，还是 APS 侧没有对应管线。
     * 只记每个 key 的第一次出现（去重），最多 200 条。
     */
    private static final java.util.HashSet<String> sReqSeen = new java.util.HashSet<>();

    private static String fmtReqVal(Object v) {
        if (v == null) return "null";
        if (v instanceof android.graphics.Rect) return "Rect" + v;
        if (v instanceof int[]) return java.util.Arrays.toString((int[]) v);
        if (v instanceof long[]) return java.util.Arrays.toString((long[]) v);
        if (v instanceof float[]) return java.util.Arrays.toString((float[]) v);
        if (v instanceof byte[]) {
            byte[] b = (byte[]) v;
            StringBuilder sb = new StringBuilder("[");
            for (int i = 0; i < Math.min(b.length, 16); i++) sb.append(b[i] & 255).append(i < Math.min(b.length,16)-1 ? "," : "");
            return sb.append("]").toString();
        }
        return String.valueOf(v);
    }

    private void installRequestTraceAll(XC_LoadPackage.LoadPackageParam lp) {
        if (!"1".equals(prop("debug.xpan.reqtrace"))) return;
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
                        Object v = param.args[1];
                        String sig = name + " = " + fmtReqVal(v);
                        if (sig.length() > 160) sig = sig.substring(0, 160);
                        synchronized (sReqSeen) {
                            if (sReqSeen.size() >= 200 || !sReqSeen.add(sig)) return;
                        }
                        XposedBridge.log(TAG + "reqAll " + sig);
                    } catch (Throwable ignored) {
                    }
                }
            });
            XposedBridge.log(TAG + "hook installed OK (请求全量探针)");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! 请求全量探针失败: " + t);
        }
    }

    /* ---------------- 测试辅助：用属性切模式（确定性，不依赖 UI 手势） ----------------
     * 背景：模式条（"双指左右滑动切换模式"）用 input swipe 实测时灵时不灵，
     *   自动化验证模式相关功能（比如水印按模式隔离）需要一条可靠通道。
     * 做法：抓 ModeManager（wh.q3）实例，轮询属性 debug.xpan.mode，
     *   有值就调它自己的 setCurrentMode —— wh.q3.d0(String, boolean, boolean)。
     * 用法：setprop debug.xpan.mode xpan  （回拍照模式用 common —— 实测应用日志：
     *   "setCurrentMode fail, the newMode: photo is not enabled"，拍照模式的内部名是 common；
     *   值不变则不动）
     * 只在属性非空时动作，平时开销就是 1.5 秒读一次属性。
     */
    private static volatile boolean sLastForceExit = false;
    private static volatile Object sModeMgr;
    private static volatile String sLastModeCmd = "";

    private void installModeSwitchHelper(XC_LoadPackage.LoadPackageParam lp) {
        try {
            Class<?> c = Class.forName("wh.q3", false, lp.classLoader);
            int n = 0;
            for (java.lang.reflect.Constructor<?> ct : c.getDeclaredConstructors()) {
                XposedBridge.hookMethod(ct, new XC_MethodHook() {
                    @Override
                    protected void afterHookedMethod(MethodHookParam param) {
                        sModeMgr = param.thisObject;
                    }
                });
                n++;
            }
            Thread t = new Thread(new Runnable() {
                @Override
                public void run() {
                    while (true) {
                        try {
                            Thread.sleep(1500);
                            // 调试：debug.xpan.forceexit=1 → 强制按"离开 X-Pan"清理皮肤/容器
                            // （程序化切模式不会走应用的退出清理，会留残影把模式条挡住）
                            if ("1".equals(prop("debug.xpan.forceexit"))) {
                                sLastForceExit = true;
                            } else if (sLastForceExit) {
                                sLastForceExit = false;
                                sXpanActive = false;
                                sDriveDone = true;
                                new android.os.Handler(android.os.Looper.getMainLooper()).post(
                                        new Runnable() {
                                            @Override
                                            public void run() {
                                                cleanupXpanContainer();
                                            }
                                        });
                                XposedBridge.log(TAG + "debug.xpan.forceexit → 已强制清理 X-Pan 皮肤");
                            }
                            String want = prop("debug.xpan.mode");
                            if (want == null || want.length() == 0 || want.equals(sLastModeCmd)) continue;
                            sLastModeCmd = want;
                            // 支持 "模式名[,flag1[,flag2]]" 便于实验 d0 的两个布尔（实测 false,false 有时切不过去）
                            String modeName = want;
                            boolean f1 = false, f2 = false;
                            String[] parts = want.split(",");
                            if (parts.length > 1) {
                                modeName = parts[0].trim();
                                if (parts.length > 2) f1 = "1".equals(parts[1].trim());
                                if (parts.length > 3) f2 = "1".equals(parts[2].trim());
                                else if (parts.length > 2) f2 = "1".equals(parts[2].trim());
                            }
                            Object mm = sModeMgr;
                            if (mm == null) {
                                XposedBridge.log(TAG + "属性切模式：ModeManager 还没抓到");
                                sLastModeCmd = "";
                                continue;
                            }
                            // ⚠️ 必须回主线程调：ModeManager 的切模式会碰 UI，
                            //    在工作线程直接调实测抛 InvocationTargetException
                            final Object mmF = mm;
                            final String wantF = want;
                            final String modeNameF = modeName;
                            final boolean f1F = f1, f2F = f2;
                            new android.os.Handler(android.os.Looper.getMainLooper()).post(
                                    new Runnable() {
                                        @Override
                                        public void run() {
                                            try {
                                                // 切模式前先把水印那份换好（跟 UI 切模式时模式门的时序一致）
                                                if (modeNameF.startsWith("xpan")) wmEnterXpan();
                                                else wmExitXpan();
                                                Method m = mmF.getClass().getDeclaredMethod(
                                                        "d0", String.class, boolean.class, boolean.class);
                                                m.setAccessible(true);
                                                Object r = m.invoke(mmF, modeNameF, f1F, f2F);
                                                XposedBridge.log(TAG + "属性切模式 -> " + modeNameF
                                                        + " (" + f1F + "," + f2F + ")，结果=" + r);
                                            } catch (Throwable t) {
                                                Throwable c = (t.getCause() != null) ? t.getCause() : t;
                                                XposedBridge.log(TAG + "属性切模式失败: " + c);
                                            }
                                        }
                                    });
                        } catch (Throwable t) {
                            XposedBridge.log(TAG + "属性切模式轮询异常: " + t);
                        }
                    }
                }
            }, "xpan-mode-cmd");
            t.setDaemon(true);
            t.start();
            XposedBridge.log(TAG + "hook installed OK (模式切换辅助 x" + n + ")");
        } catch (Throwable t) {
            XposedBridge.log(TAG + "!! 模式切换辅助失败: " + t);
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
                            wmExitXpan();                         // 水印隔离：把其他模式那份写回去
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
                            // 水印按模式隔离：进 X-Pan 换成 X-Pan 那份，出去换回其他模式那份
                            if (isXpan) wmEnterXpan(); else wmExitXpan();
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
                            } else {
                                // 退出 X-Pan：卸掉可能残留的容器视图，防止与其他模式界面重叠
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
            XC_MethodHook fix = new XC_MethodHook() {
                @Override
                protected void beforeHookedMethod(MethodHookParam param) {
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
     * 拍照 → captureSize()（默认 8192x3024；建 .xpan_smallpic 回 4096x1512；prop 可显式覆盖）
     *   app/HAL 两侧同尺寸 —— 大图档就是让 HAL 出 8192x3024 的放大流
     *   （2026-10-10 实测：HAL 收下 >4096 宽的流，内部从 12.5MP 读出放大，连拍稳定）。
     *   曾试过"app 侧大图 + HAL 侧真读出、借 APS mfll/upscale 放大"的 C 路线：
     *   本机 APS 没有 xpan 的放大算法组件，algo 配置开启后 APS 初始化卡死、快门被禁
     *   （isApsCaptureAlgoInitializing=true, not allow take picture），已判死，勿再试。
     * 认不出来 / 是视频、缩略图、metadata 等 → 原样不动
     */
    private static Size targetFor(Size s, Object wrapper) {
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
                    : (isPreview ? previewSize() : captureSize());
            if (want == null) return null;

            // ★ 变焦 ≠1× 时拍照流回退 4096x1512：8192 宽大流超出活动阵列，HAL 不理
            //   变焦裁剪（2× 实拍只有 ~1.12× 视野，见 sXpanZoomReq 注释）。prop 显式
            //   指定时尊重 prop（老语义，自行承担）。
            //   注：2026-10-11 实验过"改发显式 SCALER_CROP_REGION"的替代方案 ——
            //   HAL 直接崩进程（拍照请求带 crop 就 SIGSEGV），此路不通，勿再试。
            if (!isPreview && forced == null && sXpanZoomReq > 1.05f) {
                Size real = parseSize(DEFAULT_CAPTURE);
                if (real != null) {
                    want = real;
                    dlog("变焦档回退: " + sXpanZoomReq + "x -> " + real);
                }
            }

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
     */
    @SuppressWarnings({"unchecked", "rawtypes"})
    private void onSurfaceSize(XC_MethodHook.MethodHookParam param) {
        Object[] a = param.args;
        String surfaceType = (a != null && a.length > 1 && a[1] instanceof String) ? (String) a[1] : null;
        String cameraType = (a != null && a.length > 2 && a[2] instanceof String) ? (String) a[2] : null;
        Object orig = param.getResult();

        dlog("getSurfaceSize surfaceType=" + surfaceType
                + " cameraType=" + cameraType + " orig=" + describe(orig));

        if (!sXpanActive || surfaceType == null || !(orig instanceof List)) return;
        List<Object> list = (List<Object>) orig;
        if (list.isEmpty()) return;

        String st = surfaceType.toLowerCase();
        Size want;
        if (st.contains("preview")) {
            want = previewSize();
        } else if (st.startsWith("capture") || st.equals("raw_output") || st.equals("raw12_output")) {
            want = captureSize();
            // 注：超广角的 HAL 流尺寸在 targetFor 里单独处理（getHalSurfaceSize 那层），
            // 应用侧尺寸（这里）保持 4096x1512 不变，APS 负责最终输出。
        } else {
            return;
        }
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
     * 想回 6.2MP 小文件档：新建 /sdcard/DCIM/.xpan_smallpic（空文件）并重启相机。
     * 有缓存，改动后重启相机进程生效（与 prop 行为一致）。
     */
    private static Size captureSize() {
        if (sCaptureSize != null) return sCaptureSize;
        String p = prop("debug.xpan.capture");
        Size s = parseSize(p);
        // ★ 默认仍是 8192x3024（用户要求"1× 出 8192"，而应用会把声明尺寸跟着拍照流走，
        //   所以只有拍照流要 8192，出片才会是 8192）。
        //   ⚠️ 已知代价：8192 宽走 HAL 全分辨率(remosaic)通路，实测**自带 2~3 像素错位重影**
        //   （原生裁图看灯臂/窗框是双线）。想换成干净的 4096 通路（出片尺寸会变 4096）：
        //   setprop debug.xpan.capture 4096x1512
        if (s == null && !smallPicEnabled()) {
            s = parseSize(BIG_CAPTURE);
            XposedBridge.log(TAG + "默认大图档: " + BIG_CAPTURE + "（1× 出片才会是 8192；"
                    + "建 /sdcard/DCIM/.xpan_smallpic 或 debug.xpan.capture 可换 4096 干净通路）");
        }
        if (s == null) s = parseSize(DEFAULT_CAPTURE);
        if (s == null) s = new Size(4096, 1512);
        sCaptureSize = s;
        XposedBridge.log(TAG + "captureSize resolved = " + s);
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
