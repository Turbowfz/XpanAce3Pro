package com.xpanport.hook;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.Random;

/**
 * Dependency-free Java 8 local tests. Synthetic metadata/layout fixtures are
 * NOT valid HEVC streams and do not prove camera provenance. Optional real tests
 * accept ONLY the three explicitly permitted SHA-256-pinned C16-associated
 * samples, read/patch in memory only, and never write originals. C17 and pristine
 * capture provenance remain unverified. --emit-patched PATH emits an in-memory
 * result on stdout for a separate decoder; the evidence report goes to stderr.
 */
public final class XdrRatioPatchTest {
    private static int checks;

    private XdrRatioPatchTest() { }

    public static void main(String[] args) throws Exception {
        if (args.length == 2 && "--emit-patched".equals(args[0])) {
            byte[] output = readOnlyFixture(Paths.get(args[1]));
            System.out.write(output);
            System.out.flush();
            return;
        }
        requests();
        structuredCases();
        rationalCases();
        xmpCases();
        topologyCases();
        geometryCases();
        malformedCases();
        unknownCases();
        resourceAndAmbiguityCases();
        truncationAndMutation();
        supportedMutation();
        for (String arg : args) readOnlyFixture(Paths.get(arg));
        System.out.println("PASS " + checks + " checks; Java 8 synthetic and explicitly pinned read-only fixtures; "
                + "metadata log2 ratio, not decoder-reported ratio; C17/pristine provenance unverified");
    }

    private static void check(boolean condition, String message) {
        checks++;
        if (!condition) throw new AssertionError(message);
    }

    private static XdrRatioPatch.Result unchanged(byte[] data, double minimum) {
        byte[] before = data == null ? null : data.clone();
        XdrRatioPatch.Result result = XdrRatioPatch.raiseRatioMax(data, minimum);
        check(result.bytes() == data, "must return original reference");
        check(!result.changed(), "rejected profile must not change");
        check(Double.isNaN(result.ratioMax()), "ratio must be unknown, never guessed/defaulted");
        check(Arrays.equals(before, data), "input mutated");
        check(Arrays.equals(before, result.bytes()), "output byte difference outside empty allowlist");
        XdrRatioPatch.Result repeat = XdrRatioPatch.raiseRatioMax(result.bytes(), minimum);
        check(repeat.bytes() == data && repeat.status == result.status, "not idempotent");
        check(Arrays.equals(before, repeat.bytes()), "repeat changed bytes");
        return result;
    }

    private static void expect(byte[] data, XdrRatioPatch.Status status) {
        XdrRatioPatch.Result result = unchanged(data, 8.0);
        check(result.status == status, "expected " + status + " but was " + result.status + ": " + result.reason);
    }

    private static void requests() {
        check(unchanged(null, 8.0).status == XdrRatioPatch.Status.INVALID_INPUT, "null status");
        byte[] fixture = fixture(new Options());
        for (double target : new double[]{Double.NaN, Double.NEGATIVE_INFINITY,
                Double.POSITIVE_INFINITY, -1, 0, 1, 4.956563, 7.999999, 9, 16, Double.MAX_VALUE}) {
            check(unchanged(fixture, target).status == XdrRatioPatch.Status.INVALID_REQUEST, "invalid request accepted");
        }
        check(unchanged(fixture, 8).status == XdrRatioPatch.Status.UNSUPPORTED_TMAP_LAYOUT,
                "opaque legacy synthetic fixture must still reject");
        expect(new byte[0], XdrRatioPatch.Status.MALFORMED);
    }

    private static void structuredCases() {
        byte[] base = fixture(new Options());
        XdrRatioPatch.Result r = unchanged(base, 8);
        check(r.status == XdrRatioPatch.Status.UNSUPPORTED_TMAP_LAYOUT, "synthetic tmap status");
        check(r.itemCount == 4 && r.tmapItemCount == 1 && r.xmpItemCount == 1, "declared item evidence");
        // ispe, EXIF and opaque fake pixel/gainmap bytes are included in the exact
        // whole-file comparison above. No dimensions are rewritten or truncated.
        int ispe = offsetOf(base, "ispe");
        check(get32(base, ispe + 8) == 8192 && get32(base, ispe + 12) == 3024, "synthetic dimensions");
        for (int version = 0; version <= 2; version++) {
            Options options = new Options();
            options.locationVersion = version;
            if (version == 2) options.wideIds = true;
            expect(fixture(options), XdrRatioPatch.Status.UNSUPPORTED_TMAP_LAYOUT);
        }
        Options o = new Options(); o.integerWidth = 8;
        expect(fixture(o), XdrRatioPatch.Status.UNSUPPORTED_TMAP_LAYOUT);
        o = new Options(); o.extended = true;
        expect(fixture(o), XdrRatioPatch.Status.UNSUPPORTED_TMAP_LAYOUT);
        o = new Options(); o.zeroMdat = true;
        expect(fixture(o), XdrRatioPatch.Status.UNSUPPORTED_TMAP_LAYOUT);
        o = new Options(); o.idatTmap = true;
        expect(fixture(o), XdrRatioPatch.Status.UNSUPPORTED_TMAP_LAYOUT);
        o = new Options(); o.splitTmap = true;
        expect(fixture(o), XdrRatioPatch.Status.UNSUPPORTED_TMAP_LAYOUT);
        o = new Options(); o.baseOffset = true;
        expect(fixture(o), XdrRatioPatch.Status.UNSUPPORTED_TMAP_LAYOUT);
        o = new Options(); o.extentIndices = true;
        expect(fixture(o), XdrRatioPatch.Status.UNSUPPORTED_TMAP_LAYOUT);
        o = new Options(); o.noTmap = true;
        XdrRatioPatch.Result noTmap = unchanged(fixture(o), 8);
        check(noTmap.status == XdrRatioPatch.Status.NO_TMAP && noTmap.xmpItemCount == 1,
                "must not edit XMP without a confirmed tmap");
        o = new Options(); o.secondTmap = true;
        r = unchanged(fixture(o), 8);
        check(r.status == XdrRatioPatch.Status.UNSUPPORTED_TMAP_LAYOUT && r.tmapItemCount == 2,
                "multiple tmap items must not select first and edit");
        // Inert payload includes repeated big-endian guesses at 21/22/23 bits
        // and literal XMP keys. Their presence does not authorize any mutation.
        check(offsetOf(base, "GainMapMax=") > 0, "synthetic XMP decoy missing");
        expect(concat(base, box("free", new byte[0], false)), XdrRatioPatch.Status.UNSUPPORTED_TMAP_LAYOUT);
    }

    private static void rationalCases() {
        for (int bits : new int[]{21, 22, 25}) {
            Options o = new Options(); o.denominator = 1L << bits;
            patched(safeFixture(o), 2, new long[]{3, 8});
        }
        Options o = new Options(); o.denominator = 7; o.numerator = 10;
        o.xmp = gainXmp("1.428571");
        patched(safeFixture(o), 2, new long[]{3, 8});
        o = new Options(); o.capacityDenominator = 14; o.capacityNumerator = 20;
        o.denominator = 7; o.numerator = 10; o.xmp = gainXmp("1.428571");
        patched(safeFixture(o), 2, new long[]{3, 8});
        for (int v = 0; v <= 2; v++) {
            o = new Options(); o.locationVersion = v; o.wideIds = v == 2;
            patched(safeFixture(o), 2, new long[]{3, 8});
        }
        o = new Options(); o.integerWidth = 8; patched(safeFixture(o), 2, new long[]{3, 8});
        o = new Options(); o.baseOffset = true; patched(safeFixture(o), 2, new long[]{3, 8});
        o = new Options(); o.extentIndices = true; patched(safeFixture(o), 2, new long[]{3, 8});
        o = new Options(); o.extended = true; patched(safeFixture(o), 2, new long[]{3, 8});
        o = new Options(); o.idatTmap = true; patched(safeFixture(o), 2, new long[]{3, 8});
        o = new Options(); o.splitTmap = true; patched(safeFixture(o), 2, new long[]{3, 8});
        o = new Options(); o.zeroMdat = true; patched(safeFixture(o), 2, new long[]{3, 8});
        o = new Options(); o.xmp = ""; patched(safeFixture(o), 2, new long[]{3, 8});
        o = new Options(); o.numerator = 3 * o.denominator; o.xmp = gainXmp("3.000000");
        sufficient(safeFixture(o), 3);
        o.numerator = 4 * o.denominator; o.xmp = gainXmp("4.000000");
        sufficient(safeFixture(o), 4);
        for (long d : new long[]{715827883L, 0x40000000L, 0xffffffffL}) {
            o = new Options(); o.denominator = d; o.numerator = d / 2; o.xmp = "";
            expect(safeFixture(o), XdrRatioPatch.Status.NUMERATOR_OVERFLOW);
        }
        o = new Options(); o.denominator = 715827882L; o.numerator = 1; o.xmp = "";
        patched(safeFixture(o), 2, new long[]{3, 8}); // largest signed-safe D
        o = new Options(); o.capacityDenominator = 0xffffffffL; o.capacityNumerator = 0;
        o.numerator = 0; o.xmp = "";
        expect(safeFixture(o), XdrRatioPatch.Status.NUMERATOR_OVERFLOW);
        for (int at : new int[]{10, 18, 26, 34, 42, 50, 58}) {
            byte[] b = safeFixture(new Options()); mappedPut32(b, mappedItem(b, 2), at, 0);
            expect(b, XdrRatioPatch.Status.UNSUPPORTED_TMAP_LAYOUT);
        }
        for (int at : new int[]{0, 1, 3, 5, 6, 22, 38, 46, 54}) {
            byte[] b = safeFixture(new Options()); int[] m = mappedItem(b, 2); b[m[at]] ^= 1;
            expect(b, XdrRatioPatch.Status.UNSUPPORTED_TMAP_LAYOUT);
        }
        byte[] b = safeFixture(new Options()); mappedPut32(b, mappedItem(b, 2), 30, 0xffffffffL);
        expect(b, XdrRatioPatch.Status.UNSUPPORTED_TMAP_LAYOUT); // signed negative, not unsigned max
        b = safeFixture(new Options()); mappedPut32(b, mappedItem(b, 2), 14, 1);
        expect(b, XdrRatioPatch.Status.UNSUPPORTED_TMAP_LAYOUT);
        o = new Options(); o.tmapOverride = Arrays.copyOf(tmap(o), 61);
        expect(safeFixture(o), XdrRatioPatch.Status.UNSUPPORTED_TMAP_LAYOUT);
        o.tmapOverride = concat(tmap(new Options()), new byte[]{0});
        expect(safeFixture(o), XdrRatioPatch.Status.UNSUPPORTED_TMAP_LAYOUT);
        o.tmapOverride = new byte[142]; o.tmapOverride[5] = (byte) 0xc0;
        expect(safeFixture(o), XdrRatioPatch.Status.UNSUPPORTED_TMAP_LAYOUT);
        // Duplicate exact tmap/XMP bytes in pixels, EXIF, free and trailer are inert.
        o = new Options(); o.trailer = true;
        b = concat(safeFixture(o)); patched(b, 2, new long[]{3, 8});
        o.trailer = false;
        b = concat(safeFixture(o), box("free", concat(tmap(o), ascii(gainXmp("2.000000"))), false));
        patched(b, 2, new long[]{3, 8});
        o.topology = "notmap";
        expect(safeFixture(o), XdrRatioPatch.Status.NO_TMAP);
        o = new Options(); o.topology = "missing";
        expect(concat(safeFixture(o), box("free", tmap(o), false)), XdrRatioPatch.Status.UNSUPPORTED_TMAP_LAYOUT);
    }

    private static void xmpCases() {
        Options o = new Options();
        o.xmp = gainXmp("2.000000").replace("hdrgm:", "g:").replace("xmlns:hdrgm", "xmlns:g");
        patched(safeFixture(o), 2, new long[]{3, 8});
        o.xmp = gainXmp("2.000000").replace('"', '\'');
        patched(safeFixture(o), 2, new long[]{3, 8});
        o.xmp = gainXmp("02.000000"); patched(safeFixture(o), 2, new long[]{3, 8});
        o.xmp = gainXmp("2"); patched(safeFixture(o), 2, new long[]{3, 8});
        o.xmp = gainXmp("2.000000").replace(" hdrgm:HDRCapacityMax=\"2.000000\"", "");
        patched(safeFixture(o), 2, new long[]{3, 8});
        o.xmp = "<x:xmpmeta xmlns:x=\"adobe:ns:meta/\"><rdf:RDF xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\">"
                + "<rdf:Description xmlns:decoy=\"urn:decoy\" decoy:GainMapMax=\"99\"/>"
                + "</rdf:RDF></x:xmpmeta>";
        patched(safeFixture(o), 2, new long[]{8}); // wrong namespace is not hdrgm
        for (String key : new String[]{"GainMapMax", "HDRCapacityMax", "GainMapMin", "HDRCapacityMin",
                "Gamma", "OffsetSDR", "OffsetHDR", "BaseRenditionIsHDR", "Version"}) {
            o = new Options();
            String value = key.equals("BaseRenditionIsHDR") ? "True" : "9.000000";
            o.xmp = gainXmp("2.000000").replaceAll("hdrgm:" + key + "=\"[^\"]*\"", "hdrgm:" + key + "=\"" + value + "\"");
            expect(safeFixture(o), XdrRatioPatch.Status.CONFLICTING_METADATA);
        }
        for (String bad : new String[]{"NaN", "Infinity", "-1", "2,2,2", "2e0", "&#50;", "2.0000000000000"}) {
            o = new Options(); o.xmp = gainXmp(bad);
            expect(safeFixture(o), XdrRatioPatch.Status.UNSUPPORTED_TMAP_LAYOUT);
        }
        for (String bad : new String[]{
                gainXmp("2.000000").replace("hdrgm:Gamma=", "hdrgm:GainMapMax="),
                gainXmp("2.000000").replace("xmlns:hdrgm=", "xmlns:other="),
                gainXmp("2.000000").replace("hdrgm:Gamma", "hdrgm:Unknown"),
                gainXmp("2.000000").replace("</rdf:RDF>", "</rdf:Description>"),
                "<!DOCTYPE x [<!ENTITY a SYSTEM 'file:///not-read'>]>" + gainXmp("2.000000"),
                "<?xml encoding='UTF-16'?>" + gainXmp("2.000000"),
                "<?xml invalid junk?>" + gainXmp("2.000000"),
                gainXmp("2.000000").replace("/>\n", "><hdrgm:GainMapMax><rdf:Seq/></hdrgm:GainMapMax></rdf:Description>\n"),
                gainXmp("2.000000").replace("2.000000", "é"),
                gainXmp("2.000000") + "trailing text",
                gainXmp("2.000000") + gainXmp("2.000000")}) {
            o = new Options(); o.xmp = bad;
            expect(safeFixture(o), XdrRatioPatch.Status.UNSUPPORTED_TMAP_LAYOUT);
        }
        o = new Options(); o.xmp = gainXmp("2.000000").replace("xmlns:hdrgm=\"" + HDR + "\"",
                "xmlns:hdrgm=\"" + HDR + "\" xmlns:g=\"" + HDR + "\"")
                .replace("hdrgm:Gamma=", "g:GainMapMax=");
        expect(safeFixture(o), XdrRatioPatch.Status.UNSUPPORTED_TMAP_LAYOUT);
        // Late conflicting second packet refuses ALL changes, including prepared first packet.
        o = new Options(); o.baseXmp = gainXmp("1.000000");
        expect(safeFixture(o), XdrRatioPatch.Status.CONFLICTING_METADATA);
        o = new Options(); o.xmp = gainXmp("2.000000").replace("rdf:about=\"\"", "rdf:about=\"other\"");
        expect(safeFixture(o), XdrRatioPatch.Status.UNSUPPORTED_TMAP_LAYOUT);
        o = new Options(); o.xmp = gainXmp("2.000000").replace(HDR, "urn:decoy");
        patched(safeFixture(o), 2, new long[]{8});
        o = new Options(); o.numerator = 4843037; o.denominator = 2097152;
        o.xmp = gainXmp("2.309340"); patched(safeFixture(o), 2, new long[]{3, 8});
        o.xmp = gainXmp("2.309341"); expect(safeFixture(o), XdrRatioPatch.Status.CONFLICTING_METADATA);
        o.xmp = gainXmp("2.30934"); expect(safeFixture(o), XdrRatioPatch.Status.CONFLICTING_METADATA);
        o = new Options(); o.xmp = gainXmp("2.000000") + repeat(' ', 65537);
        expect(safeFixture(o), XdrRatioPatch.Status.UNSUPPORTED_TMAP_LAYOUT);
        o = new Options(); o.xmp = "<x:xmpmeta xmlns:x=\"adobe:ns:meta/\">";
        for (int i = 0; i < 33; i++) o.xmp += "<node>";
        for (int i = 0; i < 33; i++) o.xmp += "</node>";
        o.xmp += "</x:xmpmeta>";
        expect(safeFixture(o), XdrRatioPatch.Status.UNSUPPORTED_TMAP_LAYOUT);
    }

    private static String repeat(char value, int count) {
        char[] chars = new char[count]; Arrays.fill(chars, value); return new String(chars);
    }

    private static void topologyCases() {
        for (String problem : new String[]{"reverse", "missing", "wrongprimary", "sharedtiles", "wronggrid",
                "xmpmulti", "xmpmissing", "badaltr", "twotmap", "gridcount", "gridsize", "competitor"}) {
            Options o = new Options(); o.topology = problem;
            expect(safeFixture(o), XdrRatioPatch.Status.UNSUPPORTED_TMAP_LAYOUT);
        }
        Options o = new Options(); o.groups = true; patched(safeFixture(o), 2, new long[]{3, 8});
        o.trailer = true; patched(safeFixture(o), 2, new long[]{3, 8});
        byte[] b = safeFixture(o);
        expect(concat(b, box("free", new byte[0], false)), XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
        put32(b, b.length - o.trailerPayload().length - 8, b.length);
        expect(b, XdrRatioPatch.Status.MALFORMED);
    }

    private static void geometryCases() {
        // Every fixture is an in-memory array. The exact writable-slot oracle in
        // patched() also proves property payloads, EXIF and fake pixels stay intact.
        Options o = new Options();
        o.extraProperties = new byte[][]{box("clap", new byte[32], false)};
        o.extraAssociations[1] = new int[]{4};
        expect(safeFixture(o), XdrRatioPatch.Status.MALFORMED); // old parser PATCHED this
        for (int length = 0; length <= 33; length++) {
            if (length == 32) continue;
            o = properties("clap", new byte[length], 1, false);
            expect(safeFixture(o), XdrRatioPatch.Status.MALFORMED);
        }
        for (int at : new int[]{4, 12, 20, 28}) {
            for (long denominator : new long[]{0, 0x80000000L, 0xffffffffL}) {
                byte[] crop = clap(6000, 1, 2000, 1, 0, 1, 0, 1);
                put32(crop, at, denominator);
                expect(safeFixture(properties("clap", crop, 1, false)), XdrRatioPatch.Status.MALFORMED);
            }
        }
        for (int at : new int[]{0, 8}) {
            for (long size : new long[]{0, 0x80000000L, 0xffffffffL}) {
                byte[] crop = clap(6000, 1, 2000, 1, 0, 1, 0, 1);
                put32(crop, at, size);
                expect(safeFixture(properties("clap", crop, 1, false)), XdrRatioPatch.Status.MALFORMED);
            }
        }
        for (byte[] crop : new byte[][]{
                clap(8193, 1, 2000, 1, 0, 1, 0, 1),
                clap(6000, 1, 3025, 1, 0, 1, 0, 1),
                clap(6000, 1, 2000, 1, 1097, 1, 0, 1),
                clap(6000, 1, 2000, 1, -1097, 1, 0, 1),
                clap(6000, 1, 2000, 1, 0, 1, 513, 1),
                clap(6000, 1, 2000, 1, 0, 1, -513, 1),
                clap(8191, 1, 3023, 1, 1073741824, Integer.MAX_VALUE, 0, 1),
                clap(8191, 1, 3023, 1, -1073741824, Integer.MAX_VALUE, 0, 1)}) {
            expect(safeFixture(properties("clap", crop, 1, false)), XdrRatioPatch.Status.MALFORMED);
        }
        // A shared property must be checked against EACH associated ispe. It
        // fits the base but escapes the smaller gain image even with legal fields.
        o = properties("clap", clap(6000, 1, 2000, 1, 0, 1, 0, 1), 1, false);
        o.extraAssociations[6] = new int[]{0x84};
        expect(safeFixture(o), XdrRatioPatch.Status.MALFORMED);
        // A syntactically valid crop without any associated dimensions is unknown.
        o = properties("clap", clap(6000, 1, 2000, 1, 0, 1, 0, 1), 2, false);
        expect(safeFixture(o), XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);

        for (boolean wide : new boolean[]{false, true}) {
            for (int angle = 0; angle <= 3; angle++) {
                o = properties("irot", new byte[]{(byte) angle}, 1, wide);
                o.extraAssociations[6] = new int[]{essential(o, 4)};
                patched(safeFixture(o), 2, new long[]{3, 8});
            }
            for (int axis = 0; axis <= 1; axis++) {
                o = properties("imir", new byte[]{(byte) axis}, 1, wide);
                o.extraAssociations[6] = new int[]{essential(o, 4)};
                patched(safeFixture(o), 2, new long[]{3, 8});
            }
            o = matchingCrop(clap(8192, 1, 3024, 1, 0, 1, 0, 1),
                    clap(4096, 1, 1512, 1, 0, 1, 0, 1), wide);
            patched(safeFixture(o), 2, new long[]{3, 8});
            for (int sign : new int[]{-1, 1}) {
                o = matchingCrop(clap(6000, 1, 2000, 1, sign * 1096, 1, sign * 512, 1),
                        clap(3000, 1, 1000, 1, sign * 548, 1, sign * 256, 1), wide);
                patched(safeFixture(o), 2, new long[]{3, 8}); // exact edge contact is legal
            }
            // Equivalent fractions and subpixel apertures are legal HEIF rational
            // geometry; this parser does not impose AVIF/MIAF integer crop rules.
            o = matchingCrop(clap(12000, 2, 4000, 2, -3, 2, 7, 2),
                    clap(3000, 1, 1000, 1, -3, 4, 7, 4), wide);
            patched(safeFixture(o), 2, new long[]{3, 8});
            o = matchingCrop(clap(15361, 2, 5521, 2, 3, 2, -5, 2),
                    clap(15361, 4, 5521, 4, 3, 4, -5, 4), wide);
            patched(safeFixture(o), 2, new long[]{3, 8});
            // Products exceed signed long; signed offset INT32_MIN and positive
            // denominators remain exact instead of overflowing or using doubles.
            o = matchingCrop(clap(2000000005, 1000000003, 2000000005, 1000000003,
                            Integer.MIN_VALUE, 1000000003, Integer.MIN_VALUE, 1000000003),
                    clap(2000000005, 2000000006, 2000000005, 2000000006,
                            Integer.MIN_VALUE, 2000000006, Integer.MIN_VALUE, 2000000006), wide);
            patched(safeFixture(o), 2, new long[]{3, 8});
            o = matchingCrop(clap(6000, 1, 2000, 1, 0, 1, 0, 1),
                    clap(3000, 1, 1000, 1, 0, 1, 0, 1), wide);
            o.extraAssociations[1] = new int[]{4};
            expect(safeFixture(o), XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
            o = properties("irot", new byte[]{1}, 1, wide);
            o.extraAssociations[6] = new int[]{essential(o, 4)};
            byte[] reordered = safeFixture(o);
            swapAssociations(reordered, 1, 1, 4);
            expect(reordered, XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
            o = properties("irot", new byte[]{1}, 1, wide);
            o.extraProperties = new byte[][]{box("irot", new byte[]{1}, false), box("imir", new byte[]{1}, false)};
            o.extraAssociations[1] = new int[]{essential(o, 5), essential(o, 4)};
            o.extraAssociations[6] = new int[]{essential(o, 5), essential(o, 4)};
            patched(safeFixture(o), 2, new long[]{3, 8}); // matching retained non-crop order
            o.extraAssociations[6] = new int[]{essential(o, 4), essential(o, 5)};
            expect(safeFixture(o), XdrRatioPatch.Status.CONFLICTING_METADATA);
            o = matchingCrop(clap(6000, 1, 2000, 1, -3, 2, 7, 2),
                    clap(3000, 1, 1000, 1, -3, 4, 7, 4), wide);
            o.extraProperties = concatProperties(o.extraProperties,
                    box("irot", new byte[]{3}, o.extended), box("imir", new byte[]{1}, o.extended));
            o.extraAssociations[1] = new int[]{essential(o, 4), essential(o, 6), essential(o, 7)};
            o.extraAssociations[6] = new int[]{essential(o, 5), essential(o, 6), essential(o, 7)};
            patched(safeFixture(o), 2, new long[]{3, 8});
            // Same values with different order can change the transform.
            o.extraAssociations[6] = new int[]{essential(o, 5), essential(o, 7), essential(o, 6)};
            expect(safeFixture(o), XdrRatioPatch.Status.CONFLICTING_METADATA);
            o.extraAssociations[6] = new int[]{essential(o, 6), essential(o, 5), essential(o, 7)};
            expect(safeFixture(o), XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
        }
        for (String type : new String[]{"irot", "imir"}) {
            int maximum = "irot".equals(type) ? 3 : 1;
            for (int value = maximum + 1; value <= 255; value++) {
                expect(safeFixture(properties(type, new byte[]{(byte) value}, 1, false)), XdrRatioPatch.Status.MALFORMED);
            }
            for (int length : new int[]{0, 2, 4, 32}) {
                expect(safeFixture(properties(type, new byte[length], 1, false)), XdrRatioPatch.Status.MALFORMED);
            }
            o = properties(type, new byte[]{1}, 1, false);
            expect(safeFixture(o), XdrRatioPatch.Status.CONFLICTING_METADATA); // other image has none
            o.extraProperties = new byte[][]{box(type, new byte[]{1}, false), box(type, new byte[]{0}, false)};
            o.extraAssociations[6] = new int[]{0x85};
            expect(safeFixture(o), XdrRatioPatch.Status.CONFLICTING_METADATA);
            o = properties(type, new byte[]{1}, 1, false);
            o.extraAssociations[6] = new int[]{0x84};
            o.extraAssociations[1] = new int[]{4}; // known transform cannot be nonessential
            expect(safeFixture(o), XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
            for (int id : new int[]{2, 5, 7}) {
                expect(safeFixture(properties(type, new byte[]{1}, id, false)), XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
            }
        }
        o = matchingCrop(clap(6000, 1, 2000, 1, -3, 2, 7, 2),
                clap(3000, 1, 1000, 1, -3, 4, 7, 4), false);
        o.extended = true;
        o.extraProperties = new byte[][]{box("clap", clap(6000, 1, 2000, 1, -3, 2, 7, 2), true),
                box("clap", clap(3000, 1, 1000, 1, -3, 4, 7, 4), true),
                box("irot", new byte[]{2}, true), box("imir", new byte[]{0}, true)};
        o.extraAssociations[1] = new int[]{0x84, 0x86, 0x87};
        o.extraAssociations[6] = new int[]{0x85, 0x86, 0x87};
        byte[] extended = safeFixture(o);
        patched(extended, 2, new long[]{3, 8});
        extended[boxPayload(extended, propertyBox(extended, 6))] = 4;
        expect(extended, XdrRatioPatch.Status.MALFORMED);
        o = matchingCrop(clap(6000, 1, 2000, 1, 0, 1, 0, 1),
                clap(3000, 1, 1000, 1, 1, 1, 0, 1), false);
        expect(safeFixture(o), XdrRatioPatch.Status.CONFLICTING_METADATA);
        o = matchingCrop(clap(6000, 1, 2000, 1, 0, 1, 0, 1),
                clap(2999, 1, 1000, 1, 0, 1, 0, 1), false);
        expect(safeFixture(o), XdrRatioPatch.Status.CONFLICTING_METADATA);
        // These normalized widths round to the same double but are not equal.
        o = matchingCrop(clap(2147483646, 2147483647, 2, 1, 0, 1, 0, 1),
                clap(1073741822, 2147483645, 1, 1, 0, 1, 0, 1), false);
        check((2147483646.0 / 2147483647 / 8192) == (1073741822.0 / 2147483645 / 4096),
                "precision conflict fixture must defeat a floating-point comparison");
        expect(safeFixture(o), XdrRatioPatch.Status.CONFLICTING_METADATA);
        o = properties("irot", new byte[]{1}, 1, false);
        o.numerator = 4 * o.denominator; o.xmp = gainXmp("4.000000");
        expect(safeFixture(o), XdrRatioPatch.Status.CONFLICTING_METADATA); // no sufficient-ratio bypass

        for (int index : new int[]{1, 2}) {
            for (int at : new int[]{4, 8}) {
                for (long dimension : new long[]{0, 0x80000000L, 0xffffffffL}) {
                    byte[] b = safeFixture(new Options());
                    put32(b, boxPayload(b, propertyBox(b, index)) + at, dimension);
                    expect(b, dimension == 0 ? XdrRatioPatch.Status.MALFORMED : XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
                }
            }
        }
        o = properties("ispe", full(0, 0, concat(number(Integer.MAX_VALUE, 4), number(Integer.MAX_VALUE, 4))), 4, false);
        patched(safeFixture(o), 2, new long[]{3, 8}); // explicit dimension bound, not a decoder allocation
        essentialPropertyCases();
    }

    private static byte[] clap(long widthN, long widthD, long heightN, long heightD,
                               long horizontalN, long horizontalD, long verticalN, long verticalD) {
        return concat(number(widthN, 4), number(widthD, 4), number(heightN, 4), number(heightD, 4),
                number(horizontalN, 4), number(horizontalD, 4), number(verticalN, 4), number(verticalD, 4));
    }

    private static int essential(Options o, int index) { return index | (o.wideIds ? 0x8000 : 0x80); }

    private static Options properties(String type, byte[] payload, int id, boolean wide) {
        Options o = new Options(); o.wideIds = wide; o.locationVersion = wide ? 2 : 1;
        o.extraProperties = new byte[][]{box(type, payload, o.extended)};
        o.extraAssociations[id] = new int[]{essential(o, 4)};
        return o;
    }

    private static Options matchingCrop(byte[] base, byte[] gain, boolean wide) {
        Options o = properties("clap", base, 1, wide);
        o.extraProperties = new byte[][]{box("clap", base, o.extended), box("clap", gain, o.extended)};
        o.extraAssociations[6] = new int[]{essential(o, 5)};
        return o;
    }

    private static byte[][] concatProperties(byte[][] existing, byte[]... extra) {
        byte[][] out = Arrays.copyOf(existing, existing.length + extra.length);
        System.arraycopy(extra, 0, out, existing.length, extra.length);
        return out;
    }

    private static int propertyBox(byte[] data, int index) {
        int meta = findBox(data, 0, data.length, "meta");
        int iprp = findBox(data, boxPayload(data, meta) + 4, boxEnd(data, meta), "iprp");
        int ipco = findBox(data, boxPayload(data, iprp), boxEnd(data, iprp), "ipco");
        int at = boxPayload(data, ipco);
        for (int i = 1; i < index; i++) at = boxEnd(data, at);
        if (at >= boxEnd(data, ipco)) throw new AssertionError("synthetic property index not found");
        return at;
    }

    private static void essentialPropertyCases() {
        byte[] opaque = concat(ascii("OPAQUE-PROPERTY-SENTINEL"), box("clap", new byte[32], false));
        for (boolean wide : new boolean[]{false, true}) {
            for (int id : new int[]{1, 6, 2, 5, 7}) {
                Options o = properties("zzzz", opaque, id, wide);
                expect(safeFixture(o), XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
                o.extraAssociations[id] = new int[]{4}; // unknown nonessential stays byte-for-byte opaque
                patched(safeFixture(o), 2, new long[]{3, 8});
            }
            Options o = properties("zzzz", opaque, 1, wide);
            o.numerator = 4 * o.denominator; o.xmp = gainXmp("4.000000");
            expect(safeFixture(o), XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
            o = properties("zzzz", opaque, 4, wide); // unrelated EXIF does not define rendering
            patched(safeFixture(o), 2, new long[]{3, 8});
            o.extraAssociations[4] = null; // unassociated opaque property is also not essential
            patched(safeFixture(o), 2, new long[]{3, 8});
            // Essential is association-local even when two items share a box.
            o = properties("zzzz", opaque, 1, wide);
            o.extraAssociations[1] = new int[]{4};
            o.extraAssociations[6] = new int[]{essential(o, 4)};
            expect(safeFixture(o), XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
            o = new Options(); o.wideIds = wide; o.locationVersion = wide ? 2 : 1;
            o.extraProperties = new byte[][]{box("pixi", ascii("OPAQUE-PIXI"), false),
                    box("colr", concat(ascii("prof"), ascii("OPAQUE-ICC")), false),
                    box("auxC", ascii("OPAQUE-AUXC"), false)};
            o.extraAssociations[1] = new int[]{essential(o, 4), essential(o, 5), essential(o, 6)};
            o.extraAssociations[6] = new int[]{essential(o, 4), essential(o, 5), essential(o, 6)};
            byte[] b = safeFixture(o);
            markEssential(b, 1, 1); markEssential(b, 6, 2);
            markEssential(b, 5, 3); markEssential(b, 7, 3);
            patched(b, 2, new long[]{3, 8}); // no new codec/color/aux layout validation in this scope
        }
        Options o = properties("zzzz", opaque, 1, true);
        o.extraProperties = new byte[130][];
        for (int i = 0; i < 129; i++) o.extraProperties[i] = box("free", new byte[0], false);
        o.extraProperties[129] = box("zzzz", opaque, false); // ipco index 133, not a 7-bit index
        o.extraAssociations[1] = new int[]{133};
        patched(safeFixture(o), 2, new long[]{3, 8});
        o.extraAssociations[1] = new int[]{essential(o, 133)};
        expect(safeFixture(o), XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
    }

    private static void swapAssociations(byte[] data, long wantedItem, int firstIndex, int secondIndex) {
        int meta = findBox(data, 0, data.length, "meta");
        int iprp = findBox(data, boxPayload(data, meta) + 4, boxEnd(data, meta), "iprp");
        int ipma = findBox(data, boxPayload(data, iprp), boxEnd(data, iprp), "ipma");
        int[] c = {boxPayload(data, ipma)};
        int version = (int) read(data, c, 1), flags = (int) read(data, c, 3);
        int width = (flags & 1) == 0 ? 1 : 2, mask = width == 1 ? 0x7f : 0x7fff;
        int count = (int) read(data, c, 4), first = -1, second = -1;
        for (int i = 0; i < count; i++) {
            long id = read(data, c, version == 0 ? 2 : 4);
            int n = (int) read(data, c, 1);
            for (int j = 0; j < n; j++) {
                int at = c[0], index = (int) read(data, c, width) & mask;
                if (id == wantedItem && index == firstIndex) first = at;
                if (id == wantedItem && index == secondIndex) second = at;
            }
        }
        if (first < 0 || second < 0) throw new AssertionError("synthetic associations not found");
        for (int i = 0; i < width; i++) {
            byte value = data[first + i]; data[first + i] = data[second + i]; data[second + i] = value;
        }
    }

    private static void markEssential(byte[] data, long wantedItem, int wantedIndex) {
        int meta = findBox(data, 0, data.length, "meta");
        int iprp = findBox(data, boxPayload(data, meta) + 4, boxEnd(data, meta), "iprp");
        int ipma = findBox(data, boxPayload(data, iprp), boxEnd(data, iprp), "ipma");
        int[] c = {boxPayload(data, ipma)};
        int version = (int) read(data, c, 1), flags = (int) read(data, c, 3);
        int width = (flags & 1) == 0 ? 1 : 2, mask = width == 1 ? 0x80 : 0x8000;
        int count = (int) read(data, c, 4);
        for (int i = 0; i < count; i++) {
            long id = read(data, c, version == 0 ? 2 : 4);
            int n = (int) read(data, c, 1);
            for (int j = 0; j < n; j++) {
                int at = c[0], encoded = (int) read(data, c, width);
                if (id == wantedItem && (encoded & (mask - 1)) == wantedIndex) {
                    byte[] value = number(encoded | mask, width);
                    System.arraycopy(value, 0, data, at, width);
                    return;
                }
            }
        }
        throw new AssertionError("synthetic association not found");
    }

    private static void sufficient(byte[] data, double log2) {
        byte[] before = data.clone();
        XdrRatioPatch.Result r = XdrRatioPatch.raiseRatioMax(data, 8);
        check(r.status == XdrRatioPatch.Status.ALREADY_SUFFICIENT && !r.changed() && r.bytes() == data,
                "existing higher ratio lowered/rejected: " + r.reason);
        check(r.log2RatioMax() == log2 && r.ratioMax() == Math.pow(2, log2), "parsed sufficient ratio");
        check(Arrays.equals(data, before), "sufficient input changed");
    }

    private static XdrRatioPatch.Result patched(byte[] data, long tmapId, long[] xmpIds) {
        byte[] before = data.clone(), expected = data.clone();
        int[] tmap = mappedItem(data, tmapId);
        mappedPut32(expected, tmap, 14, 3 * mappedGet32(data, tmap, 18));
        mappedPut32(expected, tmap, 30, 3 * mappedGet32(data, tmap, 34));
        for (long id : xmpIds) {
            int[] m = mappedItem(data, id);
            String xml = new String(mappedBytes(data, m), StandardCharsets.ISO_8859_1);
            java.util.regex.Matcher matcher = java.util.regex.Pattern.compile("(?:GainMapMax|HDRCapacityMax)=[\"']([0-9]+(?:\\.[0-9]+)?)[\"']").matcher(xml);
            while (matcher.find()) {
                String value = matcher.group(1);
                int dot = value.indexOf('.'), integers = dot < 0 ? value.length() : dot;
                for (int i = 0; i < value.length(); i++) {
                    expected[m[matcher.start(1) + i]] = (byte) (i == integers - 1 ? '3' : i == dot ? '.' : '0');
                }
            }
        }
        XdrRatioPatch.Result r = XdrRatioPatch.raiseRatioMax(data, 8);
        check(r.status == XdrRatioPatch.Status.PATCHED && r.changed() && r.bytes() != data,
                "supported fixture not patched: " + r.status + " " + r.reason);
        check(Arrays.equals(data, before), "input bytes changed");
        check(Arrays.equals(r.bytes(), expected), "difference outside independently calculated writable slots");
        check(r.bytes().length == data.length, "container resized/trailer truncated");
        check(r.log2RatioMax() == 3 && r.ratioMax() == 8, "parsed output ratio not 8");
        int[] outMap = mappedItem(r.bytes(), tmapId);
        check(mappedGet32(r.bytes(), outMap, 14) == 3 * mappedGet32(r.bytes(), outMap, 18)
                && mappedGet32(r.bytes(), outMap, 30) == 3 * mappedGet32(r.bytes(), outMap, 34), "output rational log2 not 3");
        XdrRatioPatch.Result repeat = XdrRatioPatch.raiseRatioMax(r.bytes(), 8);
        check(repeat.status == XdrRatioPatch.Status.ALREADY_SUFFICIENT && !repeat.changed()
                && repeat.bytes() == r.bytes() && repeat.ratioMax() == 8, "patch not idempotent");
        return r;
    }

    private static void malformedCases() {
        byte[] b = fixture(new Options()); put32(b, 0, b.length + 1L);
        expect(b, XdrRatioPatch.Status.MALFORMED);
        b = fixture(new Options()); put32(b, 0, 7);
        expect(b, XdrRatioPatch.Status.MALFORMED);
        b = concat(fixture(new Options()), new byte[]{1});
        expect(b, XdrRatioPatch.Status.MALFORMED);
        b = fixture(new Options()); put32(b, offsetOf(b, "hdlr") - 4, b.length);
        expect(b, XdrRatioPatch.Status.MALFORMED);
        b = fixture(new Options()); put16(b, offsetOf(b, "iinf") + 8, 3);
        expect(b, XdrRatioPatch.Status.MALFORMED);
        b = fixture(new Options()); put16(b, offsetOf(b, "infe") + 8, 0);
        expect(b, XdrRatioPatch.Status.MALFORMED);
        b = fixture(new Options()); put16(b, offsetOf(b, "pitm") + 8, 999);
        expect(b, XdrRatioPatch.Status.MALFORMED);
        b = fixture(new Options()); put32(b, offsetOf(b, "ispe") + 8, 0);
        expect(b, XdrRatioPatch.Status.MALFORMED);
        b = fixture(new Options()); b[offsetOf(b, "ipma") + 15] = 2;
        expect(b, XdrRatioPatch.Status.MALFORMED);
        b = fixture(new Options()); put16(b, offsetOf(b, "iloc") + 10, 3);
        expect(b, XdrRatioPatch.Status.MALFORMED);
        b = fixture(new Options()); put16(b, offsetOf(b, "iloc") + 12, 999);
        expect(b, XdrRatioPatch.Status.MALFORMED);
        b = fixture(new Options()); put16(b, offsetOf(b, "iloc") + 14, 0x1000);
        expect(b, XdrRatioPatch.Status.MALFORMED);
        b = fixture(new Options()); put32(b, offsetOf(b, "iloc") + 20, b.length + 1L);
        expect(b, XdrRatioPatch.Status.MALFORMED);
        b = fixture(new Options()); put32(b, offsetOf(b, "iloc") + 24, 0xffffffffL);
        expect(b, XdrRatioPatch.Status.MALFORMED);
        b = fixture(new Options()); put32(b, offsetOf(b, "iloc") + 20, 8);
        expect(b, XdrRatioPatch.Status.MALFORMED);
        b = fixture(new Options()); put16(b, offsetOf(b, "dimg") + 8, 999);
        expect(b, XdrRatioPatch.Status.MALFORMED);
        // Truncated extended size and uuid user type must never read past end.
        expect(concat(number(1, 4), ascii("ftyp"), new byte[4]), XdrRatioPatch.Status.MALFORMED);
        expect(concat(number(24, 4), ascii("uuid"), new byte[3]), XdrRatioPatch.Status.MALFORMED);
        Options o = new Options(); o.extended = true;
        b = fixture(o); put32(b, 12, 0xffffffffL);
        expect(b, XdrRatioPatch.Status.MALFORMED);
        o = new Options(); o.integerWidth = 8; o.baseOffset = true;
        b = fixture(o);
        int loc = offsetOf(b, "iloc");
        // Individually supported signed-long values whose addition overflows.
        put64(b, loc + 18, Long.MAX_VALUE);
        put64(b, loc + 28, Long.MAX_VALUE);
        expect(b, XdrRatioPatch.Status.MALFORMED);
        o = new Options(); o.idatTmap = true;
        b = fixture(o);
        put32(b, offsetOf(b, "iloc") + 36, 9999);
        expect(b, XdrRatioPatch.Status.MALFORMED);
    }

    private static void unknownCases() {
        byte[] b = fixture(new Options()); b[offsetOf(b, "iloc") + 4] = 3;
        expect(b, XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
        b = fixture(new Options()); b[offsetOf(b, "iloc") + 8] = 0x24;
        expect(b, XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
        b = fixture(new Options()); put16(b, offsetOf(b, "iloc") + 16, 1);
        expect(b, XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
        b = fixture(new Options()); put16(b, offsetOf(b, "iloc") + 14, 2);
        expect(b, XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
        b = fixture(new Options()); put16(b, offsetOf(b, "infe") + 10, 1);
        expect(b, XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
        b = fixture(new Options()); b[offsetOf(b, "infe") + 7] = 2;
        expect(b, XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
        b = fixture(new Options()); put32(b, offsetOf(b, "iloc") + 24, 0);
        expect(b, XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
        b = fixture(new Options()); put32(b, offsetOf(b, "iloc") + 36,
                get32(b, offsetOf(b, "iloc") + 20));
        expect(b, XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
        b = fixture(new Options()); put32(b, 0, 0);
        expect(b, XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
        b = fixture(new Options()); replaceFourcc(b, offsetOf(b, "hdlr") + 12, "vide");
        expect(b, XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
        b = fixture(new Options()); replaceFourcc(b, offsetOf(b, "iinf"), "zzzz");
        expect(b, XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
        b = fixture(new Options()); replaceFourcc(b, offsetOf(b, "dimg"), "zzzz");
        expect(b, XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
        b = fixture(new Options()); replaceFourcc(b, 8, "zzzz"); replaceFourcc(b, 16, "zzzz");
        expect(b, XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
        expect(concat(fixture(new Options()), box("uuid", new byte[16], false)),
                XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
        Options o = new Options(); o.integerWidth = 8;
        b = fixture(o); b[offsetOf(b, "iloc") + 20] = (byte) 0x80;
        expect(b, XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
        o = new Options(); o.extended = true;
        b = fixture(o); b[8] = (byte) 0x80;
        expect(b, XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
        o = new Options(); o.extentIndices = true;
        b = fixture(o); put32(b, offsetOf(b, "iloc") + 20, 1);
        expect(b, XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
        expect(concat(fixture(new Options()), box("ftyp", concat(ascii("heic"), number(0, 4)), false)),
                XdrRatioPatch.Status.MALFORMED);
    }

    private static void resourceAndAmbiguityCases() {
        byte[] b = fixture(new Options());
        int meta = offsetOf(b, "meta") - 4;
        int metaLength = (int) get32(b, meta);
        expect(concat(b, Arrays.copyOfRange(b, meta, meta + metaLength)), XdrRatioPatch.Status.MALFORMED);
        b = fixture(new Options()); replaceFourcc(b, offsetOf(b, "pitm"), "hdlr");
        expect(b, XdrRatioPatch.Status.MALFORMED);
        b = fixture(new Options()); put16(b, offsetOf(b, "iinf") + 8, 4097);
        expect(b, XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
        b = fixture(new Options()); put16(b, offsetOf(b, "iloc") + 18, 16385);
        expect(b, XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
        b = fixture(new Options()); put16(b, offsetOf(b, "iloc") + 28, 1);
        expect(b, XdrRatioPatch.Status.MALFORMED);
        b = fixture(new Options()); b[offsetOf(b, "ipma") + 15] = 0;
        expect(b, XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
        b = fixture(new Options()); b[offsetOf(b, "ipma") + 7] = 2;
        expect(b, XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
        b = fixture(new Options()); replaceFourcc(b, offsetOf(b, "ipma"), "ipco");
        expect(b, XdrRatioPatch.Status.MALFORMED);
        b = fixture(new Options()); b[offsetOf(b, "infe") + 4] = 1;
        expect(b, XdrRatioPatch.Status.UNSUPPORTED_CONTAINER);
        b = fixture(new Options()); b[offsetOf(b, "infe") + 16] = 1;
        expect(b, XdrRatioPatch.Status.MALFORMED);
        b = fixture(new Options());
        int encoding = offsetOf(b, "application/rdf+xml") + ascii("application/rdf+xml").length + 1;
        b[encoding] = 'z';
        expect(b, XdrRatioPatch.Status.MALFORMED);
        b = fixture(new Options()); replaceFourcc(b, offsetOf(b, "infe") + 12, "uri ");
        expect(b, XdrRatioPatch.Status.MALFORMED);
        byte[] manyBoxes = new byte[8 * 16385];
        for (int i = 0; i < 16385; i++) {
            put32(manyBoxes, i * 8, 8);
            replaceFourcc(manyBoxes, i * 8 + 4, "free");
        }
        expect(manyBoxes, XdrRatioPatch.Status.MALFORMED);
    }

    private static void truncationAndMutation() {
        byte[] b = fixture(new Options());
        for (int n = 0; n < b.length; n++) {
            XdrRatioPatch.Result r = unchanged(Arrays.copyOf(b, n), 8);
            check(r.status != XdrRatioPatch.Status.UNSUPPORTED_TMAP_LAYOUT && r.status != XdrRatioPatch.Status.NO_TMAP,
                    "partial container accepted as complete structural fixture at length " + n);
        }
        for (int i = 0; i < b.length; i++) {
            byte[] mutation = b.clone(); mutation[i] ^= (byte) 0xff;
            unchanged(mutation, 8);
        }
        Random random = new Random(0x584452L);
        for (int i = 0; i < 5000; i++) {
            byte[] noise = new byte[random.nextInt(1024)]; random.nextBytes(noise);
            unchanged(noise, 8);
        }
        for (int i = 0; i < 5000; i++) {
            byte[] mutation = b.clone();
            for (int j = 0; j < 1 + random.nextInt(12); j++) mutation[random.nextInt(mutation.length)] = (byte) random.nextInt(256);
            unchanged(mutation, 8);
        }
        // Deep nesting remains opaque/rejected, never recursive without a bound.
        byte[] nested = new byte[0];
        for (int i = 0; i < 1000; i++) nested = box("meta", nested, false);
        unchanged(nested, 8);
    }

    private static void supportedMutation() {
        byte[] base = safeFixture(new Options());
        for (int n = 0; n < base.length; n++) {
            XdrRatioPatch.Result r = unchanged(Arrays.copyOf(base, n), 8);
            check(r.status != XdrRatioPatch.Status.PATCHED && r.status != XdrRatioPatch.Status.ALREADY_SUFFICIENT,
                    "truncated supported fixture patched");
        }
        Random random = new Random(0x623040L);
        for (int i = 0; i < base.length + 5000; i++) {
            byte[] mutation = base.clone();
            if (i < base.length) mutation[i] ^= (byte) 0xff;
            else for (int j = 0; j < 1 + random.nextInt(8); j++) mutation[random.nextInt(mutation.length)] ^= 1 << random.nextInt(8);
            byte[] before = mutation.clone();
            XdrRatioPatch.Result r = XdrRatioPatch.raiseRatioMax(mutation, 8);
            if (r.changed()) {
                check(Arrays.equals(before, mutation), "mutation fuzz changed original");
                boolean[] allowed = new boolean[mutation.length];
                int[] map = mappedItem(mutation, 2);
                for (int at : new int[]{14, 30}) for (int j = 0; j < 4; j++) allowed[map[at + j]] = true;
                for (long id : new long[]{3, 8}) {
                    int[] xm = mappedItem(mutation, id);
                    String xml = new String(mappedBytes(mutation, xm), StandardCharsets.ISO_8859_1);
                    java.util.regex.Matcher match = java.util.regex.Pattern.compile(
                            "(?:GainMapMax|HDRCapacityMax)\\s*=\\s*[\"']([0-9]+(?:\\.[0-9]+)?)[\"']").matcher(xml);
                    while (match.find()) for (int j = match.start(1); j < match.end(1); j++) allowed[xm[j]] = true;
                }
                for (int j = 0; j < before.length; j++) check(allowed[j] || before[j] == r.bytes()[j], "fuzz changed non-slot byte");
                check(r.ratioMax() == 8 && mappedGet32(r.bytes(), map, 30) == 3 * mappedGet32(mutation, map, 34), "fuzz gain maximum");
                XdrRatioPatch.Result repeat = XdrRatioPatch.raiseRatioMax(r.bytes(), 8);
                check(repeat.status == XdrRatioPatch.Status.ALREADY_SUFFICIENT && repeat.bytes() == r.bytes(), "fuzz patch not idempotent");
            } else if (r.status == XdrRatioPatch.Status.ALREADY_SUFFICIENT) {
                check(r.bytes() == mutation && r.log2RatioMax() >= 3, "no-lowering mutation result");
            } else unchanged(mutation, 8);
        }
        // Fragment ordering, including a non-monotonic physical order and an
        // XMP split through a value, must use mapped iloc extents, not raw scans.
        Options o = new Options(); o.splitTmap = true;
        byte[] b = safeFixture(o);
        int loc = findBox(b, boxPayload(b, findBox(b, 0, b.length, "meta")) + 4,
                boxEnd(b, findBox(b, 0, b.length, "meta")), "iloc");
        int p = boxPayload(b, loc) + 6 + 2 + 16; // second iloc entry
        int start = (int) get32(b, p + 8);
        byte[] raw = tmap(o);
        System.arraycopy(raw, 16, b, start, 46);
        System.arraycopy(raw, 0, b, start + 46, 16);
        put32(b, p + 8, start + 46); put32(b, p + 12, 16);
        put32(b, p + 16, start); put32(b, p + 20, 46);
        patched(b, 2, new long[]{3, 8});
        o = new Options(); o.splitXmp = true;
        patched(safeFixture(o), 2, new long[]{3, 8});
        o.idatTmap = true; o.splitTmap = true;
        patched(safeFixture(o), 2, new long[]{3, 8});
    }

    private static byte[] readOnlyFixture(Path path) throws Exception {
        Path root = Paths.get("C:/Users/User/Desktop/相机模块/XpanAce3Pro/XpanPort/12_验证/_证据").toAbsolutePath().normalize();
        String[] names = {"z_151320.heic", "IMG20261017144450.heic", "tone_164040.heic"};
        int[] sizes = {1523758, 2049817, 1914773};
        String[] hashes = {"0a16ee69a0ec1b5e3a4dae3993a6862e0fad2f4db7d1585df0758b678ed1914f",
                "6865dc151e32f45cb6c64ab175a7ae4222fb238402f5681caeeb109b8239b45b",
                "598ea645aac8f6f1f0d63e55bb1f324ad887570d2f39442b43f39c2d73b49b70"};
        path = path.toAbsolutePath().normalize();
        int fixture = -1;
        for (int i = 0; i < names.length; i++) if (path.equals(root.resolve(names[i]))) fixture = i;
        check(fixture >= 0 && Files.isRegularFile(path) && !Files.isSymbolicLink(path), "fixture path not explicitly permitted");
        byte[] data = Files.readAllBytes(path);
        String digest = hex(MessageDigest.getInstance("SHA-256").digest(data));
        check(data.length == sizes[fixture] && digest.equals(hashes[fixture]), "permitted fixture size/SHA mismatch");
        int[] tmap = mappedItem(data, 10000);
        long n = mappedGet32(data, tmap, 30), d = mappedGet32(data, tmap, 34);
        long gainXmp = fixture == 0 ? 10037 : 10065;
        XdrRatioPatch.Result result = patched(data, 10000, new long[]{gainXmp});
        byte[] diskAfter = Files.readAllBytes(path);
        check(Arrays.equals(data, diskAfter), "original fixture changed on disk");
        System.err.println("READ-ONLY C16-associated/unverified-pristine fixture=" + path + " bytes=" + data.length
                + " sha256=" + digest + " status=" + result.status + " items=" + result.itemCount
                + " tmap=" + result.tmapItemCount + " xmp=" + result.xmpItemCount
                + " parsed-input-log2=" + ((double) n / d) + " parsed-input-linear=" + Math.pow(2, (double) n / d)
                + " parsed-output-log2=" + result.log2RatioMax() + " parsed-output-linear=" + result.ratioMax()
                + " (NOT decoder-reported ratio; NOT a C17 production test)");
        return result.bytes();
    }

    private static String hex(byte[] bytes) {
        StringBuilder out = new StringBuilder();
        for (byte b : bytes) out.append(String.format(java.util.Locale.ROOT, "%02x", b & 255));
        return out.toString();
    }

    private static final class Options {
        int locationVersion = 1;
        int integerWidth = 4;
        boolean wideIds;
        boolean extended;
        boolean zeroMdat;
        boolean idatTmap;
        boolean splitTmap;
        boolean splitXmp;
        boolean baseOffset;
        boolean extentIndices;
        boolean noTmap;
        boolean secondTmap;
        long denominator = 1L << 21;
        long numerator = -1;
        long capacityDenominator = -1;
        long capacityNumerator = -1;
        String xmp = gainXmp("2.000000");
        String baseXmp = "<x:xmpmeta xmlns:x=\"adobe:ns:meta/\"><rdf:RDF xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\">"
                + "<rdf:Description xmlns:hdrgm=\"" + HDR + "\" hdrgm:Version=\"1.0\"/></rdf:RDF></x:xmpmeta>";
        String topology = "";
        byte[] tmapOverride;
        byte[][] extraProperties = new byte[0][];
        int[][] extraAssociations = new int[9][];
        boolean groups;
        boolean trailer;
        byte[] trailerPayload() { return concat(ascii("OPAQUE_TRAILER"), tmap(this), ascii(gainXmp("2.000000"))); }
    }

    private static final String HDR = "http://ns.adobe.com/hdr-gain-map/1.0/";

    private static String gainXmp(String max) {
        return "<x:xmpmeta xmlns:x=\"adobe:ns:meta/\"><rdf:RDF xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\">"
                + "<rdf:Description rdf:about=\"\" xmlns:hdrgm=\"" + HDR + "\" hdrgm:Version=\"1.0\""
                + " hdrgm:GainMapMin=\"0.000000\" hdrgm:GainMapMax=\"" + max + "\""
                + " hdrgm:HDRCapacityMin=\"0.000000\" hdrgm:HDRCapacityMax=\"" + max + "\""
                + " hdrgm:Gamma=\"1.000000\" hdrgm:OffsetSDR=\"0.000000\" hdrgm:OffsetHDR=\"0.000000\""
                + " hdrgm:BaseRenditionIsHDR=\"False\"/>\n</rdf:RDF></x:xmpmeta>";
    }

    private static byte[] tmap(Options o) {
        long n = o.numerator < 0 ? 2 * o.denominator : o.numerator;
        long cn = o.capacityNumerator < 0 ? n : o.capacityNumerator;
        long cd = o.capacityDenominator < 0 ? o.denominator : o.capacityDenominator;
        return concat(new byte[]{0, 0, 0, 0, 0, 0x40}, number(0, 4), number(1, 4),
                number(cn, 4), number(cd, 4), number(0, 4), number(1, 4), number(n, 4), number(o.denominator, 4),
                number(1, 4), number(1, 4), number(0, 4), number(1, 4), number(0, 4), number(1, 4));
    }

    private static byte[] safeFixture(Options o) {
        byte[] actualTmap = o.tmapOverride == null ? tmap(o) : o.tmapOverride;
        byte[] grid = concat(new byte[4], number(8192, 2), number(3024, 2));
        if (o.topology.equals("gridsize")) put16(grid, 4, 1234);
        byte[] decoys = concat(ascii("SYNTHETIC_PIXELS/EXIF"), tmap(new Options()), ascii(gainXmp("2.000000")));
        byte[][] payloads = {grid, actualTmap, ascii(o.xmp), decoys, decoys,
                concat(new byte[4], number(4096, 2), number(1512, 2)), decoys, ascii(o.baseXmp)};
        if (o.xmp.isEmpty()) payloads[2] = ascii("OPAQUE-NON-XMP");
        byte[] ftyp = box("ftyp", concat(ascii("heic"), number(0, 4), ascii("mif1")), o.extended);
        byte[] meta = safeMetadata(o, payloads, 0);
        int start = ftyp.length + meta.length + (o.extended ? 16 : 8);
        meta = safeMetadata(o, payloads, start);
        ByteArrayOutputStream mdat = new ByteArrayOutputStream();
        for (int i = 0; i < payloads.length; i++) if (!(o.idatTmap && i == 1)) append(mdat, payloads[i]);
        byte[] media = box("mdat", mdat.toByteArray(), o.extended);
        if (o.zeroMdat) put32(media, 0, 0);
        byte[] tail = o.trailer ? box("\u009a\u0099\u0099?", o.trailerPayload(), false) : new byte[0];
        return concat(ftyp, meta, media, tail);
    }

    private static byte[] safeMetadata(Options o, byte[][] payloads, int start) {
        int w = o.wideIds ? 4 : 2;
        String[] types = {"grid", "tmap", o.xmp.isEmpty() ? "Exif" : "mime", "Exif", "hvc1", "grid", "hvc1", "mime"};
        if (o.topology.equals("wronggrid")) types[5] = "hvc1";
        if (o.topology.equals("twotmap")) types[3] = "tmap";
        if (o.topology.equals("notmap")) types[1] = "Exif";
        byte[][] entries = new byte[8][];
        for (int i = 0; i < 8; i++) {
            byte[] extra = types[i].equals("mime") ? concat(ascii("application/rdf+xml"), new byte[]{0}) : new byte[0];
            entries[i] = box("infe", full(o.wideIds ? 3 : 2, i == 4 || i == 5 || i == 6 || i == 2 ? 1 : 0,
                    concat(number(i + 1, w), number(0, 2),
                    ascii(types[i]), new byte[]{0}, extra)), o.extended);
        }
        byte[] iinf = box("iinf", full(o.wideIds ? 1 : 0, 0, concat(number(8, o.wideIds ? 4 : 2), concat(entries))), o.extended);
        byte[] pitm = box("pitm", full(o.wideIds ? 1 : 0, 0, number(o.topology.equals("wrongprimary") ? 6 : 1, w)), o.extended);
        byte[] hdlr = box("hdlr", full(0, 0, concat(number(0, 4), ascii("pict"), new byte[12], new byte[]{0})), o.extended);
        byte[] ispe = box("ispe", full(0, 0, concat(number(8192, 4), number(3024, 4))), o.extended);
        byte[] gainIspe = box("ispe", full(0, 0, concat(number(4096, 4), number(1512, 4))), o.extended);
        byte[] hvcc = box("hvcC", ascii("SYNTHETIC-NOT-A-REAL-HEVC-CONFIG"), o.extended);
        byte[] ipco = box("ipco", concat(ispe, gainIspe, hvcc, concat(o.extraProperties)), o.extended);
        ByteArrayOutputStream associations = new ByteArrayOutputStream();
        int associationCount = 4;
        for (int id : new int[]{2, 3, 4, 8}) {
            if (o.extraAssociations[id] != null) associationCount++;
        }
        append(associations, number(associationCount, 4));
        for (int id : new int[]{1, 5, 6, 7, 2, 3, 4, 8}) {
            boolean image = id == 1 || id == 5 || id == 6 || id == 7;
            int[] extra = o.extraAssociations[id];
            if (!image && extra == null) continue;
            int normal = image ? id == 5 || id == 7 ? 2 : 1 : 0;
            append(associations, number(id, w));
            append(associations, number(normal + (extra == null ? 0 : extra.length), 1));
            if (image) {
                append(associations, number(id == 6 || id == 7 ? 2 : 1, o.wideIds ? 2 : 1));
                if (id == 5 || id == 7) append(associations, number(3, o.wideIds ? 2 : 1));
            }
            if (extra != null) {
                for (int index : extra) append(associations, number(index, o.wideIds ? 2 : 1));
            }
        }
        byte[] ipma = box("ipma", full(o.wideIds ? 1 : 0, o.wideIds ? 1 : 0, associations.toByteArray()), o.extended);
        byte[] iprp = box("iprp", concat(ipco, ipma), o.extended);
        ByteArrayOutputStream refs = new ByteArrayOutputStream();
        if (!o.topology.equals("missing")) append(refs, reference(o, "dimg", 2, o.topology.equals("reverse") ? new long[]{6, 1} : new long[]{1, 6}));
        append(refs, reference(o, "dimg", 1, o.topology.equals("gridcount") ? new long[]{5, 4} : new long[]{5}));
        append(refs, reference(o, "dimg", 6, new long[]{o.topology.equals("sharedtiles") ? 5 : 7}));
        if (!o.topology.equals("xmpmissing")) append(refs, reference(o, "cdsc", 3,
                o.topology.equals("xmpmulti") ? new long[]{1, 6} : new long[]{6}));
        append(refs, reference(o, "cdsc", 8, new long[]{1}));
        append(refs, reference(o, "cdsc", 4, new long[]{1}));
        if (o.topology.equals("competitor")) append(refs, reference(o, "dimg", 4, new long[]{1, 6}));
        byte[] iref = box("iref", full(o.wideIds ? 1 : 0, 0, refs.toByteArray()), o.extended);
        ByteArrayOutputStream loc = new ByteArrayOutputStream();
        append(loc, number((o.integerWidth << 4) | o.integerWidth, 1));
        append(loc, number((o.baseOffset ? o.integerWidth << 4 : 0) | (o.extentIndices ? 4 : 0), 1));
        append(loc, number(8, o.locationVersion < 2 ? 2 : 4));
        int offset = 0;
        for (int i = 0; i < 8; i++) {
            boolean idat = o.idatTmap && i == 1;
            append(loc, number(i + 1, o.locationVersion < 2 ? 2 : 4));
            if (o.locationVersion > 0) append(loc, number(idat ? 1 : 0, 2));
            append(loc, number(0, 2));
            long absolute = idat ? 0 : start + offset;
            if (o.baseOffset) append(loc, number(absolute, o.integerWidth));
            boolean split = o.splitTmap && i == 1 || o.splitXmp && i == 2;
            append(loc, number(split ? 2 : 1, 2));
            // Split THROUGH a numerator/XMP value to test exact mapped writes.
            int first = split ? i == 1 ? 16 : o.xmp.indexOf("GainMapMax=") + 14 : payloads[i].length;
            for (int j = 0; j < (split ? 2 : 1); j++) {
                if (o.extentIndices) append(loc, number(0, 4));
                append(loc, number((o.baseOffset ? 0 : absolute) + (j == 0 ? 0 : first), o.integerWidth));
                append(loc, number(j == 0 ? first : payloads[i].length - first, o.integerWidth));
            }
            if (!idat) offset += payloads[i].length;
        }
        byte[] iloc = box("iloc", full(o.locationVersion, 0, loc.toByteArray()), o.extended);
        byte[] idat = o.idatTmap ? box("idat", payloads[1], o.extended) : new byte[0];
        byte[] groups = o.groups || o.topology.equals("badaltr") ? box("grpl", box("altr", full(0, 0,
                concat(number(1, 4), number(2, 4), number(o.topology.equals("badaltr") ? 1 : 2, 4),
                        number(o.topology.equals("badaltr") ? 2 : 1, 4))), false), false) : new byte[0];
        return box("meta", full(0, 0, concat(hdlr, pitm, iinf, iloc, iprp, idat, iref, groups)), o.extended);
    }

    private static byte[] reference(Options o, String type, long from, long[] targets) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        append(out, number(from, o.wideIds ? 4 : 2)); append(out, number(targets.length, 2));
        for (long id : targets) append(out, number(id, o.wideIds ? 4 : 2));
        return box(type, out.toByteArray(), o.extended);
    }

    /** Independent test-only BMFF/iloc walk; no payload string search for real files. */
    private static int[] mappedItem(byte[] data, long wanted) {
        int meta = findBox(data, 0, data.length, "meta"), metaEnd = boxEnd(data, meta);
        int children = boxPayload(data, meta) + 4;
        int iloc = findBox(data, children, metaEnd, "iloc"), idat = findBox(data, children, metaEnd, "idat");
        int[] c = {boxPayload(data, iloc)};
        int v = (int) read(data, c, 1); read(data, c, 3);
        int a = (int) read(data, c, 1), b = (int) read(data, c, 1);
        int ow = a >>> 4, lw = a & 15, bw = b >>> 4, iw = b & 15;
        int count = (int) read(data, c, v < 2 ? 2 : 4);
        for (int i = 0; i < count; i++) {
            long id = read(data, c, v < 2 ? 2 : 4), method = v == 0 ? 0 : read(data, c, 2);
            read(data, c, 2); long base = read(data, c, bw);
            int n = (int) read(data, c, 2);
            java.util.ArrayList<Integer> offsets = new java.util.ArrayList<Integer>();
            for (int j = 0; j < n; j++) {
                if (v != 0) read(data, c, iw);
                long start = base + read(data, c, ow), length = read(data, c, lw);
                if (method == 1) start += boxPayload(data, idat);
                if (id == wanted) for (int k = 0; k < length; k++) offsets.add((int) start + k);
            }
            if (id == wanted) {
                int[] map = new int[offsets.size()];
                for (int j = 0; j < map.length; j++) map[j] = offsets.get(j);
                return map;
            }
        }
        throw new AssertionError("test item not declared: " + wanted);
    }

    private static int findBox(byte[] data, int at, int end, String type) {
        while (at < end) {
            if (new String(data, at + 4, 4, StandardCharsets.ISO_8859_1).equals(type)) return at;
            at = boxEnd(data, at);
        }
        return -1;
    }
    private static int boxPayload(byte[] data, int at) { return at + (get32(data, at) == 1 ? 16 : 8); }
    private static int boxEnd(byte[] data, int at) {
        long size = get32(data, at);
        if (size == 1) { int[] c = {at + 8}; size = read(data, c, 8); }
        return size == 0 ? data.length : at + (int) size;
    }
    private static long read(byte[] data, int[] at, int width) {
        long value = 0;
        for (int i = 0; i < width; i++) value = (value << 8) | (data[at[0]++] & 255L);
        return value;
    }
    private static byte[] mappedBytes(byte[] data, int[] map) {
        byte[] out = new byte[map.length];
        for (int i = 0; i < map.length; i++) out[i] = data[map[i]];
        return out;
    }
    private static long mappedGet32(byte[] data, int[] map, int at) {
        long value = 0;
        for (int i = 0; i < 4; i++) value = (value << 8) | (data[map[at + i]] & 255L);
        return value;
    }
    private static void mappedPut32(byte[] data, int[] map, int at, long value) {
        byte[] bytes = number(value, 4);
        for (int i = 0; i < 4; i++) data[map[at + i]] = bytes[i];
    }

    private static byte[] fixture(Options o) {
        byte[] pixels = ascii("SYNTHETIC_PIXELS!");
        byte[] tmap = concat(number(0x0049ef00, 4), number(0x0127bc00, 4), number(0x0093de00, 4),
                number(0x0049ef00, 4), ascii("OPAQUE_GAIN_DATA!"));
        byte[] xmp = ascii("<x:xmpmeta xmlns:x=\"adobe:ns:meta/\"><rdf:RDF xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\">"
                + "<rdf:Description xmlns:hdrgm=\"http://ns.adobe.com/hdr-gain-map/1.0/\" "
                + "hdrgm:GainMapMax=\"2.309350\" hdrgm:HDRCapacityMax=\"2.309350\"/>"
                + "</rdf:RDF></x:xmpmeta>");
        byte[] exif = ascii("SYNTHETIC_EXIF_SENTINEL");
        byte[][] payloads = new byte[][]{pixels, tmap, xmp, exif};
        byte[] ftyp = box("ftyp", concat(ascii("heic"), number(0, 4), ascii("mif1")), o.extended);
        byte[] metadata = metadata(o, payloads, 0);
        int mdatStart = ftyp.length + metadata.length + (o.extended ? 16 : 8);
        metadata = metadata(o, payloads, mdatStart);
        byte[] mdatPayload = o.idatTmap ? concat(pixels, xmp, exif) : concat(payloads);
        byte[] mdat = box("mdat", mdatPayload, o.extended);
        if (o.zeroMdat) put32(mdat, 0, 0);
        return concat(ftyp, metadata, mdat);
    }

    private static byte[] metadata(Options o, byte[][] payloads, int mdatStart) {
        int idWidth = o.wideIds ? 4 : 2;
        byte[][] entries = new byte[4][];
        for (int i = 0; i < 4; i++) {
            String type = i == 0 ? "hvc1" : i == 1 ? (o.noTmap ? "hvc1" : "tmap")
                    : i == 2 ? (o.secondTmap ? "tmap" : "mime") : "Exif";
            byte[] extra = "mime".equals(type) ? concat(ascii("application/rdf+xml"), new byte[]{0, 0}) : new byte[0];
            entries[i] = box("infe", full(o.wideIds ? 3 : 2, 0, concat(number(i + 1, idWidth),
                    number(0, 2), ascii(type), new byte[]{0}, extra)), o.extended);
        }
        byte[] iinf = box("iinf", full(o.wideIds ? 1 : 0, 0,
                concat(number(4, o.wideIds ? 4 : 2), concat(entries))), o.extended);
        byte[] pitm = box("pitm", full(o.wideIds ? 1 : 0, 0, number(1, idWidth)), o.extended);
        byte[] hdlr = box("hdlr", full(0, 0, concat(number(0, 4), ascii("pict"), new byte[12], new byte[]{0})), o.extended);
        byte[] ispe = box("ispe", full(0, 0, concat(number(8192, 4), number(3024, 4))), o.extended);
        byte[] ipco = box("ipco", ispe, o.extended);
        byte[] ipma = box("ipma", full(o.wideIds ? 1 : 0, o.wideIds ? 1 : 0,
                concat(number(1, 4), number(1, idWidth), number(1, 1), number(1, o.wideIds ? 2 : 1))), o.extended);
        byte[] iprp = box("iprp", concat(ipco, ipma), o.extended);
        byte[] ref = box("dimg", concat(number(2, idWidth), number(1, 2), number(1, idWidth)), o.extended);
        byte[] iref = box("iref", full(o.wideIds ? 1 : 0, 0, ref), o.extended);
        ByteArrayOutputStream loc = new ByteArrayOutputStream();
        append(loc, number((o.integerWidth << 4) | o.integerWidth, 1));
        append(loc, number((o.baseOffset ? o.integerWidth << 4 : 0) | (o.extentIndices ? 4 : 0), 1));
        append(loc, number(4, o.locationVersion < 2 ? 2 : 4));
        int mediaOffset = 0;
        for (int i = 0; i < 4; i++) {
            boolean idat = o.idatTmap && i == 1;
            append(loc, number(i + 1, o.locationVersion < 2 ? 2 : 4));
            if (o.locationVersion > 0) append(loc, number(idat ? 1 : 0, 2));
            append(loc, number(0, 2));
            long absolute = idat ? 0 : mdatStart + mediaOffset;
            if (o.baseOffset) append(loc, number(absolute, o.integerWidth));
            boolean split = o.splitTmap && i == 1;
            append(loc, number(split ? 2 : 1, 2));
            int first = split ? payloads[i].length / 2 : payloads[i].length;
            for (int j = 0; j < (split ? 2 : 1); j++) {
                if (o.extentIndices) append(loc, number(0, 4));
                append(loc, number((o.baseOffset ? 0 : absolute) + (j == 0 ? 0 : first), o.integerWidth));
                append(loc, number(j == 0 ? first : payloads[i].length - first, o.integerWidth));
            }
            if (!idat) mediaOffset += payloads[i].length;
        }
        byte[] iloc = box("iloc", full(o.locationVersion, 0, loc.toByteArray()), o.extended);
        byte[] idat = o.idatTmap ? box("idat", payloads[1], o.extended) : new byte[0];
        return box("meta", full(0, 0, concat(hdlr, pitm, iinf, iloc, iref, iprp, idat)), o.extended);
    }

    private static byte[] full(int version, int flags, byte[] payload) {
        return concat(number(version, 1), number(flags, 3), payload);
    }
    private static byte[] box(String type, byte[] payload, boolean extended) {
        return extended ? concat(number(1, 4), ascii(type), number(payload.length + 16L, 8), payload)
                : concat(number(payload.length + 8L, 4), ascii(type), payload);
    }
    private static byte[] ascii(String text) { return text.getBytes(StandardCharsets.ISO_8859_1); }
    private static byte[] number(long value, int width) {
        byte[] bytes = new byte[width];
        for (int i = width - 1; i >= 0; i--) { bytes[i] = (byte) value; value >>>= 8; }
        return bytes;
    }
    private static void append(ByteArrayOutputStream out, byte[] bytes) { out.write(bytes, 0, bytes.length); }
    private static byte[] concat(byte[]... arrays) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        for (byte[] bytes : arrays) append(out, bytes);
        return out.toByteArray();
    }
    // Test-only search of known synthetic markers, never used by production parser.
    private static int offsetOf(byte[] data, String marker) {
        byte[] needle = ascii(marker);
        outer: for (int i = 0; i <= data.length - needle.length; i++) {
            for (int j = 0; j < needle.length; j++) if (data[i + j] != needle[j]) continue outer;
            return i;
        }
        throw new AssertionError("synthetic marker missing: " + marker);
    }
    private static void put16(byte[] data, int at, long value) { System.arraycopy(number(value, 2), 0, data, at, 2); }
    private static void put32(byte[] data, int at, long value) { System.arraycopy(number(value, 4), 0, data, at, 4); }
    private static void put64(byte[] data, int at, long value) { System.arraycopy(number(value, 8), 0, data, at, 8); }
    private static long get32(byte[] data, int at) {
        long value = 0;
        for (int i = 0; i < 4; i++) value = (value << 8) | (data[at + i] & 255L);
        return value;
    }
    private static void replaceFourcc(byte[] data, int at, String value) { System.arraycopy(ascii(value), 0, data, at, 4); }
}
