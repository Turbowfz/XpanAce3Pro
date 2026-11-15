#include "gain_frame_native.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <new>

static unsigned checks = 0;
#define CHECK(x) do { ++checks; if (!(x)) { \
    fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); exit(2); } } while (false)
static void pass(const char* name) { printf("PASS %s\n", name); }
static bool empty_desc(const AHardwareBuffer_Desc& d) {
    return !d.width && !d.height && !d.layers && !d.format && !d.usage &&
           !d.stride && !d.rfu0 && !d.rfu1; // Padding bytes are not part of the API contract.
}

static void pixels(uint32_t format, const char* name) {
    const uint32_t w = 5, h = 4;
    const size_t bpp = xpan_gainframe::bytes_per_pixel(format), row = w * bpp;
    const size_t ss = row + 7, ds = row + 11;
    const size_t sc = ss * (h - 1) + row, dc = ds * (h - 1) + row;
    uint8_t *sb = static_cast<uint8_t*>(malloc(sc + 16));
    uint8_t *db = static_cast<uint8_t*>(malloc(dc + 16));
    uint8_t *snapshot = static_cast<uint8_t*>(malloc(sc + 16));
    CHECK(sb && db && snapshot);
    memset(sb, 0x81, sc + 16); memset(db, 0xa5, dc + 16);
    for (uint32_t y = 0; y < h; ++y)
        for (size_t x = 0; x < row; ++x) sb[8 + y * ss + x] = static_cast<uint8_t>(1 + y * row + x);
    sb[8 + ss + bpp] = 0; // Genuine zero content must survive unchanged.
    memcpy(snapshot, sb, sc + 16);
    XpanGainFrameConstPlane src{sb + 8, 1, bpp, ss, sc};
    XpanGainFramePlane dst{db + 8, 1, bpp, ds, dc};
    const XpanGainFrameRoi rois[] = {{1,1,3,3}, {0,0,5,4}, {4,0,5,4}, {0,3,1,4}};
    for (const auto& r : rois) {
        memset(db, 0xa5, dc + 16);
        CHECK(xpan_gainframe::copy_neutral_frame(src, dst, w, h, format, r) == XPAN_GAINFRAME_OK);
        for (uint32_t y = 0; y < h; ++y) {
            for (size_t x = 0; x < row; ++x) {
                const bool inside = y >= static_cast<uint32_t>(r.top) && y < static_cast<uint32_t>(r.bottom) &&
                    x / bpp >= static_cast<size_t>(r.left) && x / bpp < static_cast<size_t>(r.right);
                CHECK(dst.data[y * ds + x] == (inside ? src.data[y * ss + x] : 0));
            }
            if (y + 1 < h) for (size_t x = row; x < ds; ++x) CHECK(dst.data[y * ds + x] == 0xa5);
        }
        for (size_t i = 0; i < 8; ++i) CHECK(db[i] == 0xa5 && db[8 + dc + i] == 0xa5);
        CHECK(memcmp(sb, snapshot, sc + 16) == 0);
    }
    free(snapshot); free(db); free(sb); pass(name);
}

static void core_failures() {
    uint8_t source[128], target[128]; memset(source, 0x73, sizeof(source)); memset(target, 0xa5, sizeof(target));
    const XpanGainFrameConstPlane validSrc{source, 1, 3, 19, 72};
    const XpanGainFramePlane validDst{target, 1, 3, 21, 78};
    const XpanGainFrameRoi validRoi{1,1,4,3};
    const XpanGainFrameRoi badRois[] = {{-1,0,1,1}, {0,-1,1,1}, {1,1,1,2}, {0,2,1,2},
                                      {3,0,2,1}, {0,0,6,1}, {0,0,1,5}, {0,0,INT32_MAX,1}};
    for (const auto& r : badRois) CHECK(xpan_gainframe::copy_neutral_frame(validSrc, validDst, 5,4,3,r) == XPAN_GAINFRAME_GEOMETRY);
    for (unsigned i = 0; i < 16; ++i) {
        auto s = validSrc; auto d = validDst; XpanGainFrameStatus expected = XPAN_GAINFRAME_LAYOUT;
        switch (i) {
        case 0: s.data = nullptr; expected = XPAN_GAINFRAME_ARGUMENT; break;
        case 1: d.data = nullptr; expected = XPAN_GAINFRAME_ARGUMENT; break;
        case 2: s.planeCount = 0; break; case 3: d.planeCount = 2; break;
        case 4: s.pixelStride = 1; break; case 5: d.pixelStride = 4; break;
        case 6: s.rowStride = 14; break; case 7: d.rowStride = 14; break;
        case 8: --s.memoryCapacity; break; case 9: --d.memoryCapacity; break;
        case 10: s.rowStride = SIZE_MAX; expected = XPAN_GAINFRAME_LIMIT; break;
        case 11: d.rowStride = XPAN_GAINFRAME_MAX_PLANE_BYTES; expected = XPAN_GAINFRAME_LIMIT; break;
        case 12: s.memoryCapacity = XPAN_GAINFRAME_MAX_PLANE_BYTES + 1; expected = XPAN_GAINFRAME_LIMIT; break;
        case 13: d.memoryCapacity = SIZE_MAX; expected = XPAN_GAINFRAME_LIMIT; break;
        case 14: d.data = source + 1; expected = XPAN_GAINFRAME_ALIAS; break;
        case 15: s.data = reinterpret_cast<const uint8_t*>(UINTPTR_MAX - 4); expected = XPAN_GAINFRAME_LIMIT; break;
        }
        CHECK(xpan_gainframe::copy_neutral_frame(s,d,5,4,3,validRoi) == expected);
    }
    CHECK(xpan_gainframe::copy_neutral_frame(validSrc,validDst,5,4,0x36,validRoi) == XPAN_GAINFRAME_FORMAT);
    CHECK(xpan_gainframe::validate_geometry(0,4,3,validRoi) == XPAN_GAINFRAME_GEOMETRY);
    CHECK(xpan_gainframe::validate_geometry(5,0,3,validRoi) == XPAN_GAINFRAME_GEOMETRY);
    CHECK(xpan_gainframe::validate_geometry(16385,4,3,validRoi) == XPAN_GAINFRAME_LIMIT);
    CHECK(xpan_gainframe::validate_geometry(16384,16384,3,validRoi) == XPAN_GAINFRAME_LIMIT);
    size_t span = 0;
    CHECK(xpan_gainframe::plane_span(16384,8192,XPAN_GAINFRAME_Y8,1,1,16384,span) == XPAN_GAINFRAME_OK);
    CHECK(span == XPAN_GAINFRAME_MAX_PLANE_BYTES);
    CHECK(xpan_gainframe::plane_span(16384,8193,XPAN_GAINFRAME_Y8,1,1,16384,span) == XPAN_GAINFRAME_LIMIT);
    CHECK(xpan_gainframe::plane_span(1,1,3,1,3,SIZE_MAX,span) == XPAN_GAINFRAME_LIMIT);
    for (uint8_t b : target) CHECK(b == 0xa5);
    for (uint8_t b : source) CHECK(b == 0x73);
    pass("core_fail_closed_roi_capacity_overflow_alias_budget");
}

struct AHardwareBuffer {
    AHardwareBuffer_Desc desc{}; AHardwareBuffer_Planes planes{};
    uint8_t* allocation = nullptr; size_t capacity = 0;
    int refs = 1, depth = 0, locks = 0, unlocks = 0;
    int lockError = 0, unlockError = 0; bool source = false;
};
enum Fault { None, AllocError, AllocNull, AllocErrorObject, AllocAlias, TargetShape, TargetHeight,
    TargetLayer, TargetFormat, TargetUsage, TargetLock, TargetPlanes0, TargetPlanes2, TargetPlanes5,
    TargetNull, TargetPixel, TargetRow, TargetHugeRow, TargetAliasData, TargetUnlock, BothUnlock };
static struct Mock {
    Fault fault = None; AHardwareBuffer* source = nullptr;
    int live = 0, acquires = 0, releases = 0, allocations = 0, targetUnlocks = 0, maxDepth = 0;
    bool reenter = false, nestedPassed = false;
} mock;

static AHardwareBuffer* make_buffer(AHardwareBuffer_Desc d, bool isSource) {
    void* storage = malloc(sizeof(AHardwareBuffer)); CHECK(storage);
    auto* b = new (storage) AHardwareBuffer{};
    b->desc = d; b->source = isSource;
    const uint32_t bpp = static_cast<uint32_t>(xpan_gainframe::bytes_per_pixel(d.format));
    b->desc.stride = d.width + (isSource ? 2 : 4);
    const uint32_t row = b->desc.stride * bpp;
    b->capacity = static_cast<size_t>(row) * d.height;
    b->allocation = static_cast<uint8_t*>(malloc(b->capacity + 16)); CHECK(b->allocation);
    memset(b->allocation, isSource ? 0x73 : 0xa5, b->capacity + 16);
    b->planes.planeCount = 1; b->planes.planes[0] = {b->allocation + 8, bpp, row};
    ++mock.live; return b;
}
extern "C" void AHardwareBuffer_acquire(AHardwareBuffer* b) { CHECK(b && b->refs > 0); ++b->refs; ++mock.acquires; }
extern "C" void AHardwareBuffer_release(AHardwareBuffer* b) {
    CHECK(b && b->refs > 0); ++mock.releases;
    if (!--b->refs) { CHECK(b->depth == 0); free(b->allocation); b->~AHardwareBuffer(); free(b); --mock.live; }
}
extern "C" void AHardwareBuffer_describe(const AHardwareBuffer* b, AHardwareBuffer_Desc* d) {
    CHECK(b && d); if (b->source) CHECK(b->refs >= 2); *d = b->desc;
}
extern "C" int AHardwareBuffer_allocate(const AHardwareBuffer_Desc* d, AHardwareBuffer** out) {
    CHECK(d && out && !*out && d->rfu0 == 0 && d->rfu1 == 0 && d->stride == 0);
    CHECK(d->layers == 1 && !(d->usage & AHARDWAREBUFFER_USAGE_GPU_FRAMEBUFFER)); ++mock.allocations;
    if (mock.fault == AllocError) return -12;
    if (mock.fault == AllocNull) return 0;
    if (mock.fault == AllocAlias) { *out = mock.source; AHardwareBuffer_acquire(*out); return 0; }
    *out = make_buffer(*d, false); auto* b = *out;
    switch (mock.fault) {
    case TargetShape: ++b->desc.width; break; case TargetHeight: ++b->desc.height; break;
    case TargetLayer: b->desc.layers = 2; break; case TargetFormat: b->desc.format = 0x36; break;
    case TargetUsage: b->desc.usage |= AHARDWAREBUFFER_USAGE_GPU_FRAMEBUFFER; break;
    case TargetLock: b->lockError = -22; break;
    case TargetPlanes0: b->planes.planeCount = 0; break; case TargetPlanes2: b->planes.planeCount = 2; break;
    case TargetPlanes5: b->planes.planeCount = 5; break; case TargetNull: b->planes.planes[0].data = nullptr; break;
    case TargetPixel: b->planes.planes[0].pixelStride = 4; break;
    case TargetRow: b->planes.planes[0].rowStride = 1; break;
    case TargetHugeRow: b->planes.planes[0].rowStride = UINT32_MAX; break;
    case TargetAliasData: b->planes.planes[0] = mock.source->planes.planes[0]; break;
    case TargetUnlock: case BothUnlock: b->unlockError = -5; break;
    default: break;
    }
    return mock.fault == AllocErrorObject ? -12 : 0;
}
extern "C" int AHardwareBuffer_lockPlanes(AHardwareBuffer* b, uint64_t usage, int32_t fence,
                                          const ARect* rect, AHardwareBuffer_Planes* out) {
    CHECK(b && out && fence == -1 && !rect); CHECK(b->refs >= (b->source ? 2 : 1));
    CHECK(usage == (b->source ? AHARDWAREBUFFER_USAGE_CPU_READ_RARELY : AHARDWAREBUFFER_USAGE_CPU_WRITE_RARELY));
    ++b->locks; *out = b->planes; // Bogus nonempty planes on failure must not be used.
    if (b->lockError) return b->lockError;
    CHECK(b->source || b->depth == 0); ++b->depth;
    if (b->depth > mock.maxDepth) mock.maxDepth = b->depth;
    if (b->source && mock.reenter) {
        mock.reenter = false; AHardwareBuffer* nested = nullptr; AHardwareBuffer_Desc d{};
        CHECK(xpan_gainframe_clone_neutral(b,5,4,{1,1,3,3},true,false,&nested,&d) == XPAN_GAINFRAME_OK);
        CHECK(nested && nested != b && nested->refs == 1); AHardwareBuffer_release(nested); mock.nestedPassed = true;
    }
    return 0;
}
extern "C" int AHardwareBuffer_unlock(AHardwareBuffer* b, int32_t* fence) {
    CHECK(b && !fence && b->depth > 0); --b->depth; ++b->unlocks;
    if (!b->source) ++mock.targetUnlocks;
    // Error injection still records/finishes the attempt; real error lock state is driver-defined.
    return b->unlockError;
}

static AHardwareBuffer* prepare(uint32_t format = 3) {
    CHECK(mock.live == 0); mock = {};
    AHardwareBuffer_Desc d{}; d.width=5; d.height=4; d.layers=1; d.format=format;
    d.usage = AHARDWAREBUFFER_USAGE_CPU_READ_OFTEN | AHARDWAREBUFFER_USAGE_CPU_WRITE_OFTEN |
              AHARDWAREBUFFER_USAGE_GPU_FRAMEBUFFER | (UINT64_C(1) << 48);
    mock.source = make_buffer(d, true); return mock.source;
}
static void unchanged(const AHardwareBuffer* s) {
    CHECK(s->refs == 1 && s->depth == 0 && mock.live == 1);
    for (size_t i=0; i<s->capacity+16; ++i) CHECK(s->allocation[i] == 0x73);
}
static void wrapper_success(uint32_t format, bool gpu, bool nested, const char* name) {
    auto* s = prepare(format); mock.reenter = nested;
    AHardwareBuffer* t = nullptr; AHardwareBuffer_Desc d{}; const XpanGainFrameRoi r{1,1,3,3};
    CHECK(xpan_gainframe_clone_neutral(s,5,4,r,true,gpu,&t,&d) == XPAN_GAINFRAME_OK);
    CHECK(t && t != s && t->refs == 1 && t->depth == 0 && t->locks == 1 && t->unlocks == 1);
    CHECK(d.width == 5 && d.height == 4 && d.layers == 1 && d.format == format && d.stride == t->desc.stride);
    CHECK(d.usage == (AHARDWAREBUFFER_USAGE_CPU_READ_RARELY | AHARDWAREBUFFER_USAGE_CPU_WRITE_RARELY |
                     (gpu ? AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE : 0)));
    const auto& p = t->planes.planes[0];
    for (uint32_t y=0; y<4; ++y) for (uint32_t x=0; x<p.rowStride; ++x)
        CHECK(static_cast<uint8_t*>(p.data)[y*p.rowStride+x] ==
              (x >= 5*p.pixelStride ? 0xa5 : (y>=1 && y<3 && x/p.pixelStride>=1 && x/p.pixelStride<3 ? 0x73 : 0)));
    for (size_t i=0; i<8; ++i) CHECK(t->allocation[i] == 0xa5 && t->allocation[8+t->capacity+i] == 0xa5);
    CHECK(mock.acquires == (nested ? 2 : 1) && mock.maxDepth == (nested ? 2 : 1));
    CHECK(s->locks == (nested ? 2 : 1) && s->unlocks == s->locks && (!nested || mock.nestedPassed));
    AHardwareBuffer_release(t); unchanged(s); AHardwareBuffer_release(s); CHECK(mock.live == 0); pass(name);
}
static void failed_call(AHardwareBuffer* s, int32_t w, int32_t h, XpanGainFrameRoi r,
                        bool completed, XpanGainFrameStatus expected) {
    AHardwareBuffer* t = s; AHardwareBuffer_Desc d{}; d.width = 99;
    CHECK(xpan_gainframe_clone_neutral(s,w,h,r,completed,false,&t,&d) == expected);
    CHECK(!t && empty_desc(d)); unchanged(s);
}
static void wrapper_guards() {
    const XpanGainFrameRoi r{1,1,3,3};
    for (unsigned i=0; i<26; ++i) {
        auto* s = prepare(); int32_t w=5,h=4; auto roi=r; bool complete=true;
        XpanGainFrameStatus expected=XPAN_GAINFRAME_GEOMETRY;
        switch(i) {
        case 0: complete=false; expected=XPAN_GAINFRAME_PRODUCER; break;
        case 1: w=-1; break; case 2: h=0; break; case 3: w=6; break; case 4: h=5; break;
        case 5: s->desc.layers=2; break; case 6: s->desc.layers=0; break;
        case 7: roi.left=-1; break; case 8: roi.right=roi.left; break; case 9: roi.bottom=5; break;
        case 10: s->desc.format=0x36; expected=XPAN_GAINFRAME_FORMAT; break;
        case 11: s->desc.format=0x38; expected=XPAN_GAINFRAME_FORMAT; break;
        case 12: s->desc.usage=0; expected=XPAN_GAINFRAME_USAGE; break;
        case 13: s->desc.usage|=AHARDWAREBUFFER_USAGE_PROTECTED_CONTENT; expected=XPAN_GAINFRAME_USAGE; break;
        case 14: s->desc.usage=1; expected=XPAN_GAINFRAME_USAGE; break;
        case 15: s->desc.width=16385; w=16385; expected=XPAN_GAINFRAME_LIMIT; break;
        case 16: s->desc.width=16384; s->desc.height=16384; w=h=16384; expected=XPAN_GAINFRAME_LIMIT; break;
        case 17: s->lockError=-22; expected=XPAN_GAINFRAME_LOCK; break;
        case 18: s->planes.planeCount=2; expected=XPAN_GAINFRAME_LAYOUT; break;
        case 19: s->planes.planes[0].data=nullptr; expected=XPAN_GAINFRAME_ARGUMENT; break;
        case 20: s->planes.planes[0].pixelStride=1; expected=XPAN_GAINFRAME_LAYOUT; break;
        case 21: s->planes.planes[0].rowStride=UINT32_MAX; expected=XPAN_GAINFRAME_LIMIT; break;
        case 22: s->planes.planeCount=0; expected=XPAN_GAINFRAME_LAYOUT; break;
        case 23: s->planes.planeCount=5; expected=XPAN_GAINFRAME_LAYOUT; break;
        case 24: s->planes.planes[0].rowStride=14; expected=XPAN_GAINFRAME_LAYOUT; break;
        case 25: s->planes.planes[0].pixelStride=4; expected=XPAN_GAINFRAME_LAYOUT; break;
        }
        failed_call(s,w,h,roi,complete,expected); CHECK(mock.allocations == 0);
        CHECK(s->unlocks == (i>=18 ? 1 : 0)); CHECK(mock.acquires == mock.releases);
        AHardwareBuffer_release(s);
    }
    auto* s=prepare(); AHardwareBuffer* t=s; AHardwareBuffer_Desc d{};
    CHECK(xpan_gainframe_clone_neutral(nullptr,5,4,r,true,false,&t,&d) == XPAN_GAINFRAME_ARGUMENT && !t);
    CHECK(xpan_gainframe_clone_neutral(s,5,4,r,true,false,nullptr,&d) == XPAN_GAINFRAME_ARGUMENT);
    CHECK(xpan_gainframe_clone_neutral(s,5,4,r,true,false,&t,nullptr) == XPAN_GAINFRAME_ARGUMENT && !t);
    unchanged(s); CHECK(mock.acquires == 0); AHardwareBuffer_release(s);
    pass("wrapper_null_format_layer_roi_fence_dimensions_usage_source_layout");
}
static void wrapper_faults() {
    const char* names[] = {"none", "allocate_error", "allocate_null", "allocate_error_object", "allocate_alias",
        "target_shape", "target_height", "target_layer", "target_format", "target_usage", "target_lock",
        "target_planes0", "target_planes2", "target_planes5", "target_null", "target_pixel_stride", "target_row_stride",
        "target_row_budget", "target_memory_alias", "target_unlock_failure", "both_unlock_failures"};
    for (int i=AllocError; i<=BothUnlock; ++i) {
        auto* s=prepare(); mock.fault=static_cast<Fault>(i); if(i==BothUnlock) s->unlockError=-5;
        const XpanGainFrameStatus expected = i<=AllocErrorObject ? XPAN_GAINFRAME_ALLOCATE :
            i==AllocAlias || i==TargetAliasData ? XPAN_GAINFRAME_ALIAS :
            i>=TargetShape && i<=TargetFormat ? XPAN_GAINFRAME_GEOMETRY :
            i==TargetUsage ? XPAN_GAINFRAME_USAGE : i==TargetLock ? XPAN_GAINFRAME_LOCK :
            i==TargetNull ? XPAN_GAINFRAME_ARGUMENT : i==TargetHugeRow ? XPAN_GAINFRAME_LIMIT :
            i>=TargetUnlock ? XPAN_GAINFRAME_UNLOCK : XPAN_GAINFRAME_LAYOUT;
        failed_call(s,5,4,{1,1,3,3},true,expected);
        CHECK(s->locks==1 && s->unlocks==1 && mock.allocations==1);
        CHECK(mock.targetUnlocks == (i>=TargetPlanes0 ? 1 : 0));
        AHardwareBuffer_release(s); CHECK(mock.live==0); pass(names[i]);
    }
    auto* s=prepare(); s->unlockError=-5; failed_call(s,5,4,{1,1,3,3},true,XPAN_GAINFRAME_UNLOCK);
    CHECK(mock.targetUnlocks==1 && s->unlocks==1); AHardwareBuffer_release(s); pass("source_unlock_failure_discards_completed_target");
    s=prepare(); s->unlockError=-5; mock.fault=AllocError;
    failed_call(s,5,4,{1,1,3,3},true,XPAN_GAINFRAME_UNLOCK); AHardwareBuffer_release(s); pass("cleanup_unlock_failure_overrides_allocate_failure");
}
int main() {
    pixels(3,"core_rgb888_full_width_5_roi_padding_guards");
    pixels(XPAN_GAINFRAME_Y8,"core_y8_odd_width_roi_padding_guards"); core_failures();
    wrapper_success(3,false,false,"wrapper_rgb888_independent_canvas_cpu_only");
    wrapper_success(XPAN_GAINFRAME_Y8,true,false,"wrapper_y8_odd_width_explicit_gpu_sampling");
    wrapper_success(3,false,true,"wrapper_nested_reentry_balanced_borrowed_ownership");
    wrapper_guards(); wrapper_faults(); CHECK(mock.live==0);
    printf("CHECKS %u\n",checks); pass("all_host_tests"); return 0;
}
