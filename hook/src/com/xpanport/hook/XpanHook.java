package com.xpanport.hook;

import android.content.Context;
import android.graphics.ImageFormat;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.params.StreamConfigurationMap;
import android.util.Size;
import android.util.Pair;

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

        // 下面两个是排查用的取证 hook，日志量大，默认关闭。
        // 需要复现问题时：setprop debug.xpan.diag 1，重启相机即可。
        // installXpanContainerWake 也放这里：它只是把休眠的 XpanUIContainer 状态推回 0，
        // 但因为应用从不激活这个容器（XPanPresenter/XPanViewManagerV3 都没被创建），
        // 推回去也不会让 X-Pan 专属 UI / 显影动画出现，反而多改一处应用状态，所以默认不开。
        if ("1".equals(prop("debug.xpan.diag"))) {
            installXpanContainerWake(lp);
            installShutterWatch(lp);
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
                    protected void beforeHookedMethod(MethodHookParam param) {
                        try {
                            XposedBridge.log(TAG + "zoom 下发 HAL gh.z.b0 e0="
                                    + field(param.thisObject, "e0"));
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
        // 2) 工厂闸：只许 xpan 模式创建 XPanPresenter
        try {
            Class<?> d0 = Class.forName("q7.d0", false, lp.classLoader);
            Class<?> ui = Class.forName("com.oplus.camera.protocal.ui.a", false, lp.classLoader);
            Method b = d0.getDeclaredMethod("b", String.class, String.class,
                    int.class, android.app.Activity.class, ui);
            XposedBridge.hookMethod(b, new XC_MethodHook() {
                @Override
                protected void beforeHookedMethod(MethodHookParam param) {
                    try {
                        if (!FEAT_XPAN.equals(param.args[0])) return;
                        String mode = String.valueOf(param.args[1]);
                        if ("xpan".equals(mode)) {
                            XposedBridge.log(TAG + "FeatureFactory 放行: xpan 功能请求自 mode="
                                    + mode);
                            return;
                        }
                        param.setResult(null);   // 其他模式装作不支持
                        XposedBridge.log(TAG + "FeatureFactory 拦截: xpan 功能请求自 mode="
                                + mode + "（已返回 null）");
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
     *  ★ 关键：必须"让应用先自己初始化，失败了我们才补"。
     *  应用的初始化有 `Q == -1 才执行` 的守卫（zl.q.c1()）。如果我们在它之前就把 Q 改成非 -1，
     *  应用会认为"已经初始化过"从而**整个跳过**自己的装配流程 —— 结果滤镜面板(dh/w.Y)、
     *  各种监听全都没接上，表现就是"右下角滤镜点不开"（实测 2026-10-06，直开态也会中招）。
     *  所以这里先等一段宽限期（sXpanEnterTime + GRACE），宽限期内一律不插手。
     */
    private static volatile long sXpanEnterTime = 0L;
    private static final long GRACE_MS = 2600L;

    private boolean driveXpanContainer() {
        try {
            if (!sXpanActive) return true;   // 已退出 X-Pan：绝不能再把视图建上去（会与照片等界面重叠）
            if (System.currentTimeMillis() - sXpanEnterTime < GRACE_MS) {
                return false;                // 宽限期内：让应用自己初始化（别抢它的活）
            }
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
            if (fQ.getInt(container) != -1) return true;  // 已被应用自己初始化
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
            // ★ 关键：视图已经建过就不要再 bc() 重建！
            // 应用的滤镜列表/点击监听是挂在"某个视图实例"上的（实测 filterView.setXpanFilterList
            // 记录到实例 4779d4f）。如果这里重建，会造出新实例，而新实例没人给它注册滤镜和监听
            // → 滤镜徽标点不开（用户复现路径：杀进程→X-Pan→照片→X-Pan）。
            // 容器字段 B = XpanPhysicalFilterView，用它判断视图是否已经存在。
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
                XposedBridge.log(TAG + "drive: 视图已存在，跳过重建（保留滤镜装配）");
            }
            // o4() = addViewToRoot：bc 只是把视图建进缓存 map，真正挂到窗口根布局靠 o4。
            // 漏了它视图全在缓存里不上屏（实测 2026-10-06，往返回切后界面仍残缺）。
            java.lang.reflect.Method o4 = zlq.getDeclaredMethod("o4");
            o4.setAccessible(true);
            o4.invoke(container);
            // 重新挂回根布局后强制一次布局：实测往返路径下滤镜面板会被摆到屏幕外
            // （观察到某视图 bounds x 到 2309，而竖屏只有 1264 宽），点开也看不见。
            try {
                Object anyView = field(container, "B");
                if (anyView instanceof android.view.View) {
                    android.view.View root = ((android.view.View) anyView).getRootView();
                    if (root != null) {
                        root.requestLayout();
                        root.invalidate();
                    }
                }
            } catch (Throwable ignored) {
            }
            boolean skin = (Boolean) zlq.getDeclaredMethod("b5").invoke(container);
            fQ.setInt(container, skin ? 6 : 0);
            zlq.getDeclaredMethod("ic").invoke(container);
            if ("1".equals(prop("debug.xpan.diag"))) {
                XposedBridge.log(TAG + "drive: 挂载检查 B(滤镜视图)=" + describeView(field(container, "B"))
                        + " C(滤镜面板)=" + describeView(field(container, "C"))
                        + " A(快门)=" + describeView(field(container, "A"))
                        + " o(曝光轮)=" + describeView(field(container, "o")));
            }
            sDriveDone = true;      // 成功：xa() 热路径从此短路，不再做反射
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
                        Object panel = (w == null) ? null : field(w, "Y");  // dh/w.Y : 滤镜面板
                        XposedBridge.log(TAG + "filterListener.a(" + param.args[0] + ") 面板Y="
                                + (panel == null ? "null(点击会无效)" : "ok"));
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
                            XposedBridge.log(TAG + "xpan stream building = " + isXpan);
                            if (isXpan) {
                                // 进入 X-Pan：复位会话级状态
                                sDriveDone = false;
                                sDriveFails = 0;
                                sXpanEnterTime = System.currentTimeMillis();
                                // 宽限期后先试一次（应用自己初始化的话这里会空转）
                                android.os.Handler h = new android.os.Handler(
                                        android.os.Looper.getMainLooper());
                                h.postDelayed(new Runnable() {
                                    @Override
                                    public void run() {
                                        driveXpanContainer();
                                    }
                                }, GRACE_MS + 300);
                                h.postDelayed(new Runnable() {
                                    @Override
                                    public void run() {
                                        driveXpanContainer();
                                    }
                                }, GRACE_MS + 1500);
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
     *
     * 只有能确认 usage 是预览 / 拍照时才改（边界 2）：
     *   预览 → DEFAULT_PREVIEW（可被 debug.xpan.preview 覆盖）
     *   拍照 → DEFAULT_CAPTURE（可被 debug.xpan.capture 覆盖）
     *   认不出来 / 是视频、缩略图、metadata 等 → 原样不动
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
            if (forced == null) forced = isPreview ? DEFAULT_PREVIEW : DEFAULT_CAPTURE;
            Size want = parseSize(forced);
            if (want == null) return null;
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

        XposedBridge.log(TAG + "getSurfaceSize surfaceType=" + surfaceType
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
            XposedBridge.log(TAG + "  -> override [" + surfaceType + "] = " + want);
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

    /** X-Pan 拍照尺寸（实机验证过的组合，可用 debug.xpan.capture 覆盖） */
    private static Size captureSize() {
        if (sCaptureSize != null) return sCaptureSize;
        String p = prop("debug.xpan.capture");
        Size s = parseSize(p != null ? p : DEFAULT_CAPTURE);
        if (s == null) s = new Size(4096, 1512);
        sCaptureSize = s;
        XposedBridge.log(TAG + "captureSize resolved = " + s);
        return s;
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
