package com.xpanport.setup;

import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;

/**
 * LSPosed 模块启用器（参数化版，OmniCam 与 X-Pan 共用逻辑）。
 * 用法：app_process /system/bin com.xpanport.setup.Enabler <模块包名> <作用域包名>
 */
public class Enabler {

    private static final String DB = "/data/adb/lspd/config/modules_config.db";

    public static void main(String[] args) {
        if (args.length > 1 && "--remove".equals(args[0])) {
            initSystemResources();
            System.exit(remove(args[1]));
        }
        String mod = args.length > 0 ? args[0] : "com.xpanport.omnicam.hook";
        String target = args.length > 1 ? args[1] : "com.oplus.camera";
        int rc = run(mod, target);
        System.out.println("[OmniCam] enabler rc=" + rc);
        System.exit(rc);
    }

    private static void initSystemResources() {
        prefill("android.database.sqlite.SQLiteGlobal", new Object[][]{
                {"sDefaultSyncMode", "FULL"},
                {"sDefaultPageSize", 4096},
                {"sWALConnectionPoolSize", 4},
        });
        prefill("android.database.sqlite.SQLiteDatabase", new Object[][]{
                {"sIdleConnectionTimeout", 2000},
        });
    }

    private static void prefill(String cls, Object[][] kv) {
        try {
            Class<?> c = Class.forName(cls);
            for (Object[] e : kv) {
                try {
                    java.lang.reflect.Field f = c.getDeclaredField((String) e[0]);
                    f.setAccessible(true);
                    Object cur = f.get(null);
                    boolean empty = (cur == null) || (cur instanceof Integer && ((Integer) cur) == 0);
                    if (empty) f.set(null, e[1]);
                } catch (Throwable ignored) { }
            }
        } catch (Throwable t) {
            System.out.println("[OmniCam] prefill " + cls + " 失败: " + t);
        }
    }

    private static int run(String mod, String target) {
        initSystemResources();
        SQLiteDatabase db = null;
        try {
            db = SQLiteDatabase.openDatabase(DB, null, SQLiteDatabase.OPEN_READWRITE);
            if (!tableExists(db, "modules_state")) {
                System.out.println("[OmniCam] 库里没有 modules_state，LSPosed 版本不同");
                return 3;
            }
            try {
                db.execSQL("INSERT OR IGNORE INTO modules(module_pkg_name, apk_path) VALUES(?, '')",
                        new Object[]{mod});
            } catch (Throwable ignored) { }
            db.execSQL("DELETE FROM modules_state WHERE module_pkg_name=? AND user_id=0", new Object[]{mod});
            db.execSQL("INSERT INTO modules_state(module_pkg_name, user_id, enabled, scope_request_blocked)"
                    + " VALUES(?, 0, 1, 0)", new Object[]{mod});
            db.execSQL("INSERT OR IGNORE INTO scope(module_pkg_name, app_pkg_name, user_id)"
                    + " VALUES(?, ?, 0)", new Object[]{mod, target});
            System.out.println("[OmniCam] 已启用 " + mod + " 作用域=" + target);
            return 0;
        } catch (Throwable t) {
            System.out.println("[OmniCam] enabler 出错: " + t);
            t.printStackTrace(System.out);
            return 1;
        } finally {
            if (db != null) try { db.close(); } catch (Throwable ignored) { }
        }
    }

    /** --remove：把模块在 LSPosed 里的全部记录清干净（卸载残留清理用） */
    private static int remove(String mod) {
        SQLiteDatabase db = null;
        try {
            db = SQLiteDatabase.openDatabase(DB, null, SQLiteDatabase.OPEN_READWRITE);
            db.execSQL("DELETE FROM modules_state WHERE module_pkg_name=?", new Object[]{mod});
            db.execSQL("DELETE FROM scope WHERE module_pkg_name=?", new Object[]{mod});
            db.execSQL("DELETE FROM modules WHERE module_pkg_name=?", new Object[]{mod});
            System.out.println("[OmniCam] 已清除 LSPosed 记录: " + mod);
            return 0;
        } catch (Throwable t) {
            System.out.println("[OmniCam] remove 出错: " + t);
            return 1;
        } finally {
            if (db != null) try { db.close(); } catch (Throwable ignored) { }
        }
    }

    private static boolean tableExists(SQLiteDatabase db, String name) {
        Cursor c = null;
        try {
            c = db.rawQuery("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    new String[]{name});
            return c != null && c.moveToFirst();
        } catch (Throwable t) {
            return false;
        } finally {
            if (c != null) try { c.close(); } catch (Throwable ignored) { }
        }
    }
}
