#ifndef XPAN_GAIN_FRAME_CORE_H
#define XPAN_GAIN_FRAME_CORE_H

#include <stddef.h>
#include <stdint.h>

/* Local prototype: NOT IN APK / NOT NATIVE GAINMAP GENERATOR.
 * Byte-zero framing only; no gain generation, resampling, gamma or range changes.
 * Neither Y8 nor RGB channel values are used to infer geometry or neutrality.
 */
#define XPAN_GAINFRAME_MAX_DIMENSION UINT32_C(16384)
#define XPAN_GAINFRAME_MAX_PLANE_BYTES ((size_t)128 * 1024 * 1024)

typedef enum XpanGainFrameFormat {
    XPAN_GAINFRAME_Y8 = 0x20203859,
    XPAN_GAINFRAME_RGB888 = 3
} XpanGainFrameFormat;

typedef enum XpanGainFrameStatus {
    XPAN_GAINFRAME_OK = 0,
    XPAN_GAINFRAME_ARGUMENT = -1,
    XPAN_GAINFRAME_GEOMETRY = -2,
    XPAN_GAINFRAME_FORMAT = -3,
    XPAN_GAINFRAME_LAYOUT = -4,
    XPAN_GAINFRAME_LIMIT = -5,
    XPAN_GAINFRAME_ALIAS = -6,
    XPAN_GAINFRAME_PRODUCER = -7,
    XPAN_GAINFRAME_USAGE = -8,
    XPAN_GAINFRAME_ALLOCATE = -9,
    XPAN_GAINFRAME_LOCK = -10,
    XPAN_GAINFRAME_UNLOCK = -11
} XpanGainFrameStatus;

/* Half-open content ROI [left,right) x [top,bottom), in source gain pixels.
 * It must be nonempty and inside the canvas. Odd widths/coordinates are legal.
 */
typedef struct XpanGainFrameRoi {
    int32_t left, top, right, bottom;
} XpanGainFrameRoi;

/* Capacity is a caller-confirmed accessible byte span beginning at data.
 * Strides are BYTES, not pixels. Padding is never read/copied/cleared.
 * A span ending at the last active byte (no last-row padding) is sufficient.
 */
typedef struct XpanGainFrameConstPlane {
    const uint8_t* data;
    uint32_t planeCount;
    size_t pixelStride, rowStride, memoryCapacity;
} XpanGainFrameConstPlane;

typedef struct XpanGainFramePlane {
    uint8_t* data;
    uint32_t planeCount;
    size_t pixelStride, rowStride, memoryCapacity;
} XpanGainFramePlane;

#ifdef __cplusplus
namespace xpan_gainframe {
size_t bytes_per_pixel(uint32_t format);
XpanGainFrameStatus validate_geometry(uint32_t width, uint32_t height,
                                     uint32_t format, const XpanGainFrameRoi& roi);
XpanGainFrameStatus plane_span(uint32_t width, uint32_t height, uint32_t format,
                              uint32_t planeCount, size_t pixelStride,
                              size_t rowStride, size_t& activeSpan);
/* Validates both planes and disjoint spans before the first write.
 * On failure, destination is unchanged. Source is const and always untouched.
 */
XpanGainFrameStatus copy_neutral_frame(const XpanGainFrameConstPlane& source,
                                      const XpanGainFramePlane& target,
                                      uint32_t width, uint32_t height,
                                      uint32_t format, const XpanGainFrameRoi& roi);
}
#endif
#endif
