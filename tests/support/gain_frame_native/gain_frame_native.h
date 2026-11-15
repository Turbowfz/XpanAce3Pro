#ifndef XPAN_GAIN_FRAME_NATIVE_H
#define XPAN_GAIN_FRAME_NATIVE_H

#include "gain_frame_core.h"
#include <stdbool.h>
/* The mock is test-only; Android compilation always uses the NDK public header. */
#if defined(XPAN_GAINFRAME_HOST_TEST)
#include "mock_ahardware_buffer.h"
#else
#include <android/hardware_buffer.h>
#endif

#if defined(__GNUC__) || defined(__clang__)
#define XPAN_GAINFRAME_EXPORT __attribute__((visibility("default")))
#else
#define XPAN_GAINFRAME_EXPORT
#endif

#ifdef __cplusplus
extern "C" {
#endif

/* NOT IN APK / NOT NATIVE GAINMAP GENERATOR. API 29+ public AHardwareBuffer only.
 * source is BORROWED: caller keeps its reference alive for the entire call.
 * producerCompleteConfirmed MUST explicitly be true: all producer writes/fences
 * have completed, and no writer may modify source until this call returns.
 * This prototype accepts no fence FD, cannot verify that assertion, and uses -1.
 * expectedSourceWidth/Height are mandatory, positive, exact source canvas sizes.
 * ROI is explicit half-open content geometry IN GAIN PIXELS, never main-image
 * dimensions guessed from aspect ratio, white pixels or an opaque Java handle.
 * Only packed one-plane Y8 (0x20203859) / RGB888 (3), layers=1 are accepted.
 * Y8 is a requested numeric format, NOT guaranteed allocatable on every gralloc;
 * an unsupported public allocate/lock returns failure, with NO fallback format.
 * Returns independent same-size/format canvas: active bytes outside ROI = 0,
 * ROI bytes unchanged. Does not claim zero means neutral for every gain encoding.
 * No padding access: public planes have no allocation-capacity field. Locked
 * layout guarantees active pixels only; the core receives that minimum span,
 * not an invented allocation size. stride*height is checked against 128 MiB.
 * Target usage = CPU_READ_RARELY | CPU_WRITE_RARELY, plus GPU_SAMPLED_IMAGE
 * only if targetGpuSampled explicitly true; source/vendor usage is not inherited.
 * Both successful locks are unlocked with NULL fences (blocking) and checked.
 * On success *outTarget owns ONE ref; caller MUST AHardwareBuffer_release it.
 * Caller source ref is never released; only our temporary acquired ref is.
 * On any failure *outTarget is NULL; no partially constructed target escapes.
 * outTarget and outDescription are mandatory nonaliasing output storage;
 * do not pass an occupied owning slot (release its previous ref first).
 * No JNI, vendor structs, HEIF codec, tmappatch, policy or sensor tuning.
 */
XPAN_GAINFRAME_EXPORT XpanGainFrameStatus xpan_gainframe_clone_neutral(
    AHardwareBuffer* source, int32_t expectedSourceWidth, int32_t expectedSourceHeight,
    XpanGainFrameRoi contentRoi, bool producerCompleteConfirmed, bool targetGpuSampled,
    AHardwareBuffer** outTarget, AHardwareBuffer_Desc* outDescription);

#ifdef __cplusplus
}
#endif
#endif
