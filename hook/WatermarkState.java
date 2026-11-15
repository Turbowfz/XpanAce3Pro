package com.xpanport.hook;

/** Pure, fail-closed four-key watermark transaction. No Android, files or delayed repair. */
final class WatermarkState {
    interface Store {
        // A returned null is a legitimate missing key, never a swallowed read failure.
        Object read(int index) throws Throwable;
        void write(int index, Object value) throws Throwable;
        boolean canRemove(int index) throws Throwable;
        void remove(int index) throws Throwable;
    }
    interface Gate { boolean enabled(); }

    static final class Transition {
        final long epoch;
        final String oldMode, requestedMode;
        final boolean active;
        final Object[] mine, other;
        Object[] live, target;
        boolean wrote, staged, committed, restored;
        String detail = "no swap";

        Transition(long epoch, String oldMode, String requestedMode, boolean active,
                   Object[] mine, Object[] other) {
            this.epoch = epoch;
            this.oldMode = oldMode;
            this.requestedMode = requestedMode;
            this.active = active;
            this.mine = mine.clone();
            this.other = other.clone();
        }
    }

    private final Class<?>[] types;
    private final Store store;
    private final Gate gate;
    private Object[] mine, other;
    private boolean[] mineKnown;
    private boolean active;
    private long epoch;
    private Transition inFlight, cleanup;

    WatermarkState(Class<?>[] types, Store store, Gate gate) {
        if (types.length != 4) throw new IllegalArgumentException("four keys required");
        this.types = types.clone();
        this.store = store;
        this.gate = gate;
        mine = new Object[types.length];
        other = new Object[types.length];
        mineKnown = new boolean[types.length];
    }

    synchronized Object[] mine() { return mine.clone(); }
    synchronized boolean[] mineKnown() { return mineKnown.clone(); }
    synchronized Object[] other() { return other.clone(); }
    synchronized boolean active() { return active; }
    synchronized boolean hasCleanup() { return cleanup != null; }
    synchronized boolean current(Transition t) { return t != null && t.epoch == epoch; }
    synchronized long epoch() { return epoch; }
    synchronized boolean current(long value) { return value == epoch; }

    synchronized void loadMine(Object[] saved) {
        boolean[] known = new boolean[saved.length];
        for (int i = 0; i < saved.length; i++) known[i] = saved[i] != null;
        loadMine(saved, known); // old archives omit unknown entries, not explicit absence
    }

    synchronized void loadMine(Object[] saved, boolean[] known) {
        if (saved.length != types.length || known.length != types.length
                || active || inFlight != null || cleanup != null) return;
        for (int i = 0; i < types.length; i++) {
            if (saved[i] != null && (!known[i] || !types[i].isInstance(saved[i]))) return;
        }
        mine = saved.clone();
        mineKnown = known.clone();
    }

    /** Own archive: u=unknown/inherit, n=verified absence; legacy omitted b/s rows are unknown. */
    synchronized String archive() {
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < mine.length; i++) {
            Object value = mine[i];
            out.append(i).append('\t');
            if (!mineKnown[i]) out.append("u\t");
            else if (value == null) out.append("n\t");
            else {
                String text = value.toString();
                if (text.indexOf('\n') >= 0 || text.indexOf('\r') >= 0)
                    throw new IllegalStateException("invalid archive value");
                out.append(value instanceof Boolean ? "b" : "s").append('\t').append(text);
            }
            out.append('\n');
        }
        return out.toString();
    }

    synchronized boolean loadArchive(String text) {
        if (active || inFlight != null || cleanup != null || text == null || text.length() > 4096) return false;
        Object[] saved = new Object[types.length];
        boolean[] known = new boolean[types.length], seen = new boolean[types.length];
        try {
            String rows = text.endsWith("\n") ? text.substring(0, text.length() - 1) : text;
            if (!text.isEmpty()) for (String line : rows.split("\n", -1)) {
                String[] p = line.split("\t", 3);
                if (p.length != 3 || p[0].length() != 1 || p[0].charAt(0) < '0' || p[0].charAt(0) > '3') return false;
                int i = p[0].charAt(0) - '0';
                if (seen[i]) return false;
                seen[i] = true;
                if ("u".equals(p[1]) || "n".equals(p[1])) {
                    if (!p[2].isEmpty()) return false;
                    known[i] = "n".equals(p[1]);
                } else if ("b".equals(p[1]) && types[i] == Boolean.class
                        && ("true".equals(p[2]) || "false".equals(p[2]))) {
                    saved[i] = Boolean.valueOf(p[2]); known[i] = true;
                } else if ("s".equals(p[1]) && types[i] == String.class && p[2].indexOf('\r') < 0) {
                    saved[i] = p[2]; known[i] = true;
                } else return false;
            }
        } catch (RuntimeException failure) { return false; }
        loadMine(saved, known); // publish only after every row is validated
        return true;
    }

    synchronized Transition begin(String oldMode, String requestedMode, boolean enabled) {
        Transition t = new Transition(++epoch, oldMode, requestedMode, active, mine, other);
        // Never stack swaps. A stale in-flight invocation still owns cleanup of its writes.
        if (inFlight != null) { t.detail = "overlapping switch refused"; return t; }
        if (cleanup != null && !restore(cleanup)) { t.detail = "cleanup still pending"; return t; }
        if (!enabled || !gateEnabled() || requestedMode == null || same(oldMode, requestedMode)) return t;
        boolean wantsXpan = "xpan".equals(requestedMode);
        if (wantsXpan == active || "xpan".equals(oldMode) != active) return t;
        try {
            // Read and validate all backups and removal capabilities before the first mutation.
            t.live = readAll();
            t.target = t.live.clone();
            for (int i = 0; i < types.length; i++) {
                if (wantsXpan && !mineKnown[i]) continue; // first entry inherits, including absence
                Object value = wantsXpan ? mine[i] : other[i];
                if (value != null && !types[i].isInstance(value))
                    throw new IllegalStateException("invalid saved key " + i);
                if (value == null && !store.canRemove(i))
                    throw new IllegalStateException("unknown removal path " + i);
                t.target[i] = value;
            }
            for (int i = 0; i < types.length; i++) {
                if (same(t.target[i], t.live[i])) continue;
                if (!gateEnabled()) throw new IllegalStateException("gate revoked during swap");
                t.wrote = true; // a setter/remover may mutate before throwing
                put(i, t.target[i]);
            }
            Object[] applied = readAll();
            for (int i = 0; i < types.length; i++) {
                if (!same(t.target[i], applied[i]))
                    throw new IllegalStateException("swap readback mismatch " + i);
            }
            if (!gateEnabled()) throw new IllegalStateException("gate revoked after swap");
            t.staged = true;
            t.detail = "staged; actual commit pending";
            inFlight = t;
        } catch (Throwable failure) {
            // Diagnostics must not call a failing Throwable's user-defined toString().
            t.detail = "swap refused/failed";
            if (t.wrote) restore(t); // cleanup ignores the revoked gate
        }
        return t;
    }

    synchronized boolean finish(Transition t, String actualMode) {
        if (t == null) return false;
        if (!t.staged || inFlight != t) {
            if (cleanup == t) restore(t); // failed-prefix cleanup still belongs to this callback
            return false;
        }
        inFlight = null;
        boolean commit = current(t) && gateEnabled() && actualMode != null
                && !same(t.oldMode, actualMode)
                && "xpan".equals(t.requestedMode) == "xpan".equals(actualMode);
        if (!commit) {
            t.detail = t.wrote ? "uncommitted/stale/revoked switch; restoring original live keys"
                    : "uncommitted/stale/revoked switch; no live-key writes";
            restore(t); // never call an inverse enter/exit or change archive on rollback
            return false;
        }
        if ("xpan".equals(actualMode)) {
            other = t.live.clone();
            active = true;
        } else {
            mine = t.live.clone();
            for (int i = 0; i < mineKnown.length; i++) mineKnown[i] = true;
            other = new Object[types.length];
            active = false;
        }
        t.committed = true;
        t.detail = "actual mode committed";
        return true;
    }

    private Object[] readAll() throws Throwable {
        Object[] values = new Object[types.length];
        for (int i = 0; i < values.length; i++) {
            Object value = store.read(i);
            if (value != null && !types[i].isInstance(value))
                throw new IllegalStateException("untyped live key " + i);
            if (value == null && !store.canRemove(i))
                throw new IllegalStateException("unrestorable missing key " + i);
            values[i] = value;
        }
        return values;
    }

    private void put(int index, Object value) throws Throwable {
        if (value == null) store.remove(index);
        else store.write(index, value);
    }

    private boolean restore(Transition t) {
        // Staging alone creates no cleanup debt; preserve other hooks' live changes.
        if (!t.wrote) { t.restored = true; return true; }
        boolean ok = true;
        // Attempt all four, including the possibly partially applied failing setter.
        for (int i = 0; i < types.length; i++) {
            try { put(i, t.live[i]); }
            catch (Throwable failure) { ok = false; t.detail = "live restore incomplete"; }
        }
        for (int i = 0; i < types.length; i++) {
            try {
                Object actual = store.read(i);
                if (!same(t.live[i], actual)) throw new IllegalStateException("restore mismatch " + i);
            } catch (Throwable failure) { ok = false; t.detail = "live restore unverified"; }
        }
        t.restored = ok;
        if (ok) { if (cleanup == t) cleanup = null; }
        else cleanup = t; // retain backup and refuse new swaps until direct cleanup succeeds
        return ok;
    }

    /** A throwing gate is revocation, never permission to swap or to skip cleanup. */
    private boolean gateEnabled() {
        try { return gate.enabled(); }
        catch (Throwable failure) { return false; }
    }

    private static boolean same(Object a, Object b) { return a == null ? b == null : a.equals(b); }
}
