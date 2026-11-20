"""Guard the approved XPAN scope; these are source checks, not device tests."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SourceContractTests(unittest.TestCase):
    def test_old_zoom_switching_is_gone(self):
        source = (ROOT / "hook/XpanHook.java").read_text(encoding="utf-8")
        for obsolete in ("CROP_REQ_FILE", "CROP_BUSY_FILE", "cropBusy", "writeCropRequest",
                         "sCropZoom", "sZoomIgnoreUntil", "bigPicEnabled", ".xpan_bigpic"):
            self.assertNotIn(obsolete, source)
        self.assertNotIn("sXpanZoomReq >", source)
        self.assertIn('param.setResult(1.0f)', source)
        self.assertIn('parseSize(BIG_CAPTURE)', source)
        self.assertIn('new Size(8192, 3024)', source)

    def test_old_truncating_xdr_is_gone(self):
        source = (ROOT / "hook/XpanHook.java").read_text(encoding="utf-8")
        for obsolete in ("pxWriteBack", "pxWatch", "pxPatchRatioMax", "private static final class Hx"):
            self.assertNotIn(obsolete, source)
        self.assertEqual(source.count("installXdrProcessor(lp.classLoader, context)"), 2)
        self.assertIn("XdrBeforeSave.install", source)
        self.assertNotIn("XdrPostProcessor.install", source)
        self.assertFalse((ROOT / "hook/XdrPostProcessor.java").exists())
        self.assertTrue((ROOT / "tests/support/XdrPostProcessor.java").is_file())

    def test_limited_c16_zoom_lists_and_factory_guard(self):
        source = (ROOT / "hook/XpanHook.java").read_text(encoding="utf-8")
        self.assertIn("for (int index : new int[]{4, 5})", source)
        self.assertIn("!sFeatureGuardReady", source)
        self.assertIn('!"xpan".equals(param.args[1])', source)
        self.assertIn("sFeatureGuardReady = true", source)

    def test_module_readiness_is_required_before_hooks(self):
        source = (ROOT / "hook/XpanHook.java").read_text(encoding="utf-8")
        self.assertIn("sModuleGate = ModuleGate.create(context, profile)", source)
        self.assertLess(source.index("if (!sModuleGate.verified())"), source.index("XpanC17Hook.install("))
        self.assertIn("extends XC_MethodHook", source)
        self.assertIn("if (moduleEnabled()) beforeQualified(param)", source)
        self.assertIn("if (moduleEnabled()) afterQualified(param)", source)
        gate = (ROOT / "hook/ModuleGate.java").read_text(encoding="utf-8")
        self.assertIn("PackageManager.GET_SHARED_LIBRARY_FILES", gate)
        self.assertIn("app.sharedLibraryFiles", gate)
        self.assertNotIn("/data/adb/", gate)
        self.assertIn("revoked = true", gate)
        self.assertIn("!token.equals(state)", gate)

    def test_c17_menu_and_native_ui_follow_core_readiness(self):
        source = (ROOT / "hook/XpanHook.java").read_text(encoding="utf-8")
        self.assertLess(source.index("XpanC17Menu.install("), source.index("XpanC17Hook.install("))
        self.assertLess(source.index("XpanC17Ui.install("), source.index("XpanC17Hook.install("))
        self.assertEqual(source.count("sC17CoreReady && moduleEnabled()"), 5)
        self.assertIn("sC17CoreReady = result.installed", source)
        self.assertIn("c17Ui.onActualMode(manager, oldMode, actualMode)", source)
        menu = (ROOT / "hook/XpanC17Menu.java").read_text(encoding="utf-8")
        self.assertIn("nativeFiltersMissing(row.name)", menu)
        self.assertNotIn(".execSQL(", menu)
        ui = (ROOT / "hook/XpanC17Ui.java").read_text(encoding="utf-8")
        self.assertIn("renderingVerified = false", ui)
        self.assertNotIn(".setEnabled(", ui)

    def test_xpan_gain_frame_fix_is_c17_gated_and_not_photo_writeback(self):
        source = (ROOT / "hook/XpanHook.java").read_text(encoding="utf-8")
        self.assertLess(source.index("sC17CoreReady = result.installed"), source.index("XpanGainMapFrame.install("))
        self.assertIn("sC17CoreReady && moduleEnabled() && !\"0\".equals(prop(\"debug.xpan.proxdr\"))", source)
        gain = (ROOT / "hook/XpanGainMapFrame.java").read_text(encoding="utf-8")
        self.assertIn("p.args[0] = neutral", gain)
        for forbidden in ("FileOutputStream", "System.load", "b.q.setLong", "b.r.setLong", "setThrowable("):
            self.assertNotIn(forbidden, gain)
        self.assertEqual(gain.count("p.setResult(Boolean.TRUE)"), 1)
        self.assertIn("call.owner != f || call.previous != null", gain)
        self.assertIn("!active() || !currentScaling(f)", gain)
        self.assertIn('FRAME_STYLE = "hassel_style_xpan_1"', gain)
        self.assertIn("nativePixelsVerified = false", gain)

    def test_c16_support_is_preserved_alongside_exact_c17_branch(self):
        profile = (ROOT / "hook/CameraProfile.java").read_text(encoding="utf-8")
        source = (ROOT / "hook/XpanHook.java").read_text(encoding="utf-8")
        self.assertIn('"6.020.876".equals(version)', profile)
        self.assertIn('"7.013.51".equals(version)', profile)
        self.assertIn("CameraProfile.Generation.C17", source)
        self.assertIn("XpanC16Config.install(", source)
        self.assertIn("installHalSizeFix(lp)", source)
        self.assertIn("installWatermarkIsolation(lp)", source)
        for file in ("XpanC16Config.java", "XpanC17Hook.java"):
            self.assertTrue((ROOT / "hook" / file).is_file())

    def test_c17_geometry_keys_are_local_and_follow_core_readiness(self):
        source = (ROOT / "hook/XpanHook.java").read_text(encoding="utf-8")
        config = (ROOT / "hook/XpanC17Config.java").read_text(encoding="utf-8")
        self.assertLess(source.index("if (!sModuleGate.verified())"), source.index("XpanC17Config.install("))
        self.assertLess(source.index("XpanC17Config.install("), source.index("XpanC17Hook.install("))
        self.assertIn('"C17 geometry config installation: " + config.detail', source)
        self.assertIn("if (!config.installed) return;", source)
        for key in ("com.oplus.xpan.main.picturesize", "com.oplus.xpan.crop.region.main.portrait",
                    "com.oplus.xpan.crop.region.main.landscape"):
            self.assertIn(key, config)
        self.assertIn('CAMERA_VERSION = "7.013.51"', config)
        self.assertIn('new Size(4096, 1512)', config)
        self.assertIn("PORTRAIT_VALUE.clone()", config)
        self.assertIn("LANDSCAPE_VALUE.clone()", config)
        for forbidden in ("JSONObject", "getValidConfigData", "sVendorTagConfigMap", "FilterGroupManager",
                          "FileOutputStream", "System.load", "com.tlsu.opluscamerapro", "setThrowable("):
            self.assertNotIn(forbidden, config)

    def test_aps_capture_trace_is_opt_in_read_only_and_c17_gated(self):
        source = (ROOT / "hook/XpanHook.java").read_text(encoding="utf-8")
        trace = (ROOT / "hook/XpanApsCaptureTrace.java").read_text(encoding="utf-8")
        self.assertLess(source.index("sC17CoreReady = result.installed"), source.index("XpanApsCaptureTrace.install("))
        self.assertIn('if ("1".equals(prop("debug.xpan.apstrace")))', source)
        self.assertIn('moduleEnabled() && "1".equals(prop("debug.xpan.apstrace"))', source)
        self.assertIn('!"7.013.51".equals(version)', trace)
        self.assertIn('"onHeicReceived", result', trace)
        for forbidden in ("setResult(", "setThrowable(", "FileOutputStream", "System.load",
                          "getHwBuffer", "setLong(", "bytes.set("):
            self.assertNotIn(forbidden, trace)
        self.assertIn("n > 4 && n % 64 != 0", trace)
        self.assertIn("not complete HEIC/capture ownership proof", trace)

    def test_development_module_description_does_not_claim_eight_x_complete(self):
        module = (ROOT / "module/module.prop").read_text(encoding="utf-8")
        self.assertIn("version=v2.1\n", module)
        self.assertIn("versionCode=21\n", module)
        self.assertIn("XDR 8x 和相框增益仍待修复", module)
        changelog = (ROOT / "changelog.md").read_text(encoding="utf-8")
        self.assertIn("C16 实机未生效", changelog)
        self.assertIn("实拍仍约 4.96×", changelog)

    def test_installer_shows_short_progress_and_keeps_safety_checks(self):
        customize = (ROOT / "module/customize.sh").read_text(encoding="utf-8")
        for marker in ("$STEP/5", "检查模块文件", "检查本机机型与相机版本",
                       "校验本机配置与 SDK，并安装配套 Hook APK", "核对安装结果", "完成",
                       'MODEL" = PJX110', "6.020.876|7.013.51",
                       'sh "$MODPATH/runtime.sh" install "$MODPATH"'):
            self.assertIn(marker, customize)
        for forbidden in ("sleep ", "pm uninstall", "lspctl", "setenforce"):
            self.assertNotIn(forbidden, customize)
        self.assertNotRegex(customize, r"(?m)^\s*(?:exec\s+)?(?:/system/bin/)?reboot(?:\s|$)")
        # A local abort() would shadow the installer framework's command and
        # recurse through command -v abort; the private name keeps exit code 1.
        self.assertNotIn("\nabort() {", customize)
        self.assertIn("xpan_abort() {", customize)
        self.assertTrue(customize.startswith("#!/system/bin/sh\n"))
        self.assertIn("SKIPMOUNT=true", customize)

    def test_opcamera_attached_module_completion_is_bounded_and_reversible(self):
        runtime = (ROOT / "module/runtime.sh").read_text(encoding="utf-8")
        self.assertIn("xpan_op_compat() {", runtime)
        self.assertEqual(runtime.count("xpan_op_compat || true"), 2)
        body = runtime.split("xpan_op_compat() {", 1)[1].split("\nxpan_install() {", 1)[0]
        self.assertIn("ro.build.version.release", body)
        self.assertIn("17|17.*", body)
        self.assertIn("/MANIFEST", body)
        self.assertIn("op-compat-backup", body)
        self.assertIn("reboot makes it effective", body)
        for forbidden in ("umount", "pm uninstall", "rm -rf", "OPCameraPro/disable"):
            self.assertNotIn(forbidden, body)
        package = (ROOT / "tools/package_module.py").read_text(encoding="utf-8")
        self.assertIn('"payload/op-compat/framework/com.oplus.camera.unit.sdk.jar"', package)
        self.assertIn('"payload/op-compat/framework/com.oplus.camera.unit.sdk.adapter.jar"', package)
        self.assertIn("Bundled C17 completion is not the approved stock original", package)
        self.assertIn('*OP_COMPAT', package)

    def test_v21_install_refreshes_old_state_and_precedes_sdk_qualification(self):
        runtime = (ROOT / "module/runtime.sh").read_text(encoding="utf-8")
        install = runtime.split("\nxpan_install() {", 1)[1].split("\nxpan_main() {", 1)[0]
        self.assertIn("xpan_install_cleanup", install)
        self.assertLess(install.index("xpan_install_cleanup"), install.index("xpan_prepare"))
        self.assertLess(install.index("xpan_op_compat || true"), install.index("xpan_prepare"))
        self.assertIn("xpan_parent_refresh() {", runtime)
        self.assertNotIn("xpan_parent_approval() {", runtime)
        self.assertIn("xpan_reboot_ready", runtime)
        self.assertIn(".xpan-reboot-required", runtime)
        self.assertNotIn("OPCameraPro/disable", install)
        self.assertNotIn("setenforce", install)

    def test_build_does_not_compile_duplicate_source_or_generate_keys(self):
        source = (ROOT / "tools/build_hook.py").read_text(encoding="utf-8")
        self.assertIn('HOOK.glob("*.java")', source)
        self.assertNotIn("genkeypair", source)
        self.assertIn("XPAN_STORE_PASS", source)
        self.assertIn("Signing certificate mismatch", source)
        self.assertIn("Hook sources changed during build", source)
        self.assertIn("unsigned_apk_sha256", source)
        entry = (ROOT / "hook/build.sh").read_text(encoding="utf-8")
        self.assertIn("set -euo pipefail", entry)
        self.assertNotIn("Desktop/XpanPort", entry)

    def test_new_source_has_no_crlf_or_control_character(self):
        files = [ROOT / ".gitattributes", ROOT / "hook/build.sh", ROOT / "hook/uninstall.sh",
                 ROOT / "tools/build_hook.py", ROOT / "hook/CameraProfile.java",
                 ROOT / "hook/XpanHook.java", ROOT / "hook/XpanC17Hook.java"]
        for path in files:
            with self.subTest(path=path.name):
                data = path.read_bytes()
                self.assertNotIn(b"\r", data)
                self.assertNotIn(b"\x01", data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
