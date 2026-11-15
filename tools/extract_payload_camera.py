#!/usr/bin/env python3
"""Extract selected local OTA camera references; never create a partition image.

Run with python -I. Paths must already be pinned in a targeted inode inventory.
Each new output directory gets bounded read and exact compact-index provenance.
The existing device extractor retains its separate 4 MiB ceiling.
"""
import argparse
import hashlib
import importlib.util
import json
import re
import struct
import zlib
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 192 << 20


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


reader = load("xpan_local_payload_reader", Path(__file__).with_name("payload_range_reader.py"))
compressed = load("xpan_local_compressed", Path(__file__).with_name("erofs_compressed.py"))


def bounded(value):
    value = int(value)
    if not 0 < value <= LIMIT:
        raise argparse.ArgumentTypeError("explicit budget must be in 1..192 MiB")
    return value


def inventory_paths(path):
    if not path.is_file() or path.stat().st_size > 1 << 20:
        raise ValueError("inventory must be a bounded regular JSON file")
    source = json.loads(path.read_text(encoding="utf-8"))
    result = {}
    if isinstance(source, list):
        records = source
    elif isinstance(source, dict):
        records = list(source.get("files", [])) + list(source.get("inodes", []))
        for directory in source.get("directories", []):
            for entry in directory.get("entries", []):
                if "inode" in entry:
                    records.append({"path": directory["path"].rstrip("/") + "/" + entry["name"],
                                    "inode": entry["inode"]})
    else:
        raise ValueError("unsupported inventory root")
    for record in records:
        if "path" not in record or "inode" not in record:
            continue
        target = record["path"]
        reader.TargetedErofs.path_parts(target)
        if target in result:
            raise ValueError("duplicate inventory path")
        result[target] = record["inode"]
    return result


def validate_archive(path):
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        names = archive.namelist()
        if len(names) > 65536 or sum(item.file_size for item in entries) > 512 << 20:
            raise ValueError("APK/JAR expanded size or entry limit")
        if any(item.file_size > LIMIT or item.flag_bits & 1
               or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) for item in entries):
            raise ValueError("unsupported/bounded APK/JAR entry")
        if len(names) != len(set(names)):
            raise ValueError("duplicate APK/JAR ZIP entries")
        if archive.testzip() is not None:
            raise ValueError("APK/JAR ZIP CRC failure")
        for item in entries:
            name = item.orig_filename
            if (name != item.filename or name.startswith("/") or ".." in name.split("/")
                    or "\\" in name or "\x00" in name):
                raise ValueError("unsafe APK/JAR entry")
        dex = [name for name in names if re.fullmatch(r"classes(?:[2-9]|[1-9][0-9]+)?\.dex", name)]
        if not dex or "classes.dex" not in dex:
            raise ValueError("selected APK/JAR has no main DEX")
        for name in dex:
            data = archive.read(name)
            if not re.match(rb"dex\n0(?:3[5-9]|4[0-1])\x00", data) or len(data) < 112:
                raise ValueError("invalid DEX header")
            size, header, endian = struct.unpack_from("<III", data, 32)
            if size != len(data) or header != 112 or endian != 0x12345678:
                raise ValueError("invalid DEX size/byte order")
            if (hashlib.sha1(data[32:]).digest() != data[12:32]
                    or zlib.adler32(data[12:]) & 0xffffffff != struct.unpack_from("<I", data, 8)[0]):
                raise ValueError("invalid DEX signature/checksum")
        return {"zip_crc_verified": True, "dex_files": dex, "entry_count": len(names)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", required=True, type=Path)
    parser.add_argument("--partition", required=True)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--path", required=True, action="append")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--file-budget", required=True, type=bounded)
    parser.add_argument("--read-budget", required=True, type=bounded)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.is_symlink():
        parser.error("output must be a new unused directory")
    if not 1 <= len(args.path) <= 3 or len(set(args.path)) != len(args.path):
        parser.error("select one to three unique explicit paths")
    targets = inventory_paths(args.inventory)
    names = [Path(path).name for path in args.path]
    allowed = {"OplusCamera.apk", "com.oplus.camera.unit.sdk.jar", "com.oplus.camera.unit.sdk.adapter.jar",
               "camera_unit_config", "camera_unit_feature_config.protobuf", "oplus_camera_config"}
    if any(name not in allowed for name in names):
        parser.error("only the six explicit camera reference/config filenames are accepted")
    if len(set(names)) != len(names) or any(path not in targets for path in args.path):
        parser.error("every target must be uniquely pinned in the inventory")
    args.output.mkdir(parents=True)
    audit = {}
    ranges = None
    written = []
    report = {"status": "incomplete", "donor_reference_only": True, "phone_operations": False,
              "full_image": False, "partition": args.partition, "files": [],
              "source_inventory": str(args.inventory.resolve()), "plan_only": args.plan_only,
              "file_budget": args.file_budget, "read_budget": args.read_budget}
    try:
        ranges = reader.PayloadRanges(args.payload, args.partition, audit,
                                      max_target=args.read_budget, max_encoded=args.read_budget,
                                      max_decoded=args.read_budget, max_operation=8 << 20,
                                      cache_bytes=64 << 20)
        fs = reader.TargetedErofs(ranges)
        extractor = compressed.ExactCompressedErofs(
            fs, max_file_bytes=args.file_budget, max_physical_bytes=args.read_budget,
            max_metadata_bytes=4 << 20, budget_ceiling=LIMIT)
        report["superblock"] = extractor.superblock
        for target, name in zip(args.path, names):
            inode = fs.resolve(target)
            if any(inode[key] != targets[target][key] for key in compressed.INODE_PROOF_KEYS):
                raise ValueError("resolved inode differs from prior targeted inventory: " + target)
            if args.plan_only:
                proof = extractor.plan(inode)
            else:
                data, proof = extractor.extract(inode)
                destination = args.output / name
                with destination.open("xb") as stream:
                    written.append(destination)
                    stream.write(data)
                proof["host_output"] = str(destination.resolve())
                if name.endswith((".apk", ".jar")):
                    proof["container_validation"] = validate_archive(destination)
                else:
                    proof["static_validation"] = compressed.validate_config(data, name)
            proof.update(path=target, inventory_inode_verified=True)
            report["files"].append(proof)
            print(json.dumps({"path": target, "bytes": inode["size"], "sha256": proof.get("sha256"),
                              "physical_extents": len(proof["extents"]), "plan_only": args.plan_only},
                             ensure_ascii=False), flush=True)
        report.update(status="complete", selected_metadata_bytes=extractor.metadata_bytes,
                      selected_physical_bytes=extractor.physical_bytes)
    except Exception as failure:
        report.update(status="failed", error=type(failure).__name__ + ": " + str(failure))
        for destination in written:
            destination.unlink()
        report["failed_outputs_removed"] = True
    finally:
        if ranges is not None:
            ranges.close()
        with (args.output / "read-audit.json").open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(audit, indent=2) + "\n")
        with (args.output / "extraction.json").open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "error": report.get("error"),
                      "output": str(args.output.resolve()), "totals": audit.get("totals")},
                     ensure_ascii=False), flush=True)
    return 0 if report["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
