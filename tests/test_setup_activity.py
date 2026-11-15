"""Offline setup-guide checks; fake Android runtime is not device/activation proof."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
ACTIVITY = ROOT / "hook/XpanSetupActivity.java"
SDK = Path(os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME") or
           Path.home() / "AppData/Local/Android/Sdk")
ANDROID = SDK / "platforms/android-35/android.jar"
XPOSED = Path(os.environ.get("XPAN_XPOSED_API") or ROOT / "_tmp/api-82.jar")
NS = "{http://schemas.android.com/apk/res/android}"


def run(argv, folder):
    result = subprocess.run([str(arg) for arg in argv], cwd=folder, capture_output=True,
                            text=True, encoding="utf-8", errors="backslashreplace", timeout=120)
    if result.returncode:
        raise AssertionError(f"Command failed ({result.returncode}): {argv[0]}\n"
                             f"{result.stdout}\n{result.stderr}")
    return result.stdout


def compile_activity(folder):
    javac = shutil.which("javac")
    if not javac or not ANDROID.is_file() or not XPOSED.is_file():
        raise AssertionError(f"Local javac, Android API and compile-only Xposed API required: "
                             f"{javac}, {ANDROID}, {XPOSED}")
    classes = folder / "real-classes"
    classes.mkdir()
    run([javac, "-encoding", "UTF-8", "-source", "8", "-target", "8",
         "-bootclasspath", ANDROID, "-cp", os.pathsep.join(map(str, (ANDROID, XPOSED))),
         "-d", classes, ROOT / "hook/CameraProfile.java", ACTIVITY], folder)
    return classes


class SetupActivitySourceTests(unittest.TestCase):
    def test_programmatic_ui_uses_canonical_package_without_resources_or_lambdas(self):
        source = ACTIVITY.read_text(encoding="utf-8")
        self.assertIn("package com.xpanport.hook;", source)
        self.assertIn("public final class XpanSetupActivity extends Activity", source)
        self.assertIn("new ScrollView(this)", source)
        self.assertIn("new LinearLayout(this)", source)
        self.assertIn("new View.OnClickListener()", source)
        self.assertIn("setTitle(APP_TITLE)", source)
        for forbidden in ("R.layout", "R.string", "R.drawable", "LayoutInflater", "->"):
            self.assertNotIn(forbidden, source)
        self.assertNotIn(b"\r", ACTIVITY.read_bytes())

    def test_guide_never_writes_activation_scope_permissions_or_camera_options(self):
        source = ACTIVITY.read_text(encoding="utf-8")
        for forbidden in ("Runtime.getRuntime", "ProcessBuilder", "FileInputStream", "FileOutputStream",
                          "/data/adb", "SystemProperties", "SQLite", "android.database",
                          "org.lsposed", "de.robv.android.xposed", "setComponent(", "setPackage(",
                          "sendBroadcast(", "startService(", "requestPermissions(",
                          "SharedPreferences", "CheckBox", "Switch", "ToggleButton",
                          "ModuleGate", "CameraProfile.read(", "已激活", "注入正常", "激活完成"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        self.assertEqual(source.count("startActivity("), 1)
        self.assertIn("Settings.ACTION_APPLICATION_DETAILS_SETTINGS", source)
        self.assertIn('Uri.fromParts("package", getPackageName(), null)', source)
        self.assertIn("CameraProfile.select(model, version, \"\")", source)
        self.assertIn("配套模块安装状态：未知", source)
        self.assertIn("启用、作用域及 Hook 注入状态：未验证", source)

    def test_manifest_launches_only_the_guide_and_retains_module_identity(self):
        manifest = ET.parse(ROOT / "hook/AndroidManifest.xml").getroot()
        self.assertEqual(manifest.get("package"), "com.xpanport.hook")
        self.assertEqual(manifest.get(NS + "versionCode"), "20")
        self.assertEqual(manifest.get(NS + "versionName"), "2.0")
        self.assertEqual(manifest.find("uses-sdk").get(NS + "minSdkVersion"), "24")
        app = manifest.find("application")
        self.assertEqual(app.get(NS + "label"), "XPanForAce3Pro")
        activities = app.findall("activity")
        self.assertEqual(len(activities), 1)
        guide = activities[0]
        name = guide.get(NS + "name")
        if name.startswith("."):
            name = manifest.get("package") + name
        self.assertEqual(name, "com.xpanport.hook.XpanSetupActivity")
        self.assertEqual(guide.get(NS + "exported"), "true")
        self.assertEqual(guide.get(NS + "label"), "XPanForAce3Pro")
        filters = guide.findall("intent-filter")
        self.assertEqual(len(filters), 1)
        self.assertEqual([node.get(NS + "name") for node in filters[0].findall("action")],
                         ["android.intent.action.MAIN"])
        self.assertEqual({node.get(NS + "name") for node in filters[0].findall("category")},
                         {"android.intent.category.LAUNCHER",
                          "de.robv.android.xposed.category.MODULE_SETTINGS"})
        self.assertEqual([node.get(NS + "name") for node in manifest.findall("queries/package")],
                         ["com.oplus.camera"])
        self.assertEqual(manifest.findall("uses-permission"), [])
        metadata = {node.get(NS + "name"): node.get(NS + "value") for node in app.findall("meta-data")}
        self.assertEqual(metadata["xposedscope"], "com.oplus.camera")
        self.assertEqual(metadata["xposedmodule"], "true")
        self.assertEqual((ROOT / "hook/assets/xposed_init").read_text(encoding="utf-8").strip(),
                         "com.xpanport.hook.XpanHook")
        self.assertIn('HOOK.glob("*.java")', (ROOT / "tools/build_hook.py").read_text(encoding="utf-8"))


class SetupActivityRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="xpan-setup-test-")
        try:
            cls.folder = Path(cls.temp.name)
            cls.real_classes = compile_activity(cls.folder)
        except BaseException:
            cls.temp.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_real_android_and_compile_only_xposed_classpath(self):
        classes = [path.relative_to(self.real_classes).as_posix()
                   for path in self.real_classes.rglob("*.class")]
        self.assertIn("com/xpanport/hook/XpanSetupActivity.class", classes)
        self.assertIn("com/xpanport/hook/CameraProfile.class", classes)
        self.assertTrue(all(name.startswith("com/xpanport/hook/") for name in classes))
        self.assertFalse(any("Lambda" in name for name in classes))
        print("PASS setup activity javac with real Android API + compile-only Xposed API; no APK/install")

    def test_real_compiled_activity_against_fake_android_runtime(self):
        java, javac = shutil.which("java"), shutil.which("javac")
        self.assertIsNotNone(java, "Local java required")
        self.assertIsNotNone(javac, "Local javac required")
        sources = self.folder / "fake-sources"
        fakes = self.folder / "fake-classes"
        sources.mkdir()
        fakes.mkdir()
        paths = []
        for name, source in FAKE_ANDROID.items():
            path = sources / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
            paths.append(path)
        harness = sources / "com/xpanport/hook/SetupActivityHarness.java"
        harness.parent.mkdir(parents=True, exist_ok=True)
        harness.write_text(HARNESS, encoding="utf-8")
        # Do not recompile the activity against fakes: execute the real-API bytecode.
        run([javac, "-encoding", "UTF-8", "-source", "8", "-target", "8",
             "-cp", self.real_classes, "-d", fakes, *paths, harness], self.folder)
        result = run([java, "-cp", os.pathsep.join(map(str, (fakes, self.real_classes))),
                      "com.xpanport.hook.SetupActivityHarness"], self.folder)
        self.assertIn("PASS setup activity", result)
        self.assertIn("fake runtime only; no activation/device proof", result)
        print(result.strip())


# Minimal Android API-shaped doubles, kept in temporary storage and never packaged.
FAKE_ANDROID = {
    "android/content/Context.java": r'''package android.content;
public class Context {
  public android.content.pm.PackageManager packageManager = new android.content.pm.PackageManager();
  public android.content.res.Resources resources = new android.content.res.Resources();
  public String packageName = "com.xpanport.hook";
  public android.content.pm.PackageManager getPackageManager() { return packageManager; }
  public android.content.res.Resources getResources() { return resources; }
  public String getPackageName() { return packageName; }
}''',
    "android/app/Activity.java": r'''package android.app;
public class Activity extends android.content.Context {
  public CharSequence title; public android.view.View content;
  public java.util.List<android.content.Intent> intents = new java.util.ArrayList<android.content.Intent>();
  public RuntimeException launchFailure;
  protected void onCreate(android.os.Bundle state) {} protected void onResume() {}
  public void setTitle(CharSequence text) { title = text; }
  public void setContentView(android.view.View view) { content = view; }
  public void startActivity(android.content.Intent intent) {
    if (launchFailure != null) throw launchFailure; intents.add(intent);
  }
}''',
    "android/content/pm/PackageManager.java": r'''package android.content.pm;
public class PackageManager {
  public PackageInfo info = new PackageInfo(); public NameNotFoundException missing;
  public RuntimeException failure; public int calls, lastFlags; public String lastPackage;
  public PackageInfo getPackageInfo(String name, int flags) throws NameNotFoundException {
    calls++; lastPackage = name; lastFlags = flags;
    if (missing != null) throw missing; if (failure != null) throw failure; return info;
  }
  public static class NameNotFoundException extends Exception {
    public NameNotFoundException(String message) { super(message); }
  }
}''',
    "android/content/pm/PackageInfo.java": r'''package android.content.pm;
public class PackageInfo { public String versionName; }''',
    "android/os/Bundle.java": r'''package android.os; public class Bundle {}''',
    "android/os/Build.java": r'''package android.os;
public class Build { public static String MODEL = "PJX110", FINGERPRINT = "unverified"; }''',
    "android/content/res/Resources.java": r'''package android.content.res;
public class Resources {
  public android.util.DisplayMetrics metrics = new android.util.DisplayMetrics();
  public android.util.DisplayMetrics getDisplayMetrics() { return metrics; }
}''',
    "android/util/DisplayMetrics.java": r'''package android.util;
public class DisplayMetrics { public float density = 1.0f; }''',
    "android/view/View.java": r'''package android.view;
public class View {
  public static final int VISIBLE = 0, GONE = 8; public int visibility = VISIBLE;
  public int[] padding; private OnClickListener listener;
  public View(android.content.Context context) {}
  public void setPadding(int a, int b, int c, int d) { padding = new int[]{a,b,c,d}; }
  public void setVisibility(int value) { visibility = value; }
  public int getVisibility() { return visibility; }
  public void setOnClickListener(OnClickListener value) { listener = value; }
  public boolean performClick() { if (listener == null) return false; listener.onClick(this); return true; }
  public interface OnClickListener { void onClick(View view); }
}''',
    "android/view/ViewGroup.java": r'''package android.view;
public class ViewGroup extends View {
  public java.util.List<View> children = new java.util.ArrayList<View>();
  public ViewGroup(android.content.Context context) { super(context); }
  public void addView(View view) { children.add(view); }
  public void addView(View view, LayoutParams params) { children.add(view); }
  public static class LayoutParams {
    public static final int MATCH_PARENT = -1, WRAP_CONTENT = -2; public int width, height;
    public LayoutParams(int w, int h) { width = w; height = h; }
  }
}''',
    "android/widget/ScrollView.java": r'''package android.widget;
public class ScrollView extends android.view.ViewGroup {
  public ScrollView(android.content.Context context) { super(context); }
}''',
    "android/widget/LinearLayout.java": r'''package android.widget;
public class LinearLayout extends android.view.ViewGroup {
  public static final int VERTICAL = 1; public int orientation;
  public LinearLayout(android.content.Context context) { super(context); }
  public void setOrientation(int value) { orientation = value; }
  public static class LayoutParams extends android.view.ViewGroup.LayoutParams {
    public LayoutParams(int w, int h) { super(w,h); }
  }
}''',
    "android/widget/TextView.java": r'''package android.widget;
public class TextView extends android.view.View {
  public CharSequence text = ""; public float textSize;
  public TextView(android.content.Context context) { super(context); }
  public void setText(CharSequence value) { text = value; }
  public void setTextSize(float value) { textSize = value; }
}''',
    "android/widget/Button.java": r'''package android.widget;
public class Button extends TextView {
  public Button(android.content.Context context) { super(context); }
}''',
    "android/content/Intent.java": r'''package android.content;
public class Intent {
  private final String action; private final android.net.Uri data;
  public Intent(String a, android.net.Uri d) { action = a; data = d; }
  public String getAction() { return action; } public android.net.Uri getData() { return data; }
}''',
    "android/net/Uri.java": r'''package android.net;
public class Uri {
  public String scheme, part, fragment;
  public static Uri fromParts(String scheme, String part, String fragment) {
    Uri uri = new Uri(); uri.scheme = scheme; uri.part = part; uri.fragment = fragment; return uri;
  }
  public String getScheme() { return scheme; } public String getSchemeSpecificPart() { return part; }
}''',
    "android/provider/Settings.java": r'''package android.provider;
public class Settings {
  public static final String ACTION_APPLICATION_DETAILS_SETTINGS = "android.settings.APPLICATION_DETAILS_SETTINGS";
}''',
    "android/content/ActivityNotFoundException.java": r'''package android.content;
public class ActivityNotFoundException extends RuntimeException {}''',
    "android/widget/Toast.java": r'''package android.widget;
public class Toast {
  public static final int LENGTH_LONG = 1; public static int shown; public static CharSequence lastMessage;
  private CharSequence message;
  public static Toast makeText(android.content.Context context, CharSequence message, int duration) {
    Toast toast = new Toast(); toast.message = message; return toast;
  }
  public void show() { shown++; lastMessage = message; }
}''',
}

HARNESS = r'''package com.xpanport.hook;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
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
import java.util.ArrayList;
import java.util.List;

public final class SetupActivityHarness {
  static int checks;
  static void check(boolean okay, String why) { checks++; if (!okay) throw new AssertionError(why); }
  static void contains(String text, String part) { check(text.contains(part), "Missing: " + part + " in " + text); }
  static void collect(View view, List<TextView> texts, boolean visibleOnly) {
    if (visibleOnly && view.getVisibility() != View.VISIBLE) return;
    if (view instanceof TextView) texts.add((TextView) view);
    if (view instanceof ViewGroup)
      for (View child : ((ViewGroup) view).children) collect(child, texts, visibleOnly);
  }
  static String visible(XpanSetupActivity app) {
    List<TextView> texts = new ArrayList<TextView>(); collect(app.content, texts, true);
    StringBuilder result = new StringBuilder();
    for (TextView view : texts) result.append(view.text).append('\n');
    return result.toString();
  }
  static TextView find(XpanSetupActivity app, String start) {
    List<TextView> texts = new ArrayList<TextView>(); collect(app.content, texts, false);
    for (TextView view : texts) if (view.text.toString().startsWith(start)) return view;
    throw new AssertionError("Missing view: " + start);
  }
  static void unverified(XpanSetupActivity app) {
    contains(visible(app), "启用、作用域及 Hook 注入状态：未验证");
    contains(visible(app), "配套模块安装状态：未知");
    check(!visible(app).contains("已激活") && !visible(app).contains("注入正常"), "Fake success message");
  }
  public static void main(String[] args) {
    Build.MODEL = "PJX110";
    XpanSetupActivity app = new XpanSetupActivity();
    app.packageManager.missing = new PackageManager.NameNotFoundException("invisible");
    app.onCreate(new Bundle()); app.onResume();
    check("XPanForAce3Pro".contentEquals(app.title), "Wrong title");
    check(app.content instanceof ScrollView, "Guide must scroll");
    LinearLayout content = (LinearLayout) ((ViewGroup) app.content).children.get(0);
    check(content.orientation == LinearLayout.VERTICAL, "Guide is not vertical");
    List<TextView> texts = new ArrayList<TextView>(); collect(app.content, texts, false);
    int buttons = 0;
    for (TextView view : texts) {
      check(view.textSize >= 18, "Small guide text");
      if (view instanceof Button) buttons++;
    }
    check(buttons == 2, "Unexpected guide controls");
    check(app.intents.isEmpty() && Toast.shown == 0, "First open performed an action");
    check(app.packageManager.calls == 1 && app.packageManager.lastFlags == 0
        && "com.oplus.camera".equals(app.packageManager.lastPackage), "Wrong package query");
    unverified(app);
    for (String text : new String[]{"PJX110", "未知", "7.x", "v2.0", "模块", "APK", "配套",
        "LSPosed", "com.oplus.camera", "XPAN", "C16", "C17", "6.020.876", "7.013.51",
        "ROM", "配置", "SDK", "adapter", "校验", "8192×3024", "1×", "8×", "目标", "指南", "实机"})
      contains(visible(app), text);

    Button show = (Button) find(app, "查看手动设置步骤");
    TextView steps = find(app, "1. 自行确认");
    check(steps.getVisibility() == View.GONE, "Steps expanded before user action");
    int childCount = content.children.size();
    check(show.performClick(), "Show-steps click failed");
    check(steps.getVisibility() == View.VISIBLE, "Steps did not become visible");
    contains(visible(app), "仅勾选 com.oplus.camera"); contains(visible(app), "官方说明");
    show.performClick();
    check(content.children.size() == childCount, "Repeated guide click duplicated views");
    check(app.intents.isEmpty() && app.packageManager.calls == 1 && Toast.shown == 0,
        "Showing instructions changed external state");
    unverified(app);

    Button details = (Button) find(app, "打开本应用系统设置");
    app.packageName = "test.this.app";
    details.performClick();
    check(app.intents.size() == 1, "Settings intent missing");
    Intent intent = app.intents.get(0);
    check(Settings.ACTION_APPLICATION_DETAILS_SETTINGS.equals(intent.getAction()), "Private settings action");
    check("package".equals(intent.getData().getScheme())
        && "test.this.app".equals(intent.getData().getSchemeSpecificPart())
        && intent.getData().fragment == null, "Settings must target this app, not a manager");
    unverified(app);
    app.launchFailure = new ActivityNotFoundException(); details.performClick();
    check(app.intents.size() == 1 && Toast.shown == 1, "Missing Settings handler not handled");
    contains(Toast.lastMessage.toString(), "仍未验证");
    app.launchFailure = new SecurityException("denied"); details.performClick();
    check(app.intents.size() == 1 && Toast.shown == 2, "Denied Settings not handled");
    unverified(app);

    app.packageManager.missing = null; app.packageManager.info.versionName = "7.013.51";
    app.onResume();
    check(app.packageManager.calls == 2, "Device summary did not refresh on resume");
    contains(visible(app), "C17 型号/版本号初步匹配");
    contains(visible(app), "不验证 ROM、配置或 SDK / adapter 固定校验"); unverified(app);
    app.packageManager.info.versionName = "6.020.876"; app.onResume();
    contains(visible(app), "C16 型号/版本号初步匹配"); unverified(app);
    app.packageManager.info.versionName = "7.013.510"; app.onResume();
    contains(visible(app), "未匹配预设型号/相机版本"); unverified(app);
    for (RuntimeException failure : new RuntimeException[]{new SecurityException("visibility"),
        new IllegalStateException("unavailable")}) {
      app.packageManager.failure = failure; app.onResume();
      contains(visible(app), "未知（无法读取"); unverified(app);
    }
    app.packageManager.failure = null;
    for (String version : new String[]{null, "", " "}) {
      app.packageManager.info.versionName = version; app.onResume();
      contains(visible(app), "未知（无法读取"); unverified(app);
    }
    app.packageManager.info = null; app.onResume();
    contains(visible(app), "未知（无法读取"); unverified(app);
    app.packageManager.info = new PackageInfo(); app.packageManager.info.versionName = "7.013.51";
    Build.MODEL = null; app.onResume();
    contains(visible(app), "设备型号：未知"); contains(visible(app), "型号或相机版本无法读取");
    unverified(app);

    for (String model : new String[]{null, "", " ", "PJX110", "PLK110", "pjx110", "PJX110 "})
      for (String version : new String[]{null, "", " ", "6.020.876", "7.013.51", "7.013.510",
          "7.999.1", "7.013.51 ", "6.020.876-mod"}) {
        String summary = XpanSetupActivity.deviceSummary(model, version);
        boolean exact = "PJX110".equals(model) && ("6.020.876".equals(version) || "7.013.51".equals(version));
        check(summary.contains("型号/版本号初步匹配") == exact, "Loose compatibility match");
        contains(summary, "不验证模块、作用域、注入或硬件 XDR");
      }
    System.out.println("PASS setup activity " + checks + " checks; fake runtime only; no activation/device proof");
  }
}'''


if __name__ == "__main__":
    unittest.main(verbosity=2)
