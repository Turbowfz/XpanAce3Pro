package com.xpanport.omnicamset;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.Typeface;
import android.os.Bundle;
import java.io.File;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

/**
 * OmniCam Ace 3Pro 设置。
 * 首批：高像素 8192 开关（对接 XpanHook 的 4096/8192 切换）+ 注入状态卡 + 日志导出。
 * 所有需要 root 的操作走 Shell.su；改动后强停相机即时生效。
 */
public class MainActivity extends Activity {

    private TextView status;
    private CheckBox highPix;
    private CheckBox silentBox;
    private boolean refreshing;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        setTitle("OmniCam 设置（Ace 3Pro）");
        int pad = (int) (18 * getResources().getDisplayMetrics().density);

        ScrollView scroll = new ScrollView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(pad, pad, pad, pad);
        scroll.addView(root);
        setContentView(scroll);

        root.addView(title("高像素出片"));
        highPix = new CheckBox(this);
        highPix.setTextSize(17);
        highPix.setText("高像素 8192×3024（默认开；关闭后 4096×1512，文件更小）");
        highPix.setOnCheckedChangeListener(new android.widget.CompoundButton.OnCheckedChangeListener() {
            @Override
            public void onCheckedChanged(android.widget.CompoundButton gv, boolean on) {
                applyHighPix(on);
            }
        });
        root.addView(highPix);
        root.addView(note("关闭即回落 4096×1512（6.2MP）。改动后自动重启相机进程，重进相机生效。"));

        root.addView(title("注入状态"));
        root.addView(note("⚠️ 状态全是 ERR = root 未授权：KernelSU → 超级用户 → 找到 OmniCam 设置 → 允许，然后回来点刷新状态"));
        status = new TextView(this);
        status.setTypeface(Typeface.MONOSPACE);
        status.setTextSize(12);
        status.setTextColor(Color.rgb(40, 40, 40));
        root.addView(status);
        Button refresh = new Button(this);
        refresh.setText("刷新状态");
        refresh.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                refreshStatus();
            }
        });
        root.addView(refresh);

        root.addView(title("声音"));
        silentBox = new CheckBox(this);
        silentBox.setTextSize(17);
        silentBox.setText("相机静音（快门/对焦/倒计时提示音全部关闭）");
        silentBox.setOnCheckedChangeListener(new android.widget.CompoundButton.OnCheckedChangeListener() {
            @Override
            public void onCheckedChanged(android.widget.CompoundButton gv, boolean on) {
                if (refreshing) return;
                String r = Shell.su((on ? "touch" : "rm -f")
                        + " /sdcard/DCIM/.omnicam_silent; echo done");
                toast(on ? "相机已静音（下一拍生效）" : "相机提示音已恢复");
            }
        });
        root.addView(silentBox);
        root.addView(note("无需系统静音模式，相机自己的提示音独立关闭。下一张照片生效，无需重启相机。"));

        root.addView(title("工具"));
        Button log = new Button(this);
        log.setText("导出日志（logcat 全量 → Download）");
        log.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                exportLog();
            }
        });
        root.addView(log);
        Button restart = new Button(this);
        restart.setText("重启相机进程");
        restart.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                Shell.su("am force-stop com.oplus.camera");
                toast("相机已重启");
            }
        });
        root.addView(restart);

        root.addView(note("本模块与 X-Pan 模块（XpanAce3Pro）并存使用；高像素开关实际控制的是"
                + "X-Pan 钩子的出片尺寸切换。"));
    }

    @Override
    protected void onResume() {
        super.onResume();
        cleanupIfModuleGone();      // KSU 卸载不执行 uninstall.sh —— 由这里兜底清残留
        refreshing = true;
        refreshHighPix();
        refreshStatus();
        refreshing = false;
    }

    /** 模块目录消失但两个 APK 还装着 = 用户卸载了模块（KSU 不执行 uninstall.sh，实测）。
     *  由设置应用兜底：清 LSPosed 记录 → 卸钩子 → 清相机数据文件 → 自卸。全部走 su。 */
    private void cleanupIfModuleGone() {
        String mod = Shell.su("test -d /data/adb/modules/OmniCamAce3Pro && echo Y || echo N").trim();
        if (mod.equals("Y")) return;
        if (!pm("local.jiege.camera").contains("已安装")) return;
        String dex = extractEnabler();
        if (!dex.isEmpty())
            Shell.su("CLASSPATH=" + dex + " app_process /system/bin"
                    + " com.xpanport.setup.Enabler --remove com.xpanport.omnicam.hook");
        Shell.su("pm uninstall com.xpanport.omnicam.hook");
        Shell.su("rm -f /data/user/0/com.oplus.camera/files/omnicam_flash_keep.txt"
                + " /sdcard/DCIM/.omnicam_silent /data/local/tmp/omnicam_enabler.dex");
        Shell.su("pm uninstall com.xpanport.omnicam.set");   // 自卸，最后一步
    }

    private String extractEnabler() {
        try {
            java.io.InputStream in = getAssets().open("enabler.dex");
            java.io.File out = new File(getCacheDir(), "enabler.dex");
            java.io.FileOutputStream fo = new java.io.FileOutputStream(out);
            byte[] buf = new byte[4096]; int n;
            while ((n = in.read(buf)) > 0) fo.write(buf, 0, n);
            fo.close(); in.close();
            return out.getAbsolutePath();
        } catch (Throwable t) {
            return "";
        }
    }

    private void refreshSilent() {
        String r = Shell.su("test -f /sdcard/DCIM/.omnicam_silent && echo ON || echo OFF");
        silentBox.setOnCheckedChangeListener(null);
        silentBox.setChecked(r.trim().endsWith("ON"));
        silentBox.setOnCheckedChangeListener(new android.widget.CompoundButton.OnCheckedChangeListener() {
            @Override
            public void onCheckedChanged(android.widget.CompoundButton gv, boolean on) {
                if (refreshing) return;
                String r = Shell.su((on ? "touch" : "rm -f")
                        + " /sdcard/DCIM/.omnicam_silent; echo done");
                toast(on ? "相机已静音（下一拍生效）" : "相机提示音已恢复");
            }
        });
    }

    private void refreshHighPix() {
        String r = Shell.su("test -f /sdcard/DCIM/.xpan_smallpic && echo OFF || echo ON");
        highPix.setOnCheckedChangeListener(null);
        highPix.setChecked(r.trim().endsWith("ON"));
        highPix.setOnCheckedChangeListener(new android.widget.CompoundButton.OnCheckedChangeListener() {
            @Override
            public void onCheckedChanged(android.widget.CompoundButton gv, boolean on) {
                applyHighPix(on);
            }
        });
    }

    private void applyHighPix(boolean on) {
        if (refreshing) return;
        String r = Shell.su((on ? "rm -f /sdcard/DCIM/.xpan_smallpic"
                                : "touch /sdcard/DCIM/.xpan_smallpic")
                + "; am force-stop com.oplus.camera; echo done");
        toast(on ? "已切 8192（相机重启中）" : "已切 4096（相机重启中）");
        refreshHighPix();
    }

    private void refreshStatus() {
        StringBuilder sb = new StringBuilder();
        sb.append("X-Pan 钩子  : ").append(pm("com.xpanport.hook")).append('\n');
        sb.append("OmniCam钩子: ").append(pm("local.jiege.camera")).append('\n');
        sb.append("X-Pan模块  : ").append(dir("/data/adb/modules/XpanAce3Pro")).append('\n');
        sb.append("本模块挂载 : ").append(dir("/data/adb/modules/OmniCamAce3Pro")).append('\n');
        sb.append("相机版本   : ").append(camVersion()).append('\n');
        sb.append("配置md5    : ").append(Shell.su("md5sum /odm/etc/camera/config/oplus_camera_config")
                .split(" ")[0]).append('\n');
        status.setText(sb.toString());
    }

    private void exportLog() {
        // logcat 输出经 su 捕获回 Java 侧，用 Java 写文件 —— 不再依赖 root 重定向 /sdcard
        // （之前 root 重定向写 /sdcard/Download 实际不落盘/文件管理器不可见，"显示导出但没有"）
        String log = Shell.su("logcat -d");
        if (log.startsWith("ERR:") || log.trim().length() < 100) {
            toast("导出失败：logcat 无输出"); return;
        }
        String lsp = Shell.su("tail -n 2000 /data/adb/lspd/log/modules_*.log 2>/dev/null");
        try {
            java.io.File dir = new java.io.File(getExternalFilesDir(null), "logs");
            dir.mkdirs();
            String name = "omnicam_log_" + new java.text.SimpleDateFormat(
                    "MMdd_HHmmss", java.util.Locale.US).format(new java.util.Date()) + ".txt";
            java.io.File out = new java.io.File(dir, name);
            java.io.FileOutputStream fo = new java.io.FileOutputStream(out);
            fo.write(log.getBytes("UTF-8"));
            if (!lsp.startsWith("ERR:") && lsp.trim().length() > 0)
                fo.write(("\n\n===== LSPosed 模块日志（末 2000 行）=====\n" + lsp).getBytes("UTF-8"));
            fo.close();
            toast("已导出 " + name + "（" + log.split("\n").length + " 行）\n" + out.getAbsolutePath());
        } catch (Throwable t) {
            toast("导出失败: " + t);
        }
    }

    private String pm(String pkg) {
        String r = Shell.su("pm path " + pkg + " >/dev/null 2>&1 && echo 已安装 || echo 未安装");
        return r.trim();
    }

    private String dir(String d) {
        String r = Shell.su("test -d " + d + " && echo 存在 || echo 不存在");
        return r.trim();
    }

    private String camVersion() {
        String r = Shell.su("dumpsys package com.oplus.camera | grep -m1 versionName");
        r = r.replace("versionName=", "").trim();
        return r.isEmpty() ? "未知" : r;
    }

    private TextView title(String s) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setTextSize(16);
        t.setPadding(0, (int) (14 * getResources().getDisplayMetrics().density), 0, 6);
        return t;
    }

    private TextView note(String s) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(12);
        t.setTextColor(Color.GRAY);
        return t;
    }

    private void toast(String s) {
        Toast.makeText(this, s, Toast.LENGTH_SHORT).show();
    }
}
