package com.xpanport.hook;

import android.app.Activity;
import android.graphics.ImageFormat;
import android.os.Handler;
import android.util.Pair;
import android.util.Size;

import java.lang.ref.WeakReference;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Member;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.LinkedList;
import java.util.List;
import java.util.Set;
import java.util.WeakHashMap;
import java.util.concurrent.atomic.AtomicInteger;

import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;

/**
 * C17 / camera 7.013.51 的独立适配层。由主入口在 Application.attach 后按精确版本安装。
 * 取证来源：PJX110_17.0.1.201 原 SDK 与相机 smali；不把 C16 混淆名/容器生命周期搬过来。
 * 尺寸只是模块请求档（8192x3024），不代表已验证本机 HAL 支持；XDR/配置/水印 I/O 归父入口。
 */
public final class XpanC17Hook {
    public static final String CAMERA_VERSION = "7.013.51";
    private static final String TAG = "XpanPort C17: ";
    private static final String XPAN = "xpan";
    private static final String FEATURE = "com.oplus.camera.feature.xpan";
    private static final String SDK = "com.oplus.ocs.camera.";
    private static final String APP = "com.oplus.camera.";
    private static final String FRAME = "com.xpanport.hook.c17.frame";
    private static final WeakHashMap<ClassLoader, InstallResult> INSTALLS = new WeakHashMap<>();

    public interface Gate { boolean enabled() throws Throwable; }

    /**
     * 水印事务由父入口实现：before 保存/换入目标模式份，after 根据 actualMode 提交或回滚。
     * actualMode 来自已提交的 BaseMode.U0，不是请求参数，也不是建流标志。
     * 回调仅发生于真实模式调用；安装阶段不调用回调、不读取静态 DataKeys、不做持久化。
     * oldMode/actualMode 为 null 表示没有运行模式；读取失败会明确诊断，以 null 交父入口补偿。
     * before 已通知后，即使 gate 撤销，after 仍交还 actual 对账；父入口按当前 gate 安全恢复。
     */
    public interface Callbacks {
        Object beforeModeChange(Object manager, String oldMode, String requestedMode) throws Throwable;
        void afterModeChange(Object manager, String oldMode, String requestedMode,
                String actualMode, Object token, Throwable originalFailure) throws Throwable;
    }

    public static final class InstallResult {
        public final boolean installed;
        public final String cameraVersion;
        public final int validatedBindings;
        public final int registeredHooks;
        public final String detail;
        private final XpanC17Hook owner;

        private InstallResult(boolean ok, String version, int bindings, int hooks,
                String detail, XpanC17Hook owner) {
            this.installed = ok;
            this.cameraVersion = version;
            this.validatedBindings = bindings;
            this.registeredHooks = hooks;
            this.detail = detail;
            this.owner = owner;
        }

        public RuntimeStatus runtimeStatus() {
            return owner == null ? new RuntimeStatus(null) : new RuntimeStatus(owner);
        }
    }

    /** 安装成功只说明 ABI/注册通过；UI、拍照、HAL 仍需设备运行证据。 */
    public static final class RuntimeStatus {
        public final boolean enabled;
        public final int generations, presenters, viewCreates, containerEvents, captures, failures;
        public final String committedMode;
        public final Integer containerState;
        public final String detail;

        private RuntimeStatus(XpanC17Hook h) {
            enabled = h != null && h.active();
            generations = h == null ? 0 : h.generations.get();
            presenters = h == null ? 0 : h.presenters.get();
            viewCreates = h == null ? 0 : h.viewCreates.get();
            containerEvents = h == null ? 0 : h.containerEvents.get();
            captures = h == null ? 0 : h.captures.get();
            failures = h == null ? 0 : h.failures.get();
            committedMode = h == null ? null : h.committedMode;
            containerState = h == null ? null : h.containerState;
            detail = h == null ? "not installed" : h.lastDiagnostic;
        }
    }

    /** 测试兼容入口；生产入口必须传入可撤销的 gate。 */
    public static InstallResult install(ClassLoader loader, String version, Callbacks cb) {
        return install(loader, version, cb, new Gate() {
            @Override public boolean enabled() { return true; }
        });
    }

    /** 所有耦合成员先验证，再注册；注册全成前 hook 空转，失败反向卸钩，无半套注入。 */
    public static synchronized InstallResult install(ClassLoader loader, String version, Callbacks cb, Gate gate) {
        if (!CAMERA_VERSION.equals(version) || loader == null || cb == null || gate == null) {
            return new InstallResult(false, version, 0, 0,
                    "refused: exact camera 7.013.51, attached app loader, callbacks and gate required", null);
        }
        InstallResult prior = INSTALLS.get(loader);
        if (prior != null) return prior;
        Bindings b = new Bindings();
        XpanC17Hook h = null;
        try {
            b.resolve(loader);                    // Class.forName(false)：不执行目标类静态初始化
            h = new XpanC17Hook(b, cb, gate);
            h.register();
            h.enabled = true;
            InstallResult result = new InstallResult(true, version, b.count, h.hooks.size(),
                    "ABI installed; runtime/UI/HAL unverified; no CameraConfig override; masterMode unchanged", h);
            INSTALLS.put(loader, result);
            XposedBridge.log(TAG + result.detail + " bindings=" + b.count + " hooks=" + h.hooks.size());
            return result;
        } catch (Throwable failure) {
            int residual = h == null ? 0 : h.rollback();
            String detail = "core install failed at " + b.current + ": " + failure
                    + "; remaining hooks=" + residual + " (disabled)";
            InstallResult result = new InstallResult(false, version, b.count, residual, detail, h);
            if (h != null) {
                h.lastDiagnostic = detail;
                INSTALLS.put(loader, result);     // 注册曾开始：残钩保持 disabled，不重试堆积
            }
            XposedBridge.log(TAG + detail);
            return result;
        }
    }

    private final Bindings b;
    private final Callbacks callbacks;
    private final Gate gate;
    private final ArrayList<XC_MethodHook.Unhook> hooks = new ArrayList<>();
    private final ThreadLocal<Boolean> building = new ThreadLocal<>();
    private final ThreadLocal<Generation> generation = new ThreadLocal<>();
    private final ThreadLocal<StreamIdentity> streamIdentity = new ThreadLocal<>();
    private final ArrayList<SurfaceRecord> xpanSurfaces = new ArrayList<>();
    private final Size preview = new Size(2304, 1048);
    private final Size capture = new Size(8192, 3024);
    private final AtomicInteger generations = new AtomicInteger();
    private final AtomicInteger presenters = new AtomicInteger();
    private final AtomicInteger viewCreates = new AtomicInteger();
    private final AtomicInteger containerEvents = new AtomicInteger();
    private final AtomicInteger captures = new AtomicInteger();
    private final AtomicInteger failures = new AtomicInteger();
    private volatile boolean enabled;
    private volatile String committedMode;
    private volatile Integer containerState;
    private volatile String lastDiagnostic = "UI activation pending: no generation/view/container events yet; HAL unverified";
    private volatile WeakReference<Object> container = new WeakReference<>(null);

    private XpanC17Hook(Bindings b, Callbacks cb, Gate gate) { this.b = b; callbacks = cb; this.gate = gate; }

    /** 每次调用独占 marker；未进入的 frame 不能用 null prior 清除外层 scope。 */
    private static final class Scope<T> {
        final T prior;
        boolean entered;
        Scope(T prior) { this.prior = prior; }
    }

    private static final class Generation {
        final String mode;
        final int camera, mask;
        Generation(String mode, int camera, int mask) { this.mode = mode; this.camera = camera; this.mask = mask; }
    }

    private static final class StreamIdentity {
        final String stream, camera;
        StreamIdentity(String stream, String camera) { this.stream = stream; this.camera = camera; }
    }

    private static final class SurfaceRecord extends WeakReference<Object> {
        final StreamIdentity identity;
        SurfaceRecord(Object wrapper, StreamIdentity identity) { super(wrapper); this.identity = identity; }
    }

    private static final class Transition {
        final String oldMode, requested;
        Object token;
        boolean notified;
        Transition(String oldMode, String requested) { this.oldMode = oldMode; this.requested = requested; }
    }

    private void register() {
        hook(b.switchMode, new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam p) {
                if (!active()) return;
                try {
                    String old = actualMode(p.thisObject);
                    String requested = (String) p.args[0];
                    Transition t = new Transition(old, requested);
                    p.setObjectExtra(FRAME, t);
                    if (requested != null && !requested.equals(old) && active()) {
                        t.notified = true;
                        t.token = callbacks.beforeModeChange(p.thisObject, old, requested);
                    }
                } catch (Throwable e) {
                    diagnostic("mode before callback/read failed; original switch preserved", e);
                    // after 仍按实际提交状态交还父入口对账；可选水印失败不能阻断原相机切换。
                }
            }
            @Override protected void afterHookedMethod(MethodHookParam p) {
                Transition t = (Transition) p.getObjectExtra(FRAME);
                if (t == null || (!t.notified && !active())) return;
                String actual = null;
                boolean read = false;
                try {
                    actual = actualMode(p.thisObject);
                    read = true;
                } catch (Throwable e) {
                    diagnostic("cannot read committed mode; callback receives null for safe compensation", e);
                }
                if (t.notified) {
                    try {
                        callbacks.afterModeChange(p.thisObject, t.oldMode, t.requested, actual,
                                t.token, p.hasThrowable() ? p.getThrowable() : null);
                    } catch (Throwable e) { diagnostic("mode after callback failed", e); }
                }
                if (!read || !active()) return;  // 对账回调例外；gateoff 不开启新的注入/见证状态
                committedMode = actual;
                if (!same(t.oldMode, actual)) {
                    note("actual mode " + t.oldMode + " -> " + actual + " (requested=" + t.requested + ")");
                    if (!XPAN.equals(actual)) { container = new WeakReference<>(null); containerState = null; }
                }
            }
        });
        // q3.X 是原程序在 i5.a 写入后调用的刷新点。只见证提交，不拿 pendingMode 充当实际模式。
        hook(b.commitRefresh, new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam p) {
                if (!active()) return;
                try {
                    String actual = actualMode(p.thisObject);
                    if (active()) committedMode = actual;
                } catch (Throwable e) { diagnostic("commit refresh read failed", e); }
            }
        });
        hook(b.generate, new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam p) {
                Scope<Generation> frame = new Scope<>(generation.get());
                p.setObjectExtra(FRAME, frame);
                if (!active()) return;
                generation.set(new Generation((String) p.args[1], (Integer) p.args[2], (Integer) p.args[3]));
                frame.entered = true;
                if (XPAN.equals(p.args[1])) generations.incrementAndGet();
            }
            @Override protected void afterHookedMethod(MethodHookParam p) {
                Scope<?> frame = (Scope<?>) p.getObjectExtra(FRAME);
                if (frame == null || !frame.entered) return;
                try {
                    if (active() && XPAN.equals(p.args[1]) && p.hasThrowable()) {
                        diagnostic("Xpan feature generation failed", p.getThrowable());
                    }
                } finally { restore(generation, (Generation) frame.prior); }
            }
        });
        hook(b.featureList, new XC_MethodHook() {
            @Override protected void afterHookedMethod(MethodHookParam p) {
                if (!active() || p.hasThrowable()) return;
                Generation g = generation.get();
                if (g == null || !XPAN.equals(g.mode) || g.mask != (Integer) p.args[0]) return;
                Object r = p.getResult();
                if (!(r instanceof Set)) { diagnostic("feature list is not Set<String>; no injection", null); return; }
                Set<?> original = (Set<?>) r;
                for (Object value : original) {
                    if (!(value instanceof String)) { diagnostic("unknown feature list element; no injection", null); return; }
                }
                if (!original.contains(FEATURE)) {
                    LinkedHashSet<String> copy = new LinkedHashSet<>();
                    for (Object value : original) copy.add((String) value);
                    copy.add(FEATURE);             // 仅此调用副本；不污染配置缓存/非 Xpan 模式
                    if (!active()) return;
                    p.setResult(copy);
                    note("scoped Xpan feature added for camera=" + g.camera);
                }
            }
        });
        hook(b.factory, new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam p) {
                if (!active() || !FEATURE.equals(p.args[3])) return;
                if (!XPAN.equals(p.args[4]) && active()) {
                    p.setResult(null);            // BEFORE：非 Xpan 模式连构造副作用都不发生
                    note("blocked foreign Xpan feature request mode=" + p.args[4]);
                }
            }
            @Override protected void afterHookedMethod(MethodHookParam p) {
                if (!active() || !FEATURE.equals(p.args[3]) || !XPAN.equals(p.args[4])) return;
                if (p.hasThrowable() || !b.presenter.isInstance(p.getResult())) {
                    diagnostic("native Xpan factory failed/unknown presenter; no manual lifecycle replay", p.getThrowable());
                } else {
                    presenters.incrementAndGet();
                    note("native bl.d created; waiting for dl.w.h / uq.l activation");
                }
            }
        });
        hook(b.viewCreate, new XC_MethodHook() {
            @Override protected void afterHookedMethod(MethodHookParam p) {
                if (!active()) return;
                if (p.hasThrowable()) { diagnostic("native dl.w.h failed", p.getThrowable()); return; }
                try {
                    Object c = b.viewContainer.get(p.thisObject);
                    if (!b.container.isInstance(c)) { diagnostic("dl.w.h has no uq.l container; UI unverified", null); return; }
                    int state = b.containerState.getInt(c);
                    if (!active()) return;
                    container = new WeakReference<>(c);
                    containerState = state;
                    viewCreates.incrementAndGet();
                    note("native dl.w.h observed, uq.l.C0=" + containerState
                            + (containerState == -1 ? "; UI still unactivated; no forced replay" : "; lifecycle observed, rendering unverified"));
                } catch (Throwable e) { diagnostic("C17 UI witness failed", e); }
            }
        });
        hook(b.setContainerState, new XC_MethodHook() {
            @Override protected void afterHookedMethod(MethodHookParam p) {
                if (!active() || p.hasThrowable() || container.get() != p.thisObject) return;
                try {
                    int state = b.containerState.getInt(p.thisObject);
                    if (!active()) return;
                    containerState = state;
                    containerEvents.incrementAndGet();
                    note("native uq.l state=" + containerState + "; no injected lifecycle");
                } catch (Throwable e) { diagnostic("container state witness failed", e); }
            }
        });
        hook(b.build, new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam p) {
                Scope<Boolean> frame = new Scope<>(building.get());
                p.setObjectExtra(FRAME, frame);
                if (!active()) return;
                building.set(p.thisObject != null && p.thisObject.getClass() == b.xpanMode);
                frame.entered = true;
            }
            @Override protected void afterHookedMethod(MethodHookParam p) {
                Scope<?> frame = (Scope<?>) p.getObjectExtra(FRAME);
                if (frame == null || !frame.entered) return;
                try {
                    if (active() && !p.hasThrowable() && p.getResult() instanceof LinkedList) {
                        if (!Boolean.TRUE.equals(building.get())) {
                            for (Object w : (LinkedList<?>) p.getResult()) {
                                if (b.wrapper.isInstance(w)) markSurface(w, null);
                            }
                        }
                    }
                } finally { restore(building, (Boolean) frame.prior); }
            }
        });
        // getSurfaceWrapper 的 p3 是原始 stream 字符串；构造器末尾 int 只是 reader/view 类别。
        // RAW 与 YUV 的 createReaderSurface 都写 IMAGE_READER=3，不能从这个 int 反猜 stream。
        hook(b.surfaceFactory, new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam p) {
                Scope<StreamIdentity> frame = new Scope<>(streamIdentity.get());
                p.setObjectExtra(FRAME, frame);
                if (!active()) return;
                streamIdentity.set(new StreamIdentity((String) p.args[2], (String) p.args[3]));
                frame.entered = true;
            }
            @Override protected void afterHookedMethod(MethodHookParam p) {
                Scope<?> frame = (Scope<?>) p.getObjectExtra(FRAME);
                if (frame == null || !frame.entered) return;
                try {
                    if (active() && !p.hasThrowable() && b.wrapper.isInstance(p.getResult())) {
                        markSurface(p.getResult(), Boolean.TRUE.equals(building.get()) ? streamIdentity.get() : null);
                    }
                } finally { restore(streamIdentity, (StreamIdentity) frame.prior); }
            }
        });
        hook(b.wrapperConstructor, new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam p) {
                if (!active() || !Boolean.TRUE.equals(building.get())) return;
                StreamIdentity id = streamIdentity.get();
                Size want = id == null ? null : sizeTarget(id, (String) p.args[0], (String) p.args[1],
                        (Integer) p.args[4], (Integer) p.args[5]);
                if (want == null || !(p.args[2] instanceof Size)
                        || (p.args[3] != null && !(p.args[3] instanceof Size)) || !active()) return;
                p.args[2] = want;
                p.args[3] = want;                 // 该构造器直接写字段，不经过两个 setter
            }
            @Override protected void afterHookedMethod(MethodHookParam p) {
                if (!active() || p.hasThrowable() || !Boolean.TRUE.equals(building.get())) return;
                markSurface(p.thisObject, streamIdentity.get());
            }
        });
        hook(b.surfaceSize, new XC_MethodHook() {
            @Override protected void afterHookedMethod(MethodHookParam p) {
                if (!active() || p.hasThrowable() || p.thisObject.getClass() != b.xpanMode
                        || !"rear_main".equals(p.args[2]) || !"rear_main".equals(p.args[3])) return;
                String stream = (String) p.args[1];
                Size want = streamTarget(stream);
                if (want == null) return;         // RAW/DOL/tuning/metadata/fullsize 不猜，不改
                try {
                    int format = (Integer) b.formatByStream.invoke(p.thisObject, stream, p.args[2], p.args[3]);
                    if (!safeFormat(stream, format)) return;
                } catch (Throwable e) { diagnostic("stream format unresolved; surface tuples unchanged", e); return; }
                Object r = p.getResult();
                if (!(r instanceof List) || ((List<?>) r).isEmpty()) return;
                ArrayList<Pair<Size, Size>> copy = new ArrayList<>();
                for (Object item : (List<?>) r) {
                    if (!(item instanceof Pair)) { diagnostic("unknown surface tuple; unchanged", null); return; }
                    Pair<?, ?> pair = (Pair<?, ?>) item;
                    if (!(pair.first instanceof Size) || !(pair.second instanceof Size)) {
                        diagnostic("unknown surface size member; unchanged", null); return;
                    }
                    copy.add(Pair.create(want, want));
                }
                if (active()) p.setResult(copy);
            }
        });
        XC_MethodHook setter = new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam p) {
                if (!active() || !(p.args[0] instanceof Size)) return;
                Size want = wrapperTarget(p.thisObject);
                if (want != null && active()) p.args[0] = want;
            }
        };
        hook(b.setHal, setter);
        hook(b.setApp, setter);
        hook(b.getHal, new XC_MethodHook() {
            @Override protected void afterHookedMethod(MethodHookParam p) {
                if (!active() || p.hasThrowable() || !(p.getResult() instanceof Size)) return;
                Size want = wrapperTarget(p.thisObject); // mHal=null 回退 App 的实际 getter，仍须覆盖
                if (want != null && active()) p.setResult(want);
            }
        });
        // Hc 的 p5/p6，即零基 args[4]/[5]：clickValues/zoomPointList。其他浮点列表含义不同。
        XC_MethodHook zoomLists = new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam p) {
                if (!active() || !XPAN.equals(p.args[19])) return;
                ArrayList<Float> clicks = oneX(p.args[4]), points = oneX(p.args[5]);
                if (clicks == null || points == null) { diagnostic("unknown zoom list; no partial list mutation", null); return; }
                if (!active()) return;
                p.args[4] = clicks;
                p.args[5] = points;
            }
        };
        hook(b.zoomInit, zoomLists);
        hook(b.zoomSubclassInit, zoomLists);       // f.Hc 在调用 super 前会修改 p5；副本先交给它
        XC_MethodHook zoomSet = new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam p) {
                if (active() && modelIsXpan(p.thisObject) && active()) p.args[0] = 1.0f;
            }
        };
        hook(b.zoomUpdate, zoomSet);              // fl.g.i(F)：保存 requested 和 real ratio
        hook(b.zoomRemember, zoomSet);            // fl.g.j(F)：保存前/后摄记忆倍率
        hook(b.zoomReal, new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam p) {
                if (active() && modelIsXpan(p.thisObject) && active()) p.setResult(1.0f);
            }
        });
        hook(b.zoomRead, new XC_MethodHook() {
            @Override protected void afterHookedMethod(MethodHookParam p) {
                if (active() && !p.hasThrowable() && modelIsXpan(p.thisObject) && active()) p.setResult(1.0f);
            }
        });
        hook(b.takePicture, new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam p) {
                if (!active() || p.args[0] == null) return;
                try {
                    if (XPAN.equals(b.captureMode.get(p.args[0])) && active()) {
                        int n = captures.incrementAndGet();
                        if (n <= 3) note("C17 takePicture dispatched #" + n + "; success/HAL not established");
                    }
                } catch (Throwable e) { diagnostic("capture witness failed", e); }
            }
        });
    }

    private String actualMode(Object manager) throws IllegalAccessException {
        Object running = b.running.get(manager);
        if (running == null) return null;
        Object mode = b.runningMode.get(running);
        return mode == null ? null : (String) b.modeName.get(mode);
    }

    private Size streamTarget(String stream) {
        if ("preview".equals(stream) || "preview_frame".equals(stream)) return preview;
        if ("capture".equals(stream) || "capture_yuv".equals(stream)) return capture;
        return null;
    }

    // ImageFormat 标准值与原 SDK getSurfaceFormatBySurfaceType 对照：RAW16=32/RAW10=37/RAW12=38。
    // tuning_raw/yuv 的 0x32315659 不白名单；PRIVATE=34/未知 vendor BLOB 未证实也不改。
    static boolean safeFormat(String stream, int format) {
        if (format == ImageFormat.RAW_SENSOR || format == ImageFormat.RAW_PRIVATE
                || format == ImageFormat.RAW10 || format == ImageFormat.RAW12) return false;
        if ("preview".equals(stream) || "preview_frame".equals(stream)) return format == ImageFormat.YUV_420_888;
        if ("capture".equals(stream) || "capture_yuv".equals(stream)) {
            return format == ImageFormat.YUV_420_888 || format == ImageFormat.JPEG || format == ImageFormat.HEIC;
        }
        return false;
    }

    static boolean safeSurface(String stream, String camera, String usage, int format, int type) {
        if (!"rear_main".equals(camera) || !safeFormat(stream, format)) return false;
        if ("capture".equals(stream) || "capture_yuv".equals(stream)) {
            return type == 3 && "surface_key_picture".equals(usage); // IMAGE_READER，不是 RAW 身份证明
        }
        // 原 SDK SurfaceType：SURFACE_VIEW=1/TEXTURE=2/READER=3/PREVIEW_IMAGE=5/FRAME=6。
        if (type != 1 && type != 2 && type != 3 && type != 5 && type != 6) return false;
        return ("preview".equals(stream) && "surface_key_preview".equals(usage))
                || ("preview_frame".equals(stream) && "surface_key_preview_frame".equals(usage));
    }

    private Size sizeTarget(StreamIdentity id, String camera, String usage, int format, int type) {
        return same(id.camera, camera) && safeSurface(id.stream, camera, usage, format, type) ? streamTarget(id.stream) : null;
    }

    private Size wrapperTarget(Object wrapper) {
        Boolean scope = building.get();
        if (scope != null && !scope) return null;  // 非 Xpan 建流优先于旧 wrapper 标记
        StreamIdentity id = surfaceIdentity(wrapper);
        if (scope != null) {
            StreamIdentity current = streamIdentity.get();
            if (current == null || (id != null && (!same(id.stream, current.stream) || !same(id.camera, current.camera)))) return null;
            id = current;
        }
        if (id == null) return null;              // 只有 usage/format，没有可追溯 stream 身份：不改
        try {
            return sizeTarget(id, (String) b.cameraType.invoke(wrapper), (String) b.usage.invoke(wrapper),
                    (Integer) b.format.invoke(wrapper), (Integer) b.surfaceType.invoke(wrapper));
        } catch (Throwable e) { diagnostic("wrapper identity/usage/format failed; unchanged", e); }
        return null;
    }

    // SurfaceWrapper.equals 会调用 getHalSurfaceSize；WeakHashMap 在 getter 内查找可递归。
    // 严格按对象身份记弱引用，保存原 stream（包括 RAW），不把标记传给另一模式的同值面。
    private void markSurface(Object wrapper, StreamIdentity identity) {
        synchronized (xpanSurfaces) {
            if (!active()) return;
            for (int i = xpanSurfaces.size() - 1; i >= 0; i--) {
                Object w = xpanSurfaces.get(i).get();
                if (w == null || w == wrapper) xpanSurfaces.remove(i);
            }
            if (identity != null) xpanSurfaces.add(new SurfaceRecord(wrapper, identity));
        }
    }
    private StreamIdentity surfaceIdentity(Object wrapper) {
        synchronized (xpanSurfaces) {
            StreamIdentity found = null;
            for (int i = xpanSurfaces.size() - 1; i >= 0; i--) {
                SurfaceRecord record = xpanSurfaces.get(i);
                Object w = record.get();
                if (w == null) xpanSurfaces.remove(i);
                else if (w == wrapper) found = record.identity;
            }
            return found;
        }
    }

    private boolean modelIsXpan(Object model) {
        try {
            Object config = b.zoomConfigure.get(model);
            // fl.g.d 是 AudioManager surround_record，不是 mode！真正归属由 ab.c.W0 标记。
            return config != null && XPAN.equals(b.zoomMode.get(config));
        } catch (Throwable e) { diagnostic("zoom model ownership failed; unchanged", e); return false; }
    }

    static ArrayList<Float> oneX(Object value) {
        if (!(value instanceof ArrayList)) return null;
        boolean found = false;
        for (Object item : (ArrayList<?>) value) {
            if (!(item instanceof Float)) return null;
            if (Float.compare((Float) item, 1.0f) == 0) found = true;
        }
        if (!found) return null;                  // 未验证含 1x 的列表不凭空造档
        ArrayList<Float> copy = new ArrayList<>(1);
        copy.add(1.0f);
        return copy;
    }

    private boolean active() {
        if (!enabled) return false;
        try { return gate.enabled(); }
        catch (Throwable e) {
            try { diagnostic("gate check failed; mutation disabled for this call", e); }
            catch (Throwable ignored) { /* Gate/诊断失败都不能传回原相机。 */ }
            return false;
        }
    }

    private static <T> void restore(ThreadLocal<T> local, T prior) {
        if (prior == null) local.remove(); else local.set(prior);
    }
    private static boolean same(Object a, Object z) { return a == z || (a != null && a.equals(z)); }
    private void note(String message) { lastDiagnostic = message; XposedBridge.log(TAG + message); }
    private void diagnostic(String message, Throwable error) {
        int n = failures.incrementAndGet();
        lastDiagnostic = message + (error == null ? "" : ": " + error);
        if (n <= 12) {
            try { XposedBridge.log(TAG + "!! " + lastDiagnostic); }
            catch (Throwable ignored) { /* 故障诊断不能阻断原相机或 after 补偿。 */ }
        } // 故障高信号，热路径不刷屏
    }
    private void hook(Member method, XC_MethodHook callback) {
        b.current = method.toString();
        XC_MethodHook.Unhook unhook = XposedBridge.hookMethod(method, callback);
        if (unhook == null) throw new IllegalStateException("hookMethod returned no rollback handle");
        hooks.add(unhook);
    }
    private int rollback() {
        enabled = false;
        int remaining = 0;
        for (int i = hooks.size() - 1; i >= 0; i--) {
            try { hooks.get(i).unhook(); }
            catch (Throwable e) { remaining++; diagnostic("hook rollback failed", e); }
        }
        return remaining;
    }

    /** 精确参数/返回类型/静态性验证，不按同名或参数个数猜测；所有字段均不读取静态值。 */
    private static final class Bindings {
        int count;
        String current = "version/loader";
        Class<?> xpanMode, wrapper, presenter, container;
        Method build, surfaceSize, surfaceFactory, formatByStream, getHal, setHal, setApp, usage, cameraType, format, surfaceType, takePicture;
        Method generate, factory, featureList, viewCreate, setContainerState, switchMode, commitRefresh;
        Method zoomInit, zoomSubclassInit, zoomUpdate, zoomRemember, zoomRead, zoomReal;
        Constructor<?> wrapperConstructor;
        Field running, runningMode, modeName, captureMode, containerState, viewContainer, zoomConfigure, zoomMode;

        void resolve(ClassLoader loader) throws Exception {
            Class<?> producer = cls(loader, SDK + "producer.mode.BaseMode");
            xpanMode = cls(loader, SDK + "producer.mode.XpanMode");
            if (!producer.isAssignableFrom(xpanMode)) throw new IllegalStateException("XpanMode is not producer BaseMode");
            Class<?> cfg = cls(loader, SDK + "common.parameter.SdkCameraDeviceConfig");
            wrapper = cls(loader, SDK + "common.surface.SurfaceWrapper");
            build = method(producer, "buildStreamSurface", LinkedList.class, false, cfg, String.class);
            surfaceSize = method(xpanMode, "getSurfaceSize", List.class, false, cfg, String.class, String.class, String.class, int.class);
            surfaceFactory = method(producer, "getSurfaceWrapper", wrapper, false, cfg, int.class,
                    String.class, String.class, Size.class, Size.class, boolean.class, String.class);
            formatByStream = method(producer, "getSurfaceFormatBySurfaceType", int.class, false,
                    String.class, String.class, String.class);
            getHal = method(wrapper, "getHalSurfaceSize", Size.class, false);
            method(wrapper, "getAppSurfaceSize", Size.class, false);
            setHal = method(wrapper, "setHalSurfaceSize", void.class, false, Size.class);
            setApp = method(wrapper, "setAppSurfaceSize", void.class, false, Size.class);
            usage = method(wrapper, "getSurfaceUsage", String.class, false);
            cameraType = method(wrapper, "getCameraType", String.class, false);
            format = method(wrapper, "getFormat", int.class, false);
            surfaceType = method(wrapper, "getSurfaceType", int.class, false);
            wrapperConstructor = constructor(wrapper, String.class, String.class, Size.class, Size.class, int.class, int.class);
            Class<?> tag = cls(loader, SDK + "common.util.CameraRequestTag");
            captureMode = field(tag, "mCaptureMode", String.class);
            takePicture = method(cls(loader, SDK + "producer.device.Camera2Impl"), "takePicture", void.class,
                    false, tag, cls(loader, SDK + "metadata.parameter.Parameter"), Handler.class, boolean.class, boolean.class);
            Class<?> ui = cls(loader, APP + "protocal.ui.a");
            Class<?> appMode = cls(loader, APP + "module.BaseMode");
            generate = method(cls(loader, "eb.i1"), "f", void.class, false, Activity.class, String.class,
                    int.class, int.class, cls(loader, APP + "filter.IEffectProcessor"), appMode, cls(loader, "gp.a"));
            factory = method(cls(loader, "eb.f0"), "c", cls(loader, APP + "mg"), true,
                    int.class, Activity.class, ui, String.class, String.class);
            featureList = method(cls(loader, SDK + "config.FeatureConfigureAdapter"), "getFeatureInfoList", Set.class, false, int.class);
            presenter = cls(loader, "bl.d");
            constructor(presenter, int.class, Activity.class, ui, String.class);
            if (!factory.getReturnType().isAssignableFrom(presenter)) throw new IllegalStateException("bl.d does not implement camera.mg");
            Class<?> view = cls(loader, "dl.w");
            viewCreate = method(view, "h", void.class, false, Activity.class, ui, cls(loader, "dl.j$f"), cls(loader, "bl.d$b"));
            container = cls(loader, "uq.l");
            method(ui, "gf", container, false);
            containerState = field(container, "C0", int.class);
            setContainerState = method(container, "uh", void.class, false, int.class);
            viewContainer = field(view, "T", cls(loader, "io.a"));
            if (!viewContainer.getType().isAssignableFrom(container)) throw new IllegalStateException("uq.l is not io.a");
            Class<?> manager = cls(loader, "cm.q3"), runningClass = cls(loader, "cm.i5");
            switchMode = method(manager, "o0", boolean.class, false, String.class, boolean.class, boolean.class);
            commitRefresh = method(manager, "X", void.class, false);
            running = field(manager, "a", runningClass);
            runningMode = field(runningClass, "a", appMode);
            modeName = field(appMode, "U0", String.class);
            Class<?> zoom = cls(loader, APP + "feature.zoom.view.ZoomSeekBar");
            Class<?>[] init = {float.class, cls(loader, "fl.h"), float.class, float.class,
                    ArrayList.class, ArrayList.class, ArrayList.class, int.class,
                    boolean.class, boolean.class, boolean.class, boolean.class, boolean.class, boolean.class,
                    ArrayList.class, boolean.class, boolean.class, float.class, ArrayList.class, String.class};
            zoomInit = method(zoom, "Hc", void.class, false, init);
            Class<?> zoomSub = cls(loader, APP + "feature.zoom.view.f");
            if (!zoom.isAssignableFrom(zoomSub)) throw new IllegalStateException("zoom f is not ZoomSeekBar");
            zoomSubclassInit = method(zoomSub, "Hc", void.class, false, init);
            Class<?> model = cls(loader, "fl.g"), configure = cls(loader, "ab.c");
            zoomConfigure = field(model, "j", configure);
            zoomMode = field(configure, "W0", String.class);
            method(configure, "d0", void.class, false, String.class);
            zoomUpdate = method(model, "i", void.class, false, float.class);
            zoomRemember = method(model, "j", void.class, false, float.class);
            zoomRead = method(model, "h", float.class, false);
            zoomReal = method(model, "g", float.class, false, float.class);
        }
        private Class<?> cls(ClassLoader loader, String name) throws ClassNotFoundException {
            current = name;
            return Class.forName(name, false, loader);
        }
        private Method method(Class<?> owner, String name, Class<?> result, boolean statik,
                Class<?>... params) throws NoSuchMethodException {
            current = owner.getName() + "." + name;
            Method m = owner.getDeclaredMethod(name, params);
            if (m.getReturnType() != result || Modifier.isStatic(m.getModifiers()) != statik
                    || (!owner.isInterface() && Modifier.isAbstract(m.getModifiers()))) {
                throw new NoSuchMethodException("unexpected ABI " + m + "; expected return=" + result.getName() + " static=" + statik);
            }
            m.setAccessible(true);
            count++;
            return m;
        }
        private Field field(Class<?> owner, String name, Class<?> type) throws NoSuchFieldException {
            current = owner.getName() + "." + name;
            Field f = owner.getDeclaredField(name);
            if (f.getType() != type || Modifier.isStatic(f.getModifiers())) throw new NoSuchFieldException("unexpected field ABI " + f);
            f.setAccessible(true);
            count++;
            return f;
        }
        private Constructor<?> constructor(Class<?> owner, Class<?>... params) throws NoSuchMethodException {
            current = owner.getName() + ".<init>";
            Constructor<?> c = owner.getDeclaredConstructor(params);
            c.setAccessible(true);
            count++;
            return c;
        }
    }
}
