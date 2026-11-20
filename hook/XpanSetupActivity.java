package com.xpanport.hook;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

/** Ordinary app guide only; it cannot certify or change module/LSPosed state. */
public final class XpanSetupActivity extends Activity {
    private static final String APP_TITLE = "XPanForAce3Pro";
    private static final String CAMERA_PACKAGE = "com.oplus.camera";
    private static final String STATE_TEXT =
            "启用、作用域及 Hook 注入状态：未验证。配套模块安装状态：未知。";
    private static final String GUIDE_TEXT =
            "1. v2.1 模块与 Hook APK 必须配套使用。单独装 APK 不能替代模块。\n\n"
            + "2. 在 LSPosed 中手动启用 XPanForAce3Pro，作用域仅勾选 com.oplus.camera（系统相机）。\n\n"
            + "3. 打开系统相机，进入 XPAN，再检查实际效果。\n\n"
            + "目标：8192×3024，仅 1×，XDR 上限至少 8×。\n\n"
            + "精确版本：一加 Ace 3 Pro（PJX110）原版 C16 相机 6.020.876 / C17 相机 7.013.51。"
            + "还须匹配固定的 ROM 指纹、相机配置和 unit SDK / adapter 文件校验值；不是支持所有 7.x 相机。\n\n"
            + "注意：此页只是指南，不是激活或效果检测；查看步骤不代表完成设置。未知版本不作兼容承诺，"
            + "实际画幅及硬件/实拍 XDR 效果需在对应设备上实机验收。";
    private static final String MANUAL_STEPS =
            "1. 自行确认安装的是配套的 v2.1 模块与 Hook APK。\n\n"
            + "2. 自行打开 LSPosed 管理器，在模块列表找到 XPanForAce3Pro，手动启用。\n\n"
            + "3. 在作用域中仅勾选 com.oplus.camera（系统相机），在管理器中核对后进入相机的 XPAN。\n\n"
            + "界面不同请按所用管理器的官方说明操作。本页不自动安装、重启或修改 LSPosed 设置。";

    private TextView deviceInfo;

    @Override protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setTitle(APP_TITLE);
        ScrollView scroll = new ScrollView(this);
        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        int padding = dp(20);
        content.setPadding(padding, padding, padding, padding);
        scroll.addView(content);
        addText(content, APP_TITLE, 24);
        addText(content, STATE_TEXT, 18);
        addText(content, GUIDE_TEXT, 18);

        Button showSteps = addButton(content, "查看手动设置步骤");
        final TextView steps = addText(content, MANUAL_STEPS, 18);
        steps.setVisibility(View.GONE);
        showSteps.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View view) {
                steps.setVisibility(View.VISIBLE);
            }
        });

        addText(content, "下方只打开本应用的 Android 应用信息页，不是 LSPosed 设置。", 18);
        Button appDetails = addButton(content, "打开本应用系统设置");
        appDetails.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View view) {
                openAppDetails();
            }
        });
        deviceInfo = addText(content, "", 18);
        setContentView(scroll);
    }

    @Override protected void onResume() {
        super.onResume();
        if (deviceInfo != null) deviceInfo.setText(readDeviceSummary());
    }

    private String readDeviceSummary() {
        String version = null;
        try {
            // The launcher manifest must allow querying this package on Android 11+.
            PackageInfo info = getPackageManager().getPackageInfo(CAMERA_PACKAGE, 0);
            if (info != null) version = info.versionName;
        } catch (PackageManager.NameNotFoundException | RuntimeException unavailable) {
            // Package visibility or a failed query is not proof that a module is absent.
        }
        return deviceSummary(Build.MODEL, version);
    }

    static String deviceSummary(String model, String version) {
        boolean modelKnown = hasText(model), versionKnown = hasText(version);
        String comparison = "未知（型号或相机版本无法读取）";
        if (modelKnown && versionKnown) {
            CameraProfile.Generation generation = CameraProfile.select(model, version, "").generation;
            comparison = generation == CameraProfile.Generation.UNSUPPORTED
                    ? "未匹配预设型号/相机版本，不作兼容承诺"
                    : generation.name() + " 型号/版本号初步匹配";
        }
        return "设备型号：" + (modelKnown ? model : "未知")
                + "\n系统相机（" + CAMERA_PACKAGE + "）版本："
                + (versionKnown ? version : "未知（无法读取；可能未安装或对本应用不可见）")
                + "\n兼容性：" + comparison + "。"
                + "\n型号/版本号比对不验证 ROM、配置或 SDK / adapter 固定校验，也不验证模块、作用域、注入或硬件 XDR。";
    }

    private static boolean hasText(String value) {
        return value != null && value.trim().length() != 0;
    }

    private void openAppDetails() {
        Intent details = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                Uri.fromParts("package", getPackageName(), null));
        try {
            startActivity(details);
        } catch (ActivityNotFoundException | SecurityException unavailable) {
            Toast.makeText(this, "系统无法打开本应用信息，请自行在系统设置中查看；LSPosed 状态仍未验证。",
                    Toast.LENGTH_LONG).show();
        }
    }

    private TextView addText(LinearLayout parent, String text, float size) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextSize(size);
        view.setPadding(0, dp(8), 0, dp(8));
        parent.addView(view, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return view;
    }

    private Button addButton(LinearLayout parent, String text) {
        Button button = new Button(this);
        button.setText(text);
        button.setTextSize(18);
        parent.addView(button, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return button;
    }

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }
}
