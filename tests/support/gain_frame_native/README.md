# Gain-frame local prototype

**NOT IN APK / NOT NATIVE GAINMAP GENERATOR / NOT HDR DISPLAY PROOF.**

`gain_frame_core.cpp` preserves bytes inside an explicit half-open ROI and writes
zero to active pixels outside it. Canvas size and format do not change. Packed
one-plane Y8 (`0x20203859`) and RGB888 (`3`) are supported. Row and pixel strides
are bytes; padding is untouched. Invalid dimensions, ROI, capacity, format,
overlap or byte budgets are rejected before writing.

`gain_frame_native.cpp` uses only the Android API29+ public AHardwareBuffer API.
The source reference is borrowed and retained temporarily. The caller must
confirm producer completion and exclude concurrent writers. A new independent
buffer is returned only after both successful locks are unlocked and checked.
Any allocation/lock/unlock failure discards the target. No fence, geometry or
metadata producer contract is inferred from a pointer or method return.

`host_tests.cpp` executes the same wrapper using test-only public-API mocks.
`decoded_replay.cpp` executes the same core on independently decoded gain bytes;
it never decodes, writes or re-encodes HEIC. Its output is created exclusively,
never overwriting an existing file.

## Build and test

Run from the trusted project checkout, not an extracted firmware directory:

```sh
python -I tools/build_gain_frame_prototype.py
python -I tests/test_gain_frame_native.py
python -I tests/test_gain_frame_replay.py
```

An existing host compiler and local NDK are required. Nothing is downloaded.
Use `--host-cxx` or `XPAN_HOST_CXX` for an explicit compiler and `--ndk-root` for
an explicit NDK. On Windows an existing MSVC installation can be located with
its existing `vswhere`; the matching `vcvars64.bat` initializes only the test
process environment. Environment values are not logged or stored.

Outputs and provenance go into a new project `_tmp` directory. Host sanitizer
support requires both a working clean probe and diagnostics from a deliberate
fault. Unavailable sanitizer runtimes are recorded, not claimed as passes.
Android compilation verifies the actual public header, ELF machine, imports,
sole prototype export and public-system-only dependencies; none of these prove
real gralloc or HDR rendering behavior.

## Replay decoded data

```sh
python -I tools/replay_gain_frame.py --gain-png <decoded-gain.png> \
  --format y8 --roi <left> <top> <right> <bottom> \
  --source-heic <read-only-original.heic>
```

All coordinates must already be known to belong to the supplied decoded gain
canvas. RGB input is accepted as Y8 only when all three channels are identical;
no luminance conversion or content-color ROI inference occurs. For `rgb888`,
actual decoded RGB channels are required.

Zero bytes are a byte-level operation, not a universal neutral-gain guarantee.
The caller must independently verify gain minimum, offsets, gamma, base rendition
and the current source/destination coordinate relationship. A final-file ROI
does not establish a pre-frame raw gain ROI. There is no production JNI, camera
hook integration, HEIF encoder or system-library replacement in this directory.

Verified sample results and remaining integration gaps are documented in
`docs/ProXDR宽幅移植核查.md`.
