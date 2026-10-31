package com.xpanport.omnicamset;

import java.io.BufferedReader;
import java.io.DataOutputStream;
import java.io.File;
import java.io.InputStreamReader;

/**
 * root 命令执行。su 路径多路探测：KernelSU 的 su 对未授权应用可能表现为
 * "找不到文件"（内核层拦截），所以按已知路径逐个试，找到能用就记住。
 */
public final class Shell {

    private static final String[] SU_PATHS = {
            "su",
            "/system/bin/su",
            "/system/xbin/su",
            "/sbin/su",
            "/data/adb/ksu/bin/su",
            "/debug_ramdisk/su",
    };
    private static volatile String suCmd;
    private static final Object lock = new Object();

    private static String resolveSu() {
        if (suCmd != null) return suCmd;
        synchronized (lock) {
            if (suCmd != null) return suCmd;
            for (String p : SU_PATHS) {
                try {
                    Process p2 = Runtime.getRuntime().exec(new String[]{p, "-c", "id"});
                    BufferedReader r = new BufferedReader(new InputStreamReader(p2.getInputStream()));
                    String out = r.readLine();
                    r.close();
                    p2.waitFor();
                    if (out != null && out.contains("uid=0")) {
                        suCmd = p;
                        return suCmd;
                    }
                } catch (Throwable ignored) {
                }
            }
            return null;
        }
    }

    /** 阻塞执行，返回合并输出；失败返回以 "ERR: " 开头的串。 */
    public static String su(String cmd) {
        String su = resolveSu();
        if (su == null) return "ERR: 找不到可用的 su（KernelSU 超级用户里给本应用授权）";
        try {
            Process p = Runtime.getRuntime().exec(new String[]{su, "-c", cmd});
            DataOutputStream os = new DataOutputStream(p.getOutputStream());
            os.writeBytes("exit\n");
            os.flush();
            StringBuilder sb = new StringBuilder();
            BufferedReader out = new BufferedReader(new InputStreamReader(p.getInputStream()));
            BufferedReader err = new BufferedReader(new InputStreamReader(p.getErrorStream()));
            String line;
            while ((line = out.readLine()) != null) sb.append(line).append('\n');
            while ((line = err.readLine()) != null) sb.append("[err] ").append(line).append('\n');
            p.waitFor();
            String r = sb.toString();
            return r.isEmpty() ? "(无输出)" : r;
        } catch (Throwable t) {
            return "ERR: " + t;
        }
    }
}
