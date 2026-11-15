"""Local-only gain-frame prototype builder; NOT IN APK / NOT NATIVE GAINMAP GENERATOR.

Run with Python -I. No APK builder, JNI, device, network, signing or publishing.
Outputs always go to a NEW directory in _tmp. Host tests link the real wrapper
against public-API mocks; Android arm64 uses the real NDK header and libandroid.
Sanitizers are detected by compiling/running clean and deliberately faulty probes,
not by platform name. Unavailable runtimes are recorded, never reported as passes.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests/support/gain_frame_native"
LIB_NAME = "libxpan_gainframe_prototype.so"
EXPORT = "xpan_gainframe_clone_neutral"
MARKERS = ["NOT IN APK", "NOT NATIVE GAINMAP GENERATOR"]
SOURCE_NAMES = ("gain_frame_core.h", "gain_frame_core.cpp", "gain_frame_native.h",
                "gain_frame_native.cpp", "mock_ahardware_buffer.h", "host_tests.cpp")
PUBLIC_AHB = {"AHardwareBuffer_" + name for name in
              ("allocate", "acquire", "release", "describe", "lockPlanes", "unlock")}
ALLOWED_NEEDED = {"libandroid.so", "liblog.so", "libm.so", "libdl.so", "libc.so"}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_file(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError(f"Required local file is missing: {path}")
    return path


def fresh_output():
    parent = ROOT / "_tmp"
    parent.mkdir(exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="gain-frame-prototype-", dir=parent)).resolve()


def save_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def execute(argv, output, log_name, *, env=None, input_text=None, must_succeed=True):
    argv = [str(a) for a in argv]
    log = output / (log_name + ".log")
    try:
        result = subprocess.run(argv, cwd=output, env=env, input=input_text,
                                capture_output=True, text=True, encoding="utf-8",
                                errors="backslashreplace", timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.write_text(json.dumps(argv, ensure_ascii=False) + "\n" + str(exc), encoding="utf-8")
        raise RuntimeError(f"Command could not complete; see {log}") from exc
    text = result.stdout + result.stderr
    log.write_text(json.dumps(argv, ensure_ascii=False) + f"\nreturncode={result.returncode}\n" + text,
                   encoding="utf-8")
    evidence = {"command": argv, "returncode": result.returncode, "log": str(log)}
    if must_succeed and result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}); see {log}")
    return text, evidence


def find_host_compiler(explicit=None):
    explicit = explicit or os.environ.get("XPAN_HOST_CXX")
    if explicit:
        candidates = [explicit]
    else:
        candidates = [shutil.which(n) for n in ("clang++", "g++", "cl")]
        if os.name == "nt":
            vswhere = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Microsoft Visual Studio/Installer/vswhere.exe"
            if vswhere.is_file():
                result = subprocess.run([str(vswhere), "-latest", "-products", "*", "-requires",
                    "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-find",
                    "VC/Tools/MSVC/*/bin/Hostx64/x64/cl.exe"], cwd=ROOT, capture_output=True,
                    text=True, encoding="utf-8-sig", errors="backslashreplace", timeout=30)
                if result.returncode == 0:
                    candidates.extend(line.strip() for line in result.stdout.splitlines() if line.strip())
        candidates.append(shutil.which("zig"))
    for candidate in candidates:
        if not candidate:
            continue
        executable = require_file(shutil.which(str(candidate)) or candidate)
        return [str(executable)] + (["c++"] if executable.stem.lower() == "zig" else [])
    raise ValueError("No native host C++ compiler; provide --host-cxx with an existing clang++, g++, zig or cl")


def is_msvc(compiler):
    return Path(compiler[0]).stem.lower() == "cl"


def host_flags(compiler):
    if is_msvc(compiler):
        return ["/nologo", "/std:c++17", "/O1", "/Zi", "/W4", "/WX", "/EHs-c-", "/GR-", "/MD"]
    return ["-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-fno-exceptions", "-fno-rtti"]


def msvc_setup_command(cmd, setup, version):
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("MSVC toolset version must come from the selected compiler directory")
    if any(c in str(cmd) + str(setup) for c in ('"', "%", "!", "^", "\r", "\n", "\x00")):
        raise ValueError("MSVC setup path contains unsupported cmd expansion characters")
    # Pass a native command line: argv/list2cmdline quoting is not cmd syntax.
    return f'"{cmd}" /d /u /v:off /s /c "call "{setup}" -vcvars_ver={version} >nul && set"'


def host_environment(compiler, output):
    env = os.environ.copy()
    if is_msvc(compiler):
        selected = require_file(compiler[0])
        toolset = next((p for p in selected.parents if p.parent.name.lower() == "msvc"), None)
        if toolset is None or tuple(p.lower() for p in selected.relative_to(toolset).parts) != (
                "bin", "hostx64", "x64", "cl.exe"):
            raise ValueError("MSVC host compiler must be the exact installed Hostx64/x64 toolset")
        vc = toolset.parent.parent.parent
        setup = require_file(vc / "Auxiliary/Build/vcvars64.bat")
        cmd = require_file(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/cmd.exe")
        command = msvc_setup_command(cmd, setup, toolset.name)
        result = subprocess.run(command, executable=str(cmd), cwd=output, capture_output=True, timeout=60)
        if result.returncode:
            raise ValueError("Existing MSVC environment initialization failed")
        values = dict(line.split("=", 1) for line in result.stdout.decode("utf-16-le").splitlines()
                      if "=" in line and not line.startswith("="))
        lower = {key.lower(): value for key, value in values.items()}
        if not {"include", "lib", "path", "vctoolsversion", "vctoolsinstalldir"}.issubset(lower):
            raise ValueError("MSVC environment did not supply toolset/headers/libraries/search paths")
        if lower["vctoolsversion"].rstrip("\\/") != toolset.name or Path(lower["vctoolsinstalldir"]).resolve() != toolset:
            raise ValueError("MSVC environment does not match the selected compiler toolset")
        env = {key: value for key, value in env.items() if key.lower() not in lower}
        env.update(values)
        # Do not log inherited environment values, which can contain credentials.
        save_json(output / "host-environment.json", {"setup": str(setup), "sha256": sha256(setup),
                  "selected_toolset": toolset.name, "toolset_match_verified": True,
                  "returncode": result.returncode, "environment_values_logged": False})
    elif len(compiler) == 2:
        env["ZIG_LOCAL_CACHE_DIR"] = str(output / "zig-local")
        env["ZIG_GLOBAL_CACHE_DIR"] = str(output / "zig-global")
    env["ASAN_OPTIONS"] = "halt_on_error=1:detect_leaks=1"
    env["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
    return env


def host_version_command(compiler):
    if is_msvc(compiler):
        return compiler
    return [compiler[0], "version"] if len(compiler) == 2 else compiler + ["--version"]


def host_compile_command(compiler, flags, instrument, inputs, executable, *, mock=False):
    if is_msvc(compiler):
        return compiler + flags + instrument + (["/DXPAN_GAINFRAME_HOST_TEST=1"] if mock else []) + list(inputs) + ["/Fe:" + str(executable)]
    return compiler + flags + instrument + (["-DXPAN_GAINFRAME_HOST_TEST=1"] if mock else []) + list(inputs) + ["-o", executable]


def sanitizer_flags(kind, compiler):
    if is_msvc(compiler):
        return ["/fsanitize=" + kind, "/Oy-"]
    return ["-fsanitize=" + kind, "-fno-sanitize-recover=all", "-fno-omit-frame-pointer"]


def find_ndk(explicit=None):
    configured = explicit or os.environ.get("XPAN_NDK_ROOT") or os.environ.get("ANDROID_NDK_HOME")
    if configured:
        roots = [Path(configured)]
    else:
        sdks = [Path.home() / "Desktop/工具/AndroidSdk", Path.home() / "AppData/Local/Android/Sdk"]
        for name in ("ANDROID_SDK_ROOT", "ANDROID_HOME"):
            if os.environ.get(name):
                sdks.insert(0, Path(os.environ[name]))
        roots = [sdk / "ndk/29.0.14206865" for sdk in sdks]
        roots += [p for sdk in sdks if (sdk / "ndk").is_dir()
                  for p in sorted((sdk / "ndk").iterdir(), reverse=True)]
    tag = "windows-x86_64" if os.name == "nt" else "darwin-x86_64" if sys.platform == "darwin" else "linux-x86_64"
    suffix = ".exe" if os.name == "nt" else ""
    for ndk in roots:
        tc = ndk / "toolchains/llvm/prebuilt" / tag
        if (tc / ("bin/clang++" + suffix)).is_file():
            require_file(tc / ("bin/llvm-readelf" + suffix))
            require_file(tc / "sysroot/usr/include/android/hardware_buffer.h")
            require_file(tc / "sysroot/usr/include/jni.h")
            return ndk.resolve(), tc.resolve(), suffix
    raise ValueError("No usable local NDK; provide --ndk-root (nothing is downloaded)")


def host_summary(text, compile_evidence, run_evidence):
    cases = re.findall(r"(?m)^PASS (\S+)$", text)
    counts = re.findall(r"(?m)^CHECKS (\d+)$", text)
    if "all_host_tests" not in cases or len(counts) != 1 or int(counts[0]) < 1000:
        raise ValueError("Native host test executable did not produce complete success evidence")
    return {"compile": compile_evidence, "run": run_evidence, "checks": int(counts[0]), "cases": cases}


def probe_sanitizer(kind, compiler, flags, output, env):
    suffix = ".exe" if os.name == "nt" else ""
    instrument = sanitizer_flags(kind, compiler)
    codes = {
        "address": "#include <stdlib.h>\nint main(){char* p=(char*)malloc(1); volatile int i=1; p[i]=7; volatile char v=p[i]; free(p); return v==7 ? 0 : 3;}\n",
        "undefined": "int main(){volatile int x=2147483647; volatile int y=x+1; return y==0;}\n",
    }
    evidence = {"available": False, "status": "unavailable", "probes": []}
    for label, code in (("clean", "int main(){return 0;}\n"), ("violation", codes[kind])):
        exe = output / ("probe-" + kind + "-" + label + suffix)
        if is_msvc(compiler):
            source = output / ("probe-" + kind + "-" + label + ".cpp")
            source.write_text(code, encoding="utf-8")
            command = host_compile_command(compiler, flags, instrument, [source], exe)
            input_text = None
        else:
            command = compiler + flags + instrument + ["-x", "c++", "-", "-o", exe]
            input_text = code
        _, compiled = execute(command, output, "probe-" + kind + "-" + label + "-compile", env=env,
                              input_text=input_text, must_succeed=False)
        probe = {"kind": label, "source": code, "compile": compiled}
        evidence["probes"].append(probe)
        if compiled["returncode"]:
            evidence["reason"] = "sanitizer probe could not compile/link; see actual compiler log"
            return evidence
        text, ran = execute([exe], output, "probe-" + kind + "-" + label + "-run", env=env, must_succeed=False)
        probe["run"] = ran
        if label == "clean" and ran["returncode"]:
            evidence["reason"] = "clean sanitizer executable could not run; see actual runtime log"
            return evidence
        if label == "violation":
            detected = ("AddressSanitizer" in text and "heap-buffer-overflow" in text) if kind == "address" else "signed integer overflow" in text
            if ran["returncode"] == 0 or not detected:
                evidence["reason"] = "deliberate violation was not confirmed by sanitizer diagnostics"
                return evidence
            probe["violation_confirmed"] = True
    evidence.update(available=True, status="instrumentation_confirmed")
    return evidence


def build_host(output, explicit=None, require_sanitizers=False):
    compiler = find_host_compiler(explicit)
    flags = host_flags(compiler)
    env = host_environment(compiler, output)
    version, version_evidence = execute(host_version_command(compiler), output, "host-compiler-version", env=env)
    inputs = [SOURCE / name for name in ("gain_frame_core.cpp", "gain_frame_native.cpp", "host_tests.cpp")]
    suffix = ".exe" if os.name == "nt" else ""

    def suite(label, instrument):
        exe = output / ("host-tests-" + label + suffix)
        command = host_compile_command(compiler, flags, instrument, inputs, exe, mock=True)
        _, compiled = execute(command, output, "host-" + label + "-compile", env=env)
        text, ran = execute([exe], output, "host-" + label + "-run", env=env)
        return host_summary(text, compiled, ran)

    report = {"compiler": {"path": compiler[0], "sha256": sha256(compiler[0]), "version": version.strip(),
                            "version_evidence": version_evidence}, "normal": suite("normal", []), "sanitizers": {}}
    for kind in ("address", "undefined"):
        probe = probe_sanitizer(kind, compiler, flags, output, env)
        report["sanitizers"][kind] = probe
        if probe["available"]:
            probe["suite"] = suite(kind, sanitizer_flags(kind, compiler))
            probe["status"] = "passed"
        elif require_sanitizers:
            raise ValueError(f"Required {kind} sanitizer is unavailable: {probe['reason']}")
        print(f"Host {kind}: {probe['status']}", flush=True)
    print(f"Host normal: {report['normal']['checks']} checks passed", flush=True)
    return report


def validate_elf_evidence(data, text):
    if len(data) < 64 or data[:7] != b"\x7fELF\x02\x01\x01":
        raise ValueError("Expected ELF64 little-endian version-1 output")
    elf_type, machine = struct.unpack_from("<HH", data, 16)
    if elf_type != 3 or machine != 183:
        raise ValueError("Expected ET_DYN / AArch64 (183), not a host or other-ABI binary")
    if not re.search(r"(?m)^\s*Machine:\s+AArch64\s*$", text) or ".dynsym" not in text:
        raise ValueError("llvm-readelf evidence is incomplete or disagrees with ELF header")
    needed = re.findall(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]", text)
    if len(needed) != len(set(needed)) or not {"libandroid.so", "liblog.so", "libc.so"}.issubset(needed) or set(needed) - ALLOWED_NEEDED:
        raise ValueError(f"Unexpected DT_NEEDED (no vendor or libc++_shared runtime allowed): {needed}")
    if re.findall(r"\(SONAME\)\s+Library soname: \[([^\]]+)\]", text) != [LIB_NAME]:
        raise ValueError("Prototype SONAME is missing or unexpected")
    rows = re.findall(r"(?m)^[ \t]*\d+:[ \t]+\S+[ \t]+\S+[ \t]+(\S+)[ \t]+(\S+)[ \t]+(\S+)[ \t]+(\S+)[ \t]*([^\r\n]*)$", text)
    exports, imports = {}, set()
    for kind, bind, visibility, index, name in rows:
        name = name.strip().split(" ", 1)[0].split("@", 1)[0]
        if index == "UND" and name:
            imports.add(name)
        elif bind in ("GLOBAL", "WEAK") and visibility in ("DEFAULT", "PROTECTED"):
            exports[name] = kind
    if exports != {EXPORT: "FUNC"}:
        raise ValueError(f"Expected exactly one exported C function, got {exports}")
    if {name for name in imports if name.startswith("AHardwareBuffer_")} != PUBLIC_AHB:
        raise ValueError("Missing or unexpected public AHardwareBuffer imports")
    return {"class": "ELF64", "machine": "AArch64", "machine_number": machine,
            "type": "ET_DYN", "soname": LIB_NAME, "dt_needed": needed,
            "exports": list(exports), "imports": sorted(imports)}


def dependency_paths(path):
    text = Path(path).read_text(encoding="utf-8").replace("\\\n", " ")
    if ": " not in text:
        raise ValueError(f"Cannot parse compiler dependency evidence: {path}")
    return {require_file(p) for p in shlex.split(text.split(": ", 1)[1], posix=True)}


def build_android(output, ndk_spec=None):
    ndk, tc, suffix = find_ndk(ndk_spec)
    clang = require_file(tc / ("bin/clang++" + suffix))
    readelf = require_file(tc / ("bin/llvm-readelf" + suffix))
    sysroot = tc / "sysroot"
    static_cxx = require_file(sysroot / "usr/lib/aarch64-linux-android/libc++_static.a")
    target = ["--target=aarch64-linux-android29", "--sysroot=" + str(sysroot)]
    version, version_evidence = execute([clang, "--version"], output, "android-compiler-version")
    common = [clang, *target, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-fPIC",
              "-fvisibility=hidden", "-fno-exceptions", "-fno-rtti", "-stdlib=libc++"]
    objects, compiled, dependencies = [], [], set()
    for name in ("gain_frame_core", "gain_frame_native"):
        obj, dep = output / (name + ".o"), output / (name + ".d")
        _, evidence = execute(common + ["-MD", "-MF", dep, "-c", SOURCE / (name + ".cpp"), "-o", obj],
                              output, "android-" + name + "-compile")
        objects.append(obj); compiled.append(evidence); dependencies.update(dependency_paths(dep))
    public_header = (sysroot / "usr/include/android/hardware_buffer.h").resolve()
    if public_header not in dependencies or any(p.name == "mock_ahardware_buffer.h" for p in dependencies):
        raise ValueError("Android compilation did not use exclusively the real public AHardwareBuffer header")
    lib = output / LIB_NAME
    _, linked = execute([clang, *target, "-stdlib=libc++", "-static-libstdc++", "-shared", *objects,
                         "-Wl,--no-undefined", "-Wl,--build-id=sha1", "-Wl,-soname," + LIB_NAME,
                         "-Wl,--no-as-needed", "-landroid", "-llog", "-o", lib], output, "android-link")
    text, inspected = execute([readelf, "-h", "-d", "--dyn-syms", "--wide", lib], output, "android-elf")
    elf = validate_elf_evidence(lib.read_bytes(), text)
    properties = require_file(ndk / "source.properties")
    headers = {str(p): sha256(p) for p in sorted(dependencies) if p.suffix == ".h"}
    jni = require_file(sysroot / "usr/include/jni.h")
    headers[str(jni)] = sha256(jni)  # Availability evidence only; prototype intentionally has no JNI attachment.
    print(f"Android verified: {lib}", flush=True)
    return {"library": str(lib), "library_sha256": sha256(lib), "ndk": str(ndk),
            "ndk_properties": properties.read_text(encoding="utf-8").strip(),
            "ndk_properties_sha256": sha256(properties), "target": "aarch64-linux-android29",
            "tools": {str(p): sha256(p) for p in (clang, readelf)}, "compiler_version": version.strip(),
            "compiler_version_evidence": version_evidence, "header_sha256": headers,
            "cxx_runtime": "NDK libc++ static selected; no libc++_shared DT_NEEDED",
            "libcxx_static": {"path": str(static_cxx), "sha256": sha256(static_cxx)},
            "compile": compiled, "link": linked, "inspection": inspected, "elf": elf,
            "runtime_tested_on_device": False, "jni_header_is_availability_only": True}


def build(args):
    output = fresh_output()
    print(f"Prototype output: {output}", flush=True)
    paths = [require_file(SOURCE / n) for n in SOURCE_NAMES] + [Path(__file__).resolve(), require_file(ROOT / "tests/test_gain_frame_native.py")]
    hashes = {str(p): sha256(p) for p in paths}
    report = {"ok": False, "markers": MARKERS, "created_utc": datetime.now(timezone.utc).isoformat(),
              "output": str(output), "source_sha256": hashes, "in_apk": False, "native_gainmap_generator": False,
              "bounds": {"max_dimension": 16384, "max_bytes_per_plane_including_stride_budget": 128 * 1024 * 1024,
                         "formats": {"Y8": "0x20203859", "RGB888": 3}, "layers": 1, "empty_roi": "rejected", "padding": "untouched"},
              "caller_gap": "Real source AHardwareBuffer + exact gain-pixel ROI/canvas and producer completion/lifetime must be confirmed by caller; no JNI or real-device geometry wiring",
              "neutrality": "Outside-ROI byte zero only, not a verified neutral value for all HDR gain encodings"}
    try:
        report["host"] = build_host(output, args.host_cxx, args.require_sanitizers)
        report["android"] = None if args.host_only else build_android(output, args.ndk_root)
        if {str(p): sha256(p) for p in paths} != hashes:
            raise ValueError("Prototype inputs changed during build; evidence rejected")
        report["ok"] = True
        save_json(output / "provenance.json", report)
    except Exception as exc:
        report["error"] = str(exc)
        save_json(output / "failure.json", report)
        raise
    return output, report


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--ndk-root", help="Existing local NDK directory; no download")
    result.add_argument("--host-cxx", help="Path to an existing native clang++, g++ or zig executable")
    result.add_argument("--host-only", action="store_true", help="Run native host/mock tests without Android cross-compilation")
    result.add_argument("--require-sanitizers", action="store_true", help="Fail if either ASan or UBSan runtime cannot be confirmed")
    return result


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    try:
        output, _ = build(parser().parse_args())
        print(f"Evidence: {output / 'provenance.json'}")
        print(" / ".join(MARKERS))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Prototype build FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
