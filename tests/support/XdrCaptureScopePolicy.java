package com.xpanport.hook.study;

import java.lang.ref.WeakReference;
import java.util.ArrayList;

/**
 * TEST SUPPORT ONLY, not a hook, installer, or original-camera ABI adapter.
 *
 * Original C16/C17 fragments do not verify a non-replayable fresh request/frame
 * to APS merge identity to exact HEIC/save-owner bridge. The real Camera2 callback
 * keeps the request; ConsumerImpl forwards tag/hash/timestamp to ApsProcessor.
 * APSClient parses resultinfo_identity, which CameraPicture.S can also reuse in
 * deferred recovery. Dispatch time and helper-delivered onCaptureStarted are NOT
 * freshness proof. This model assumes evidence a future verified adapter would
 * have to supply. Passing these tests does not establish camera/XDR coverage.
 */
final class XdrCaptureScopePolicy {
    interface Gate { boolean enabled(); }
    interface Clock { long elapsedMillis(); }
    static final long MAX_TTL_MILLIS = 120000L;
    static final int MAX_ENTRIES = 128;

    /** Opaque policy-local token, never a request hash or a numeric APS identity. */
    static final class Token {
        private static final int REQUESTED = 0, STARTED = 1, LINKED = 2, OWNED = 3, DEAD = 4;
        private final XdrCaptureScopePolicy policy;
        private final long born;
        private final WeakReference<Object> device, session, request;
        private Object strongDevice, strongSession, strongRequest;
        private WeakReference<Object> owner;
        private int stage = REQUESTED;
        private boolean hasStart, hasIdentity;
        private long sensorTimestamp, frameNumber, apsIdentity;

        private Token(XdrCaptureScopePolicy policy, long born, Object device, Object session, Object request) {
            this.policy = policy;
            this.born = born;
            this.device = new WeakReference<Object>(device);
            this.session = new WeakReference<Object>(session);
            this.request = new WeakReference<Object>(request);
            strongDevice = device;
            strongSession = session;
            strongRequest = request;
        }

        private void releaseStrongReferences() {
            strongDevice = null;
            strongSession = null;
            strongRequest = null;
        }
    }

    private final Gate gate;
    private final Clock clock;
    private final long ttlMillis;
    private final int capacity;
    private final ArrayList<Token> entries;
    private long lastNow = -1;
    private boolean stopped, polling;

    XdrCaptureScopePolicy(Gate gate, Clock clock, long ttlMillis, int capacity) {
        if (gate == null || clock == null || ttlMillis <= 0 || ttlMillis > MAX_TTL_MILLIS
                || capacity <= 0 || capacity > MAX_ENTRIES)
            throw new IllegalArgumentException("explicit gate/monotonic clock and bounded TTL/capacity required");
        this.gate = gate;
        this.clock = clock;
        this.ttlMillis = ttlMillis;
        this.capacity = capacity;
        entries = new ArrayList<Token>(capacity);
    }

    /**
     * Pending registration ONLY. The caller must already have verified an actual
     * fresh, non-reprocess request path, never just takePicture dispatch. This
     * policy does not implement or prove that event source. No mode filtering.
     */
    synchronized Token newRequest(Object device, Object session, Object request, boolean reprocess) {
        long now = now();
        if (now < 0 || reprocess || device == null || session == null || request == null) return null;
        for (Token t : entries) {
            if (t.request.get() == request) { invalidate(t); return null; }
        }
        // Dead weak tombstones still protect live requests/owners. Saturation
        // refuses new evidence instead of evicting it and allowing a replay.
        if (entries.size() >= capacity) return null;
        Token t = new Token(this, now, device, session, request);
        entries.add(t);
        return t;
    }

    /** Requires the exact device/session/request AND real sensor-start tuple. */
    synchronized boolean captureStarted(Token t, Object device, Object session, Object request,
                                        long sensorTimestamp, long frameNumber) {
        if (now() < 0 || !require(t, Token.REQUESTED, device, session, request)) return false;
        if (sensorTimestamp <= 0 || frameNumber < 0) { invalidate(t); return false; }
        t.hasStart = true;
        t.sensorTimestamp = sensorTimestamp;
        t.frameNumber = frameNumber;
        for (Token other : entries) {
            if (other != t && other.hasStart && other.device.get() == device
                    && other.session.get() == session && other.sensorTimestamp == sensorTimestamp
                    && other.frameNumber == frameNumber) {
                invalidate(other); invalidate(t); return false;
            }
        }
        t.stage = Token.STARTED;
        return true;
    }

    /**
     * Requires a separately verified exact request/frame to APS identity bridge.
     * The identity is opaque: do not derive it from, or order it against, any clock.
     * A numeric match or an APS callback on its own cannot supply this evidence.
     */
    synchronized boolean linkApsIdentity(Token t, Object device, Object session, Object request,
                                         long sensorTimestamp, long frameNumber, long apsIdentity) {
        if (now() < 0 || !require(t, Token.STARTED, device, session, request)) return false;
        if (sensorTimestamp != t.sensorTimestamp || frameNumber != t.frameNumber || apsIdentity <= 0) {
            invalidate(t); return false;
        }
        t.hasIdentity = true;
        t.apsIdentity = apsIdentity;
        for (Token other : entries) {
            // Owner identity is global; do not disambiguate collisions by mode/device guesses.
            if (other != t && other.hasIdentity && other.apsIdentity == apsIdentity) {
                invalidate(other); invalidate(t); return false;
            }
        }
        t.stage = Token.LINKED;
        return true;
    }

    /** Explicit proven owner provenance event, NOT a storage lookup by CameraPicture.S. */
    synchronized boolean bindOwner(Token t, Object owner, long apsIdentity) {
        if (now() < 0 || !owns(t)) return false;
        if (t.stage != Token.LINKED || owner == null || apsIdentity != t.apsIdentity) {
            invalidate(t); return false;
        }
        for (Token other : entries) {
            if (other != t && other.owner != null && other.owner.get() == owner) {
                invalidate(other); invalidate(t); return false;
            }
        }
        t.owner = new WeakReference<Object>(owner);
        t.stage = Token.OWNED;
        t.releaseStrongReferences();
        return true;
    }

    /**
     * Repeatable only for the exact bound owner (save/update), without renewing
     * TTL. Unknown/rebuilt owners remain ineligible even with identical identity.
     */
    synchronized boolean ownerEligible(Object owner, long observedApsIdentity) {
        if (now() < 0 || owner == null) return false;
        for (Token t : entries) {
            if (t.owner != null && t.owner.get() == owner) {
                if (t.stage == Token.OWNED && t.apsIdentity == observedApsIdentity) return true;
                invalidate(t); return false;
            }
        }
        return false;
    }

    synchronized boolean captureFailed(Token t, Object device, Object session, Object request) {
        if (now() < 0 || !owns(t)) return false;
        boolean matching = matches(t, device, session, request) && t.stage != Token.DEAD;
        invalidate(t); // Contradictory failure evidence cannot keep this local token eligible.
        return matching;
    }

    synchronized int retainedCount() { return now() < 0 ? 0 : entries.size(); }
    synchronized boolean revoked() { return stopped; }

    private boolean owns(Token t) { return t != null && t.policy == this; }
    private boolean require(Token t, int stage, Object device, Object session, Object request) {
        if (!owns(t)) return false;
        if (t.stage != stage || !matches(t, device, session, request)) { invalidate(t); return false; }
        return true;
    }
    private boolean matches(Token t, Object device, Object session, Object request) {
        return device != null && session != null && request != null && t.device.get() == device
                && t.session.get() == session && t.request.get() == request;
    }
    private void invalidate(Token t) { t.stage = Token.DEAD; t.releaseStrongReferences(); }

    /** Lazy expiry on every operation; no timers or camera side effects. */
    private long now() {
        if (stopped) return -1;
        if (polling) { stop(); return -1; }
        polling = true;
        try {
            if (!gate.enabled() || stopped) { stop(); return -1; }
            long now = clock.elapsedMillis();
            if (stopped || now < 0 || (lastNow >= 0 && now < lastNow)) { stop(); return -1; }
            lastNow = now;
            for (int i = entries.size() - 1; i >= 0; i--) {
                Token t = entries.get(i);
                // Nonnegative monotonic subtraction, not an overflowing deadline addition.
                if (t.stage != Token.DEAD && now - t.born >= ttlMillis) invalidate(t);
                if (t.stage == Token.DEAD && t.request.get() == null
                        && (t.owner == null || t.owner.get() == null)) entries.remove(i);
            }
            return now;
        } catch (Throwable failure) {
            stop(); return -1; // Never substitute default time or default-on qualification.
        } finally { polling = false; }
    }

    private void stop() {
        stopped = true;
        for (Token t : entries) invalidate(t);
        entries.clear();
    }
}
