package com.xpanport.hook;

import android.content.Context;
import android.os.Environment;
import android.os.Handler;
import android.os.SystemClock;
import android.os.storage.StorageManager;
import android.os.storage.StorageVolume;
import android.system.Os;
import android.system.OsConstants;
import android.system.StructStat;

import java.io.File;
import java.io.FileDescriptor;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ScheduledThreadPoolExecutor;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.TimeUnit;

import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;

/**
 * Global NEW-capture HEIC metadata adjustment; unrelated to XPAN size/mode hooks.
 * No bitmap decoding, ispe edits, root paths, property/file toggles or old-image scan.
 *
 * IMPORTANT: takePicture, CLOSE_WRITE and quiet size/mtime/digest samples are NOT
 * final-file completion. An open camera FD can write the old inode after rename;
 * a later save can replace our result. Default is bounded read/parse validation
 * ONLY. Install a CompletionSource only after independently validating the late
 * final-file callback AND excluding all writers/path replacement through release.
 * This source's absence is explicit, not a guessed ratio or removed enhancement.
 * Static ABI validation does not validate C17/HAL support or HEVC decodability.
 */
public final class XdrPostProcessor {
    /** Java 8 source, ordinary anonymous classes; no lambda/metafactory dependency. */
    public interface Gate { boolean getAsBoolean(); }

    /**
     * Nonblocking, independently verified final-save proof for this exact revision.
     * acquire must return null until ALL writers (including open FDs) are finished.
     * A non-null lease must exclude later file writes/replacement AND directory
     * mutation until close, including the rename. Quiet filesystem samples alone
     * cannot implement this contract. Do not retain or mutate the digest argument.
     */
    public interface CompletionSource {
        CompletionLease acquire(File file, byte[] sha256) throws Exception;
    }
    public interface CompletionLease {
        boolean isFinalAndExclusive();
        void close();
    }

    public enum Status {
        IDLE, CAPTURED, DISABLED, CAPACITY, RETRY, REJECTED, ALREADY_SUFFICIENT,
        FINAL_COMPLETION_UNVERIFIED, PUBLISHED, STOPPED
    }
    public static final class Report {
        public final Status status;
        public final String reason;
        public final XdrRatioPatch.Status patchStatus;
        Report(Status status, String reason, XdrRatioPatch.Status patchStatus) {
            this.status = status; this.reason = reason; this.patchStatus = patchStatus;
        }
    }

    private static final String SDK = "com.oplus.ocs.camera.";
    private static final Gate GLOBAL_INTENT = new Gate() {
        @Override public boolean getAsBoolean() { return true; }
    };
    private static XdrPostProcessor installed; // One runtime/worker for the process, not per capture/loader.
    interface Clock { long now(); }
    private static final Clock ANDROID_CLOCK = new Clock() {
        @Override public long now() { return SystemClock.elapsedRealtime(); }
    };
    private final Engine engine;
    private final ScheduledThreadPoolExecutor worker;
    private XC_MethodHook.Unhook hook;
    private final Wakeups wakeups = new Wakeups();
    private boolean stopped;
    private final Runnable pulse = new Runnable() {
        @Override public void run() {
            try { engine.tick(SystemClock.elapsedRealtime()); }
            catch (Exception e) { engine.report(Status.REJECTED, "worker: " + e, null); }
            finally {
                synchronized (XdrPostProcessor.this) {
                    wakeups.finished();
                    if (!stopped && engine.hasCaptures()) schedule();
                }
            }
        }
    };

    /** Context must belong to the actual camera process/current Android user. */
    public static synchronized XdrPostProcessor install(ClassLoader loader,
            Context context, Gate gate) throws Exception {
        if (loader == null || context == null) throw new IllegalArgumentException("loader/context required");
        if (installed != null) throw new IllegalStateException("XDR already installed in this process");
        Method method = exactTakePicture(loader); // Validate BEFORE registering anything.
        XdrPostProcessor post = new XdrPostProcessor(new AndroidFiles(cameraDirectory(context)),
                gate == null ? GLOBAL_INTENT : gate);
        try {
            post.hook = XposedBridge.hookMethod(method, post.captureHook());
            if (post.hook == null) throw new IllegalStateException("hookMethod returned no handle");
            installed = post;
            XposedBridge.log("Xpan XDR: exact takePicture registered; global validation only; "
                    + "FINAL_COMPLETION_UNVERIFIED (late final-file callback pending)");
            return post;
        } catch (Exception e) {
            post.worker.shutdownNow();
            throw e;
        } catch (Error e) {
            post.worker.shutdownNow();
            throw e;
        }
    }

    /** Explicit integration point; setting a provider is NOT device validation. */
    public void setCompletionSource(CompletionSource source) { engine.completion = source; }
    public Report lastReport() { return engine.last; }

    /** Call on process/module teardown; no file changes or old-state cleanup. */
    public void shutdown() {
        synchronized (this) { stopped = true; worker.shutdownNow(); }
        engine.stop();
        if (hook != null) hook.unhook();
        synchronized (XdrPostProcessor.class) {
            if (installed == this) installed = null;
        }
    }

    private XdrPostProcessor(Files files, Gate gate) {
        engine = new Engine(files, gate, ANDROID_CLOCK);
        worker = new ScheduledThreadPoolExecutor(1, new ThreadFactory() {
            @Override public Thread newThread(Runnable r) {
                Thread t = new Thread(r, "xpan-xdr-bounded"); t.setDaemon(true); return t;
            }
        });
        worker.setRemoveOnCancelPolicy(true);
        worker.setExecuteExistingDelayedTasksAfterShutdownPolicy(false);
        worker.setKeepAliveTime(5, TimeUnit.SECONDS);
        worker.allowCoreThreadTimeOut(true);
    }

    private XC_MethodHook captureHook() {
        return new XC_MethodHook() {
            @Override protected void beforeHookedMethod(MethodHookParam param) {
                if (!engine.enabled() || !engine.hasRoom()) return;
                try {
                    // Snapshot BEFORE dispatch, never at worker start or install time.
                    Set<String> before = engine.files.names();
                    Capture c = engine.capture(before, SystemClock.elapsedRealtime());
                    param.setObjectExtra("xpan.xdr.capture", c);
                    if (c != null) synchronized (XdrPostProcessor.this) { schedule(); }
                } catch (Exception e) { engine.report(Status.REJECTED, "capture snapshot: " + e, null); }
            }
            @Override protected void afterHookedMethod(MethodHookParam param) {
                if (param.hasThrowable()) engine.cancel((Capture) param.getObjectExtra("xpan.xdr.capture"));
                // A successful void return is NOT final file completion.
            }
        };
    }

    private void schedule() {
        if (stopped || !wakeups.reserve()) return;
        worker.schedule(pulse, Engine.TICK_MS, TimeUnit.MILLISECONDS);
    }

    static Method exactTakePicture(ClassLoader loader) throws Exception {
        Class<?> owner = Class.forName(SDK + "producer.device.Camera2Impl", false, loader);
        Class<?> tag = Class.forName(SDK + "common.util.CameraRequestTag", false, loader);
        Class<?> parameter = Class.forName(SDK + "metadata.parameter.Parameter", false, loader);
        return exactMethod(owner, tag, parameter, Handler.class);
    }
    static Method exactMethod(Class<?> owner, Class<?> tag, Class<?> parameter,
            Class<?> handler) throws Exception {
        Method m = owner.getDeclaredMethod("takePicture", tag, parameter, handler,
                boolean.class, boolean.class);
        int modifiers = m.getModifiers();
        if (m.getReturnType() != void.class || Modifier.isStatic(modifiers)
                || Modifier.isAbstract(modifiers) || m.isSynthetic() || m.isBridge()) {
            throw new NoSuchMethodException("unexpected takePicture ABI: " + m);
        }
        m.setAccessible(true);
        return m;
    }

    private static File cameraDirectory(Context context) throws Exception {
        StorageManager manager = (StorageManager) context.getSystemService(Context.STORAGE_SERVICE);
        if (manager == null) throw new IOException("no Context StorageManager");
        StorageVolume primary = null;
        for (StorageVolume v : manager.getStorageVolumes()) {
            if (v.isPrimary()) {
                if (primary != null) throw new IOException("ambiguous primary volume");
                primary = v;
            }
        }
        if (primary == null || !Environment.MEDIA_MOUNTED.equals(primary.getState())
                || primary.getDirectory() == null) throw new IOException("primary storage unavailable");
        File root = primary.getDirectory().getCanonicalFile();
        File dcim = new File(root, Environment.DIRECTORY_DCIM);
        File camera = new File(dcim, "Camera");
        if (!dcim.getAbsoluteFile().equals(dcim.getCanonicalFile())
                || !camera.getAbsoluteFile().equals(camera.getCanonicalFile())
                || !dcim.getCanonicalFile().getParentFile().equals(root)
                || !camera.getCanonicalFile().getParentFile().equals(dcim.getCanonicalFile())) {
            throw new IOException("redirected DCIM/Camera");
        }
        return camera.getCanonicalFile(); // Exactly this directory; no fallbacks/recursive events.
    }

    /* Pure deterministic engine and I/O boundary; tests supply a fake FS/clock. */
    static final class Wakeups {
        private boolean reserved;
        synchronized boolean reserve() {
            if (reserved) return false;
            reserved = true; return true;
        }
        synchronized void finished() { reserved = false; }
    }
    interface PublishGuard { boolean allow(); }
    interface Files {
        Set<String> names() throws Exception;
        File path(String name);
        Snapshot read(String name) throws Exception;
        Temp stage(String source, Snapshot expected, byte[] bytes) throws Exception;
        Snapshot readTemp(Temp temp) throws Exception;
        void rename(Temp temp, String source, Snapshot expected, PublishGuard guard) throws Exception;
        void discard(Temp temp);
    }
    static final class Meta {
        final long dev, inode, size, seconds, nanos;
        final int uid, gid, mode;
        Meta(long dev, long inode, long size, long seconds, long nanos, int uid, int gid, int mode) {
            this.dev = dev; this.inode = inode; this.size = size; this.seconds = seconds;
            this.nanos = nanos; this.uid = uid; this.gid = gid; this.mode = mode;
        }
        boolean same(Meta b) {
            return b != null && dev == b.dev && inode == b.inode && size == b.size
                    && seconds == b.seconds && nanos == b.nanos && uid == b.uid
                    && gid == b.gid && mode == b.mode;
        }
        boolean sameIdentity(Meta b) { return b != null && dev == b.dev && inode == b.inode; }
        boolean samePermissions(Meta b) { return b != null && uid == b.uid && gid == b.gid && mode == b.mode; }
    }
    static final class Snapshot {
        final Meta meta;
        final byte[] bytes, digest;
        Snapshot(Meta meta, byte[] bytes) throws Exception {
            this.meta = meta; this.bytes = bytes; this.digest = sha256(bytes);
            if (meta.size != bytes.length) throw new IOException("size/content mismatch");
        }
        boolean same(Snapshot b) { return b != null && meta.same(b.meta) && Arrays.equals(digest, b.digest); }
    }
    static final class Temp {
        final String name;
        final Meta meta;
        final byte[] digest;
        Temp(String name, Meta meta, byte[] digest) { this.name = name; this.meta = meta; this.digest = digest; }
    }
    static final class Capture {
        final Set<String> before;
        final long deadline;
        volatile boolean cancelled;
        Capture(Set<String> before, long now) {
            this.before = new HashSet<String>(before); deadline = now + Engine.LIFETIME_MS;
        }
    }
    static final class Candidate {
        final Capture capture;
        Meta previous;
        byte[] digest, attempted;
        long stableSince;
        CompletionSource attemptedSource;
        Candidate(Capture capture) { this.capture = capture; }
    }
    static byte[] sha256(byte[] bytes) throws Exception {
        return MessageDigest.getInstance("SHA-256").digest(bytes);
    }
    static boolean imageName(String name) {
        return safeName(name) && name.toLowerCase(java.util.Locale.ROOT).endsWith(".heic");
    }
    static boolean safeName(String name) {
        return name != null && name.length() > 0 && !name.equals(".") && !name.equals("..")
                && name.indexOf('/') < 0 && name.indexOf('\\') < 0 && name.indexOf('\0') < 0;
    }

    static final class Engine {
        static final int MAX_NAMES = 8192, MAX_CAPTURES = 8, MAX_FILES = 32;
        static final int MAX_BYTES = 48 * 1024 * 1024, FILES_PER_TICK = 2;
        static final long TICK_MS = 500, QUIET_MS = 1000, LIFETIME_MS = 15000;
        final Files files;
        private final Gate gate;
        private final Clock clock;
        private final List<Capture> captures = new ArrayList<Capture>();
        // Only worker accesses candidates; capture admission never waits for file I/O.
        private final Map<String, Candidate> candidates = new LinkedHashMap<String, Candidate>();
        private final Set<String> claimed = new HashSet<String>();
        private int cursor;
        private volatile boolean stopped;
        volatile CompletionSource completion;
        volatile Report last = new Report(Status.IDLE, "late final-file callback pending; writes disabled", null);
        Engine(Files files, Gate gate, Clock clock) { this.files = files; this.gate = gate; this.clock = clock; }
        void report(Status status, String reason, XdrRatioPatch.Status patch) { last = new Report(status, reason, patch); }
        boolean enabled() {
            try {
                if (!stopped && gate.getAsBoolean()) return true;
            } catch (RuntimeException e) { report(Status.DISABLED, "global gate threw: " + e, null); return false; }
            report(Status.DISABLED, "global intent disabled/stopped", null); return false;
        }
        synchronized boolean hasRoom() {
            if (captures.size() < MAX_CAPTURES && !stopped) return true;
            report(Status.CAPACITY, "capture cap reached", null); return false;
        }
        synchronized Capture capture(Set<String> before, long now) {
            if (before == null || before.size() > MAX_NAMES) {
                report(Status.REJECTED, "missing/oversized directory snapshot", null); return null;
            }
            if (!hasRoom() || !enabled()) return null;
            Capture c = new Capture(before, now); captures.add(c);
            report(Status.CAPTURED, "new capture snapshot; no completion proof yet", null); return c;
        }
        void cancel(Capture capture) { if (capture != null) capture.cancelled = true; }
        synchronized boolean hasCaptures() { return !captures.isEmpty(); }
        synchronized void stop() { stopped = true; captures.clear(); report(Status.STOPPED, "stopped", null); }
        synchronized int captureCount() { return captures.size(); }
        int fileCount() { return candidates.size(); }

        void tick(long now) throws Exception {
            List<Capture> active;
            synchronized (this) {
                for (int i = captures.size() - 1; i >= 0; i--) {
                    Capture c = captures.get(i);
                    if (c.cancelled || now >= c.deadline) captures.remove(i);
                }
                active = new ArrayList<Capture>(captures);
            }
            java.util.Iterator<Candidate> it = candidates.values().iterator();
            while (it.hasNext()) {
                Candidate c = it.next();
                if (!active.contains(c.capture)) it.remove();
            }
            if (active.isEmpty()) { claimed.clear(); cursor = 0; return; }
            if (!enabled()) return;
            Set<String> names = files.names();
            if (names == null || names.size() > MAX_NAMES) {
                report(Status.REJECTED, "unsafe/oversized directory listing", null); return;
            }
            for (String name : names) {
                if (!imageName(name) || claimed.contains(name)) continue;
                for (Capture c : active) {
                    if (c.before.contains(name)) continue;
                    if (claimed.size() >= MAX_FILES) {
                        report(Status.CAPACITY, "new-file/claim cap reached", null); break;
                    }
                    claimed.add(name); candidates.put(name, new Candidate(c)); break;
                }
            }
            List<String> keys = new ArrayList<String>(candidates.keySet());
            for (int n = 0; n < Math.min(FILES_PER_TICK, keys.size()); n++) {
                String name = keys.get(cursor++ % keys.size());
                if (cursor > MAX_FILES * 2) cursor = 0;
                try { process(name, candidates.get(name), now); }
                catch (Exception e) { report(Status.RETRY, name + ": " + e, null); }
            }
        }

        private void process(String name, final Candidate c, long now) throws Exception {
            if (c.capture.cancelled || now >= c.capture.deadline || !enabled()) return;
            Snapshot input = files.read(name);
            if (input.bytes.length == 0 || input.bytes.length > MAX_BYTES) {
                report(Status.REJECTED, "empty/oversized HEIC", null); return;
            }
            if (c.previous == null || !c.previous.same(input.meta) || !Arrays.equals(c.digest, input.digest)) {
                c.previous = input.meta; c.digest = input.digest; c.stableSince = now;
                c.attempted = null;
                report(Status.RETRY, "size/mtime/identity/content stabilization", null); return;
            }
            if (now - c.stableSince < QUIET_MS) return;
            final CompletionSource source = completion;
            if (Arrays.equals(c.attempted, input.digest) && c.attemptedSource == source) return;
            XdrRatioPatch.Result result = XdrRatioPatch.raiseRatioMax(input.bytes, 8.0);
            if (!result.changed()) {
                c.attempted = input.digest; c.attemptedSource = source;
                Status status = result.status == XdrRatioPatch.Status.ALREADY_SUFFICIENT ? Status.ALREADY_SUFFICIENT
                        : result.status == XdrRatioPatch.Status.NO_TMAP || result.status == XdrRatioPatch.Status.MALFORMED
                        ? Status.RETRY : Status.REJECTED;
                report(status, result.reason, result.status); // Unknown ratio never falls back.
                return; // A changed revision will retry, including phase-1 NO_TMAP.
            }
            if (source == null) {
                c.attempted = input.digest; c.attemptedSource = null;
                report(Status.FINAL_COMPLETION_UNVERIFIED,
                        "patch validated, NOT written: independently verified late final-file callback missing", result.status);
                return;
            }
            final CompletionLease lease = source.acquire(files.path(name), input.digest.clone());
            if (lease == null) {
                report(Status.FINAL_COMPLETION_UNVERIFIED, "late final-file completion/exclusion not ready", result.status);
                return;
            }
            Temp temp = null;
            try {
                if (!lease.isFinalAndExclusive() || completion != source || !enabled() || c.capture.cancelled
                        || clock.now() >= c.capture.deadline
                        || !input.same(files.read(name))) throw new IOException("completion/gate/source changed");
                byte[] output = result.bytes();
                XdrRatioPatch.Result checked = XdrRatioPatch.raiseRatioMax(output, 8.0);
                if (checked.changed() || checked.status != XdrRatioPatch.Status.ALREADY_SUFFICIENT
                        || !(checked.ratioMax() >= 8.0) || output.length != input.bytes.length) {
                    throw new IOException("incomplete/unverified HEIF output: " + checked.status + " " + checked.reason);
                }
                temp = files.stage(name, input, output);
                Snapshot disk = files.readTemp(temp);
                XdrRatioPatch.Result reparsed = XdrRatioPatch.raiseRatioMax(disk.bytes, 8.0);
                if (!Arrays.equals(output, disk.bytes) || !input.meta.samePermissions(disk.meta)
                        || reparsed.changed() || reparsed.status != XdrRatioPatch.Status.ALREADY_SUFFICIENT
                        || !(reparsed.ratioMax() >= 8.0)) throw new IOException("temp integrity/permission/reparse failed");
                if (!lease.isFinalAndExclusive() || completion != source || !enabled() || c.capture.cancelled
                        || clock.now() >= c.capture.deadline
                        || !input.same(files.read(name))) throw new IOException("source/gate changed before publish");
                files.rename(temp, name, input, new PublishGuard() {
                    @Override public boolean allow() {
                        return lease.isFinalAndExclusive() && completion == source && enabled()
                                && !c.capture.cancelled && clock.now() < c.capture.deadline;
                    }
                }); // Final helper digest check and guard; Android Os.rename ONLY.
                temp = null;
                c.attempted = input.digest; c.attemptedSource = source;
                report(Status.PUBLISHED, "checked metadata >=8; atomic rename under supplied completion lease", result.status);
            } finally {
                if (temp != null) files.discard(temp);
                lease.close();
            }
        }
    }

    /** Android-only filesystem helper. Never opens the original for writing. */
    static final class AndroidFiles implements Files {
        private final File directory, dcim;
        private final long directoryDevice, directoryInode, dcimDevice, dcimInode;
        private final SecureRandom random = new SecureRandom();
        AndroidFiles(File directory) throws Exception {
            this.directory = directory.getCanonicalFile();
            dcim = this.directory.getParentFile();
            StructStat parent = Os.lstat(dcim.getPath());
            StructStat st = Os.lstat(this.directory.getPath());
            if (!directory.getAbsoluteFile().equals(this.directory) || !OsConstants.S_ISDIR(parent.st_mode)
                    || !OsConstants.S_ISDIR(st.st_mode)) throw new IOException("not an exact safe capture directory");
            directoryDevice = st.st_dev; directoryInode = st.st_ino;
            dcimDevice = parent.st_dev; dcimInode = parent.st_ino;
        }
        private void checkDirectory() throws Exception {
            StructStat parent = Os.lstat(dcim.getPath());
            StructStat st = Os.lstat(directory.getPath());
            if (!OsConstants.S_ISDIR(parent.st_mode) || parent.st_dev != dcimDevice || parent.st_ino != dcimInode
                    || !dcim.equals(dcim.getCanonicalFile())
                    || !OsConstants.S_ISDIR(st.st_mode) || st.st_dev != directoryDevice || st.st_ino != directoryInode
                    || !directory.equals(directory.getCanonicalFile())) throw new IOException("capture directory changed");
        }
        @Override public Set<String> names() throws Exception {
            checkDirectory();
            String[] list = directory.list();
            if (list == null || list.length > Engine.MAX_NAMES) throw new IOException("unreadable/oversized directory");
            Set<String> names = new HashSet<String>();
            for (String name : list) {
                if (!safeName(name)) throw new IOException("unsafe directory name");
                names.add(name);
            }
            checkDirectory(); return names;
        }
        @Override public File path(String name) {
            if (!safeName(name)) throw new IllegalArgumentException("unsafe file name");
            return new File(directory, name);
        }
        private File safePath(String name) throws Exception {
            checkDirectory();
            File file = path(name);
            if (!file.equals(file.getCanonicalFile()) || !directory.equals(file.getCanonicalFile().getParentFile())) {
                throw new IOException("redirected file path");
            }
            return file;
        }
        private Meta regular(StructStat st) throws IOException {
            if (!OsConstants.S_ISREG(st.st_mode) || st.st_nlink != 1 || (st.st_mode & 07000) != 0
                    || st.st_size < 0 || st.st_size > Engine.MAX_BYTES) throw new IOException("unsafe regular-file state/size");
            return new Meta(st.st_dev, st.st_ino, st.st_size, st.st_mtim.tv_sec, st.st_mtim.tv_nsec,
                    st.st_uid, st.st_gid, st.st_mode);
        }
        private Meta stat(File file) throws Exception {
            Meta noLink = regular(Os.lstat(file.getPath()));
            Meta followed = regular(Os.stat(file.getPath()));
            if (!noLink.same(followed)) throw new IOException("path changed/symlink");
            return noLink;
        }
        @Override public Snapshot read(String name) throws Exception {
            File file = safePath(name);
            Meta before = stat(file);
            FileDescriptor fd = Os.open(file.getPath(), OsConstants.O_RDONLY | OsConstants.O_NOFOLLOW
                    | OsConstants.O_CLOEXEC | OsConstants.O_NONBLOCK, 0);
            FileInputStream stream = new FileInputStream(fd);
            try {
                if (!before.same(regular(Os.fstat(fd)))) throw new IOException("file changed at open");
                byte[] bytes = new byte[(int) before.size];
                int offset = 0;
                while (offset < bytes.length) {
                    int n = stream.read(bytes, offset, bytes.length - offset);
                    if (n <= 0) throw new IOException("truncated read");
                    offset += n;
                }
                if (stream.read() != -1 || !before.same(regular(Os.fstat(fd)))
                        || !before.same(stat(file))) throw new IOException("file changed during read");
                checkDirectory();
                return new Snapshot(before, bytes);
            } finally { stream.close(); }
        }
        @Override public Temp stage(String source, Snapshot expected, byte[] bytes) throws Exception {
            File file = safePath(source);
            if (!expected.same(read(source))) throw new IOException("source changed before temp");
            if (bytes.length != expected.bytes.length) throw new IOException("unexpected output length");
            String name = ".xpan-xdr-" + Long.toHexString(random.nextLong()) + ".tmp";
            File target = safePath(name);
            // O_EXCL: collisions fail closed; never truncate any existing pathname.
            FileDescriptor fd = Os.open(target.getPath(), OsConstants.O_WRONLY | OsConstants.O_CREAT
                    | OsConstants.O_EXCL | OsConstants.O_NOFOLLOW | OsConstants.O_CLOEXEC, 0600);
            FileOutputStream stream = new FileOutputStream(fd);
            boolean success = false;
            Meta identity = null;
            try {
                identity = regular(Os.fstat(fd));
                stream.write(bytes); stream.flush(); Os.fsync(fd);
                if (!identity.sameIdentity(stat(target))) throw new IOException("temp path replaced");
                Os.chmod(target.getPath(), expected.meta.mode & 0777);
                Meta done = regular(Os.fstat(fd));
                if (!done.same(stat(target)) || !expected.meta.samePermissions(done)) {
                    throw new IOException("cannot preserve owner/group/mode without chown");
                }
                if (!expected.meta.same(stat(file))) throw new IOException("source metadata changed");
                checkDirectory(); success = true; return new Temp(name, done, sha256(bytes));
            } finally {
                try { stream.close(); }
                finally { if (!success && identity != null) discard(new Temp(name, identity, null)); }
            }
        }
        @Override public Snapshot readTemp(Temp temp) throws Exception {
            Snapshot snapshot = read(temp.name);
            if (!temp.meta.same(snapshot.meta) || !Arrays.equals(temp.digest, snapshot.digest)) {
                throw new IOException("temp revision changed");
            }
            return snapshot;
        }
        @Override public void rename(Temp temp, String source, Snapshot expected, PublishGuard guard) throws Exception {
            File original = safePath(source), target = safePath(temp.name);
            if (!expected.same(read(source)) || !temp.meta.same(readTemp(temp).meta)
                    || !expected.meta.samePermissions(temp.meta)) throw new IOException("final revision/permissions changed");
            checkDirectory();
            if (!guard.allow()) throw new IOException("completion/gate/expiry changed at rename");
            // No TOCTOU guarantee absent the caller's independent writer/directory exclusion.
            Os.rename(target.getPath(), original.getPath());
            // Atomic visibility, not crash-durability, inode/ACL/xattr/SELinux preservation.
        }
        @Override public void discard(Temp temp) {
            try {
                File target = safePath(temp.name);
                if (temp.meta.sameIdentity(stat(target))) target.delete();
            } catch (Exception ignored) { /* Leave an untrusted/replaced pathname untouched. */ }
        }
    }
}
