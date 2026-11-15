#include "gain_frame_native.h"

namespace {
/* Owns exactly one reference, not the caller's borrowed reference. */
struct BufferRef {
    AHardwareBuffer* buffer = nullptr;
    ~BufferRef() { if (buffer) AHardwareBuffer_release(buffer); }
    AHardwareBuffer* take() { AHardwareBuffer* b = buffer; buffer = nullptr; return b; }
};
struct CpuLock {
    AHardwareBuffer* buffer;
    bool locked = false;
    explicit CpuLock(AHardwareBuffer* b) : buffer(b) {}
    ~CpuLock() { if (locked) (void)finish(); }
    int finish() {
        if (!locked) return 0;
        locked = false;  // Attempt exactly once; a failed unlock has no safe retry contract.
        return AHardwareBuffer_unlock(buffer, nullptr);
    }
    int begin(uint64_t usage, AHardwareBuffer_Planes& planes) {
        const int result = AHardwareBuffer_lockPlanes(buffer, usage, -1, nullptr, &planes);
        locked = result == 0;
        return result;
    }
};

XpanGainFrameStatus locked_span(const AHardwareBuffer_Desc& d,
                                const AHardwareBuffer_Planes& p, size_t& span) {
    if (p.planeCount != 1) return XPAN_GAINFRAME_LAYOUT;
    if (!p.planes[0].data) return XPAN_GAINFRAME_ARGUMENT;
    return xpan_gainframe::plane_span(d.width, d.height, d.format, p.planeCount,
                                      p.planes[0].pixelStride, p.planes[0].rowStride, span);
}
}

extern "C" XPAN_GAINFRAME_EXPORT XpanGainFrameStatus xpan_gainframe_clone_neutral(
    AHardwareBuffer* source, int32_t expectedW, int32_t expectedH,
    XpanGainFrameRoi roi, bool producerCompleteConfirmed, bool gpuSampled,
    AHardwareBuffer** outTarget, AHardwareBuffer_Desc* outDescription) {
    if (outTarget) *outTarget = nullptr;
    if (outDescription) *outDescription = {};
    if (!source || !outTarget || !outDescription) return XPAN_GAINFRAME_ARGUMENT;
    if (!producerCompleteConfirmed) return XPAN_GAINFRAME_PRODUCER;
    if (expectedW <= 0 || expectedH <= 0) return XPAN_GAINFRAME_GEOMETRY;
    BufferRef src;
    AHardwareBuffer_acquire(source);
    src.buffer = source;
    AHardwareBuffer_Desc d{};
    AHardwareBuffer_describe(source, &d);
    if (d.layers != 1 || d.width != static_cast<uint32_t>(expectedW) ||
        d.height != static_cast<uint32_t>(expectedH)) return XPAN_GAINFRAME_GEOMETRY;
    XpanGainFrameStatus status = xpan_gainframe::validate_geometry(d.width, d.height, d.format, roi);
    if (status != XPAN_GAINFRAME_OK) return status;
    const uint64_t readMode = d.usage & AHARDWAREBUFFER_USAGE_CPU_READ_MASK;
    if ((readMode != AHARDWAREBUFFER_USAGE_CPU_READ_RARELY &&
         readMode != AHARDWAREBUFFER_USAGE_CPU_READ_OFTEN) ||
        (d.usage & AHARDWAREBUFFER_USAGE_PROTECTED_CONTENT)) return XPAN_GAINFRAME_USAGE;
    AHardwareBuffer_Planes sp{}, tp{};
    CpuLock srcLock(source);
    if (srcLock.begin(AHARDWAREBUFFER_USAGE_CPU_READ_RARELY, sp)) return XPAN_GAINFRAME_LOCK;
    BufferRef target;
    size_t srcSpan = 0;
    AHardwareBuffer_Desc actual{};
    const uint64_t usage = AHARDWAREBUFFER_USAGE_CPU_READ_RARELY |
                          AHARDWAREBUFFER_USAGE_CPU_WRITE_RARELY |
                          (gpuSampled ? AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE : 0);
    /* No early returns while locked: both unlock results are always checked. */
    do {
        status = locked_span(d, sp, srcSpan);
        if (status != XPAN_GAINFRAME_OK) break;
        AHardwareBuffer_Desc wanted{};
        wanted.width = d.width; wanted.height = d.height;
        wanted.layers = 1; wanted.format = d.format; wanted.usage = usage;
        const int allocated = AHardwareBuffer_allocate(&wanted, &target.buffer);
        if (allocated || !target.buffer) { status = XPAN_GAINFRAME_ALLOCATE; break; }
        if (target.buffer == source) { status = XPAN_GAINFRAME_ALIAS; break; }
        AHardwareBuffer_describe(target.buffer, &actual);
        if (actual.width != d.width || actual.height != d.height || actual.layers != 1 ||
            actual.format != d.format) { status = XPAN_GAINFRAME_GEOMETRY; break; }
        if (actual.usage != usage) { status = XPAN_GAINFRAME_USAGE; break; }
        CpuLock targetLock(target.buffer);
        if (targetLock.begin(AHARDWAREBUFFER_USAGE_CPU_WRITE_RARELY, tp)) {
            status = XPAN_GAINFRAME_LOCK; break;
        }
        size_t targetSpan = 0;
        status = locked_span(actual, tp, targetSpan);
        if (status == XPAN_GAINFRAME_OK) {
            const auto& s = sp.planes[0];
            const auto& t = tp.planes[0];
            const XpanGainFrameConstPlane srcPlane{
                static_cast<const uint8_t*>(s.data), sp.planeCount, s.pixelStride, s.rowStride, srcSpan};
            const XpanGainFramePlane targetPlane{
                static_cast<uint8_t*>(t.data), tp.planeCount, t.pixelStride, t.rowStride, targetSpan};
            status = xpan_gainframe::copy_neutral_frame(srcPlane, targetPlane, d.width, d.height, d.format, roi);
        }
        if (targetLock.finish()) status = XPAN_GAINFRAME_UNLOCK;
    } while (false);
    if (srcLock.finish()) status = XPAN_GAINFRAME_UNLOCK;
    if (status != XPAN_GAINFRAME_OK) return status;
    *outDescription = actual;
    *outTarget = target.take();
    return XPAN_GAINFRAME_OK;
}
