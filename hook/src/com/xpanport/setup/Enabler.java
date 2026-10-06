package com.xpanport.setup;

import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;

/**
 * 在设备上直接改 LSPosed 的 modules_config.db，把本模块打开 + 设作用域。
 *
 * 为什么不用 sqlite3 命令：这手机没有 sqlite3。
 * 为什么用 app_process：Android 自带 SQLite API，root 下跑 app_process 就能用。
 *
 * 由 KSU 模块的 customize.sh / service.sh 调用：
 *   CLASSPATH=/data/adb/modules/XpanAce3Pro/tools/enabler.dex \
 *     app_process /system/bin com.xpanport.setup.Enabler
 */
public class Enabler {

    private static final String DB = "/data/adb/lspd/config/modules_config.db";
    private static final String MOD = "com.xpanport.hook";
    private static final String TARGET = "com.oplus.camera";

    public static void main(String[] args) {
        int rc = run();
        System.out.println("[XpanPort] enabler rc=" + rc);
        System.exit(rc);
    }

    /**
     * app_process 起来时 Resources.getSystem() 还没准备好，
     * 而 Android 的 SQLite 在打开库时要读系统资源里的默认值
     * （SQLiteGlobal.getDefaultSyncMode → Resources.getSystem().getString(...)），
     * 不处理就 NPE。试过 ActivityThread.systemMain() 起不来，
     * 所以直接把这几个静态缓存反射填上，让它跳过资源读取。
     */
    private static void initSystemResources() {
        prefill("android.database.sqlite.SQLiteGlobal", new Object[][]{
                {"sDefaultSyncMode", "FULL"},
                {"sDefaultPageSize", 4096},
                {"sWALConnectionPoolSize", 4},
        });
        prefill("android.database.sqlite.SQLiteDatabase", new Object[][]{
                {"sIdleConnectionTimeout", 2000},
        });
        System.out.println("[XpanPort] SQLite 静态默认值已预填");
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
                    if (empty) {
                        f.set(null, e[1]);
                        System.out.println("[XpanPort]   " + e[0] + " = " + e[1]);
                    }
                } catch (Throwable ignored) { }
            }
        } catch (Throwable t) {
            System.out.println("[XpanPort] prefill " + cls + " 失败: " + t);
        }
    }

    private static int run() {
        initSystemResources();
        SQLiteDatabase db = null;
        try {
            db = SQLiteDatabase.openDatabase(DB, null, SQLiteDatabase.OPEN_READWRITE);
            if (db == null) {
                System.out.println("[XpanPort] 打不开 " + DB);
                return 2;
            }

            if (!tableExists(db, "modules_state")) {
                System.out.println("[XpanPort] 库里没有 modules_state，可能 LSPosed 版本不同");
                return 3;
            }

            // modules 表登记一下（apk_path 允许空，LSPosed 自己会补）
            try {
                db.execSQL("INSERT OR IGNORE INTO modules(module_pkg_name, apk_path) VALUES(?, '')",
                        new Object[]{MOD});
            } catch (Throwable ignored) { }

            // 开关：先删同 user 的旧行再插，避免主键冲突
            db.execSQL("DELETE FROM modules_state WHERE module_pkg_name=? AND user_id=0", new Object[]{MOD});
            db.execSQL("INSERT INTO modules_state(module_pkg_name, user_id, enabled, scope_request_blocked)"
                    + " VALUES(?, 0, 1, 0)", new Object[]{MOD});

            // 作用域
            db.execSQL("INSERT OR IGNORE INTO scope(module_pkg_name, app_pkg_name, user_id)"
                    + " VALUES(?, ?, 0)", new Object[]{MOD, TARGET});

            System.out.println("[XpanPort] 已启用 " + MOD + " 作用域=" + TARGET);
            return 0;
        } catch (Throwable t) {
            System.out.println("[XpanPort] enabler 出错: " + t);
            t.printStackTrace(System.out);
            return 1;
        } finally {
            if (db != null) {
                try { db.close(); } catch (Throwable ignored) { }
            }
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
