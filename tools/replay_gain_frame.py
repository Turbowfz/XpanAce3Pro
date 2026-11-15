"""Replay decoded gain pixels through the local byte-zero frame prototype.

NOT IN APK / NOT NATIVE GAINMAP GENERATOR / NOT HDR DISPLAY PROOF.
Run with Python -I. Requires an explicit same-canvas ROI; no geometry inference,
HEIC re-encoding, native gain generation, phone operations, download or overwrite.
The resulting PNG is analytical decoded gain data, not a repaired HDR photo.
"""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests/support/gain_frame_native"
spec = importlib.util.spec_from_file_location("gain_frame_builder", ROOT / "tools/build_gain_frame_prototype.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
Y8, RGB888 = 0x20203859, 3
LIMIT = 128 * 1024 * 1024


def geometry(width, height, roi, bpp):
    if not (0 < width <= 16384 and 0 < height <= 16384):
        raise ValueError("Unsupported decoded dimensions")
    left, top, right, bottom = roi
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise ValueError("Explicit nonempty half-open ROI must be in decoded gain pixels")
    if (width * bpp + 11) * height > LIMIT:
        raise ValueError("Decoded canvas including replay stride exceeds the prototype budget")


def decoded_input(path, format_name, roi):
    path = builder.require_file(path)
    if path.stat().st_size > LIMIT:
        raise ValueError("Decoded PNG file exceeds the input limit")
    digest = builder.sha256(path)
    with Image.open(path) as image:
        if image.format != "PNG" or image.mode not in ("RGB", "L"):
            raise ValueError("Only explicit RGB or L decoded PNG data is accepted")
        width, height = image.size
        bpp = 1 if format_name == "y8" else 3
        geometry(width, height, roi, bpp)
        if image.mode == "RGB" and width * height * 3 > LIMIT:
            raise ValueError("Decoded RGB input exceeds the memory budget")
        image.load()
        data = np.array(image, dtype=np.uint8, copy=True)
        mode = image.mode
    replicated = False
    if format_name == "y8" and mode == "RGB":
        if not (np.array_equal(data[:, :, 0], data[:, :, 1]) and
                np.array_equal(data[:, :, 1], data[:, :, 2])):
            raise ValueError("RGB gain channels differ; refusing Y8 conversion")
        data = data[:, :, 0].copy()
        replicated = True
    elif format_name == "rgb888" and mode != "RGB":
        raise ValueError("RGB888 replay requires actual decoded RGB channels")
    if builder.sha256(path) != digest:
        raise ValueError("Decoded input changed while reading")
    return path, digest, data, replicated


def replay(args):
    path, digest, data, replicated = decoded_input(args.gain_png, args.format, args.roi)
    original = None
    if args.source_heic:
        photo = builder.require_file(args.source_heic)
        if photo.stat().st_size > LIMIT:
            raise ValueError("Read-only original HEIC hash input exceeds the limit")
        original = {"path": str(photo), "sha256": builder.sha256(photo), "bytes": photo.stat().st_size}
    parent = ROOT / "_tmp"
    parent.mkdir(exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="gain-frame-replay-", dir=parent)).resolve()
    inputs = output / "input"
    inputs.mkdir()
    raw = inputs / "decoded-gain.raw"
    raw.write_bytes(data.tobytes())
    result_raw = output / "bytezero-gain.raw"
    sources = [SOURCE / "gain_frame_core.h", SOURCE / "gain_frame_core.cpp", SOURCE / "decoded_replay.cpp",
               Path(__file__).resolve(), ROOT / "tools/build_gain_frame_prototype.py"]
    hashes = {str(p): builder.sha256(builder.require_file(p)) for p in sources}
    report = {"ok": False, "created_utc": datetime.now(timezone.utc).isoformat(), "output": str(output),
              "markers": ["NOT IN APK", "NOT NATIVE GAINMAP GENERATOR", "NOT HDR DISPLAY PROOF"],
              "in_apk": False, "source_sha256": hashes,
              "decoded_input": {"path": str(path), "sha256": digest, "size": [data.shape[1], data.shape[0]],
                                "format": args.format, "replicated_rgb_to_y8_lossless": replicated},
              "roi": list(args.roi), "roi_source": "explicit caller-supplied final decoded gain-pixel coordinates",
              "original_heic": original, "heic_reencoded": False, "hardware_buffer_runtime_tested": False,
              "neutrality": "Byte zero only; caller must independently verify gain minimum/offset/gamma/base metadata"}
    try:
        compiler = builder.find_host_compiler(args.host_cxx)
        flags = builder.host_flags(compiler)
        env = builder.host_environment(compiler, output)
        version, evidence = builder.execute(builder.host_version_command(compiler), output, "compiler-version", env=env)
        report["compiler"] = {"path": compiler[0], "sha256": builder.sha256(compiler[0]),
                              "version": version.strip(), "evidence": evidence}
        instrument = []
        if args.sanitizer != "none":
            probe = builder.probe_sanitizer(args.sanitizer, compiler, flags, output, env)
            report["sanitizer"] = probe
            if not probe["available"]:
                raise ValueError("Requested sanitizer unavailable: " + probe["reason"])
            instrument = builder.sanitizer_flags(args.sanitizer, compiler)
        executable = output / ("decoded-replay.exe" if os.name == "nt" else "decoded-replay")
        command = builder.host_compile_command(compiler, flags, instrument,
            [SOURCE / "gain_frame_core.cpp", SOURCE / "decoded_replay.cpp"], executable)
        _, report["compile"] = builder.execute(command, output, "replay-compile", env=env)
        width, height = data.shape[1], data.shape[0]
        format_code = Y8 if args.format == "y8" else RGB888
        text, report["run"] = builder.execute([executable, raw, result_raw, width, height, format_code, *args.roi],
                                             output, "replay-run", env=env)
        if text.strip() != "PASS decoded_gain_roi_bytes_frame_zero_padding_guards":
            raise ValueError("Native replay did not report complete byte/padding/source validation")
        if result_raw.stat().st_size != data.nbytes:
            raise ValueError("Native replay output dimensions differ")
        result = np.frombuffer(result_raw.read_bytes(), dtype=np.uint8).reshape(data.shape)
        left, top, right, bottom = args.roi
        if not np.array_equal(data[top:bottom, left:right], result[top:bottom, left:right]):
            raise ValueError("Photo ROI gain bytes differ")
        mask = np.ones((height, width), dtype=bool)
        mask[top:bottom, left:right] = False
        if np.any(result[mask]):
            raise ValueError("Outside-photo frame bytes are not zero")
        changed = data != result
        if data.ndim == 3:
            changed = np.any(changed, axis=2)
        png = output / "analytical-bytezero-gain.png"
        preview = output / "analytical-bytezero-gain-preview.png"
        image = Image.fromarray(result)
        image.save(png)
        image.thumbnail((1276, 750))
        image.save(preview)
        report["pixels"] = {"photo_pixels": (right - left) * (bottom - top),
                            "frame_pixels": int(np.count_nonzero(mask)),
                            "photo_roi_bytes_unchanged": True, "all_frame_bytes_zero": True,
                            "changed_photo_pixels": int(np.count_nonzero(changed & ~mask)),
                            "changed_frame_pixels": int(np.count_nonzero(changed & mask)),
                            "native_source_padding_guards_unchanged": True}
        report["result"] = {"raw": str(result_raw), "png": str(png), "png_sha256": builder.sha256(png),
                            "preview": str(preview), "preview_sha256": builder.sha256(preview)}
        if builder.sha256(path) != digest or (original and builder.sha256(original["path"]) != original["sha256"]):
            raise ValueError("Read-only input changed during replay")
        if {str(p): builder.sha256(p) for p in sources} != hashes:
            raise ValueError("Prototype source changed during replay")
        report["ok"] = True
        if args.sanitizer != "none":
            report["sanitizer"]["status"] = "passed"
        builder.save_json(output / "provenance.json", report)
    except Exception as exc:
        report["error"] = str(exc)
        builder.save_json(output / "failure.json", report)
        raise
    print(json.dumps({"evidence": str(output / "provenance.json"), "pixels": report["pixels"],
                      "original_heic_unchanged": original is not None, "heic_reencoded": False},
                     ensure_ascii=False, indent=2))
    return output, report


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--gain-png", required=True, help="Existing independently decoded PNG; read-only")
    result.add_argument("--format", required=True, choices=("y8", "rgb888"))
    result.add_argument("--roi", required=True, nargs=4, type=int, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"))
    result.add_argument("--source-heic", help="Optional original HEIC to hash before/after; never decoded or written")
    result.add_argument("--host-cxx", help="Existing native compiler; nothing is downloaded")
    result.add_argument("--sanitizer", choices=("none", "address", "undefined"), default="none")
    return result


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    try:
        replay(parser().parse_args())
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print("Decoded gain replay FAILED: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
