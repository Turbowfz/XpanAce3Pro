package com.xpanport.hook;

/** 仅验证选择规则；不伪造任何设备成像或 SDK 能力。 */
public final class CameraProfileTest {
    private CameraProfileTest() { }

    public static void main(String[] args) {
        CameraProfile c16 = CameraProfile.select("PJX110", "6.020.876", "C16-fixture");
        check(c16.generation == CameraProfile.Generation.C16, "C16 exact version");
        check("C16-fixture".equals(c16.buildFingerprint), "C16 fingerprint preserved");
        CameraProfile c17 = CameraProfile.select("PJX110", "7.013.51", "C17-fixture");
        check(c17.generation == CameraProfile.Generation.C17, "C17 exact version");
        check("C17-fixture".equals(c17.buildFingerprint), "C17 fingerprint preserved");
        for (String version : new String[]{"", "7.012.64", "7.013.40", "7.013.52", "6.020.877"}) {
            check(CameraProfile.select("PJX110", version, "unknown").generation
                    == CameraProfile.Generation.UNSUPPORTED, "unknown version: " + version);
        }
        for (String model : new String[]{"PMA110", "PLK110", "PJZ110", "", null}) {
            check(CameraProfile.select(model, "7.013.51", "wrong-model").generation
                    == CameraProfile.Generation.UNSUPPORTED, "wrong model: " + model);
        }
        System.out.println("PASS CameraProfile exact model/version rules (not runtime verification)");
    }

    private static void check(boolean passed, String reason) {
        if (!passed) throw new AssertionError(reason);
    }
}
