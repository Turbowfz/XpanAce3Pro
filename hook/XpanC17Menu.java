package com.xpanport.hook;

import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;

import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;

/**
 * C17 / 7.013.51 的 XPAN 菜单视图。父入口在 Application.attach 后、正常菜单读取前安装，
 * 并传入已核实 ROM/SDK/配置的可撤销 ModuleGate；安装不打开数据库、不初始化目标静态类。
 *
 * 原 mp.h.j 是多模式 merge，mp.h.a 还写非 SQLite 的已插入标记，不能用它们做 XPAN-only
 * 事务。这里跟随原 mp.h.p 的已初始化 UI 读取，使用其现有连接只读核对六列全表，再向返回
 * ArrayList 的副本追加一个原厂 fa.a。没有迁移、版本调整、reset 标记或原列表/数据库写入。
 * 已有 XPAN（包括移到 headline 的项）始终由原程序返回，不改变其 rank/edit/front。
 *
 * 新虚拟项追加到“更多模式”列表，fa.a.b/support_edit=false、support_front=false；这只是
 * 数据标记，不证明 op.d.b 创建的 mp.k 已禁用拖拽。实际 GUI 编辑控制未验证，虚拟项没有
 * 编辑持久化保证；不改变 CameraConfig.legacy 皮肤配置，也不拦截已有项的编辑流程。
 * 本组件只证明列表发布，不宣称设备 UI、拍照或 native void 插入成功；持久化迁移须另行审批。
 */
public final class XpanC17Menu {
    public static final String CAMERA_VERSION = "7.013.51";
    private static final String TAG = "XpanPort C17 menu: ";
    private static final String XPAN = "xpan";
    private static final int XPAN_ID = 28;
    private static final String HEADLINE = "position_headline";
    private static final String PANEL = "position_mode_panel";
    private static final String SUPPORT = "com.oplus.feature.xpan.mode.support";
    private static final String LEGACY = "com.oplus.xpan.legacy.ui.style";
    private static final String STICKER_SUPPORT = "com.oplus.app.feature.sticker.support";
    private static final String SUPER_TEXT_SUPPORT = "com.oplus.camera.super.text.mode.support";
    private static final String SUPER_TEXT_V2 = "com.oplus.feature.super.text.support.v2";
    private static final String TABLE = "mode_info";
    private static final String[] COLUMNS = {
            "mode_name", "mode_id", "position", "rank", "support_edit", "support_front"};
    private static final int MODE_BOUND = 64; // 包含新虚拟项时也必须少于 64 项。
    // 仅识别原 p 的两个 SQL 字面量，不执行/替换 SQL。
    private static final String NATIVE_SELECT = "select * from mode_info where position = ?";
    private static final String READ_FRAME = "com.xpanport.hook.c17.menu.read.frame";
    private static final IdentityHashMap<ClassLoader, InstallResult> INSTALLS = new IdentityHashMap<>();

    public interface Gate { boolean enabled() throws Throwable; }

    public static final class InstallResult {
        public final boolean installed;
        public final int validatedBindings, registeredHooks;
        public final String detail;
        private final XpanC17Menu owner;

        private InstallResult(boolean ok, int bindings, int hooks, String detail, XpanC17Menu owner) {
            installed = ok; validatedBindings = bindings; registeredHooks = hooks;
            this.detail = detail; this.owner = owner;
        }

        public RuntimeStatus runtimeStatus() { return new RuntimeStatus(owner); }
    }

    /** appends 只统计 setResult 的列表副本，不是数据库提交或 UI 渲染见证。 */
    public static final class RuntimeStatus {
        public final boolean enabled;
        public final int reads, candidateBuilds, appends, failures;
        public final String detail;

        private RuntimeStatus(XpanC17Menu h) {
            enabled = h != null && h.active();
            reads = h == null ? 0 : h.reads.get();
            candidateBuilds = h == null ? 0 : h.candidateBuilds.get();
            appends = h == null ? 0 : h.appends.get();
            failures = h == null ? 0 : h.failures.get();
            detail = h == null ? "not installed" : h.lastDiagnostic;
        }
    }

    /** 所有精确 ABI 先解析，再全量注册；失败反向卸钩，残钩保持 disabled。无 always-on 重载。 */
    public static synchronized InstallResult install(ClassLoader loader, String version, Gate gate) {
        if (loader == null || gate == null || !CAMERA_VERSION.equals(version)) {
            return new InstallResult(false, 0, 0,
                    "refused: exact camera 7.013.51, attached loader and live Gate required", null);
        }
        InstallResult prior = INSTALLS.get(loader);
        if (prior != null) {
            if (prior.owner.gate == gate) return prior;
            return new InstallResult(false, prior.validatedBindings, prior.registeredHooks,
                    "refused: loader already bound to another menu Gate", prior.owner);
        }
        Bindings b = new Bindings();
        XpanC17Menu h = null;
        try {
            b.resolve(loader);
            h = new XpanC17Menu(b, gate);
            h.register();
            InstallResult result = new InstallResult(true, b.count, h.hooks.size(),
                    "ABI installed; XPAN-only process-local menu item in More modes (support_edit=false); no DB migration; device UI/edit controls unverified", h);
            INSTALLS.put(loader, result);
            h.enabled = true;
            log(result.detail);
            return result;
        } catch (Throwable failure) {
            int remaining = h == null ? 0 : h.rollback();
            String detail = "install failed at " + b.current + ": " + failure.getClass().getName()
                    + "; remaining hooks=" + remaining + " (disabled)";
            InstallResult result = new InstallResult(false, b.count, remaining, detail, h);
            if (h != null) h.lastDiagnostic = detail;
            if (remaining != 0) INSTALLS.put(loader, result);
            log(detail);
            return result;
        }
    }

    private final Bindings b;
    private final Gate gate;
    private final ArrayList<XC_MethodHook.Unhook> hooks = new ArrayList<>();
    private final ThreadLocal<ReadFrame> reading = new ThreadLocal<>();
    private final AtomicInteger reads = new AtomicInteger(), candidateBuilds = new AtomicInteger();
    private final AtomicInteger appends = new AtomicInteger(), failures = new AtomicInteger();
    private volatile boolean enabled;
    private volatile String lastDiagnostic = "waiting for normal initialized panel read; no DB migration; UI unverified";
    private Candidate cached; // 仅缓存不可变原厂候选；每次发布仍核对当前全表，不缓存数据库句柄。

    private XpanC17Menu(Bindings b, Gate gate) { this.b = b; this.gate = gate; }

    private static final class ReadFrame {
        final ReadFrame prior;
        boolean entered, eligible, ordered, invalid, projecting;
        SQLiteDatabase database;
        ReadFrame(ReadFrame prior) { this.prior = prior; }
    }

    private void register() {
        hook(b.rawQuery, new XC_MethodHook() {
            @Override protected void afterHookedMethod(MethodHookParam p) {
                ReadFrame frame = reading.get();
                if (frame == null || !frame.eligible || frame.projecting || !active()) return;
                try {
                    if (p.args == null || p.args.length != 2 || !(p.args[1] instanceof String[])) return;
                    String[] selection = (String[]) p.args[1];
                    String expected = NATIVE_SELECT + (frame.ordered ? " order by rank" : "");
                    if (!expected.equals(p.args[0]) || selection.length != 1 || !PANEL.equals(selection[0])) return;
                    if (p.hasThrowable() || !(p.getResult() instanceof Cursor)
                            || !(p.thisObject instanceof SQLiteDatabase) || frame.database != null) {
                        frame.invalid = true;
                        return;
                    }
                    // 原查询 Cursor 由 p 自己消费/关闭；这里不读、不替换、不关闭它。
                    frame.database = (SQLiteDatabase) p.thisObject;
                } catch (Throwable failure) {
                    frame.invalid = true;
                    diagnostic("native query capture failed; original list preserved", failure);
                }
            }
        });
        hook(b.getList, new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam p) {
                ReadFrame frame = new ReadFrame(reading.get());
                p.setObjectExtra(READ_FRAME, frame);
                reading.set(frame);
                frame.entered = true;
                // 即使内层 gateoff/非 UI 调用，也用独立 frame 遮蔽外层，不能串用初始化的查询。
                if (!active() || (frame.prior != null && frame.prior.projecting)) return;
                try {
                    if (p.args == null || p.args.length != 3 || !PANEL.equals(p.args[0])
                            || !Boolean.TRUE.equals(p.args[1]) || !(p.args[2] instanceof Boolean)
                            || !b.operator.isInstance(p.thisObject)
                            || !b.helper.isInstance(b.helperField.get(p.thisObject))) return;
                    frame.ordered = (Boolean) p.args[2];
                    frame.eligible = true;
                } catch (Throwable failure) {
                    diagnostic("menu read scope unavailable; original list preserved", failure);
                }
            }
            @Override protected void afterHookedMethod(MethodHookParam p) {
                ReadFrame frame = (ReadFrame) p.getObjectExtra(READ_FRAME);
                if (frame == null || !frame.entered) return;
                try {
                    if (!frame.eligible || !active() || p.hasThrowable()) return;
                    if (frame.invalid || frame.database == null || !(p.getResult() instanceof ArrayList)
                            || !b.initialized.getBoolean(p.thisObject)) {
                        diagnostic("uninitialized/failed native panel read; original list preserved", null);
                        return;
                    }
                    frame.projecting = true;
                    project(p, frame.database);
                } catch (Throwable failure) {
                    diagnostic("menu view preparation failed; original list preserved", failure);
                } finally {
                    if (frame.prior == null) reading.remove(); else reading.set(frame.prior);
                }
            }
        });
    }

    private synchronized void project(XC_MethodHook.MethodHookParam p, SQLiteDatabase db) throws Exception {
        if (!active() || !db.isOpen()) return;
        reads.incrementAndGet();
        ArrayList<?> original = (ArrayList<?>) p.getResult();
        ArrayList<Row> nativeRows = new ArrayList<>();
        HashSet<String> names = new HashSet<>();
        if (original.size() >= MODE_BOUND) throw new IllegalStateException("unbounded native list");
        for (Object item : original) {
            Row row = b.row(item);
            if (!PANEL.equals(row.position) || !names.add(row.name)) throw new IllegalStateException("unknown/duplicate panel row");
            nativeRows.add(row);
        }
        if (names.contains(XPAN)) return; // 也不改另一 hook 已经返回的 XPAN。
        if (!active()) return;
        Snapshot before = snapshot(db);
        if (before.rows.containsKey(XPAN)) return; // headline/panel 已持久化的 XPAN 均不覆盖。
        if (before.rows.size() + 1 >= MODE_BOUND) throw new IllegalStateException("no room within mode bound");
        if (!before.hasHeadline) throw new IllegalStateException("no initialized headline snapshot");
        for (Row row : before.rows.values()) {
            if (row.id == XPAN_ID) throw new IllegalStateException("XPAN id belongs to another mode");
        }
        for (Row row : nativeRows) {
            Row stored = before.rows.get(row.name);
            // p 对 masterVideo 的 front 可作运行期调整；保留原返回 flags，不拿 DB flags 覆盖它。
            if (stored == null || !stored.samePlacement(row)) throw new IllegalStateException("native list/snapshot drift");
        }
        for (Row row : before.rows.values()) {
            if (PANEL.equals(row.position) && !names.contains(row.name)) {
                if (!active()) return;
                // 仅原 p 的精确“不支持”条件成立才能解释缺行；支持中的漏项一律失败关闭。
                if (!nativeFiltersMissing(row.name)) throw new IllegalStateException("partial native panel list");
            }
        }
        if (!active() || !Boolean.TRUE.equals(b.support.invoke(null, SUPPORT))) return;
        // 保留原厂 legacy getter/ABI 资格检查，不覆盖决定皮肤的配置。
        b.legacy.invoke(null, LEGACY, false, false);
        boolean edit = false; // 仅新候选数据标记；不能据此宣称 GUI 已阻止拖拽。
        int rank = before.nextPanelRank();
        Candidate candidate = cached;
        if (candidate == null || candidate.edit != edit || !candidate.snapshot.same(before)) {
            if (!active()) return;
            Row want = new Row(XPAN, XPAN_ID, PANEL, rank, edit, false);
            Object mode = b.create(want);
            ArrayList<Object> headline = new ArrayList<>(), panel = new ArrayList<>();
            for (Row row : before.rows.values()) {
                (HEADLINE.equals(row.position) ? headline : panel).add(b.create(row));
            }
            // 原厂查重按名称查两份列表；不使用 fa.a.equals（它只比较 id）。
            if (b.existing.invoke(null, mode, headline, panel) != null) {
                throw new IllegalStateException("unexpected native duplicate result");
            }
            if (!want.same(b.row(mode))) throw new IllegalStateException("native candidate changed during lookup");
            candidate = new Candidate(before, mode, edit);
            candidateBuilds.incrementAndGet();
        }
        if (!active()) return;
        Snapshot after = snapshot(db);
        if (!before.same(after)) throw new IllegalStateException("snapshot changed during view preparation");
        Row want = new Row(XPAN, XPAN_ID, PANEL, rank, edit, false);
        if (!want.same(b.row(candidate.mode))) throw new IllegalStateException("cached candidate changed");
        ArrayList<Object> copy = new ArrayList<>(original);
        copy.add(candidate.mode); // 仅新副本：原元素身份、顺序、rank/edit/front 全部原样保留。
        if (!active()) return;
        p.setResult(copy);       // 唯一发布边界；无 DB/标记写入，也不依赖 void 返回值。
        cached = candidate;
        int n = appends.incrementAndGet();
        lastDiagnostic = "XPAN menu item in More modes appended at rank=" + rank
                + " (support_edit=false); no DB migration; device UI/edit controls unverified";
        if (n <= 3) log(lastDiagnostic);
    }

    /** 复用安装前已解析的 CameraConfig 精确 ABI、默认值和短路顺序，不重放 p/库存初始化。 */
    private boolean nativeFiltersMissing(String mode) throws Exception {
        if ("sticker".equals(mode)) {
            return !((Boolean) b.support.invoke(null, STICKER_SUPPORT));
        }
        if ("superText".equals(mode)) {
            return !((Boolean) b.legacy.invoke(null, SUPER_TEXT_SUPPORT, true, false))
                    && !((Boolean) b.legacy.invoke(null, SUPER_TEXT_V2, false, false));
        }
        return false;
    }

    private static final class Candidate {
        final Snapshot snapshot;
        final Object mode;
        final boolean edit;
        Candidate(Snapshot snapshot, Object mode, boolean edit) { this.snapshot = snapshot; this.mode = mode; this.edit = edit; }
    }

    private static final class Row {
        final String name, position;
        final int id, rank;
        final boolean edit, front;
        Object storedEdit, storedFront;
        Row(String name, int id, String position, int rank, boolean edit, boolean front) {
            this.name = name; this.id = id; this.position = position; this.rank = rank;
            this.edit = edit; this.front = front;
        }
        boolean samePlacement(Row z) { return name.equals(z.name) && id == z.id && position.equals(z.position) && rank == z.rank; }
        boolean same(Row z) { return samePlacement(z) && edit == z.edit && front == z.front; }
        boolean sameStored(Row z) { return same(z) && storedEdit.equals(z.storedEdit) && storedFront.equals(z.storedFront); }
    }

    private static final class Snapshot {
        final LinkedHashMap<String, Row> rows = new LinkedHashMap<>();
        boolean hasHeadline;
        int nextPanelRank() {
            Integer max = null;
            for (Row row : rows.values()) {
                if (PANEL.equals(row.position) && (max == null || row.rank > max)) max = row.rank;
            }
            if (max == null) return 0;
            if (max == Integer.MAX_VALUE) throw new IllegalStateException("panel rank overflow");
            return max + 1;
        }
        boolean same(Snapshot z) {
            if (rows.size() != z.rows.size()) return false;
            for (Map.Entry<String, Row> entry : rows.entrySet()) {
                Row row = z.rows.get(entry.getKey());
                if (row == null || !entry.getValue().sameStored(row)) return false;
            }
            return true;
        }
    }

    /** 只查询原 p 已打开的连接；只关闭自己创建的 Cursor，绝不关闭 helper/database。 */
    private Snapshot snapshot(SQLiteDatabase db) {
        if (!db.isOpen()) throw new IllegalStateException("native database closed");
        try (Cursor cursor = db.query(TABLE, null, null, null, null, null, null)) {
            if (cursor == null) throw new IllegalStateException("null snapshot cursor");
            String[] columns = cursor.getColumnNames();
            if (columns == null || columns.length != COLUMNS.length) throw new IllegalStateException("unknown mode schema");
            int[] index = new int[COLUMNS.length];
            HashSet<String> seen = new HashSet<>();
            for (String column : columns) if (column == null || !seen.add(column)) throw new IllegalStateException("duplicate schema column");
            for (int i = 0; i < COLUMNS.length; i++) {
                index[i] = cursor.getColumnIndex(COLUMNS[i]);
                if (index[i] < 0 || index[i] >= columns.length || !COLUMNS[i].equals(columns[index[i]])) {
                    throw new IllegalStateException("missing mode column");
                }
            }
            int count = cursor.getCount();
            if (count < 0 || count >= MODE_BOUND) throw new IllegalStateException("unbounded snapshot");
            Snapshot result = new Snapshot();
            while (cursor.moveToNext()) {
                if (result.rows.size() >= count) throw new IllegalStateException("snapshot count drift");
                String name = text(cursor, index[0]), position = text(cursor, index[2]);
                validateText(name, position);
                int id = integer(cursor, index[1]), rank = integer(cursor, index[3]);
                if (id < 0) throw new IllegalStateException("unknown mode id");
                Object edit = flag(cursor, index[4]), front = flag(cursor, index[5]);
                // 库存 p 使用 Boolean.parseBoolean(getString)，INTEGER 1 也不是字符串 true。
                Row row = new Row(name, id, position, rank, Boolean.parseBoolean(edit.toString()), Boolean.parseBoolean(front.toString()));
                row.storedEdit = edit; row.storedFront = front;
                if (result.rows.put(name, row) != null) throw new IllegalStateException("duplicate stored mode name");
                if (HEADLINE.equals(position)) result.hasHeadline = true;
            }
            if (result.rows.size() != count) throw new IllegalStateException("partial snapshot cursor");
            return result;
        }
    }

    private static String text(Cursor cursor, int index) {
        if (cursor.getType(index) != Cursor.FIELD_TYPE_STRING) throw new IllegalStateException("unknown text storage");
        return cursor.getString(index);
    }
    private static int integer(Cursor cursor, int index) {
        if (cursor.getType(index) != Cursor.FIELD_TYPE_INTEGER) throw new IllegalStateException("unknown integer storage");
        long value = cursor.getLong(index);
        if (value < Integer.MIN_VALUE || value > Integer.MAX_VALUE) throw new IllegalStateException("integer out of range");
        return (int) value;
    }
    private static Object flag(Cursor cursor, int index) {
        int type = cursor.getType(index);
        if (type == Cursor.FIELD_TYPE_STRING) {
            String value = cursor.getString(index);
            if ("true".equalsIgnoreCase(value) || "false".equalsIgnoreCase(value)) return value;
        } else if (type == Cursor.FIELD_TYPE_INTEGER) {
            long value = cursor.getLong(index);
            if (value == 0 || value == 1) return value;
        }
        throw new IllegalStateException("unknown boolean storage");
    }
    private static void validateText(String name, String position) {
        if (name == null || name.isEmpty() || name.length() > 128 || (!HEADLINE.equals(position) && !PANEL.equals(position))) {
            throw new IllegalStateException("unknown mode name/position");
        }
        for (int i = 0; i < name.length(); i++) if (Character.isISOControl(name.charAt(i))) throw new IllegalStateException("invalid mode name");
    }

    private boolean active() {
        if (!enabled) return false;
        try { return gate.enabled(); }
        catch (Throwable failure) { diagnostic("Gate failed; original camera behavior preserved", failure); return false; }
    }
    private void diagnostic(String message, Throwable failure) {
        lastDiagnostic = message + (failure == null ? "" : ": " + failure.getClass().getName());
        if (failures.incrementAndGet() <= 12) log(lastDiagnostic);
    }
    private static void log(String message) {
        try { XposedBridge.log(TAG + message); }
        catch (Throwable ignored) { /* 诊断不能改变原方法的结果、异常或 frame 清理。 */ }
    }
    private void hook(Method method, XC_MethodHook callback) {
        b.current = method.toString();
        XC_MethodHook.Unhook handle = XposedBridge.hookMethod(method, callback);
        if (handle == null) throw new IllegalStateException("registration returned no rollback handle");
        hooks.add(handle);
    }
    private int rollback() {
        enabled = false;
        for (int i = hooks.size() - 1; i >= 0; i--) {
            try { hooks.get(i).unhook(); hooks.remove(i); }
            catch (Throwable failure) { diagnostic("unhook failed; residual callback disabled", failure); }
        }
        return hooks.size();
    }

    private static final class Bindings {
        int count;
        String current = "version/loader";
        Class<?> operator, helper, modeInfo;
        Method getList, existing, rawQuery, support, legacy;
        Constructor<?> modeConstructor;
        Field helperField, initialized, name, id, position, rank, edit, front;

        void resolve(ClassLoader loader) throws Exception {
            operator = cls(loader, "mp.h");
            helper = cls(loader, "mp.j");
            modeInfo = cls(loader, "fa.a");
            if (!Modifier.isFinal(operator.getModifiers()) || !Modifier.isFinal(modeInfo.getModifiers())
                    || !SQLiteOpenHelper.class.isAssignableFrom(helper)) throw new IllegalStateException("unexpected menu class ABI");
            helperField = field(operator, "a", helper, true);
            initialized = field(operator, "g", boolean.class, false);
            name = field(modeInfo, "a", String.class, true);
            edit = field(modeInfo, "b", boolean.class, true);
            front = field(modeInfo, "c", boolean.class, true);
            position = field(modeInfo, "d", String.class, true);
            rank = field(modeInfo, "e", int.class, true);
            id = field(modeInfo, "f", int.class, true);
            current = "fa.a.<init>";
            modeConstructor = modeInfo.getDeclaredConstructor(String.class, boolean.class, String.class, int.class, int.class, boolean.class);
            if (!Modifier.isPublic(modeConstructor.getModifiers())) throw new NoSuchMethodException("non-public mode constructor");
            count++;
            getList = method(operator, "p", ArrayList.class, false, String.class, boolean.class, boolean.class);
            if (!Modifier.isFinal(getList.getModifiers())) throw new NoSuchMethodException("non-final native list reader");
            existing = method(operator, "r", modeInfo, true, modeInfo, List.class, List.class);
            rawQuery = method(SQLiteDatabase.class, "rawQuery", Cursor.class, false, String.class, String[].class);
            Class<?> config = cls(loader, "com.oplus.camera.configure.CameraConfig");
            support = method(config, "getConfigBooleanValue", boolean.class, true, String.class);
            legacy = method(config, "d", boolean.class, true, String.class, boolean.class, boolean.class);
        }
        Object create(Row row) throws Exception {
            Object value = modeConstructor.newInstance(row.name, row.edit, row.position, row.rank, row.id, row.front);
            if (!row.same(row(value))) throw new IllegalStateException("native ModeDataInfo constructor mismatch");
            return value;
        }
        Row row(Object value) throws IllegalAccessException {
            if (!modeInfo.isInstance(value)) throw new IllegalStateException("unknown native list element");
            String n = (String) name.get(value), p = (String) position.get(value);
            validateText(n, p);
            int identifier = id.getInt(value);
            if (identifier < 0) throw new IllegalStateException("unknown native mode id");
            return new Row(n, identifier, p, rank.getInt(value), edit.getBoolean(value), front.getBoolean(value));
        }
        private Class<?> cls(ClassLoader loader, String name) throws ClassNotFoundException {
            current = name;
            return Class.forName(name, false, loader);
        }
        private Field field(Class<?> owner, String name, Class<?> type, boolean readonly) throws NoSuchFieldException {
            current = owner.getName() + "." + name;
            Field f = owner.getDeclaredField(name);
            int flags = f.getModifiers();
            if (f.getType() != type || !Modifier.isPublic(flags) || Modifier.isStatic(flags)
                    || Modifier.isFinal(flags) != readonly) throw new NoSuchFieldException("unexpected field ABI");
            count++;
            return f;
        }
        private Method method(Class<?> owner, String name, Class<?> result, boolean statik, Class<?>... params) throws NoSuchMethodException {
            current = owner.getName() + "." + name;
            Method m = owner.getDeclaredMethod(name, params);
            int flags = m.getModifiers();
            if (m.getReturnType() != result || !Modifier.isPublic(flags) || Modifier.isStatic(flags) != statik
                    || Modifier.isAbstract(flags)) throw new NoSuchMethodException("unexpected method ABI");
            count++;
            return m;
        }
    }
}
