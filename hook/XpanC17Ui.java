package com.xpanport.hook;

import android.app.Activity;
import android.view.View;

import java.lang.ref.WeakReference;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Member;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.List;
import java.util.WeakHashMap;

import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;

/**
 * Optional camera 7.013.51 native UI guard; independently installed, not a C16 wake replay.
 * Verified PJX110_17.0.1.201 chain: bl.d.M -> dl.w.h -> ThreadManager.m(g(kind=8))
 * -> uq.l.b7 (inflate/add/layout/uh(6 or 4)/native animation) -> native filter binding.
 * b7 has NO C0==-1 guard. h may return before that runnable; -1 alone is not an init gap.
 * The missing guard is on a queued OLD g: it reads mutable dl.j.y/T/z after dl.w reuse.
 * Native i clears dl.j.A and y, but not ThreadManager's queue; a later h can refill y.
 * Only jobs captured inside this exact h are guarded. No views, listeners, enable states,
 * animations, or native cleanup are owned/rewritten here. Rendering still needs a device.
 */
public final class XpanC17Ui {
    public static final String CAMERA_VERSION = "7.013.51";
    private static final String FRAME = "com.xpanport.hook.c17.ui.create";
    private static final String RUN = "com.xpanport.hook.c17.ui.run";
    private static final WeakHashMap<ClassLoader, InstallResult> INSTALLS = new WeakHashMap<>();
    public interface Gate { boolean enabled() throws Throwable; }

    public static final class InstallResult {
        public final boolean installed;
        public final int validatedBindings, registeredHooks;
        public final String detail;
        private final XpanC17Ui owner;
        private InstallResult(boolean ok, int bindings, int hooks, String detail, XpanC17Ui owner) {
            installed = ok; validatedBindings = bindings; registeredHooks = hooks;
            this.detail = detail; this.owner = owner;
        }
        /** Notifications are hints; q3.a -> i5.a -> BaseMode.U0 is re-read, never trusted blindly. */
        public void onActualMode(Object manager, String oldMode, String actualMode) {
            if (owner != null) owner.refresh(manager);
        }
        public void onGateChanged() { if (owner != null) owner.invalidate(null); }
        /** Explicit identity/configuration events; these never force a native lifecycle replay. */
        public void onUiChanged(Activity activity) { if (owner != null) owner.invalidate(activity); }
        public void onActivityDestroyed(Activity activity) { onUiChanged(activity); }
        public RuntimeStatus runtimeStatus() {
            RuntimeStatus result = new RuntimeStatus();
            if (owner != null) owner.snapshot(result);
            return result;
        }
    }

    /** Live native facts, not hook-call counts. Ready does not assert a rendered Hasselblad UI. */
    public static final class RuntimeStatus {
        public boolean enabled, nativeCreatePending, nativeCreateFailed, nativeReady;
        public boolean physicalNativeState, nativeViewsBound, nativeViewsAttached, nativeFilterBound;
        public final boolean renderingVerified = false;
        public long epoch;
        public Integer containerState;
        public String committedMode;
        public String detail = "not installed";
    }

    /** Resolve the whole coupled ABI before ANY hook; failed optional installation stays dormant. */
    public static synchronized InstallResult install(ClassLoader loader, String version, Gate gate) {
        if (loader == null || gate == null || !CAMERA_VERSION.equals(version)) {
            return new InstallResult(false, 0, 0, "exact camera 7.013.51, app loader and gate required", null);
        }
        InstallResult prior = INSTALLS.get(loader);
        if (prior != null) return prior;
        Bindings b = new Bindings();
        XpanC17Ui h = null;
        try {
            b.resolve(loader);
            h = new XpanC17Ui(b, gate);
            h.register();
            h.enabled = true;
            InstallResult result = new InstallResult(true, b.count, h.hooks.size(),
                    "native create guard installed; no forced wake; UI rendering unverified", h);
            INSTALLS.put(loader, result);
            return result;
        } catch (Throwable failure) {
            int residual = h == null ? 0 : h.rollback();
            String detail = "optional UI ABI/install failed at " + b.current + ": " + errorName(failure)
                    + "; residual hooks=" + residual + " (disabled); native camera unchanged";
            if (h != null) h.detail = detail;
            InstallResult result = new InstallResult(false, b.count, residual, detail, h);
            if (h != null) INSTALLS.put(loader, result); // Do not stack a second group onto residual hooks.
            log(detail);
            return result;
        }
    }

    private final Bindings b;
    private final Gate gate;
    private final ArrayList<XC_MethodHook.Unhook> hooks = new ArrayList<>();
    private final ArrayList<Job> jobs = new ArrayList<>();
    private final ThreadLocal<Scope> creating = new ThreadLocal<>();
    private volatile boolean enabled;
    private String detail = "waiting for actual XPAN native create; no initialization requested";
    private Context current;
    private Create latest;
    private long epoch;
    private XpanC17Ui(Bindings b, Gate gate) { this.b = b; this.gate = gate; }

    private static WeakReference<Object> weak(Object value) { return new WeakReference<>(value); }
    private static final class Context {
        final WeakReference<Object> manager, mode, activity, ui, container;
        final String name;
        Context(Object manager, Object mode, Object activity, Object ui, Object container, String name) {
            this.manager = weak(manager); this.mode = weak(mode); this.activity = weak(activity);
            this.ui = weak(ui); this.container = weak(container); this.name = name;
        }
        boolean same(Context other) {
            return other != null && manager.get() == other.manager.get() && mode.get() == other.mode.get()
                    && activity.get() == other.activity.get() && ui.get() == other.ui.get()
                    && container.get() == other.container.get() && equal(name, other.name);
        }
    }
    private static final class Create {
        final Context context;
        final WeakReference<Object> view, presenter, callback;
        final long epoch;
        boolean started, finished, failed, cancelled;
        Create(Context context, Object view, Object presenter, Object callback, long epoch) {
            this.context = context; this.view = weak(view); this.presenter = weak(presenter);
            this.callback = weak(callback); this.epoch = epoch;
        }
    }
    private static final class Scope {
        final Scope prior;
        Create create;
        boolean entered;
        Scope(Scope prior) { this.prior = prior; }
    }
    private static final class Job extends WeakReference<Object> {
        final Create create;
        boolean consumed;
        Job(Object job, Create create) { super(job); this.create = create; }
    }

    private void register() {
        hook(b.create, new SafeHook() {
            @Override void before(MethodHookParam p) throws Throwable {
                Scope scope = new Scope(creating.get());
                p.setObjectExtra(FRAME, scope);
                creating.set(scope); scope.entered = true; // A skipped nested h must hide an outer scope.
                if (!active()) return;
                scope.create = begin(p.thisObject, p.args);
            }
            @Override void after(MethodHookParam p) {
                Scope scope = (Scope) p.getObjectExtra(FRAME);
                if (scope == null || !scope.entered) return;
                try {
                    if (scope.create != null && p.hasThrowable()) {
                        failed(scope.create, "native dl.w.h failed", p.getThrowable());
                    }
                } finally {
                    if (scope.prior == null) creating.remove(); else creating.set(scope.prior);
                }
            }
        });
        hook(b.jobConstructor, new SafeHook() {
            @Override void after(MethodHookParam p) throws Throwable {
                Scope scope = creating.get();
                if (!active() || p.hasThrowable() || scope == null || !scope.entered || scope.create == null) return;
                if (((Integer) p.args[0]) != 8 || p.args[1] != scope.create.view.get()
                        || p.args[2] != scope.create.callback.get()) return;
                synchronized (XpanC17Ui.this) {
                    for (int i = jobs.size() - 1; i >= 0; i--) if (jobs.get(i).get() == null) jobs.remove(i);
                    jobs.add(new Job(p.thisObject, scope.create));
                }
            }
        });
        hook(b.run, new SafeHook() {
            @Override void before(MethodHookParam p) throws Throwable { guard(p); }
            @Override void after(MethodHookParam p) {
                Job job = (Job) p.getObjectExtra(RUN);
                if (job == null) return;
                synchronized (XpanC17Ui.this) {
                    job.create.finished = true;
                    if (p.hasThrowable()) failed(job.create, "native b7/binding runnable failed", p.getThrowable());
                }
            }
        });
        hook(b.destroy, new SafeHook() {
            @Override void before(MethodHookParam p) {
                if (!active()) return;
                synchronized (XpanC17Ui.this) {
                    for (Job job : jobs) if (job.create.view.get() == p.thisObject) job.create.cancelled = true;
                    if (latest != null && latest.view.get() == p.thisObject) latest.cancelled = true;
                } // Original i/R5/sh/panel cleanup always runs, even with a failed/pending create.
            }
        });
        hook(b.commit, new SafeHook() {
            @Override void before(MethodHookParam p) { refresh(p.thisObject); }
        });
    }

    private synchronized Create begin(Object view, Object[] args) throws Throwable {
        if (args == null || args.length != 4 || !b.presenter.isInstance(args[2])) return null;
        Object presenter = args[2], ui = args[1], container = b.getContainer.invoke(ui);
        if (!b.container.isInstance(container)) return null;
        Object owner = b.containerOwner.get(container), manager = b.getManager.invoke(owner);
        Context context = read(manager);
        if (!"xpan".equals(context.name) || context.activity.get() != args[0] || context.ui.get() != ui
                || context.container.get() != container || b.presenterView.get(presenter) != view
                || b.presenterUi.get(presenter) != ui || b.presenterActivity.get(presenter) != args[0]
                || b.presenterMode.get(presenter) != context.mode.get()
                || b.exposureOwner.get(args[3]) != presenter) return null;
        adopt(context); // Native e/N1/h can precede q3.X, but i5.a has ALREADY been committed.
        if (latest != null) latest.cancelled = true;
        latest = new Create(context, view, presenter, args[3], epoch);
        detail = "native h captured; awaiting its original b7/binding job (no forced initialization)";
        return latest;
    }

    private synchronized void guard(XC_MethodHook.MethodHookParam p) throws Throwable {
        if (!active() || p.hasThrowable()) return;
        Job found = null;
        for (Job job : jobs) if (job.get() == p.thisObject) { found = job; break; }
        if (found == null || b.jobKind.getInt(p.thisObject) != 8) return; // Other R8 branches are untouched.
        Create c = found.create;
        Object view = c.view.get();
        if (view == null || b.viewUi.get(view) == null || b.viewContainer.get(view) == null
                || b.viewPresenter.get(view) == null) return; // Native run already has this skip guard.
        Context actual = read(c.context.manager.get());
        Activity activity = (Activity) c.context.activity.get();
        boolean stale = c.cancelled || found.consumed || c.started || latest != c || c.epoch != epoch
                || !c.context.same(current) || !c.context.same(actual) || !"xpan".equals(actual.name)
                || activity == null || activity.isFinishing() || activity.isDestroyed()
                || b.jobView.get(p.thisObject) != view || b.jobCallback.get(p.thisObject) != c.callback.get()
                || b.viewActivity.get(view) != activity || b.viewUi.get(view) != actual.ui.get()
                || b.viewContainer.get(view) != actual.container.get()
                || b.viewPresenter.get(view) != c.presenter.get()
                || b.presenterView.get(c.presenter.get()) != view
                || b.presenterMode.get(c.presenter.get()) != actual.mode.get();
        if (!active()) return; // Revocation between read and write must leave original behavior intact.
        found.consumed = true;
        if (stale) {
            if (!c.started) c.cancelled = true; // Rejecting a duplicate must not invalidate already-correct UI.
            p.setResult(null); // Only a proven tagged OLD/duplicate native create; no NPE masking/replay.
            return;
        }
        c.started = true;
        p.setObjectExtra(RUN, found); // Once per original job, including failure; never retry it ourselves.
    }

    private Context read(Object manager) throws Throwable {
        Object owner = b.managerOwner.get(manager), ui = b.getUi.invoke(owner);
        Object running = b.running.get(manager), mode = running == null ? null : b.runningMode.get(running);
        Object activity = b.managerActivity.get(manager), container = b.getContainer.invoke(ui);
        if (b.uiActivity.get(ui) != activity || b.getManager.invoke(owner) != manager
                || (mode != null && b.modeActivity.get(mode) != activity)
                || (container != null && (b.containerActivity.get(container) != activity
                        || b.containerOwner.get(container) != owner))) {
            throw new IllegalStateException("native Activity/UI/container ownership mismatch");
        }
        return new Context(manager, mode, activity, ui, container, mode == null ? null : (String) b.modeName.get(mode));
    }
    private void adopt(Context context) {
        if (!context.same(current)) {
            epoch++;
            if (latest != null) latest.cancelled = true;
            current = context;
        }
    }
    private synchronized void refresh(Object manager) {
        if (!active()) return;
        try { adopt(read(manager)); }
        catch (Throwable e) { detail = "actual native state unavailable: " + errorName(e); }
    }
    private synchronized void invalidate(Activity activity) {
        if (activity != null && (current == null || current.activity.get() != activity)) return;
        epoch++;
        if (latest != null) latest.cancelled = true;
    }
    private synchronized void failed(Create create, String message, Throwable failure) {
        create.failed = true;
        detail = message + ": " + errorName(failure) + "; original failure/cleanup preserved, no replay";
        log(detail);
    }

    private synchronized void snapshot(RuntimeStatus s) {
        s.enabled = active(); s.epoch = epoch; s.detail = detail;
        if (!s.enabled || current == null) return;
        try {
            Context actual = read(current.manager.get());
            s.committedMode = actual.name;
            if (!actual.same(current) || !"xpan".equals(actual.name) || latest == null
                    || !latest.context.same(actual)) return;
            Object container = actual.container.get(), view = latest.view.get();
            s.nativeCreateFailed = latest.failed;
            s.nativeCreatePending = !latest.cancelled && !latest.failed && !latest.finished;
            if (container == null || view == null || latest.cancelled) return;
            s.containerState = b.state.getInt(container);
            int state = s.containerState;
            s.physicalNativeState = state == 1 || state == 2 || state == 6 || state == 7; // Native qf predicate.
            Object filter = b.filter.get(container), panel = b.panel.get(container);
            s.nativeViewsBound = filter != null && panel != null && b.viewFilter.get(view) == filter
                    && b.viewPanel.get(view) == panel && b.viewContainer.get(view) == container
                    && b.viewPresenter.get(view) == latest.presenter.get();
            s.nativeViewsAttached = attached(filter) && attached(panel);
            Object listener = filter == null ? null : b.filterListener.get(filter);
            int callbacks = 0;
            if (panel != null) for (Object cb : (List<?>) b.panelCallbacks.get(panel)) {
                if (b.panelListener.isInstance(cb) && b.panelListenerOwner.get(cb) == view) callbacks++;
            }
            s.nativeFilterBound = b.filterListenerClass.isInstance(listener)
                    && b.filterListenerOwner.get(listener) == view && callbacks == 1
                    && nonempty(filter == null ? null : b.filterList.get(filter))
                    && nonempty(panel == null ? null : b.panelList.get(panel));
            s.nativeReady = !s.nativeCreateFailed && state != -1 && s.nativeViewsBound
                    && s.nativeViewsAttached && s.nativeFilterBound;
            if (s.nativeReady) s.nativeCreatePending = false;
        } catch (Throwable e) { s.nativeReady = false; s.detail = "live native UI snapshot failed: " + errorName(e); }
    }
    private static boolean attached(Object value) { return value instanceof View && ((View) value).getParent() != null; }
    private static boolean nonempty(Object value) { return value instanceof List && !((List<?>) value).isEmpty(); }
    private static boolean equal(Object a, Object z) { return a == z || (a != null && a.equals(z)); }
    private boolean active() {
        if (!enabled) return false;
        try { return gate.enabled(); } catch (Throwable e) { return false; }
    }
    private static String errorName(Throwable failure) { return failure == null ? "unknown" : failure.getClass().getName(); }
    private static void log(String message) {
        try { XposedBridge.log("XpanPort C17 UI: " + message); } catch (Throwable ignored) { }
    }
    private abstract class SafeHook extends XC_MethodHook {
        void before(MethodHookParam p) throws Throwable { }
        void after(MethodHookParam p) throws Throwable { }
        @Override protected final void beforeHookedMethod(MethodHookParam p) {
            try { before(p); } catch (Throwable e) { log("optional native guard failed; original preserved: " + errorName(e)); }
        }
        @Override protected final void afterHookedMethod(MethodHookParam p) {
            try { after(p); } catch (Throwable e) { log("optional native observer failed; original preserved: " + errorName(e)); }
        }
    }
    private void hook(Member member, XC_MethodHook callback) {
        b.current = member.toString();
        XC_MethodHook.Unhook unhook = XposedBridge.hookMethod(member, callback);
        if (unhook == null) throw new IllegalStateException("hook has no rollback handle");
        hooks.add(unhook);
    }
    private int rollback() {
        enabled = false;
        int residual = 0;
        for (int i = hooks.size() - 1; i >= 0; i--) {
            try { hooks.get(i).unhook(); } catch (Throwable e) { residual++; }
        }
        return residual;
    }

    private static final class Bindings {
        int count;
        String current = "loader/version";
        Class<?> presenter, container, filterListenerClass, panelListener;
        Method create, destroy, run, commit, getContainer, getManager, getUi;
        Constructor<?> jobConstructor;
        Field running, runningMode, modeName, modeActivity, managerOwner, managerActivity, uiActivity;
        Field presenterView, presenterUi, presenterActivity, presenterMode, exposureOwner;
        Field viewActivity, viewUi, viewPresenter, viewContainer, viewFilter, viewPanel;
        Field containerOwner, containerActivity, state, filter, panel;
        Field jobKind, jobView, jobCallback, filterListener, filterListenerOwner, panelListenerOwner;
        Field filterList, panelList, panelCallbacks;
        void resolve(ClassLoader loader) throws Exception {
            String app = "com.oplus.camera.";
            Class<?> ui = cls(loader, app + "protocal.ui.a"), n6 = cls(loader, app + "n6");
            Class<?> main = cls(loader, "gp.t2"), mainImpl = cls(loader, "gp.b1");
            Class<?> manager = cls(loader, "cm.q3"), runningClass = cls(loader, "cm.i5");
            Class<?> mode = cls(loader, app + "module.BaseMode"), basePresenter = cls(loader, "ic.w");
            Class<?> baseView = cls(loader, "dl.j"), view = cls(loader, "dl.w"), provider = cls(loader, "dl.j$f");
            presenter = cls(loader, "bl.d"); container = cls(loader, "uq.l");
            Class<?> exposure = cls(loader, "bl.d$b"), job = cls(loader, "androidx.lifecycle.g");
            if (!main.isAssignableFrom(mainImpl) || !ui.isAssignableFrom(main) || !baseView.isAssignableFrom(view)
                    || !basePresenter.isAssignableFrom(presenter) || !provider.isAssignableFrom(presenter)
                    || !cls(loader, "io.a").isAssignableFrom(container) || !Runnable.class.isAssignableFrom(job)) {
                throw new IllegalStateException("native C17 class hierarchy mismatch");
            }
            constructor(presenter, int.class, Activity.class, ui, String.class);
            constructor(container, Activity.class, n6, cls(loader, "mo.o"), cls(loader, app + "protocal.ui.context.EventMessageUIContainerContext"));
            create = method(view, "h", void.class, Activity.class, ui, provider, exposure);
            destroy = method(view, "i", void.class); run = method(job, "run", void.class);
            commit = method(manager, "X", void.class);
            jobConstructor = constructor(job, int.class, Object.class, Object.class);
            getContainer = method(ui, "gf", container); getManager = method(n6, "e0", manager);
            getUi = method(n6, "Y", mainImpl);
            method(container, "b7", void.class); method(container, "uh", void.class, int.class);
            method(container, "R5", void.class, String.class, boolean.class); method(container, "sh", void.class);
            running = field(manager, "a", runningClass); runningMode = field(runningClass, "a", mode);
            modeName = field(mode, "U0", String.class); modeActivity = field(mode, "Z0", Activity.class);
            managerOwner = field(manager, "X", n6); managerActivity = field(manager, "E0", Activity.class);
            uiActivity = field(main, "b", Activity.class);
            presenterView = field(presenter, "M", baseView); presenterUi = field(basePresenter, "e", ui);
            presenterActivity = field(basePresenter, "f", Activity.class);
            presenterMode = field(basePresenter, "a", cls(loader, "xg.a")); exposureOwner = field(exposure, "a", presenter);
            viewActivity = field(baseView, "x", Activity.class); viewUi = field(baseView, "y", ui);
            viewPresenter = field(baseView, "z", provider); viewContainer = field(view, "T", cls(loader, "io.a"));
            containerOwner = field(container, "g", n6); containerActivity = field(container, "f", Activity.class);
            state = field(container, "C0", int.class);
            jobKind = field(job, "a", int.class); jobView = field(job, "c", Object.class); jobCallback = field(job, "b", Object.class);
            Class<?> fv = cls(loader, app + "feature.xpan.view.widget.XpanPhysicalFilterView");
            Class<?> fp = cls(loader, app + "feature.xpan.view.widget.c");
            filter = field(container, "B", fv); panel = field(container, "C", fp);
            viewFilter = field(view, "X", fv); viewPanel = field(view, "Y", fp);
            filterListenerClass = cls(loader, "dl.s"); panelListener = cls(loader, "dl.t");
            Class<?> listener = cls(loader, app + "feature.xpan.view.widget.XpanPhysicalFilterView$d");
            if (!View.class.isAssignableFrom(fv) || !View.class.isAssignableFrom(fp)
                    || !listener.isAssignableFrom(filterListenerClass) || !listener.isAssignableFrom(panelListener)) {
                throw new IllegalStateException("native filter hierarchy mismatch");
            }
            filterListener = field(fv, "n", listener); filterListenerOwner = field(filterListenerClass, "a", view);
            panelListenerOwner = field(panelListener, "a", view);
            filterList = field(fv, "e", List.class); panelList = field(fp, "c", List.class);
            panelCallbacks = field(fp, "q", ArrayList.class);
        }
        Class<?> cls(ClassLoader loader, String name) throws ClassNotFoundException {
            current = name; return Class.forName(name, false, loader);
        }
        Method method(Class<?> owner, String name, Class<?> result, Class<?>... parameters) throws Exception {
            current = owner.getName() + "." + name;
            Method m = owner.getDeclaredMethod(name, parameters);
            if (m.getReturnType() != result || Modifier.isStatic(m.getModifiers())
                    || (!owner.isInterface() && Modifier.isAbstract(m.getModifiers()))) throw new NoSuchMethodException(current);
            m.setAccessible(true); count++; return m;
        }
        Field field(Class<?> owner, String name, Class<?> type) throws Exception {
            current = owner.getName() + "." + name;
            Field f = owner.getDeclaredField(name);
            if (f.getType() != type || Modifier.isStatic(f.getModifiers())) throw new NoSuchFieldException(current);
            f.setAccessible(true); count++; return f;
        }
        Constructor<?> constructor(Class<?> owner, Class<?>... parameters) throws Exception {
            current = owner.getName() + ".<init>";
            Constructor<?> c = owner.getDeclaredConstructor(parameters);
            c.setAccessible(true); count++; return c;
        }
    }
}
