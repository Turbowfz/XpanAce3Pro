package com.xpanport.hook;

import android.content.Context;
import android.content.pm.PackageInfo;
import android.os.Build;

/** 实际相机版本与反射契约分别验证，不能以 Android 版本猜混淆名。 */
final class CameraProfile {
    enum Generation { C16, C17, UNSUPPORTED }

    final Generation generation;
    final String cameraVersion;
    final String buildFingerprint;

    private CameraProfile(Generation generation, String version, String fingerprint) {
        this.generation = generation;
        this.cameraVersion = version;
        this.buildFingerprint = fingerprint;
    }

    static CameraProfile read(Context context) throws Exception {
        if (context == null || !"com.oplus.camera".equals(context.getPackageName())) {
            throw new IllegalArgumentException("Not the camera context");
        }
        PackageInfo info = context.getPackageManager().getPackageInfo("com.oplus.camera", 0);
        String version = info.versionName == null ? "" : info.versionName;
        return select(Build.MODEL, version, Build.FINGERPRINT);
    }

    static CameraProfile select(String model, String version, String fingerprint) {
        if ("PJX110".equals(model) && "6.020.876".equals(version)) {
            return new CameraProfile(Generation.C16, version, fingerprint);
        }
        if ("PJX110".equals(model) && "7.013.51".equals(version)) {
            return new CameraProfile(Generation.C17, version, fingerprint);
        }
        return new CameraProfile(Generation.UNSUPPORTED, version, fingerprint);
    }
}
