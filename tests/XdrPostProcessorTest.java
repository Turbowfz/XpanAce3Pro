package com.xpanport.hook;

import java.io.File;
import java.io.IOException;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.Arrays;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

/** Deterministic host tests: no phone, Android filesystem calls, sleeps or threads. */
public final class XdrPostProcessorTest {
    private static int checks;
    private static byte[] supported;
    private static byte[] noTmap;
    public static void main(String[] args) throws Exception {
        supported = fixture(false); noTmap = fixture(true);
        check(XdrRatioPatch.raiseRatioMax(supported, 8.0).changed(), "supported synthetic fixture");
        check(XdrRatioPatch.raiseRatioMax(noTmap, 8.0).status == XdrRatioPatch.Status.NO_TMAP, "phase1 fixture");
        defaultNoWrite(); completionAndStabilization(); twoPhase(); failures(); safetyGates();
        listingAndBounds(); exactAbi();
        System.out.println("PASS " + checks + " deterministic XDR runtime checks; no device I/O; "
                + "atomic publication requires independent final-file writer exclusion");
    }
    private static void check(boolean ok, String message) {
        checks++; if (!ok) throw new AssertionError(message);
    }
    /* Reuse only the existing synthetic builder, never open the pinned phone fixtures. */
    private static byte[] fixture(boolean firstPhase) throws Exception {
        Class<?> options = Class.forName("com.xpanport.hook.XdrRatioPatchTest$Options");
        Constructor<?> ctor = options.getDeclaredConstructor(); ctor.setAccessible(true);
        Object o = ctor.newInstance();
        if (firstPhase) { Field topology = options.getDeclaredField("topology"); topology.setAccessible(true); topology.set(o, "notmap"); }
        Method method = Class.forName("com.xpanport.hook.XdrRatioPatchTest").getDeclaredMethod("safeFixture", options);
        method.setAccessible(true); return (byte[]) method.invoke(null, o);
    }
    private static final class Time implements XdrPostProcessor.Clock {
        long value;
        @Override public long now() { return value; }
    }
    private static final class Toggle implements XdrPostProcessor.Gate {
        boolean enabled = true;
        @Override public boolean getAsBoolean() { return enabled; }
    }
    private static final class Node {
        byte[] bytes;
        long inode, mtime;
        int uid = 1000, gid = 1000, mode = 0100644;
        Node(byte[] bytes, long inode) { this.bytes = bytes.clone(); this.inode = inode; mtime = 1; }
        XdrPostProcessor.Meta meta() {
            return new XdrPostProcessor.Meta(1, inode, bytes.length, mtime, 123, uid, gid, mode);
        }
    }
    private static final class FakeFiles implements XdrPostProcessor.Files {
        final Map<String, Node> disk = new LinkedHashMap<String, Node>();
        long inode = 10;
        int reads, stages, renames, discards;
        String attack = "", source;
        int sourceReads;
        Toggle toggle;
        Time time;
        void put(String name, byte[] bytes) { disk.put(name, new Node(bytes, inode++)); }
        void changeSameStamp(String name) { disk.get(name).bytes[disk.get(name).bytes.length - 1] ^= 1; }
        @Override public Set<String> names() throws Exception {
            if (attack.equals("listing")) throw new IOException("listing failed");
            return new HashSet<String>(disk.keySet());
        }
        @Override public File path(String name) { return new File("captured/DCIM/Camera", name); }
        @Override public XdrPostProcessor.Snapshot read(String name) throws Exception {
            reads++;
            if (attack.equals("read")) throw new IOException("short/unstable read");
            Node node = disk.get(name);
            if (node == null) throw new IOException("not found");
            if (name.equals(source) && ++sourceReads == 3 && attack.equals("beforeRename")) changeSameStamp(name);
            return new XdrPostProcessor.Snapshot(node.meta(), node.bytes.clone());
        }
        @Override public XdrPostProcessor.Temp stage(String name, XdrPostProcessor.Snapshot expected, byte[] bytes) throws Exception {
            stages++; source = name; sourceReads = 0;
            if (attack.equals("stage")) throw new IOException("no permission/free space");
            if (!expected.same(read(name))) throw new IOException("source revision changed");
            String tmp = ".xpan-xdr-test.tmp"; put(tmp, bytes);
            Node node = disk.get(tmp);
            node.uid = expected.meta.uid; node.gid = expected.meta.gid; node.mode = expected.meta.mode;
            XdrPostProcessor.Temp temp = new XdrPostProcessor.Temp(tmp, node.meta(), XdrPostProcessor.sha256(bytes));
            if (attack.equals("duringStage")) changeSameStamp(name);
            if (attack.equals("mtime")) disk.get(name).mtime++;
            if (attack.equals("identity")) put(name, disk.get(name).bytes);
            if (attack.equals("tempCorrupt")) changeSameStamp(tmp);
            if (attack.equals("owner")) node.uid++;
            if (attack.equals("gate")) toggle.enabled = false;
            if (attack.equals("expiry")) time.value = XdrPostProcessor.Engine.LIFETIME_MS;
            return temp;
        }
        @Override public XdrPostProcessor.Snapshot readTemp(XdrPostProcessor.Temp temp) throws Exception {
            XdrPostProcessor.Snapshot snapshot = read(temp.name);
            if (attack.equals("tempRead")) throw new IOException("temp unavailable");
            if (!temp.meta.same(snapshot.meta) || !Arrays.equals(temp.digest, snapshot.digest)) throw new IOException("temp changed");
            return snapshot;
        }
        @Override public void rename(XdrPostProcessor.Temp temp, String name, XdrPostProcessor.Snapshot expected,
                XdrPostProcessor.PublishGuard guard) throws Exception {
            if (attack.equals("finalSource")) changeSameStamp(name);
            if (attack.equals("finalTemp")) changeSameStamp(temp.name);
            if (!expected.same(read(name))) throw new IOException("final source changed");
            readTemp(temp);
            if (attack.equals("expiryFinal")) time.value = XdrPostProcessor.Engine.LIFETIME_MS;
            if (attack.equals("gateFinal")) toggle.enabled = false;
            if (!guard.allow()) throw new IOException("final guard failed");
            if (attack.equals("rename")) throw new IOException("rename denied");
            renames++; disk.put(name, disk.remove(temp.name));
        }
        @Override public void discard(XdrPostProcessor.Temp temp) { discards++; disk.remove(temp.name); }
        byte[] bytes(String name) { return disk.get(name).bytes; }
    }
    private static final class Proof implements XdrPostProcessor.CompletionSource {
        boolean ready = true, exclusive = true;
        int acquired, released;
        String attack = "";
        FakeFiles fs;
        @Override public XdrPostProcessor.CompletionLease acquire(File file, byte[] digest) throws Exception {
            acquired++;
            if (!ready) return null;
            if (attack.equals("acquire")) fs.changeSameStamp(file.getName());
            return new XdrPostProcessor.CompletionLease() {
                int validated;
                @Override public boolean isFinalAndExclusive() {
                    validated++; return exclusive && !(attack.equals("leaseLost") && validated > 1);
                }
                @Override public void close() { released++; }
            };
        }
    }
    private static final class Run {
        final FakeFiles fs = new FakeFiles();
        final Time time = new Time();
        final Toggle toggle = new Toggle();
        final XdrPostProcessor.Engine engine = new XdrPostProcessor.Engine(fs, toggle, time);
        final Proof proof = new Proof();
        Run() { fs.toggle = toggle; fs.time = time; proof.fs = fs; }
        void begin() throws Exception { engine.capture(fs.names(), time.value); }
        void tick(long now) throws Exception { time.value = now; engine.tick(now); }
        void mature() throws Exception { tick(0); tick(500); tick(1000); }
        void authorize() { engine.completion = proof; }
    }
    private static void defaultNoWrite() throws Exception {
        Run r = new Run(); r.fs.put("old.heic", supported); r.begin(); r.fs.put("new.HEIC", supported); r.mature();
        check(r.fs.stages == 0 && r.fs.renames == 0, "default cannot stage/write original");
        check(r.engine.last.status == XdrPostProcessor.Status.FINAL_COMPLETION_UNVERIFIED, "explicit completion caveat");
        check(Arrays.equals(supported, r.fs.bytes("new.HEIC")), "default original bytes intact");
        check(Arrays.equals(supported, r.fs.bytes("old.heic")), "old image never modified");
        r.authorize(); r.tick(1500);
        check(r.fs.renames == 1, "validated enhancement retained and usable after explicit proof integration");
        check(XdrRatioPatch.raiseRatioMax(r.fs.bytes("new.HEIC"), 8).status == XdrRatioPatch.Status.ALREADY_SUFFICIENT, "output >=8 reparse");
    }
    private static void completionAndStabilization() throws Exception {
        Run r = new Run(); r.authorize(); r.proof.ready = false; r.begin(); r.fs.put("new.heic", supported);
        r.tick(0); r.tick(500);
        check(r.proof.acquired == 0 && r.fs.stages == 0, "quiet threshold before completion query");
        r.tick(1000); r.tick(1500);
        check(r.fs.stages == 0 && r.engine.last.status == XdrPostProcessor.Status.FINAL_COMPLETION_UNVERIFIED, "wait for independent proof");
        r.proof.ready = true; r.tick(2000);
        check(r.fs.renames == 1 && r.proof.released == 1, "single checked rename/lease release");
        check(r.engine.last.status == XdrPostProcessor.Status.PUBLISHED, "published report");
        byte[] output = r.fs.bytes("new.heic");
        check(output.length == supported.length, "same length metadata-only");
        int ispe = index(supported, new byte[]{'i','s','p','e'});
        check(Arrays.equals(Arrays.copyOfRange(output, ispe, ispe + 16), Arrays.copyOfRange(supported, ispe, ispe + 16)), "ispe unchanged");
        r.tick(2500); r.tick(3500); r.tick(4500);
        check(r.fs.renames == 1, "already sufficient never republished");
        Run changed = new Run(); changed.authorize(); changed.begin(); changed.fs.put("new.heic", supported);
        changed.tick(0); changed.fs.disk.get("new.heic").mtime++; changed.tick(1000);
        check(changed.fs.stages == 0, "mtime resets stabilization");
        changed.tick(2000); check(changed.fs.renames == 1, "restabilized revision succeeds");
    }
    private static void twoPhase() throws Exception {
        Run r = new Run(); r.authorize(); r.begin(); r.fs.put("phase.heic", noTmap); r.mature();
        check(r.engine.last.patchStatus == XdrRatioPatch.Status.NO_TMAP && r.fs.stages == 0, "no-tmap phase1 retry/no fallback");
        r.fs.put("phase.heic", supported); r.tick(1500); r.tick(2000);
        check(r.fs.renames == 0, "phase2 needs its own stabilization");
        r.tick(2500); check(r.fs.renames == 1, "phase2 new inode retried and patched");
        Run unknown = new Run(); unknown.authorize(); unknown.begin(); unknown.fs.put("unknown.heic", new byte[]{1, 2, 3}); unknown.mature();
        check(unknown.fs.stages == 0 && unknown.fs.renames == 0, "unknown ratio fail-closed");
    }
    private static void failures() throws Exception {
        String[] attacks = {"acquire", "duringStage", "mtime", "identity", "tempCorrupt", "owner", "tempRead",
                "finalSource", "finalTemp", "stage", "rename", "read", "gate", "expiry", "leaseLost", "beforeRename",
                "expiryFinal", "gateFinal"};
        for (String attack : attacks) {
            Run r = new Run(); r.authorize(); r.begin(); r.fs.put("new.heic", supported);
            if (attack.equals("acquire") || attack.equals("leaseLost")) r.proof.attack = attack;
            else r.fs.attack = attack;
            r.mature();
            check(r.fs.renames == 0, "no unsafe publish: " + attack);
            check(!r.fs.disk.containsKey(".xpan-xdr-test.tmp"), "temp cleaned: " + attack);
            if (!attack.equals("acquire") && !attack.equals("duringStage") && !attack.equals("finalSource")
                    && !attack.equals("beforeRename")) {
                check(Arrays.equals(supported, r.fs.bytes("new.heic")), "original never truncated/edited: " + attack);
            }
            check(r.proof.released == (r.proof.acquired == 0 ? 0 : 1), "lease release on failure: " + attack);
        }
        Run sameStamp = new Run(); sameStamp.authorize(); sameStamp.begin(); sameStamp.fs.put("new.heic", supported);
        sameStamp.tick(0); sameStamp.fs.changeSameStamp("new.heic"); sameStamp.tick(1000);
        check(sameStamp.fs.stages == 0 && sameStamp.engine.last.reason.contains("stabilization"), "same size+mtime content change resets stability");
        Run notExclusive = new Run(); notExclusive.authorize(); notExclusive.proof.exclusive = false;
        notExclusive.begin(); notExclusive.fs.put("new.heic", supported); notExclusive.mature();
        check(notExclusive.fs.stages == 0 && notExclusive.proof.released == 1, "unverified lease rejected before temp");
    }
    private static void safetyGates() throws Exception {
        Run r = new Run(); r.authorize(); r.toggle.enabled = false; r.begin();
        check(r.engine.captureCount() == 0, "caller global false prevents capture");
        Run cancelled = new Run(); cancelled.authorize();
        XdrPostProcessor.Capture c = cancelled.engine.capture(cancelled.fs.names(), 0);
        cancelled.fs.put("new.heic", supported); cancelled.engine.cancel(c); cancelled.mature();
        check(cancelled.fs.reads == 0 && cancelled.engine.captureCount() == 0, "failed dispatch snapshot cancelled");
        Run expired = new Run(); expired.authorize(); expired.begin(); expired.fs.put("new.heic", supported);
        expired.tick(XdrPostProcessor.Engine.LIFETIME_MS);
        check(expired.fs.reads == 0 && !expired.engine.hasCaptures(), "finite lifetime ends without old-image sweep");
        check(!XdrPostProcessor.imageName("../new.heic") && !XdrPostProcessor.imageName("sub/new.heic")
                && !XdrPostProcessor.imageName("C:\\new.heic") && !XdrPostProcessor.imageName("foo.heif")
                && XdrPostProcessor.imageName("new.HEIC"), "exact captured safe HEIC names only");
    }
    private static void listingAndBounds() throws Exception {
        Run r = new Run(); r.authorize(); r.fs.put("old.heic", supported);
        Set<String> captured = r.fs.names(); r.engine.capture(captured, 0);
        captured.clear(); // Snapshot defensively copied, not live list.
        r.fs.put("new.jpg", supported); r.fs.put("new.heif", supported); r.mature();
        check(r.fs.reads == 0, "old image/JPEG/HEIF not selected");
        Run bounds = new Run();
        for (int n = 0; n < 100; n++) bounds.engine.capture(new HashSet<String>(), 0);
        check(bounds.engine.captureCount() == XdrPostProcessor.Engine.MAX_CAPTURES, "bounded in-flight captures");
        for (int n = 0; n < 100; n++) bounds.fs.put("new" + n + ".heic", supported);
        bounds.tick(0);
        check(bounds.engine.fileCount() <= XdrPostProcessor.Engine.MAX_FILES, "bounded candidate/file lock state");
        check(bounds.fs.reads == XdrPostProcessor.Engine.FILES_PER_TICK, "bounded worker read budget");
        bounds.tick(XdrPostProcessor.Engine.LIFETIME_MS);
        check(bounds.engine.captureCount() == 0 && bounds.engine.fileCount() == 0, "all state expires");
        XdrPostProcessor.Wakeups wakes = new XdrPostProcessor.Wakeups();
        int admitted = 0;
        for (int n = 0; n < 10000; n++) if (wakes.reserve()) admitted++;
        check(admitted == 1, "10k click/pulse reservations coalesce into ONE worker task");
        wakes.finished(); check(wakes.reserve() && !wakes.reserve(), "one successor task after completion");
        Set<String> oversized = new HashSet<String>();
        for (int n = 0; n <= XdrPostProcessor.Engine.MAX_NAMES; n++) oversized.add("old" + n);
        check(new Run().engine.capture(oversized, 0) == null, "oversized snapshots fail closed");
        Run failedListing = new Run(); failedListing.begin(); failedListing.fs.attack = "listing";
        boolean rejected = false;
        try { failedListing.tick(0); } catch (IOException expected) { rejected = true; }
        check(rejected && failedListing.fs.reads == 0, "failed listing never becomes empty snapshot");
    }
    static final class Tag { }
    static final class Parameter { }
    static final class Handler { }
    static final class Good {
        void takePicture(Tag t, Parameter p, Handler h, boolean a, boolean b) { }
        void takePicture(Object t, Object p, Object h, boolean a, boolean b) { }
    }
    static final class WrongReturn { int takePicture(Tag t, Parameter p, Handler h, boolean a, boolean b) { return 0; } }
    static final class WrongTypes { void takePicture(Tag t, Object p, Handler h, boolean a, boolean b) { } }
    static final class WrongStatic { static void takePicture(Tag t, Parameter p, Handler h, boolean a, boolean b) { } }
    private static void exactAbi() throws Exception {
        Method m = XdrPostProcessor.exactMethod(Good.class, Tag.class, Parameter.class, Handler.class);
        check(m.getParameterTypes()[1] == Parameter.class, "select exact descriptor not ambiguous arity");
        for (Class<?> type : new Class<?>[]{WrongReturn.class, WrongTypes.class, WrongStatic.class}) {
            boolean rejected = false;
            try { XdrPostProcessor.exactMethod(type, Tag.class, Parameter.class, Handler.class); }
            catch (NoSuchMethodException expected) { rejected = true; }
            check(rejected, "ABI rejected " + type.getSimpleName());
        }
    }
    private static int index(byte[] bytes, byte[] needle) {
        for (int n = 0; n <= bytes.length - needle.length; n++) {
            boolean same = true;
            for (int j = 0; j < needle.length; j++) same &= bytes[n + j] == needle[j];
            if (same) return n;
        }
        throw new AssertionError("fixture missing marker");
    }
}
