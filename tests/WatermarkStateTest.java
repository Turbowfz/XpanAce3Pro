package com.xpanport.hook;

import java.util.Arrays;

/** Offline fakes exercise the production helper, not a separately reimplemented model. */
public final class WatermarkStateTest {
    static int checks;
    static final Class<?>[] TYPES = {String.class, Boolean.class, String.class, String.class};
    static Object[] values(String prefix, boolean enabled) {
        return new Object[] {prefix + "style", enabled, prefix + "watermark", prefix + "hasselblad"};
    }
    static void check(boolean ok, String why) { checks++; if (!ok) throw new AssertionError(why); }
    static void equal(Object[] want, Object[] actual, String why) { check(Arrays.equals(want, actual), why); }
    static final class HostileFailure extends RuntimeException {
        static int formattingCalls;
        @Override public String toString() {
            formattingCalls++;
            throw new AssertionError("failure formatting must not run during cleanup");
        }
    }
    static class Fake implements WatermarkState.Store, WatermarkState.Gate {
        Object[] live = values("photo-", true);
        int reads, writes, removes, failRead = -1, failWrite = -1, dropWrite = -1, revokeWrite = -1;
        int failRemove = -1, dropRemove = -1, unknownRemove = -1, presentNull = -1;
        int gateCalls, failGate = -1;
        boolean enabled = true, mutateThenThrow, cleanupFailure, throwGate, hostileFailures;
        RuntimeException failure(String message) {
            return hostileFailures ? new HostileFailure() : new IllegalStateException(message);
        }
        public Object read(int i) {
            reads++;
            if (reads == failRead || (i == presentNull && live[i] == null)) throw failure("fake read");
            return live[i];
        }
        public boolean canRemove(int i) { return i != unknownRemove; }
        public void remove(int i) {
            removes++;
            if (removes != dropRemove && (removes != failRemove || mutateThenThrow)) live[i] = null;
            if (removes == failRemove) throw failure("fake removal");
        }
        public void write(int i, Object value) {
            writes++;
            if (cleanupFailure && value.toString().startsWith("photo-")) throw failure("fake cleanup");
            if (writes != dropWrite && (writes != failWrite || mutateThenThrow)) live[i] = value;
            if (writes == revokeWrite) enabled = false;
            if (writes == failWrite) throw failure("fake setter");
        }
        public boolean enabled() {
            gateCalls++;
            if (throwGate || gateCalls == failGate) throw failure("fake gate");
            return enabled;
        }
        WatermarkState state() {
            WatermarkState s = new WatermarkState(TYPES, this, this);
            s.loadMine(values("xpan-", false));
            return s;
        }
    }
    static void failedEntry(String actual) {
        Fake f = new Fake(); WatermarkState s = f.state();
        Object[] before = f.live.clone(), mine = s.mine(), other = s.other();
        WatermarkState.Transition t = s.begin("photo", "xpan", true);
        check(t.staged && !s.active(), "stage prematurely committed entry");
        f.live = values("app-failed-entry-", true);
        check(!s.finish(t, actual), "failed entry committed");
        equal(before, f.live, "entry rollback not original four live keys");
        equal(mine, s.mine(), "failed entry corrupts archive");
        equal(other, s.other(), "failed entry corrupts other backup");
        check(t.restored && !s.active(), "failed entry state");
    }
    static void failedExit() {
        Fake f = new Fake(); WatermarkState s = f.state(); Object[] photo = f.live.clone();
        WatermarkState.Transition enter = s.begin("photo", "xpan", true);
        check(s.finish(enter, "xpan") && s.active(), "entry did not commit");
        f.live = values("user-xpan-", true);
        Object[] live = f.live.clone(), mine = s.mine(), other = s.other();
        WatermarkState.Transition exit = s.begin("xpan", "photo", true);
        equal(photo, f.live, "exit not immediate isolation");
        f.live = values("failed-exit-clobber-", false);
        check(!s.finish(exit, "xpan"), "unchanged actual exit committed");
        equal(live, f.live, "exit rollback not direct live");
        equal(mine, s.mine(), "failed exit changed archive");
        equal(other, s.other(), "failed exit cleared backup");
        check(s.active() && exit.restored, "exit rollback state");
    }
    static void failedReadAndWrite() {
        for (int index = 1; index <= 4; index++) {
            Fake f = new Fake(); WatermarkState s = f.state(); Object[] before = f.live.clone();
            f.failRead = index;
            WatermarkState.Transition t = s.begin("photo", "xpan", true);
            check(!t.staged && f.writes == 0, "partial read allowed a write");
            equal(before, f.live, "partial read changed live");
        }
        for (Object invalid : new Object[] {null, Integer.valueOf(1)}) {
            Fake f = new Fake(); WatermarkState s = f.state(); f.live[2] = invalid;
            f.unknownRemove = 2;
            WatermarkState.Transition t = s.begin("photo", "xpan", true);
            check(!t.staged && f.writes == 0 && f.removes == 0, "unknown null path/wrong type guessed a default or removal");
        }
        for (boolean mutate : new boolean[] {false, true}) for (int index = 1; index <= 4; index++) {
            Fake f = new Fake(); WatermarkState s = f.state(); Object[] before = f.live.clone(), mine = s.mine();
            f.failWrite = index; f.mutateThenThrow = mutate;
            WatermarkState.Transition t = s.begin("photo", "xpan", true);
            check(!t.staged && !s.active() && t.restored, "failed prefix claimed success");
            equal(before, f.live, "failed setter prefix not restored, including failing index");
            equal(mine, s.mine(), "partial write changed archive");
        }
        Fake f = new Fake(); WatermarkState s = f.state(); Object[] before = f.live.clone();
        f.dropWrite = 1;
        WatermarkState.Transition t = s.begin("photo", "xpan", true);
        check(!t.staged && t.restored, "silently dropped setter not detected by readback");
        equal(before, f.live, "readback mismatch not cleaned");
    }
    static void cleanupFailure() {
        Fake f = new Fake(); WatermarkState s = f.state(); Object[] before = f.live.clone(), mine = s.mine();
        WatermarkState.Transition enter = s.begin("photo", "xpan", true);
        f.cleanupFailure = true;
        check(!s.finish(enter, null) && s.hasCleanup() && !enter.restored, "cleanup failure backup lost");
        WatermarkState.Transition blocked = s.begin("photo", "xpan", true);
        check(!blocked.staged && s.hasCleanup() && !s.active(), "new swap allowed despite failed cleanup");
        equal(mine, s.mine(), "failed cleanup corrupted archive");
        f.cleanupFailure = false; f.enabled = false;
        WatermarkState.Transition off = s.begin("photo", "xpan", true);
        check(!off.staged && !s.hasCleanup(), "revoked gate blocked already-written cleanup");
        equal(before, f.live, "gateoff cleanup not restored original four");
        f = new Fake(); s = f.state(); before = f.live.clone();
        f.failWrite = 2; f.mutateThenThrow = true; f.cleanupFailure = true;
        WatermarkState.Transition partial = s.begin("photo", "xpan", true);
        check(!partial.staged && s.hasCleanup() && !partial.restored, "partial prefix cleanup backup lost");
        f.cleanupFailure = false; f.enabled = false;
        check(!s.finish(partial, "xpan") && partial.restored && !s.hasCleanup(), "failed-prefix after did not retry gateoff cleanup");
        equal(before, f.live, "failed-prefix after cleanup not original live");
    }
    static void gateOff() {
        Fake f = new Fake(); WatermarkState s = f.state(); Object[] before = f.live.clone();
        WatermarkState.Transition t = s.begin("photo", "xpan", true);
        f.enabled = false;
        check(!s.finish(t, "xpan") && t.restored, "gateoff committed or skipped cleanup");
        equal(before, f.live, "gateoff after cleanup not original live");
        int writes = f.writes;
        check(!s.begin("photo", "xpan", true).staged && f.writes == writes, "gateoff started new swap");
        f = new Fake(); s = f.state(); before = f.live.clone(); f.revokeWrite = 2;
        t = s.begin("photo", "xpan", true);
        check(!t.staged && t.restored, "midprefix gateoff not rolled back");
        equal(before, f.live, "midprefix gateoff cleanup");
    }
    static void epochsAndUserValues() {
        Fake f = new Fake(); WatermarkState s = f.state(); Object[] photo = f.live.clone();
        WatermarkState.Transition enter = s.begin("photo", "xpan", true);
        check(s.finish(enter, "xpan"), "fast entry");
        WatermarkState.Transition exit = s.begin("xpan", "photo", true);
        check(s.finish(exit, "photo"), "fast exit");
        WatermarkState.Transition again = s.begin("photo", "xpan", true);
        check(s.finish(again, "xpan") && !s.current(enter) && !s.current(exit) && s.current(again), "epoch not advanced");
        // A legitimate same-epoch setting may equal the old clobber. No delayed guesses exist.
        f.live = photo.clone(); int writes = f.writes;
        check(!s.finish(enter, "xpan") && !s.finish(exit, "photo") && !s.finish(again, "xpan"), "repeated finish committed twice");
        equal(photo, f.live, "old/current finished callback rewrote user settings");
        check(f.writes == writes, "stale jobs caused repair writes");
        WatermarkState.Transition same = s.begin("xpan", "xpan", true);
        check(!same.staged && f.writes == writes, "same-mode invokes watermark swap");
        // Nested calls cancel the owning epoch; direct rollback rather than stacking two swaps.
        f = new Fake(); s = f.state(); photo = f.live.clone();
        enter = s.begin("photo", "xpan", true);
        WatermarkState.Transition overlapping = s.begin("photo", "photo", true);
        check(!overlapping.staged && !s.finish(enter, "xpan"), "overlapping stale transaction committed");
        equal(photo, f.live, "overlap cleanup did not restore owning live snapshot");
    }
    static void firstEntryAndActualCommit() {
        Fake f = new Fake(); WatermarkState s = new WatermarkState(TYPES, f, f);
        Object[] photo = f.live.clone(); WatermarkState.Transition t = s.begin("photo", "xpan", true);
        check(t.staged && f.writes == 0 && s.finish(t, "xpan"), "first entry did not inherit");
        equal(photo, f.live, "first entry changed settings");
        f.live = values("user-", false); Object[] user = f.live.clone();
        t = s.begin("xpan", "photo", true);
        // Parent's original throwable is irrelevant once actual mode truly committed elsewhere.
        check(s.finish(t, "video") && !s.active(), "actual non-Xpan commit category refused");
        equal(user, s.mine(), "actual exit did not archive Xpan settings");
        equal(photo, f.live, "actual exit leaked Xpan live settings");
    }
    static void readOnlyRollbackWithOtherModuleChanges() {
        for (boolean exiting : new boolean[] {false, true}) for (boolean saved : new boolean[] {false, true}) {
            for (int missing = -1; missing <= 4; missing++) {
                for (String outcome : new String[] {"unchanged", "unknown", "gateoff", "throwing-gate", "stale"}) {
                    Fake f = new Fake();
                    if (missing == 4) f.live = new Object[4];
                    else if (missing >= 0) f.live[missing] = null;
                    WatermarkState s = new WatermarkState(TYPES, f, f);
                    if (saved) s.loadMine(f.live.clone(), new boolean[] {true, true, true, true});
                    if (exiting) {
                        WatermarkState.Transition enter = s.begin("photo", "xpan", true);
                        check(enter.staged && !enter.wrote && s.finish(enter, "xpan"), "read-only exit setup failed");
                    }
                    String oldMode = exiting ? "xpan" : "photo", requested = exiting ? "photo" : "xpan";
                    Object[] mine = s.mine(), other = s.other();
                    boolean[] known = s.mineKnown(); String archive = s.archive();
                    WatermarkState.Transition t = s.begin(oldMode, requested, true);
                    check(t.staged && !t.wrote && f.writes == 0 && f.removes == 0,
                            "inheritance/equal-bank fixture unexpectedly mutated keys");
                    // Another hook changes both typed values and absence while the original switch runs.
                    f.live = values("opcamera-", false);
                    f.live[missing < 0 ? 1 : (missing + 1) % 4] = null;
                    Object[] current = f.live.clone(); int reads = f.reads;
                    String actual = "unchanged".equals(outcome) ? oldMode : "unknown".equals(outcome) ? null : requested;
                    if ("gateoff".equals(outcome)) f.enabled = false;
                    else if ("throwing-gate".equals(outcome)) f.throwGate = true;
                    else if ("stale".equals(outcome))
                        check(!s.begin(oldMode, "video", true).staged, "nested read-only switch stacked swaps");
                    check(!s.finish(t, actual), "uncommitted read-only switch committed");
                    equal(current, f.live, "read-only rollback clobbered other module settings");
                    check(f.writes == 0 && f.removes == 0 && f.reads == reads,
                            "read-only rollback touched the live store");
                    equal(mine, s.mine(), "read-only rollback changed own bank");
                    equal(other, s.other(), "read-only rollback changed other bank");
                    check(Arrays.equals(known, s.mineKnown()) && archive.equals(s.archive()),
                            "read-only rollback changed archive presence");
                    check(t.restored && !t.committed && !s.hasCleanup() && s.active() == exiting,
                            "read-only rollback left a cleanup debt or changed actual state");
                    check(!s.finish(t, requested) && !s.begin(oldMode, requested, false).staged
                                    && f.writes == 0 && f.removes == 0 && f.reads == reads,
                            "finished read-only callback performed late cleanup");
                    equal(current, f.live, "finished read-only callback rewrote other module settings");
                }
            }
        }
    }
    static void gateExceptionsAndHostileDiagnostics() {
        int formatted = HostileFailure.formattingCalls;
        // Initial gate, every write-prefix gate, and the final post-write gate.
        for (int gateCall = 1; gateCall <= 6; gateCall++) {
            Fake f = new Fake(); WatermarkState s = f.state();
            Object[] before = f.live.clone(), mine = s.mine(), other = s.other();
            f.failGate = gateCall; f.hostileFailures = true;
            WatermarkState.Transition t = s.begin("photo", "xpan", true);
            check(!t.staged && !s.active(), "throwing begin gate escaped or staged swap");
            if (gateCall <= 2) check(f.writes == 0, "initial/prewrite throwing gate wrote new values");
            else check(t.restored && !s.hasCleanup(), "throwing prefix/final gate skipped cleanup");
            equal(before, f.live, "throwing begin gate changed original live keys");
            equal(mine, s.mine(), "throwing begin gate changed archive");
            equal(other, s.other(), "throwing begin gate changed backup");
        }
        Fake f = new Fake(); WatermarkState s = f.state();
        Object[] before = f.live.clone(), mine = s.mine(), other = s.other();
        WatermarkState.Transition t = s.begin("photo", "xpan", true);
        check(t.staged, "finish gate exception fixture not staged");
        f.throwGate = true; f.hostileFailures = true;
        check(!s.finish(t, "xpan") && t.restored && !t.committed && !s.active(),
                "throwing finish gate escaped, committed, or skipped cleanup");
        equal(before, f.live, "throwing finish gate failed direct rollback");
        equal(mine, s.mine(), "throwing finish gate changed archive");
        equal(other, s.other(), "throwing finish gate changed backup");
        int writes = f.writes;
        check(!s.begin("photo", "xpan", true).staged && f.writes == writes,
                "throwing gate allowed follow-up swap after cleared inFlight");

        // Exceptions whose toString also throws cannot interrupt the four-key cleanup loop.
        f = new Fake(); s = f.state(); before = f.live.clone(); mine = s.mine();
        f.failWrite = 2; f.mutateThenThrow = true; f.cleanupFailure = true; f.hostileFailures = true;
        t = s.begin("photo", "xpan", true);
        check(!t.staged && !t.restored && s.hasCleanup(), "hostile prefix/cleanup exception lost backup");
        check(f.writes == 6 && f.reads == 8, "hostile diagnostics interrupted all-four cleanup/readback");
        equal(mine, s.mine(), "hostile failed cleanup changed archive");
        WatermarkState.Transition blocked = s.begin("photo", "xpan", true);
        check(!blocked.staged && s.hasCleanup() && f.writes == 10 && f.reads == 12,
                "hostile pending-cleanup retry escaped or allowed new swap");
        f.cleanupFailure = false; f.throwGate = true;
        check(!s.finish(t, "xpan") && t.restored && !s.hasCleanup(),
                "hostile failed-prefix cleanup did not survive throwing gate");
        equal(before, f.live, "hostile failed-prefix cleanup not original live");

        // A hostile readback exception also retains cleanup until verified restoration.
        f = new Fake(); s = f.state(); before = f.live.clone();
        t = s.begin("photo", "xpan", true);
        f.throwGate = true; f.hostileFailures = true; f.failRead = f.reads + 1;
        check(!s.finish(t, "xpan") && !t.restored && s.hasCleanup(),
                "hostile restore-read failure escaped or dropped cleanup backup");
        check(f.reads == 12, "hostile restore-read exception stopped remaining key verification");
        check(!s.begin("photo", "xpan", true).staged && t.restored && !s.hasCleanup(),
                "pending readback cleanup failed under throwing gate");
        equal(before, f.live, "throwing gate cleanup retry changed original live");
        check(HostileFailure.formattingCalls == formatted, "cleanup formatted hostile exception");
    }
    static void nullEnterExitAndArchive() {
        for (int index = 0; index < 4; index++) {
            Fake f = new Fake(); WatermarkState s = f.state(); f.live[index] = null;
            Object[] photo = f.live.clone(), mine = s.mine(); boolean[] known = s.mineKnown();
            WatermarkState.Transition enter = s.begin("photo", "xpan", true);
            check(enter.staged && f.reads == 8, "legitimate missing entry refused or skipped all-four readback");
            check(!s.finish(enter, null) && enter.restored, "null actual mode committed missing entry");
            equal(photo, f.live, "null actual rollback failed to remove originally absent key");
            equal(mine, s.mine(), "null actual entry archived speculative keys");
            check(Arrays.equals(known, s.mineKnown()), "failed entry changed archive-known bits");
            enter = s.begin("photo", "xpan", true);
            check(s.finish(enter, "xpan"), "missing entry actual commit refused");
            equal(photo, s.other(), "missing non-Xpan backup lost");
            f.live = values("user-", true); f.live[(index + 1) % 4] = null;
            Object[] user = f.live.clone();
            WatermarkState.Transition exit = s.begin("xpan", "photo", true);
            check(exit.staged, "null exit refused");
            equal(photo, f.live, "exit did not restore true absence");
            check(!s.finish(exit, null) && exit.restored && s.active(), "null actual exit committed");
            equal(user, f.live, "failed exit lost missing Xpan live key");
            equal(mine, s.mine(), "failed null exit updated archive");
            exit = s.begin("xpan", "photo", true);
            check(s.finish(exit, "video"), "missing exit actual category commit refused");
            equal(user, s.mine(), "successful null exit not archived");
            for (boolean bit : s.mineKnown()) check(bit, "archived absence not marked known");
            check(!s.finish(exit, "photo"), "null archive repeated finish committed");
            enter = s.begin("video", "xpan", true);
            check(s.finish(enter, "xpan"), "known absent archive reentry refused");
            equal(user, f.live, "known absent archive treated as first-entry inherit");
            // Rehydration preserves a known null while old omitted archive rows remain unknown.
            Fake reloaded = new Fake(); WatermarkState loaded = new WatermarkState(TYPES, reloaded, reloaded);
            loaded.loadMine(s.mine(), s.mineKnown());
            WatermarkState.Transition again = loaded.begin("photo", "xpan", true);
            check(loaded.finish(again, "xpan"), "rehydrated absence refused");
            equal(user, reloaded.live, "rehydrated null inherited instead of removed");
        }
        Fake f = new Fake(); f.live = new Object[4];
        WatermarkState s = new WatermarkState(TYPES, f, f);
        WatermarkState.Transition t = s.begin("photo", "xpan", true);
        check(t.staged && f.writes == 0 && f.removes == 0 && s.finish(t, "xpan"), "first entry all-null inheritance");
        equal(new Object[4], f.live, "first entry guessed typed defaults");
        f.live = values("user-", true);
        t = s.begin("xpan", "photo", true);
        check(s.finish(t, "photo") && f.removes == 4, "all-null backup did not use four single-key removes");
        equal(new Object[4], f.live, "all-null exit filled defaults");
    }
    static void nullFailuresAndGateCleanup() {
        for (int missing = 0; missing < 4; missing++) for (boolean mutate : new boolean[] {false, true}) {
            for (int prefix = 1; prefix <= 4; prefix++) {
                Fake f = new Fake(); WatermarkState s = f.state(); f.live[missing] = null;
                Object[] before = f.live.clone(), mine = s.mine();
                f.failWrite = prefix; f.mutateThenThrow = mutate;
                WatermarkState.Transition t = s.begin("photo", "xpan", true);
                check(!t.staged && t.restored && !s.hasCleanup(), "missing failed set prefix not compensated");
                equal(before, f.live, "missing failed prefix including changed-then-error not restored");
                equal(mine, s.mine(), "missing failed prefix archived");
            }
            Fake f = new Fake(); WatermarkState s = f.state(); f.live[missing] = null;
            Object[] before = f.live.clone();
            WatermarkState.Transition t = s.begin("photo", "xpan", true);
            f.enabled = false;
            check(!s.finish(t, "xpan") && t.restored && !s.active(), "gateoff skipped missing removal cleanup");
            equal(before, f.live, "gateoff missing cleanup filled a default");
            f = new Fake(); s = f.state(); f.live[missing] = null; before = f.live.clone();
            t = s.begin("photo", "xpan", true);
            f.failRemove = 1; f.mutateThenThrow = mutate; f.enabled = false;
            check(!s.finish(t, null) && !t.restored && s.hasCleanup(), "throwing restore remove lost cleanup ownership");
            check(!s.begin("photo", "xpan", true).staged && t.restored && !s.hasCleanup(), "gateoff removal retry failed");
            equal(before, f.live, "gateoff removal retry not verified original absence");
        }
        // Remover failure during the forward swap, including removal before throwing.
        for (boolean mutate : new boolean[] {false, true}) for (int prefix = 1; prefix <= 4; prefix++) {
            Fake f = new Fake(); WatermarkState s = f.state(); Object[] before = f.live.clone(), mine = s.mine();
            s.loadMine(new Object[4], new boolean[] {true, true, true, true});
            f.failRemove = prefix; f.mutateThenThrow = mutate;
            WatermarkState.Transition t = s.begin("photo", "xpan", true);
            check(!t.staged && t.restored && !s.hasCleanup(), "failed remove prefix not compensated");
            equal(before, f.live, "failed remove prefix including failing index changed live");
            equal(new Object[4], s.mine(), "failed remove prefix changed known-null archive");
            check(!s.finish(t, "xpan"), "failed removal actual request archived");
        }
        Fake f = new Fake(); WatermarkState s = f.state(); Object[] before = f.live.clone();
        s.loadMine(new Object[4], new boolean[] {true, true, true, true}); f.dropRemove = 2;
        WatermarkState.Transition t = s.begin("photo", "xpan", true);
        check(!t.staged && t.restored, "silently dropped remove passed readback");
        equal(before, f.live, "dropped forward remove not compensated");
        f = new Fake(); s = f.state(); f.live[0] = null; before = f.live.clone();
        t = s.begin("photo", "xpan", true); f.dropRemove = 1;
        check(!s.finish(t, "photo") && s.hasCleanup() && !t.restored, "restore absence falsely verified");
        f.enabled = false;
        check(!s.finish(t, null) && t.restored && !s.hasCleanup(), "restore remove readback retry failed");
        equal(before, f.live, "remove verification retry lost backup");
        // All four reads must finish before any set/remove, even when earlier reads are legitimate nulls.
        for (int failure = 1; failure <= 4; failure++) {
            f = new Fake(); s = f.state(); f.live = new Object[4]; f.failRead = failure;
            t = s.begin("photo", "xpan", true);
            check(!t.staged && f.writes == 0 && f.removes == 0, "null prefix read failure allowed mutations");
        }
        f = new Fake(); s = f.state(); f.live[1] = null; f.presentNull = 1;
        check(!s.begin("photo", "xpan", true).staged && f.writes == 0 && f.removes == 0,
                "ambiguous present-null reflection read treated as absence");
        f = new Fake(); s = f.state(); f.unknownRemove = 3;
        s.loadMine(new Object[4], new boolean[] {true, true, true, true});
        check(!s.begin("photo", "xpan", true).staged && f.writes == 0 && f.removes == 0,
                "unknown saved-null removal ABI allowed a write prefix");
    }
    static void archiveFormat() {
        Fake f = new Fake(); WatermarkState s = new WatermarkState(TYPES, f, f);
        check("0\tu\t\n1\tu\t\n2\tu\t\n3\tu\t\n".equals(s.archive()), "new archive must explicitly encode four unknowns");
        check(s.loadArchive("0\ts\txpan-style\n1\tb\tfalse\n2\tn\t\n3\tu\t\n"), "mixed typed/missing/unknown archive refused");
        String before = s.archive();
        Object[] mine = s.mine(); boolean[] known = s.mineKnown();
        check(mine[2] == null && known[2] && !known[3], "known absence conflated with unknown");
        for (String bad : new String[] {"0\tn\t\n1\tb\twrong\n", "0\tn\t\n0\tu\t\n", "4\tn\t\n",
                "2\tb\tfalse\n", "1\ts\tfalse\n", "0\tn\tvalue\n", "0\tu\tvalue\n", "0\tn\t\nbad\n",
                "0\tn\t\n2\tx\t\n", "0\ts\tbad\r\n", "bad\tn\t\n", "0\tn\t\n\n", "\n", "00\tn\t\n", "+0\tn\t\n"}) {
            check(!s.loadArchive(bad), "malformed archive accepted");
            equal(mine, s.mine(), "malformed partial parse clobbered mine");
            check(Arrays.equals(known, s.mineKnown()) && before.equals(s.archive()), "malformed parse clobbered archive presence");
        }
        char[] oversized = new char[4097]; Arrays.fill(oversized, 'x');
        check(!s.loadArchive(new String(oversized)) && !s.loadArchive(null) && before.equals(s.archive()),
                "oversized/null archive not refused atomically");
        WatermarkState loaded = new WatermarkState(TYPES, f, f);
        check(loaded.loadArchive(before) && before.equals(loaded.archive()), "production archive roundtrip failed");
        WatermarkState.Transition t = loaded.begin("photo", "xpan", true);
        check(t.staged && f.live[2] == null && "photo-hasselblad".equals(f.live[3]), "archive missing/unknown did not remove/inherit respectively");
        check(!loaded.loadArchive("0\tn\t\n") && before.equals(loaded.archive()), "in-flight archive load overwrote snapshot");
        check(loaded.finish(t, "xpan") && !loaded.loadArchive("0\tn\t\n"), "active archive load overwrote snapshot");
        f = new Fake(); loaded = new WatermarkState(TYPES, f, f);
        check(loaded.loadArchive("0\ts\txpan-style\n1\tb\tfalse\n"), "legacy sparse b/s archive rejected");
        known = loaded.mineKnown();
        check(known[0] && known[1] && !known[2] && !known[3], "legacy omitted row guessed absence");
        t = loaded.begin("photo", "xpan", true);
        check(t.staged && "photo-watermark".equals(f.live[2]) && "photo-hasselblad".equals(f.live[3]), "legacy omissions did not inherit live");
        check(!loaded.finish(t, null), "archive fixture null actual committed");
        check(loaded.loadArchive("0\ts\t\n1\tn\t\n2\ts\twith\ttab\n3\tu\t\n"), "empty string/embedded tab confused with absence");
        check("".equals(loaded.mine()[0]) && loaded.mineKnown()[0] && loaded.mine()[1] == null && loaded.mineKnown()[1],
                "empty string and missing Boolean not distinct");
    }
    public static void main(String[] args) {
        failedEntry(null); failedEntry("photo"); failedExit(); failedReadAndWrite();
        cleanupFailure(); gateOff(); epochsAndUserValues(); firstEntryAndActualCommit();
        readOnlyRollbackWithOtherModuleChanges(); gateExceptionsAndHostileDiagnostics();
        nullEnterExitAndArchive(); nullFailuresAndGateCleanup(); archiveFormat();
        System.out.println("PASS WatermarkState " + checks + " checks; offline fakes only; verified absence supported");
    }
}
