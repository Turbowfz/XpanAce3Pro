package com.xpanport.hook;

import java.math.BigDecimal;
import java.math.BigInteger;
import java.math.RoundingMode;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Pure-Java, fail-closed HEIF metadata patch, with no I/O or pixel decoding.
 *
 * The narrow 62-byte, version/minimum/writer 0, single-channel 0x40 tmap
 * layout is supported by C16-associated samples (not verified pristine/C17).
 * Semantics: libavif src/read.c, commit
 * 90d3b6661e1475bb1652cba7d85c24f8c748ab07, lines 2258-2333.
 * Offset 14 is alternate HDR headroom (unsigned); offset 30 is channel gain
 * maximum (SIGNED numerator). They are distinct fields, not byte signatures.
 * Only SDR-base, equal maxima, zero minima/offsets and unit gamma are accepted.
 * Ordered dimg, grid extents, property associations and optional altr establish
 * the base/gain topology. Geometry properties and essential associations are
 * guarded before edits; matching transforms do NOT prove pixel/content alignment.
 * Common codec/colour properties remain opaque. Declared, associated XMP is checked
 * by namespace. Unknown layouts/metadata return the ORIGINAL reference with an unknown ratio.
 * No certification of HEVC decodability, device provenance or arbitrary C17 data
 * is implied. Callers must gate integration to the approved camera/mode.
 */
public final class XdrRatioPatch {
    private static final int MAX_BOXES = 16384;
    private static final int MAX_ITEMS = 4096;
    private static final int MAX_EXTENTS = 16384;
    // Geometry is metadata only; this bound does not certify decoder allocation.
    private static final long MAX_IMAGE_DIMENSION = Integer.MAX_VALUE;

    private XdrRatioPatch() { }

    public enum Status {
        INVALID_INPUT, INVALID_REQUEST, MALFORMED, UNSUPPORTED_CONTAINER,
        NO_TMAP, UNSUPPORTED_TMAP_LAYOUT, CONFLICTING_METADATA, NUMERATOR_OVERFLOW,
        PATCHED, ALREADY_SUFFICIENT
    }

    public static final class Result {
        private final byte[] output;
        private final boolean changed;
        private final double log2Max;
        public final Status status;
        public final String reason;
        public final int itemCount;
        public final int tmapItemCount;
        public final int xmpItemCount;

        private Result(byte[] output, Status status, String reason,
                       int itemCount, int tmapItemCount, int xmpItemCount) {
            this(output, status, reason, itemCount, tmapItemCount, xmpItemCount, false, Double.NaN);
        }

        private Result(byte[] output, Status status, String reason, int itemCount,
                       int tmapItemCount, int xmpItemCount, boolean changed, double log2Max) {
            this.output = output;
            this.status = status;
            this.reason = reason;
            this.itemCount = itemCount;
            this.tmapItemCount = tmapItemCount;
            this.xmpItemCount = xmpItemCount;
            this.changed = changed;
            this.log2Max = log2Max;
        }

        /** Clone only on success; otherwise the original reference (including null). */
        public byte[] bytes() { return output; }
        public boolean changed() { return changed; }
        /** Parsed linear metadata ratio, NOT a pixel-decoder measurement. */
        public double ratioMax() { return Math.pow(2.0, log2Max); }
        /** Parsed tmap gain maximum; NaN on every rejection, never a default. */
        public double log2RatioMax() { return log2Max; }
    }

    /**
     * The only approved request is 8x (log2 = 3). Never lowers an existing maximum.
     * Validated slots and all XMP edits are prepared before cloning; no input I/O
     * or mutation. Do not mutate input concurrently with this synchronous call.
     */
    public static Result raiseRatioMax(byte[] input, double minimumRatio) {
        if (input == null) return result(null, Status.INVALID_INPUT, "null input");
        if (minimumRatio != 8.0) {
            return result(input, Status.INVALID_REQUEST, "only the approved finite 8x request is supported");
        }
        Parser p = new Parser(input);
        try {
            p.parse();
            return p.patch();
        } catch (Rejected e) {
            return new Result(input, e.status, e.getMessage(), p.items.size(),
                    p.count("tmap"), p.xmpCount());
        }
    }

    private static Result result(byte[] data, Status status, String reason) {
        return new Result(data, status, reason, 0, 0, 0);
    }

    private static void layout(boolean condition, String message) throws Rejected {
        if (!condition) throw new Rejected(Status.UNSUPPORTED_TMAP_LAYOUT, message);
    }

    private static void consistent(boolean condition, String message) throws Rejected {
        if (!condition) throw new Rejected(Status.CONFLICTING_METADATA, message);
    }

    private static final class Rejected extends Exception {
        private static final long serialVersionUID = 1L;
        final Status status;
        Rejected(Status status, String message) { super(message); this.status = status; }
    }

    private static void require(boolean condition, String message) throws Rejected {
        if (!condition) throw new Rejected(Status.MALFORMED, message);
    }

    private static void supported(boolean condition, String message) throws Rejected {
        if (!condition) throw new Rejected(Status.UNSUPPORTED_CONTAINER, message);
    }

    private static final class Box {
        final String type;
        final int payload;
        final int end;
        Box(String type, int payload, int end) {
            this.type = type;
            this.payload = payload;
            this.end = end;
        }
    }

    private static final class Association {
        final Box property;
        final boolean essential;
        Association(Box property, boolean essential) {
            this.property = property; this.essential = essential;
        }
    }

    private static final class Item {
        final String type;
        final String contentType;
        final List<Extent> extents = new ArrayList<Extent>();
        final Map<String, Association> properties = new HashMap<String, Association>();
        final List<String> transforms = new ArrayList<String>(); // retain ipma order
        Item(String type, String contentType) { this.type = type; this.contentType = contentType; }
        Box property(String type) {
            Association association = properties.get(type);
            return association == null ? null : association.property;
        }
    }

    private static final class Extent {
        final long start;
        final long end;
        Extent(long start, long end) { this.start = start; this.end = end; }
    }

    private static final class Cursor {
        final byte[] data;
        final int end;
        int pos;
        Cursor(byte[] data, int start, int end) {
            this.data = data; this.pos = start; this.end = end;
        }
        void need(int count) throws Rejected {
            require(count >= 0 && pos >= 0 && pos <= end && count <= end - pos,
                    "truncated field");
        }
        long uint(int width) throws Rejected {
            require(width >= 0 && width <= 8, "invalid integer width");
            need(width);
            supported(width != 8 || (data[pos] & 0x80) == 0, "unsigned 64-bit value exceeds supported range");
            long value = 0;
            for (int i = 0; i < width; i++) value = (value << 8) | (data[pos++] & 255L);
            return value;
        }
        String fourcc() throws Rejected {
            need(4);
            String value = new String(data, pos, 4, StandardCharsets.ISO_8859_1);
            pos += 4;
            return value;
        }
        String terminated() throws Rejected {
            int start = pos;
            while (pos < end && data[pos] != 0) pos++;
            require(pos < end, "unterminated item string");
            supported(pos - start <= 4096, "item string exceeds parser limit");
            String value = new String(data, start, pos - start, StandardCharsets.ISO_8859_1);
            pos++;
            return value;
        }
        int full(int expectedVersion) throws Rejected {
            int version = (int) uint(1);
            int flags = (int) uint(3);
            supported(expectedVersion < 0 || version == expectedVersion, "unsupported full-box version");
            supported(flags == 0, "unsupported full-box flags");
            return version;
        }
        void done() throws Rejected { require(pos == end, "unexpected trailing fields"); }
    }

    private static final class Rational {
        final long n, d;
        Rational(long n, long d) { this.n = n; this.d = d; }
        boolean equal(Rational other) {
            return BigInteger.valueOf(n).multiply(BigInteger.valueOf(other.d))
                    .equals(BigInteger.valueOf(other.n).multiply(BigInteger.valueOf(d)));
        }
        double value() { return (double) n / d; }
    }

    private static boolean transform(String type) {
        return "clap".equals(type) || "irot".equals(type) || "imir".equals(type);
    }

    private static final class CleanAperture {
        final Rational width, height, horizontal, vertical;
        CleanAperture(Rational width, Rational height, Rational horizontal, Rational vertical) {
            this.width = width; this.height = height;
            this.horizontal = horizontal; this.vertical = vertical;
        }

        boolean within(long imageWidth, long imageHeight) {
            return withinAxis(width, horizontal, imageWidth) && withinAxis(height, vertical, imageHeight);
        }

        private static boolean withinAxis(Rational size, Rational offset, long imageSize) {
            // (imageSize - size) / 2 +/- offset >= 0, without rounding or overflow.
            BigInteger sizeD = BigInteger.valueOf(size.d), offsetD = BigInteger.valueOf(offset.d);
            BigInteger needed = BigInteger.valueOf(size.n).multiply(offsetD)
                    .add(BigInteger.valueOf(offset.n).abs().multiply(sizeD).shiftLeft(1));
            return needed.compareTo(BigInteger.valueOf(imageSize).multiply(sizeD).multiply(offsetD)) <= 0;
        }
    }

    private static final class Geometry {
        final long width, height;
        final CleanAperture crop;
        final int rotation, mirror;
        final List<String> order;
        Geometry(long width, long height, CleanAperture crop, int rotation, int mirror, List<String> order) {
            this.width = width; this.height = height; this.crop = crop;
            this.rotation = rotation; this.mirror = mirror; this.order = order;
        }

        boolean aligned(Geometry other) {
            if (!order.equals(other.order) || rotation != other.rotation || mirror != other.mirror) return false;
            if (crop == null || other.crop == null) return crop == other.crop;
            // Gain images may be lower-resolution. Compare only explicitly declared
            // crop fractions/offsets relative to ispe, never infer a frame/content ROI.
            return scaledEqual(crop.width, width, other.crop.width, other.width)
                    && scaledEqual(crop.height, height, other.crop.height, other.height)
                    && scaledEqual(crop.horizontal, width, other.crop.horizontal, other.width)
                    && scaledEqual(crop.vertical, height, other.crop.vertical, other.height);
        }

        private static boolean scaledEqual(Rational left, long leftSize, Rational right, long rightSize) {
            return BigInteger.valueOf(left.n).multiply(BigInteger.valueOf(right.d))
                    .multiply(BigInteger.valueOf(rightSize)).equals(BigInteger.valueOf(right.n)
                    .multiply(BigInteger.valueOf(left.d)).multiply(BigInteger.valueOf(leftSize)));
        }
    }

    private static Rational rational(Cursor c, boolean signed) throws Rejected {
        long n = c.uint(4), d = c.uint(4);
        layout(d != 0, "zero rational denominator");
        return new Rational(signed ? (int) n : n, d);
    }

    private static byte[] uint32(long n) {
        return new byte[]{(byte) (n >>> 24), (byte) (n >>> 16), (byte) (n >>> 8), (byte) n};
    }

    private static final class Payload {
        final byte[] bytes;
        final int[] offsets;
        Payload(byte[] bytes, int[] offsets) { this.bytes = bytes; this.offsets = offsets; }
    }

    private static final class Change {
        final Payload payload;
        final int at;
        final byte[] value;
        Change(Payload payload, int at, byte[] value) {
            this.payload = payload; this.at = at; this.value = value;
        }
        void apply(byte[] output) {
            for (int i = 0; i < value.length; i++) output[payload.offsets[at + i]] = value[i];
        }
    }

    /**
     * Deliberately restricted ASCII XML/RDF attribute profile matching the
     * samples, not a general XML parser. No DTD/entities/CDATA/network access;
     * non-ASCII, scalar elements, text/arrays and unknown gain-map keys reject.
     * Namespace bindings are resolved at each element, not by literal prefix.
     * All offsets refer to original bytes; no XML serialization changes padding.
     */
    private static final class Xmp {
        static final String HDR = "http://ns.adobe.com/hdr-gain-map/1.0/";
        static final String RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#";
        final Payload raw;
        final String text;
        final Rational capacity, gain;
        final boolean raise;
        final List<Change> changes;
        final Set<String> keys = new HashSet<String>();
        final List<Frame> stack = new ArrayList<Frame>();
        int pos, nodes;
        boolean root;

        Xmp(Payload raw, Rational capacity, Rational gain, boolean raise, List<Change> changes) {
            this.raw = raw; this.capacity = capacity; this.gain = gain;
            this.raise = raise; this.changes = changes;
            text = new String(raw.bytes, StandardCharsets.ISO_8859_1);
        }

        static final class Attribute {
            final String name, value;
            final int at;
            Attribute(String name, String value, int at) { this.name = name; this.value = value; this.at = at; }
        }
        static final class Frame {
            final String name;
            final Map<String, String> ns;
            Frame(String name, Map<String, String> ns) { this.name = name; this.ns = ns; }
        }

        void valid(boolean condition, String reason) throws Rejected {
            layout(condition, "unsupported/malformed XMP: " + reason);
        }
        boolean space(char c) { return c == ' ' || c == '\t' || c == '\r' || c == '\n'; }
        boolean white() {
            int start = pos;
            while (pos < text.length() && space(text.charAt(pos))) pos++;
            return start != pos;
        }
        boolean take(String s) {
            if (!text.startsWith(s, pos)) return false;
            pos += s.length();
            return true;
        }
        String name() throws Rejected {
            int at = pos;
            while (pos < text.length()) {
                char c = text.charAt(pos);
                if (!(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9'
                        || c == '_' || c == '-' || c == '.' || c == ':')) break;
                pos++;
            }
            String n = text.substring(at, pos);
            valid(n.matches("[A-Za-z_][A-Za-z0-9_.-]*(?::[A-Za-z_][A-Za-z0-9_.-]*)?"), "invalid qualified name");
            return n;
        }
        String expanded(String name, Map<String, String> ns, boolean attribute) throws Rejected {
            int colon = name.indexOf(':');
            String prefix = colon < 0 ? "" : name.substring(0, colon);
            String local = colon < 0 ? name : name.substring(colon + 1);
            String uri = attribute && colon < 0 ? "" : ns.get(prefix);
            valid(colon < 0 || uri != null && !uri.isEmpty(), "unbound namespace prefix");
            return (uri == null ? "" : uri) + "|" + local;
        }

        void parse() throws Rejected {
            for (int i = 0; i < text.length(); i++) {
                char c = text.charAt(i);
                valid(c >= 32 && c < 127 || space(c), "only decoded ASCII XML is supported");
            }
            while (pos < text.length()) {
                white();
                if (pos == text.length()) break;
                valid(!text.startsWith("<?", pos), "processing instructions/encoding declarations are outside this profile");
                if (take("</")) {
                    String n = name(); white();
                    valid(take(">") && !stack.isEmpty() && stack.get(stack.size() - 1).name.equals(n), "mismatched end tag");
                    stack.remove(stack.size() - 1);
                    continue;
                }
                valid(take("<") && ++nodes <= 4096 && stack.size() < 32, "text/declaration/depth limit");
                String n = name();
                List<Attribute> attributes = new ArrayList<Attribute>();
                Set<String> names = new HashSet<String>();
                boolean empty = false;
                while (true) {
                    boolean separated = white();
                    if (take("/>")) { empty = true; break; }
                    if (take(">")) break;
                    valid(separated && attributes.size() < 128, "attribute separator/count");
                    String a = name();
                    valid(names.add(a), "duplicate attribute");
                    white(); valid(take("="), "attribute equals"); white();
                    valid(pos < text.length() && (text.charAt(pos) == '\'' || text.charAt(pos) == '"'), "attribute quote");
                    char quote = text.charAt(pos++);
                    int at = pos, end = text.indexOf(quote, pos);
                    valid(end >= pos, "unterminated attribute");
                    String value = text.substring(at, end);
                    valid(value.indexOf('&') < 0 && value.indexOf('<') < 0, "entities/invalid attribute");
                    attributes.add(new Attribute(a, value, at));
                    pos = end + 1;
                }
                Map<String, String> ns = stack.isEmpty() ? new HashMap<String, String>()
                        : new HashMap<String, String>(stack.get(stack.size() - 1).ns);
                ns.put("xml", "http://www.w3.org/XML/1998/namespace");
                for (Attribute a : attributes) {
                    if ("xmlns".equals(a.name) || a.name.startsWith("xmlns:")) {
                        String prefix = "xmlns".equals(a.name) ? "" : a.name.substring(6);
                        valid(!"xmlns".equals(prefix) && !"xml".equals(prefix)
                                && !a.value.isEmpty() && !a.value.contains("|"), "reserved/empty namespace");
                        ns.put(prefix, a.value);
                    }
                }
                String element = expanded(n, ns, false);
                if (stack.isEmpty()) {
                    valid(!root && "adobe:ns:meta/|xmpmeta".equals(element), "root element");
                    root = true;
                }
                valid(!element.startsWith(HDR + "|"), "gain-map element/array layout");
                Set<String> expandedNames = new HashSet<String>();
                boolean description = (RDF + "|Description").equals(element);
                for (Attribute a : attributes) {
                    if ("xmlns".equals(a.name) || a.name.startsWith("xmlns:")) continue;
                    String key = expanded(a.name, ns, true);
                    valid(expandedNames.add(key), "duplicate expanded attribute");
                    if ((RDF + "|about").equals(key) && description) valid(a.value.isEmpty(), "nonempty RDF subject");
                    if (key.startsWith(HDR + "|")) {
                        valid(description && stack.size() == 2
                                && (RDF + "|RDF").equals(expanded(stack.get(1).name, stack.get(1).ns, false)),
                                "gain-map attribute outside RDF description");
                        property(key.substring(HDR.length() + 1), a);
                    }
                }
                if (!empty) stack.add(new Frame(n, ns));
            }
            valid(root && stack.isEmpty(), "incomplete XML document");
        }

        BigDecimal decimal(String s) throws Rejected {
            valid(s.length() <= 32 && s.matches("[0-9]+(?:\\.[0-9]+)?"), "non-scalar/non-decimal gain-map value");
            BigDecimal v = new BigDecimal(s);
            valid(v.scale() <= 12, "decimal precision limit");
            return v;
        }

        void property(String key, Attribute a) throws Rejected {
            valid(keys.add(key), "duplicate gain-map property");
            if ("Version".equals(key)) { consistent("1.0".equals(a.value), "XMP gain-map version conflict"); return; }
            if ("BaseRenditionIsHDR".equals(key)) {
                consistent("False".equals(a.value), "XMP base rendition conflicts with SDR tmap"); return;
            }
            if ("GainMapMax".equals(key) || "HDRCapacityMax".equals(key)) {
                Rational r = "GainMapMax".equals(key) ? gain : capacity;
                BigDecimal v = decimal(a.value);
                BigDecimal n = BigDecimal.valueOf(r.n), d = BigDecimal.valueOf(r.d);
                // Exact equality, or documented scalar decimal rounding to >=6
                // places. Low-precision agreement must not hide a conflict.
                consistent(v.multiply(d).compareTo(n) == 0 || v.scale() >= 6
                        && n.divide(d, v.scale(), RoundingMode.HALF_UP).compareTo(v) == 0,
                        "XMP " + key + " conflicts with tmap rational");
                if (raise) {
                    int dot = a.value.indexOf('.');
                    int integers = dot < 0 ? a.value.length() : dot;
                    StringBuilder replacement = new StringBuilder();
                    for (int i = 1; i < integers; i++) replacement.append('0');
                    replacement.append('3');
                    if (dot >= 0) {
                        replacement.append('.');
                        for (int i = dot + 1; i < a.value.length(); i++) replacement.append('0');
                    }
                    changes.add(new Change(raw, a.at, replacement.toString().getBytes(StandardCharsets.ISO_8859_1)));
                }
                return;
            }
            int expected;
            if ("Gamma".equals(key)) expected = 1;
            else if ("GainMapMin".equals(key) || "HDRCapacityMin".equals(key)
                    || "OffsetSDR".equals(key) || "OffsetHDR".equals(key)) expected = 0;
            else { valid(false, "unknown gain-map property"); return; }
            consistent(decimal(a.value).compareTo(BigDecimal.valueOf(expected)) == 0,
                    "XMP " + key + " conflicts with supported tmap profile");
        }
    }

    private static final class Parser {
        final byte[] data;
        final Map<Long, Item> items = new HashMap<Long, Item>();
        final List<Box> media = new ArrayList<Box>();
        final List<Extent> extents = new ArrayList<Extent>();
        final Map<String, List<Long>> references = new HashMap<String, List<Long>>();
        final List<List<Long>> alternatives = new ArrayList<List<Long>>();
        int boxCount;
        Box idat;
        long primary;

        Parser(byte[] data) { this.data = data; }

        Cursor cursor(Box b) { return new Cursor(data, b.payload, b.end); }

        List<Box> boxes(int start, int end, boolean top) throws Rejected {
            Cursor c = new Cursor(data, start, end);
            List<Box> out = new ArrayList<Box>();
            while (c.pos < c.end) {
                require(++boxCount <= MAX_BOXES, "box count exceeds parser limit");
                int at = c.pos;
                long length = c.uint(4);
                String type = c.fourcc();
                if (length == 1) length = c.uint(8);
                if ("uuid".equals(type)) { c.need(16); c.pos += 16; }
                if (length == 0) {
                    supported(top && "mdat".equals(type), "unsupported zero-sized box");
                    length = end - (long) at;
                }
                require(length >= c.pos - (long) at && length <= end - (long) at,
                        "box size escapes parent or is shorter than header");
                int boxEnd = at + (int) length;
                out.add(new Box(type, c.pos, boxEnd));
                c.pos = boxEnd;
            }
            return out;
        }

        Map<String, Box> unique(List<Box> children, String[] allowed) throws Rejected {
            Set<String> names = new HashSet<String>();
            for (String name : allowed) names.add(name);
            Map<String, Box> found = new HashMap<String, Box>();
            for (Box b : children) {
                supported(names.contains(b.type), "unknown container child");
                require(!found.containsKey(b.type), "duplicate singleton box");
                found.put(b.type, b);
            }
            return found;
        }

        Box required(Map<String, Box> map, String type) throws Rejected {
            Box b = map.get(type);
            require(b != null, "missing required " + type + " box");
            return b;
        }

        void parse() throws Rejected {
            List<Box> roots = boxes(0, data.length, true);
            require(!roots.isEmpty() && "ftyp".equals(roots.get(0).type), "missing leading ftyp");
            Box meta = null;
            int ftypCount = 0;
            for (Box b : roots) {
                if ("ftyp".equals(b.type)) { ftypCount++; parseFtyp(b); }
                else if ("meta".equals(b.type)) {
                    require(meta == null, "duplicate meta box"); meta = b;
                } else if ("mdat".equals(b.type)) media.add(b);
                else if ("\u009a\u0099\u0099?".equals(b.type)) {
                    // C16-associated opaque trailer: bounded final box only. Never
                    // item data, never searched for XMP or rational byte patterns.
                    supported(b == roots.get(roots.size() - 1) && !media.isEmpty()
                            && b.end - b.payload > 0 && b.end - b.payload <= 1024 * 1024,
                            "unsupported proprietary trailer placement/size");
                } else supported("free".equals(b.type) || "skip".equals(b.type), "unknown top-level box");
            }
            require(ftypCount == 1 && meta != null, "missing or duplicate HEIF root boxes");
            Cursor c = cursor(meta);
            c.full(0);
            Map<String, Box> children = unique(boxes(c.pos, c.end, false),
                    new String[]{"hdlr", "pitm", "iinf", "iloc", "iref", "iprp", "idat", "grpl"});
            idat = children.get("idat");
            require(!media.isEmpty() || idat != null, "missing item data container");
            parseHandler(required(children, "hdlr"));
            parseInfo(required(children, "iinf"));
            parsePrimary(required(children, "pitm"));
            parseLocations(required(children, "iloc"));
            if (children.containsKey("iref")) parseReferences(children.get("iref"));
            parseProperties(required(children, "iprp"));
            if (children.containsKey("grpl")) parseGroups(children.get("grpl"));
        }

        int count(String type) {
            int count = 0;
            for (Item item : items.values()) if (type.equals(item.type)) count++;
            return count;
        }

        int xmpCount() {
            int count = 0;
            for (Item item : items.values()) if (isXmp(item)) count++;
            return count;
        }

        boolean isXmp(Item item) {
            return "mime".equals(item.type) && "application/rdf+xml".equals(item.contentType);
        }

        List<Long> refs(String type, long id) {
            List<Long> ids = references.get(type + "/" + id);
            return ids == null ? java.util.Collections.<Long>emptyList() : ids;
        }

        void parseGroups(Box b) throws Rejected {
            Set<Long> groupIds = new HashSet<Long>();
            Set<Long> entities = new HashSet<Long>();
            for (Box group : boxes(b.payload, b.end, false)) {
                supported("altr".equals(group.type), "unsupported entity group");
                Cursor c = cursor(group);
                c.full(0);
                require(groupIds.add(c.uint(4)), "duplicate group ID");
                long count = c.uint(4);
                supported(count >= 2 && count <= MAX_ITEMS, "unsupported alternative count");
                List<Long> ids = new ArrayList<Long>();
                for (int i = 0; i < count; i++) {
                    long id = c.uint(4);
                    require(items.containsKey(id) && entities.add(id), "invalid/ambiguous alternative entity");
                    ids.add(id);
                }
                c.done();
                alternatives.add(ids);
            }
        }

        Payload payload(Item item, int limit) throws Rejected {
            long length = 0;
            for (Extent e : item.extents) length += e.end - e.start;
            layout(length > 0 && length <= limit, "unsupported metadata payload length");
            byte[] bytes = new byte[(int) length];
            int[] offsets = new int[(int) length];
            int at = 0;
            for (Extent e : item.extents) {
                for (int p = (int) e.start; p < (int) e.end; p++) {
                    bytes[at] = data[p];
                    offsets[at++] = p;
                }
            }
            return new Payload(bytes, offsets);
        }

        void grid(long id, Set<Long> tiles) throws Rejected {
            Item item = items.get(id);
            layout(item != null && "grid".equals(item.type), "tmap input is not a declared grid");
            Payload raw = payload(item, 12);
            Cursor c = new Cursor(raw.bytes, 0, raw.bytes.length);
            layout(c.uint(1) == 0, "unsupported grid version");
            int flags = (int) c.uint(1);
            layout((flags & ~1) == 0, "unsupported grid flags");
            long rows = c.uint(1) + 1, columns = c.uint(1) + 1;
            long width = c.uint((flags & 1) == 0 ? 2 : 4);
            long height = c.uint((flags & 1) == 0 ? 2 : 4);
            c.done();
            layout(width > 0 && height > 0, "zero grid dimensions");
            Box ispe = item.property("ispe");
            layout(ispe != null, "grid has no associated ispe");
            Cursor size = cursor(ispe);
            size.full(0);
            layout(size.uint(4) == width && size.uint(4) == height, "grid/ispe dimensions disagree");
            size.done();
            List<Long> ids = refs("dimg", id);
            layout(ids.size() == rows * columns, "grid tile count disagrees with dimg");
            for (Long tile : ids) {
                Item image = items.get(tile);
                layout(tiles.add(tile) && "hvc1".equals(image.type)
                        && image.properties.containsKey("hvcC") && image.properties.containsKey("ispe")
                        && refs("dimg", tile).isEmpty(), "ambiguous/unsupported grid tile");
            }
        }

        Result patch() throws Rejected {
            int tmapCount = count("tmap"), xmpCount = xmpCount();
            if (tmapCount == 0) {
                return new Result(data, Status.NO_TMAP, "no declared tmap; no XMP-only edits",
                        items.size(), 0, xmpCount);
            }
            layout(tmapCount == 1, "multiple tmap items are ambiguous");
            long tmapId = 0;
            for (Map.Entry<Long, Item> e : items.entrySet()) {
                if ("tmap".equals(e.getValue().type)) tmapId = e.getKey();
            }
            List<Long> inputs = refs("dimg", tmapId);
            layout(inputs.size() == 2 && inputs.get(0) == primary,
                    "tmap must reference primary base grid then distinct gain grid");
            long gainId = inputs.get(1);
            Set<Long> tiles = new HashSet<Long>();
            grid(primary, tiles);
            grid(gainId, tiles);
            validateRelevantProperties(items.get(tmapId));
            Geometry baseGeometry = geometry(items.get(primary));
            Geometry gainGeometry = geometry(items.get(gainId));
            consistent(baseGeometry.aligned(gainGeometry), "base/gain geometry transforms disagree");
            // Tile-level transforms require a different grid-composition model.
            // Never silently treat transformed tiles as untransformed grid inputs.
            for (Long tile : tiles) {
                Item image = items.get(tile);
                validateRelevantProperties(image);
                supported(image.transforms.isEmpty(), "transformed grid tiles are outside the supported geometry profile");
            }
            supported(items.get(tmapId).transforms.isEmpty(),
                    "tmap-level transforms are outside the supported geometry profile");
            for (List<Long> group : alternatives) {
                if (group.contains(tmapId) || group.contains(primary)) {
                    layout(group.size() == 2 && group.get(0) == tmapId && group.get(1) == primary,
                            "conflicting tmap/base alternative preference");
                }
            }
            // Reject competing references into this tone-mapping topology.
            for (Map.Entry<String, List<Long>> ref : references.entrySet()) {
                String key = ref.getKey();
                if (key.startsWith("dimg/") && !key.equals("dimg/" + tmapId)
                        && !key.equals("dimg/" + primary) && !key.equals("dimg/" + gainId)) {
                    layout(!ref.getValue().contains(primary) && !ref.getValue().contains(gainId)
                            && !ref.getValue().contains(tmapId), "competing derived-image topology");
                }
                if (key.startsWith("auxl/")) {
                    layout(!ref.getValue().contains(primary) && !ref.getValue().contains(gainId),
                            "competing auxiliary topology");
                }
            }
            Payload raw = payload(items.get(tmapId), 62);
            layout(raw.bytes.length == 62, "only the 62-byte single-channel tmap is supported");
            Cursor c = new Cursor(raw.bytes, 0, 62);
            layout(c.uint(1) == 0 && c.uint(2) == 0 && c.uint(2) == 0 && c.uint(1) == 0x40,
                    "unsupported tmap version/channel/colour-space/reserved bits");
            Rational base = rational(c, false), capacity = rational(c, false);
            Rational min = rational(c, true), max = rational(c, true);
            Rational gamma = rational(c, false), epsilonBase = rational(c, true), epsilonAlt = rational(c, true);
            c.done();
            layout(base.n == 0 && min.n == 0 && max.n >= 0 && gamma.n == gamma.d
                    && epsilonBase.n == 0 && epsilonAlt.n == 0 && capacity.equal(max),
                    "unsupported minima/gamma/offsets or unequal capacity/gain maxima");
            double log2 = max.value();
            layout(!Double.isInfinite(Math.pow(2, log2)), "linear metadata ratio overflows");
            boolean raise = max.n < 3L * max.d;
            List<Change> changes = new ArrayList<Change>();
            int totalXmp = 0;
            for (Map.Entry<Long, Item> entry : items.entrySet()) {
                if (!isXmp(entry.getValue())) continue;
                List<Long> targets = refs("cdsc", entry.getKey());
                layout(targets.size() == 1 && (targets.get(0) == primary || targets.get(0) == gainId),
                        "XMP association is missing or ambiguous");
                Payload packet = payload(entry.getValue(), 65536);
                totalXmp += packet.bytes.length;
                layout(totalXmp <= 1024 * 1024, "XMP total exceeds parser limit");
                new Xmp(packet, capacity, max, raise, changes).parse();
            }
            if (!raise) {
                return new Result(data, Status.ALREADY_SUFFICIENT, "validated metadata already >= 8x; no lowering",
                        items.size(), tmapCount, xmpCount, false, log2);
            }
            // Channel gain numerator is int32, unlike unsigned capacity. Preserve
            // each denominator, even if equivalent fractions have different D.
            long capacityN = 3L * capacity.d, gainN = 3L * max.d;
            if (capacityN > 0xffffffffL || gainN > Integer.MAX_VALUE) {
                throw new Rejected(Status.NUMERATOR_OVERFLOW, "3 * encoded denominator overflows numerator; no edits");
            }
            changes.add(new Change(raw, 14, uint32(capacityN)));
            changes.add(new Change(raw, 30, uint32(gainN)));
            byte[] output = data.clone();
            for (Change change : changes) change.apply(output);
            return new Result(output, Status.PATCHED, "validated rational/XMP metadata raised atomically to 8x",
                    items.size(), tmapCount, xmpCount, true, 3.0);
        }

        void parseFtyp(Box b) throws Rejected {
            Cursor c = cursor(b);
            require(c.end - c.pos >= 8 && (c.end - c.pos) % 4 == 0, "invalid ftyp payload");
            boolean heif = heifBrand(c.fourcc());
            c.uint(4); // minor version is opaque
            while (c.pos < c.end) heif |= heifBrand(c.fourcc());
            supported(heif, "no supported still-image HEIF brand");
        }

        boolean heifBrand(String brand) {
            return "mif1".equals(brand) || "heic".equals(brand) || "heix".equals(brand);
        }

        void parseHandler(Box b) throws Rejected {
            Cursor c = cursor(b);
            c.full(0);
            supported(c.uint(4) == 0 && "pict".equals(c.fourcc()), "unsupported metadata handler");
            for (int i = 0; i < 3; i++) supported(c.uint(4) == 0, "nonzero handler reserved field");
            if (c.pos < c.end) c.terminated();
            c.done();
        }

        void parseInfo(Box b) throws Rejected {
            Cursor c = cursor(b);
            int version = c.full(-1);
            supported(version == 0 || version == 1, "unsupported iinf version");
            long count = c.uint(version == 0 ? 2 : 4);
            supported(count > 0 && count <= MAX_ITEMS, "unsupported item count");
            List<Box> entries = boxes(c.pos, c.end, false);
            require(entries.size() == count, "iinf count does not match entries");
            for (Box entry : entries) {
                require("infe".equals(entry.type), "non-infe entry in iinf");
                Cursor e = cursor(entry);
                int v = (int) e.uint(1);
                int flags = (int) e.uint(3);
                // ISO infe flags bit 0 is item_hidden, used by the actual
                // C16-associated gain grid, tiles and XMP. It is not protection.
                supported((flags & ~1) == 0, "unsupported infe flags");
                supported(v == 2 || v == 3, "unsupported infe version");
                long id = e.uint(v == 2 ? 2 : 4);
                require(id != 0 && !items.containsKey(id), "zero or duplicate item ID");
                supported(e.uint(2) == 0, "protected item");
                String type = e.fourcc();
                e.terminated(); // item name is deliberately not reported
                String contentType = null;
                if ("mime".equals(type)) {
                    contentType = e.terminated();
                    if (e.pos < e.end) supported(e.terminated().isEmpty(), "encoded MIME item");
                } else if ("uri ".equals(type)) {
                    e.terminated();
                    supported(false, "URI item payload unsupported");
                }
                e.done();
                items.put(id, new Item(type, contentType));
            }
        }

        void parsePrimary(Box b) throws Rejected {
            Cursor c = cursor(b);
            int v = c.full(-1);
            supported(v == 0 || v == 1, "unsupported pitm version");
            primary = c.uint(v == 0 ? 2 : 4);
            require(items.containsKey(primary), "primary item is undeclared");
            c.done();
        }

        boolean width(int n) { return n == 0 || n == 4 || n == 8; }

        void parseLocations(Box b) throws Rejected {
            Cursor c = cursor(b);
            int v = c.full(-1);
            supported(v >= 0 && v <= 2, "unsupported iloc version");
            int a = (int) c.uint(1);
            int z = (int) c.uint(1);
            int offsetWidth = a >>> 4, lengthWidth = a & 15;
            int baseWidth = z >>> 4, indexWidth = z & 15;
            supported(width(offsetWidth) && width(lengthWidth) && width(baseWidth) && width(indexWidth),
                    "unsupported iloc integer width");
            supported(lengthWidth != 0 && (v != 0 || indexWidth == 0), "implicit extent length or reserved iloc nibble");
            long count = c.uint(v < 2 ? 2 : 4);
            require(count == items.size(), "iloc and iinf item counts disagree");
            Set<Long> seen = new HashSet<Long>();
            for (int i = 0; i < count; i++) {
                long id = c.uint(v < 2 ? 2 : 4);
                require(items.containsKey(id) && seen.add(id), "undeclared or duplicate iloc item");
                int method = v == 0 ? 0 : (int) c.uint(2);
                require((method & 0xfff0) == 0, "nonzero iloc construction reserved bits");
                supported(method == 0 || method == 1, "unsupported iloc construction method");
                supported(c.uint(2) == 0, "external item data reference");
                long base = c.uint(baseWidth);
                int n = (int) c.uint(2);
                supported(n > 0 && n <= MAX_EXTENTS - extents.size(), "unsupported extent count");
                for (int j = 0; j < n; j++) {
                    supported(indexWidth == 0 || c.uint(indexWidth) == 0, "indexed extent unsupported");
                    long offset = c.uint(offsetWidth);
                    long length = c.uint(lengthWidth);
                    require(offset <= Long.MAX_VALUE - base, "extent offset overflow");
                    long start = base + offset;
                    if (method == 1) {
                        require(idat != null, "idat construction without idat");
                        require(start <= Long.MAX_VALUE - idat.payload, "idat offset overflow");
                        start += idat.payload;
                    }
                    supported(length != 0, "zero-length extent unsupported");
                    require(start <= data.length && length <= data.length - start, "extent escapes input");
                    long end = start + length;
                    boolean contained = method == 1 && start >= idat.payload && end <= idat.end;
                    if (method == 0) {
                        for (Box m : media) if (start >= m.payload && end <= m.end) contained = true;
                    }
                    require(contained, "extent escapes its item-data payload");
                    Extent extent = new Extent(start, end);
                    extents.add(extent);
                    items.get(id).extents.add(extent); // retain iloc order, not physical sort order
                }
            }
            c.done();
            java.util.Collections.sort(extents, new java.util.Comparator<Extent>() {
                @Override
                public int compare(Extent left, Extent right) {
                    return Long.compare(left.start, right.start);
                }
            });
            for (int i = 1; i < extents.size(); i++) {
                supported(extents.get(i).start >= extents.get(i - 1).end, "overlapping item extents are ambiguous");
            }
        }

        void parseReferences(Box b) throws Rejected {
            Cursor c = cursor(b);
            int v = c.full(-1);
            supported(v == 0 || v == 1, "unsupported iref version");
            Set<String> seen = new HashSet<String>();
            for (Box ref : boxes(c.pos, c.end, false)) {
                supported("dimg".equals(ref.type) || "thmb".equals(ref.type)
                        || "cdsc".equals(ref.type) || "auxl".equals(ref.type), "unknown item reference type");
                Cursor r = cursor(ref);
                long from = r.uint(v == 0 ? 2 : 4);
                require(items.containsKey(from), "reference source is undeclared");
                supported(seen.add(ref.type + "/" + from), "duplicate reference source/type");
                int count = (int) r.uint(2);
                supported(count > 0 && count <= MAX_ITEMS, "unsupported reference count");
                Set<Long> targets = new HashSet<Long>();
                List<Long> ordered = new ArrayList<Long>();
                for (int i = 0; i < count; i++) {
                    long to = r.uint(v == 0 ? 2 : 4);
                    require(items.containsKey(to) && to != from && targets.add(to), "invalid item reference target");
                    ordered.add(to);
                }
                references.put(ref.type + "/" + from, ordered);
                r.done();
            }
        }

        long[] dimensions(Box property) throws Rejected {
            Cursor c = cursor(property);
            c.full(0);
            long width = c.uint(4), height = c.uint(4);
            require(width > 0 && height > 0, "zero image dimension");
            supported(width <= MAX_IMAGE_DIMENSION && height <= MAX_IMAGE_DIMENSION,
                    "image dimension exceeds supported geometry range");
            c.done();
            return new long[]{width, height};
        }

        CleanAperture cleanAperture(Box property) throws Rejected {
            Cursor c = cursor(property);
            // ISO/IEC 14496-12 12.1.4.1: the stored uint32 fields have signed
            // int32 semantics. Width/height N and every D must be positive;
            // offset N may be zero or negative. This is NOT the tmap rational.
            Rational width = geometryRational(c, true), height = geometryRational(c, true);
            Rational horizontal = geometryRational(c, false), vertical = geometryRational(c, false);
            c.done();
            return new CleanAperture(width, height, horizontal, vertical);
        }

        Rational geometryRational(Cursor c, boolean size) throws Rejected {
            long n = (int) c.uint(4), d = (int) c.uint(4);
            require(d > 0, "clap denominator must be positive signed int32");
            require(!size || n > 0, "clap width/height must be positive signed int32");
            return new Rational(n, d);
        }

        int orientation(Box property, int maximum) throws Rejected {
            Cursor c = cursor(property);
            int value = (int) c.uint(1);
            require(value <= maximum, property.type + " has nonzero reserved bits");
            c.done();
            return value;
        }

        void validateGeometry(Item item) throws Rejected {
            Box size = item.property("ispe"), crop = item.property("clap");
            if (crop == null) return;
            supported(size != null, "associated clap requires ispe to establish its bounds");
            // Only the narrow clap-before-orientation order is understood here.
            // A different sequence must not be validated in the wrong coordinate space.
            supported(!item.transforms.isEmpty() && "clap".equals(item.transforms.get(0)),
                    "clap after orientation is outside the supported geometry profile");
            long[] image = dimensions(size);
            require(cleanAperture(crop).within(image[0], image[1]), "clap escapes associated image bounds");
        }

        void validateRelevantProperties(Item item) throws Rejected {
            for (Association association : item.properties.values()) {
                String type = association.property.type;
                // Recognized descriptive/codec properties remain opaque as before.
                // This guard does not claim hvcC/pixi/colr/auxC payload validity or
                // decodability. Unknown ESSENTIAL properties may affect rendering
                // and cannot be ignored on the base/gain/tmap topology.
                boolean known = "ispe".equals(type) || transform(type) || "hvcC".equals(type)
                        || "pixi".equals(type) || "colr".equals(type) || "auxC".equals(type);
                supported(!association.essential || known, "unknown essential property on relevant image item");
                supported(!transform(type) || association.essential,
                        "associated geometry transform must be essential");
            }
        }

        Geometry geometry(Item item) throws Rejected {
            validateRelevantProperties(item);
            long[] size = dimensions(item.property("ispe")); // grid() established the association
            Box crop = item.property("clap"), rotation = item.property("irot"), mirror = item.property("imir");
            return new Geometry(size[0], size[1], crop == null ? null : cleanAperture(crop),
                    rotation == null ? -1 : orientation(rotation, 3),
                    mirror == null ? -1 : orientation(mirror, 1), item.transforms);
        }

        void parseProperties(Box b) throws Rejected {
            Map<String, Box> children = unique(boxes(b.payload, b.end, false), new String[]{"ipco", "ipma"});
            Box ipco = required(children, "ipco");
            List<Box> properties = boxes(ipco.payload, ipco.end, false);
            supported(!properties.isEmpty() && properties.size() <= MAX_ITEMS, "unsupported property count");
            for (Box property : properties) {
                if ("ispe".equals(property.type)) dimensions(property);
                else if ("clap".equals(property.type)) cleanAperture(property);
                else if ("irot".equals(property.type)) orientation(property, 3);
                else if ("imir".equals(property.type)) orientation(property, 1);
                // All other payloads stay opaque and are never edited.
            }
            Cursor c = cursor(required(children, "ipma"));
            int version = (int) c.uint(1);
            int flags = (int) c.uint(3);
            supported((version == 0 || version == 1) && (flags & ~1) == 0, "unsupported ipma version/flags");
            long count = c.uint(4);
            supported(count > 0 && count <= MAX_ITEMS, "unsupported association entry count");
            Set<Long> seen = new HashSet<Long>();
            for (int i = 0; i < count; i++) {
                long id = c.uint(version == 0 ? 2 : 4);
                require(items.containsKey(id) && seen.add(id), "invalid association item ID");
                Item item = items.get(id);
                int n = (int) c.uint(1);
                Set<Integer> indices = new HashSet<Integer>();
                for (int j = 0; j < n; j++) {
                    int encoded = (int) c.uint((flags & 1) == 0 ? 1 : 2);
                    int essentialMask = (flags & 1) == 0 ? 0x80 : 0x8000;
                    boolean essential = (encoded & essentialMask) != 0;
                    int index = encoded & (essentialMask - 1);
                    supported(index != 0, "null property association unsupported");
                    require(index <= properties.size() && indices.add(index), "invalid property association index");
                    Box property = properties.get(index - 1);
                    supported(!item.properties.containsKey(property.type), "ambiguous repeated property type");
                    item.properties.put(property.type, new Association(property, essential));
                    if (transform(property.type)) {
                        supported(item.properties.containsKey("ispe"),
                                "ispe must be associated before geometry transforms");
                        item.transforms.add(property.type);
                    }
                }
            }
            require(seen.contains(primary), "primary item has no association entry");
            c.done();
            for (Item item : items.values()) validateGeometry(item);
        }
    }
}
