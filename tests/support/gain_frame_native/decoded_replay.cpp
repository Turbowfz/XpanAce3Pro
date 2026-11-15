#include "gain_frame_core.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#if defined(_MSC_VER)
#include <fcntl.h>
#include <io.h>
#include <share.h>
#include <sys/stat.h>
#endif

/* NOT IN APK / NOT NATIVE GAINMAP GENERATOR.
 * Replays already decoded gain pixels with an explicit same-canvas ROI.
 * Filenames are supplied by the trusted runner in its fresh output directory.
 */
namespace {
struct Bytes {
    uint8_t* data;
    explicit Bytes(size_t size) : data(static_cast<uint8_t*>(malloc(size))) {}
    ~Bytes() { free(data); }
};
struct File {
    FILE* handle;
    explicit File(FILE* file) : handle(file) {}
    ~File() { if (handle) fclose(handle); }
    int close() { FILE* f = handle; handle = nullptr; return fclose(f); }
};
bool number(const char* text, uint32_t& value) {
    if (!text || !*text) return false;
    for (const char* p = text; *p; ++p) if (*p < '0' || *p > '9') return false;
    errno = 0;
    char* end = nullptr;
    const unsigned long long n = strtoull(text, &end, 10);
    if (errno || !end || *end || n > INT32_MAX) return false;
    value = static_cast<uint32_t>(n);
    return true;
}
FILE* open_input(const char* path) {
#if defined(_MSC_VER)
    FILE* file = nullptr;
    return fopen_s(&file, path, "rb") ? nullptr : file;
#else
    return fopen(path, "rb");
#endif
}
FILE* open_new_output(const char* path) {
#if defined(_MSC_VER)
    int fd = -1;
    if (_sopen_s(&fd, path, _O_WRONLY | _O_CREAT | _O_EXCL | _O_BINARY,
                 _SH_DENYRW, _S_IREAD | _S_IWRITE)) return nullptr;
    FILE* file = _fdopen(fd, "wb");
    if (!file) _close(fd);
    return file;
#else
    return fopen(path, "wbx");
#endif
}
int fail(const char* text) { fprintf(stderr, "FAIL %s\n", text); return 1; }
}

int main(int argc, char** argv) {
    if (argc != 10) return fail("expected input output width height format left top right bottom");
    uint32_t values[7]{};
    for (unsigned i = 0; i < 7; ++i)
        if (!number(argv[i + 3], values[i])) return fail("invalid explicit numeric geometry");
    const uint32_t w = values[0], h = values[1], format = values[2];
    const XpanGainFrameRoi roi{static_cast<int32_t>(values[3]), static_cast<int32_t>(values[4]),
                              static_cast<int32_t>(values[5]), static_cast<int32_t>(values[6])};
    if (xpan_gainframe::validate_geometry(w, h, format, roi) != XPAN_GAINFRAME_OK)
        return fail("unsupported format, dimensions or ROI");
    const size_t bpp = xpan_gainframe::bytes_per_pixel(format), row = static_cast<size_t>(w) * bpp;
    const size_t sourceStride = row + 7, targetStride = row + 11;
    size_t sourceSpan = 0, targetSpan = 0;
    if (xpan_gainframe::plane_span(w, h, format, 1, bpp, sourceStride, sourceSpan) != XPAN_GAINFRAME_OK ||
        xpan_gainframe::plane_span(w, h, format, 1, bpp, targetStride, targetSpan) != XPAN_GAINFRAME_OK)
        return fail("stride budget exceeded");
    Bytes source(sourceSpan + 16), target(targetSpan + 16), snapshot(sourceSpan + 16);
    if (!source.data || !target.data || !snapshot.data) return fail("allocation failed");
    memset(source.data, 0x81, sourceSpan + 16);
    memset(target.data, 0xa5, targetSpan + 16);
    const XpanGainFrameConstPlane src{source.data + 8, 1, bpp, sourceStride, sourceSpan};
    const XpanGainFramePlane dst{target.data + 8, 1, bpp, targetStride, targetSpan};
    File input(open_input(argv[1]));
    if (!input.handle) return fail("cannot open decoded input");
    for (uint32_t y = 0; y < h; ++y)
        if (fread(source.data + 8 + static_cast<size_t>(y) * sourceStride, 1, row, input.handle) != row)
            return fail("decoded input is shorter than the explicit canvas");
    if (fgetc(input.handle) != EOF || ferror(input.handle)) return fail("decoded input size/read mismatch");
    if (input.close()) return fail("input close failed");
    memcpy(snapshot.data, source.data, sourceSpan + 16);
    if (xpan_gainframe::copy_neutral_frame(src, dst, w, h, format, roi) != XPAN_GAINFRAME_OK)
        return fail("core rejected decoded replay");
    for (uint32_t y = 0; y < h; ++y) {
        for (size_t x = 0; x < row; ++x) {
            const bool inside = y >= static_cast<uint32_t>(roi.top) && y < static_cast<uint32_t>(roi.bottom) &&
                x / bpp >= static_cast<size_t>(roi.left) && x / bpp < static_cast<size_t>(roi.right);
            if (dst.data[static_cast<size_t>(y) * targetStride + x] !=
                (inside ? src.data[static_cast<size_t>(y) * sourceStride + x] : 0))
                return fail("ROI byte or outside-frame byte changed incorrectly");
        }
        if (y + 1 < h)
            for (size_t x = row; x < targetStride; ++x)
                if (dst.data[static_cast<size_t>(y) * targetStride + x] != 0xa5)
                    return fail("target row padding was accessed");
    }
    for (size_t i = 0; i < 8; ++i)
        if (target.data[i] != 0xa5 || target.data[8 + targetSpan + i] != 0xa5)
            return fail("target guard was accessed");
    if (memcmp(snapshot.data, source.data, sourceSpan + 16)) return fail("borrowed source or padding changed");
    File output(open_new_output(argv[2]));
    if (!output.handle) return fail("cannot create independent decoded output (no overwrite)");
    for (uint32_t y = 0; y < h; ++y)
        if (fwrite(dst.data + static_cast<size_t>(y) * targetStride, 1, row, output.handle) != row)
            return fail("decoded output write failed");
    if (output.close()) return fail("decoded output close failed");
    puts("PASS decoded_gain_roi_bytes_frame_zero_padding_guards");
    return 0;
}
