package com.xpanport.hook;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.util.Arrays;

/** Explicit local sample check; never encodes or rewrites the original photo. */
public final class XpanRatioSampleCheck {
    private static final String SOURCE_HASH = "49c3365b2ace06fcb316a147ff14d8ddb01fcf05b9e19d2316780fe0db3dd76c";
    private static String hash(byte[] data) throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(data);
        StringBuilder result = new StringBuilder();
        for (byte b : digest) result.append(String.format("%02x", b & 255));
        return result.toString();
    }
    public static void main(String[] args) throws Exception {
        if (args.length != 2) throw new IllegalArgumentException("read-only source and new analytical output required");
        Path source = Paths.get(args[0]), target = Paths.get(args[1]);
        if (Files.size(source) != 3720203 || Files.exists(target)) throw new IllegalArgumentException("exact source/new target required");
        byte[] input = Files.readAllBytes(source), snapshot = input.clone();
        if (!SOURCE_HASH.equals(hash(input))) throw new IllegalArgumentException("sample hash mismatch");
        XdrRatioPatch.Result raised = XdrRatioPatch.raiseRatioMax(input, 8.0);
        if (raised.status != XdrRatioPatch.Status.PATCHED || raised.log2RatioMax() != 3.0 || raised.ratioMax() != 8.0)
            throw new AssertionError("ratioMax8 metadata not produced: " + raised.status);
        XdrRatioPatch.Result repeat = XdrRatioPatch.raiseRatioMax(raised.bytes(), 8.0);
        if (repeat.status != XdrRatioPatch.Status.ALREADY_SUFFICIENT || repeat.bytes() != raised.bytes())
            throw new AssertionError("8x patch not idempotent");
        if (!Arrays.equals(input, snapshot) || !Arrays.equals(snapshot, Files.readAllBytes(source)))
            throw new AssertionError("source changed");
        int changed = 0;
        for (int i = 0; i < input.length; i++) if (input[i] != raised.bytes()[i]) changed++;
        Files.write(target, raised.bytes(), StandardOpenOption.CREATE_NEW);
        System.out.println("PASS exact sample metadata ratioMax=8.0 log2=3.0 changedBytes=" + changed
                + " originalHash=" + hash(input) + " analyticalHash=" + hash(raised.bytes())
                + "; original unchanged; not HDR display proof");
    }
}
