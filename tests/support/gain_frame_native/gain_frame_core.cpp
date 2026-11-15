#include "gain_frame_core.h"
#include <string.h>

namespace xpan_gainframe {
namespace {
bool multiply(size_t a, size_t b, size_t& out) {
    if (b && a > SIZE_MAX / b) return false;
    out = a * b;
    return true;
}
}

size_t bytes_per_pixel(uint32_t format) {
    return format == XPAN_GAINFRAME_Y8 ? 1 :
           format == XPAN_GAINFRAME_RGB888 ? 3 : 0;
}

XpanGainFrameStatus validate_geometry(uint32_t w, uint32_t h, uint32_t format,
                                     const XpanGainFrameRoi& roi) {
    const size_t bpp = bytes_per_pixel(format);
    if (!bpp) return XPAN_GAINFRAME_FORMAT;
    if (!w || !h) return XPAN_GAINFRAME_GEOMETRY;
    if (w > XPAN_GAINFRAME_MAX_DIMENSION || h > XPAN_GAINFRAME_MAX_DIMENSION)
        return XPAN_GAINFRAME_LIMIT;
    if (roi.left < 0 || roi.top < 0 || roi.right <= roi.left || roi.bottom <= roi.top ||
        static_cast<uint32_t>(roi.right) > w || static_cast<uint32_t>(roi.bottom) > h)
        return XPAN_GAINFRAME_GEOMETRY;
    size_t row = 0, bytes = 0;
    if (!multiply(w, bpp, row) || !multiply(row, h, bytes) ||
        bytes > XPAN_GAINFRAME_MAX_PLANE_BYTES) return XPAN_GAINFRAME_LIMIT;
    return XPAN_GAINFRAME_OK;
}

XpanGainFrameStatus plane_span(uint32_t w, uint32_t h, uint32_t format,
                              uint32_t count, size_t pixelStride,
                              size_t rowStride, size_t& span) {
    span = 0;
    const size_t bpp = bytes_per_pixel(format);
    if (!bpp) return XPAN_GAINFRAME_FORMAT;
    if (!w || !h || w > XPAN_GAINFRAME_MAX_DIMENSION || h > XPAN_GAINFRAME_MAX_DIMENSION)
        return XPAN_GAINFRAME_LIMIT;
    if (count != 1 || pixelStride != bpp) return XPAN_GAINFRAME_LAYOUT;
    size_t row = 0, fullRows = 0, lastRow = 0;
    if (!multiply(w, pixelStride, row)) return XPAN_GAINFRAME_LIMIT;
    if (rowStride < row) return XPAN_GAINFRAME_LAYOUT;
    if (!multiply(rowStride, h, fullRows) || fullRows > XPAN_GAINFRAME_MAX_PLANE_BYTES ||
        !multiply(rowStride, h - 1, lastRow) || lastRow > SIZE_MAX - row)
        return XPAN_GAINFRAME_LIMIT;
    span = lastRow + row;
    return XPAN_GAINFRAME_OK;
}

XpanGainFrameStatus copy_neutral_frame(const XpanGainFrameConstPlane& src,
                                      const XpanGainFramePlane& dst,
                                      uint32_t w, uint32_t h, uint32_t format,
                                      const XpanGainFrameRoi& roi) {
    XpanGainFrameStatus status = validate_geometry(w, h, format, roi);
    if (status != XPAN_GAINFRAME_OK) return status;
    if (!src.data || !dst.data) return XPAN_GAINFRAME_ARGUMENT;
    size_t srcSpan = 0, dstSpan = 0;
    status = plane_span(w, h, format, src.planeCount, src.pixelStride, src.rowStride, srcSpan);
    if (status != XPAN_GAINFRAME_OK) return status;
    status = plane_span(w, h, format, dst.planeCount, dst.pixelStride, dst.rowStride, dstSpan);
    if (status != XPAN_GAINFRAME_OK) return status;
    if (src.memoryCapacity > XPAN_GAINFRAME_MAX_PLANE_BYTES ||
        dst.memoryCapacity > XPAN_GAINFRAME_MAX_PLANE_BYTES) return XPAN_GAINFRAME_LIMIT;
    if (src.memoryCapacity < srcSpan || dst.memoryCapacity < dstSpan)
        return XPAN_GAINFRAME_LAYOUT;
    const uintptr_t s = reinterpret_cast<uintptr_t>(src.data);
    const uintptr_t d = reinterpret_cast<uintptr_t>(dst.data);
    if (src.memoryCapacity > UINTPTR_MAX - s || dst.memoryCapacity > UINTPTR_MAX - d)
        return XPAN_GAINFRAME_LIMIT;
    if (s < d + dst.memoryCapacity && d < s + src.memoryCapacity)
        return XPAN_GAINFRAME_ALIAS;
    const size_t rowBytes = static_cast<size_t>(w) * src.pixelStride;
    const size_t leftBytes = static_cast<size_t>(roi.left) * src.pixelStride;
    const size_t copyBytes = static_cast<size_t>(roi.right - roi.left) * src.pixelStride;
    for (uint32_t y = 0; y < h; ++y) memset(dst.data + y * dst.rowStride, 0, rowBytes);
    for (int32_t y = roi.top; y < roi.bottom; ++y)
        memcpy(dst.data + static_cast<size_t>(y) * dst.rowStride + leftBytes,
               src.data + static_cast<size_t>(y) * src.rowStride + leftBytes, copyBytes);
    return XPAN_GAINFRAME_OK;
}
}
