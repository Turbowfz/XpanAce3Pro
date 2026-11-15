#ifndef XPAN_MOCK_AHARDWARE_BUFFER_H
#define XPAN_MOCK_AHARDWARE_BUFFER_H
#ifndef XPAN_GAINFRAME_HOST_TEST
#error "AHardwareBuffer mock is host-test-only, never an Android ABI replacement"
#endif
#include <stdint.h>

/* Public API subset for linking the REAL wrapper against in-process host mocks.
 * Not a vendor layout, not JNI, and never included in the Android build.
 */
typedef struct AHardwareBuffer AHardwareBuffer;
typedef struct ARect { int32_t left, top, right, bottom; } ARect;
typedef struct AHardwareBuffer_Desc {
    uint32_t width, height, layers, format;
    uint64_t usage;
    uint32_t stride, rfu0;
    uint64_t rfu1;
} AHardwareBuffer_Desc;
typedef struct AHardwareBuffer_Plane {
    void* data;
    uint32_t pixelStride, rowStride;
} AHardwareBuffer_Plane;
typedef struct AHardwareBuffer_Planes {
    uint32_t planeCount;
    AHardwareBuffer_Plane planes[4];
} AHardwareBuffer_Planes;

#define AHARDWAREBUFFER_USAGE_CPU_READ_RARELY UINT64_C(2)
#define AHARDWAREBUFFER_USAGE_CPU_READ_OFTEN UINT64_C(3)
#define AHARDWAREBUFFER_USAGE_CPU_READ_MASK UINT64_C(15)
#define AHARDWAREBUFFER_USAGE_CPU_WRITE_RARELY UINT64_C(32)
#define AHARDWAREBUFFER_USAGE_CPU_WRITE_OFTEN UINT64_C(48)
#define AHARDWAREBUFFER_USAGE_CPU_WRITE_MASK UINT64_C(240)
#define AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE (UINT64_C(1) << 8)
#define AHARDWAREBUFFER_USAGE_GPU_FRAMEBUFFER (UINT64_C(1) << 9)
#define AHARDWAREBUFFER_USAGE_PROTECTED_CONTENT (UINT64_C(1) << 14)

#ifdef __cplusplus
extern "C" {
#endif
int AHardwareBuffer_allocate(const AHardwareBuffer_Desc*, AHardwareBuffer**);
void AHardwareBuffer_acquire(AHardwareBuffer*);
void AHardwareBuffer_release(AHardwareBuffer*);
void AHardwareBuffer_describe(const AHardwareBuffer*, AHardwareBuffer_Desc*);
int AHardwareBuffer_lockPlanes(AHardwareBuffer*, uint64_t, int32_t, const ARect*,
                               AHardwareBuffer_Planes*);
int AHardwareBuffer_unlock(AHardwareBuffer*, int32_t*);
#ifdef __cplusplus
}
#endif
#endif
