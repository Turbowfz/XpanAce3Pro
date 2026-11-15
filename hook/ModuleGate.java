package com.xpanport.hook;

import android.content.Context;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.SystemClock;
import java.io.File;
import java.io.FileInputStream;
import java.lang.reflect.Method;
import java.util.HashSet;
import java.security.MessageDigest;

/** Root publishes readiness; the camera independently checks its actual unit SDK,
 * compatible same-parent adapter footprint and mounted config inputs.
 * A running camera is bound to one install token. Revocation is permanent for
 * that process, not permission to adopt a later installation's token.
 */
final class ModuleGate {
    interface Source {
        String property(String name) throws Exception;
        String model() throws Exception;
        String fingerprint() throws Exception;
        String cameraVersion() throws Exception;
        String[] libraries() throws Exception;
        String hash(String path) throws Exception;
        long elapsed();
    }

    private static final String CONFIG = "/odm/etc/camera/config/";
    private static final String SDK = "com.oplus.camera.unit.sdk.jar";
    private static final String ADAPTER = "com.oplus.camera.unit.sdk.adapter.jar";
    private static final long CACHE_MS = 3000;
    private final Source source;
    private final CameraProfile profile;
    private String token;
    private boolean revoked;
    private long verifiedAt = -1;
    private String detail = "not qualified";

    ModuleGate(Source source, CameraProfile profile) { this.source = source; this.profile = profile; }

    static ModuleGate create(final Context context, CameraProfile profile) {
        return new ModuleGate(new Source() {
            private final Method get = propertyGetter();
            @Override public String property(String name) throws Exception {
                return get == null ? "" : (String) get.invoke(null, name, "");
            }
            @Override public String model() { return Build.MODEL; }
            @Override public String fingerprint() { return Build.FINGERPRINT; }
            @Override public String cameraVersion() throws Exception {
                return context.getPackageManager().getPackageInfo("com.oplus.camera", 0).versionName;
            }
            @Override public String[] libraries() throws Exception {
                ApplicationInfo app = context.getPackageManager().getApplicationInfo(
                        "com.oplus.camera", PackageManager.GET_SHARED_LIBRARY_FILES);
                return app.sharedLibraryFiles;
            }
            @Override public String hash(String path) throws Exception { return fileHash(path); }
            @Override public long elapsed() { return SystemClock.elapsedRealtime(); }
        }, profile);
    }

    synchronized boolean enabled() { return check(false); }
    synchronized boolean verified() { return check(true); }
    synchronized String detail() { return detail; }

    private boolean check(boolean force) {
        if (revoked) return false;
        try {
            String id = profile.generation == CameraProfile.Generation.C16 ? "c16"
                    : profile.generation == CameraProfile.Generation.C17 ? "c17" : "";
            String state = state(id);
            if (state == null || (token != null && !token.equals(state))) return revoke("readiness/token revoked");
            long now = source.elapsed();
            if (!force && token != null && verifiedAt >= 0 && now >= verifiedAt && now - verifiedAt < CACHE_MS)
                return true;
            String[] expected = expected(id);
            if (expected == null || !"PJX110".equals(source.model())
                    || !expected[0].equals(source.fingerprint()) || !expected[0].equals(profile.buildFingerprint)
                    || !profile.cameraVersion.equals(source.cameraVersion())) return revoke("ROM/camera mismatch");
            String[] libs = libraryPaths(source.libraries());
            if (libs == null) return revoke("actual unit SDK path unverified or incompatible adapter path");
            String[] paths = {CONFIG + "camera_unit_config", CONFIG + "camera_unit_feature_config.protobuf",
                    CONFIG + "oplus_camera_config", libs[0], libs[1]};
            for (int i = 0; i < paths.length; i++) {
                if (!expected[i + 1].equals(source.hash(paths[i]))) return revoke("input hash mismatch: " + paths[i]);
            }
            if (!state.equals(state(id))) return revoke("readiness changed during qualification");
            token = state;
            verifiedAt = source.elapsed();
            detail = id + " qualified; install token bound; runtime effects unverified";
            return true;
        } catch (Throwable failure) { return revoke("qualification failed; original camera behavior preserved"); }
    }

    private String state(String id) throws Exception {
        if (id.isEmpty() || !"1".equals(source.property("debug.xpan.active"))) return null;
        String selected = source.property("debug.xpan.profile"), value = source.property("debug.xpan.token");
        if (!id.equals(selected) || value == null
                || !value.matches("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")) return null;
        if (!"1".equals(source.property("debug.xpan.active"))
                || !selected.equals(source.property("debug.xpan.profile"))
                || !value.equals(source.property("debug.xpan.token"))) return null;
        return value;
    }

    private boolean revoke(String why) { revoked = true; detail = why; return false; }

    /** The camera directly imports unit SDK. Original ClientAdapter constructs its
     * CameraUnitImpl/ProducerImpl/ConsumerImpl without loading adapter.jar. When
     * adapter is not declared by the app, its same-parent file is checked only as
     * a pinned compatibility footprint, never advertised as an actual loaded JAR.
     */
    static String[] libraryPaths(String[] paths) {
        if (paths == null || paths.length == 0 || paths.length > 64) return null;
        String sdk = null, adapter = null;
        HashSet<String> seen = new HashSet<String>();
        for (String path : paths) {
            if (path == null || path.length() > 4096 || !seen.add(path)) return null;
            if (path.matches("/[A-Za-z0-9_./-]+\\.(jar|so)")) {
                if (path.contains("//") || path.matches(".*(?:/\\.\\.?)(?:/|$).*")) return null;
            } else if (!path.matches("lib[A-Za-z0-9_.+-]+\\.so")) return null;
            String name = path.substring(path.lastIndexOf('/') + 1);
            if (!SDK.equals(name) && !ADAPTER.equals(name)) {
                if (path.contains("com.oplus.camera.unit.sdk")) return null;
                continue;
            }
            String parent = path.substring(0, path.lastIndexOf('/') + 1);
            if (!"/product/framework/".equals(parent) && !"/my_product/product_overlay/framework/".equals(parent)) return null;
            if (SDK.equals(name)) { if (sdk != null) return null; sdk = path; }
            else { if (adapter != null) return null; adapter = path; }
        }
        if (sdk == null) return null;
        String parent = sdk.substring(0, sdk.lastIndexOf('/') + 1);
        if (adapter == null) adapter = parent + ADAPTER;
        if (!parent.equals(adapter.substring(0, adapter.lastIndexOf('/') + 1))) return null;
        return new String[]{sdk, adapter};
    }

    private static Method propertyGetter() {
        try { return Class.forName("android.os.SystemProperties").getMethod("get", String.class, String.class); }
        catch (Throwable failure) { return null; }
    }

    private static String fileHash(String path) throws Exception {
        File file = new File(path);
        long size = file.length(), modified = file.lastModified();
        if (!file.isFile() || size <= 0 || size > 16 * 1024 * 1024) throw new IllegalStateException("unreadable/bounded input: " + path);
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        long count = 0;
        byte[] buffer = new byte[65536];
        try (FileInputStream input = new FileInputStream(file)) {
            for (int n; (n = input.read(buffer)) != -1;) {
                count += n;
                if (count > size) throw new IllegalStateException("input grew during hash");
                digest.update(buffer, 0, n);
            }
        }
        if (count != size || file.length() != size || file.lastModified() != modified)
            throw new IllegalStateException("input changed during hash");
        StringBuilder hex = new StringBuilder(64);
        for (byte b : digest.digest()) { hex.append(Character.forDigit((b >>> 4) & 15, 16)); hex.append(Character.forDigit(b & 15, 16)); }
        return hex.toString();
    }

    private static String[] expected(String id) {
        String unit = "6dff1eaf085af1e61817eda9c34e4937e67e2ef4416cbee31409051e43ce39f7";
        String feature = "baab74e1dae8501a0ecf2c9d75847a64cfbb2afee91945e2311edb9e1caedd2e";
        if ("c16".equals(id)) return new String[]{
                "OnePlus/PJX110/OP5D06L1:16/UKQ1.231108.001/U.26f642a-3a0080-3a0082:user/release-keys", unit, feature,
                "c26086ad5d1f90bc89aba87d305aa5cc1c186b9276daeb914bdf6780c33f50b6",
                "37a13d408f7ee95b8e954c2ef11c44a0aee433da45958cc7ddb80c688e0692c6",
                "ac0b595faae8d3f684aa457f223fbfb0f167f0d0e0cc461ef08dbbe52d6e61cf"};
        if ("c17".equals(id)) return new String[]{
                "OnePlus/PJX110/OP5D06L1:17/CP2A.260605.016/C.3601857-f47121-f4775e:user/release-keys", unit, feature,
                "5d2002fddbfe4d21bb446081e84c30a5bb96261edb69c21bd2617e2f489424ff",
                "d2b20133252414e26ac7c68255c76fa61cf41826c3b63d423a14c1187af42899",
                "7418d59955746d8aca56438a5e0e26e1f08ff6ae4d0cacc22057d0041b12aac4"};
        return null;
    }
}
