#!/usr/bin/env python3
"""Bounded, read-only OTA partition ranges and targeted EROFS directory lookup.

Run with Python -I. Standard-library only; never creates a partition image.
Only REPLACE/REPLACE_BZ/REPLACE_XZ/ZERO are accepted. Encoded operation
SHA-256 is checked BEFORE decoding. ZERO has no payload hash in OTA manifests;
its absence is recorded explicitly. Compressed EROFS inodes are NOT decoded.
All inputs and outputs are explicit arguments (no machine-specific paths).
"""
import argparse
import base64
import bisect
import bz2
from collections import OrderedDict, defaultdict
import hashlib
import json
import lzma
from pathlib import Path
import stat
import struct
import sys


class UnsafeInput(ValueError):
    pass


class UnsupportedLayout(UnsafeInput):
    pass


def varint(data, pos):
    value = 0
    for shift in range(0, 70, 7):
        if pos >= len(data):
            raise UnsafeInput("truncated protobuf varint")
        byte = data[pos]
        pos += 1
        if shift == 63 and byte > 1:
            raise UnsafeInput("protobuf varint exceeds uint64")
        value |= (byte & 127) << shift
        if byte < 128:
            return value, pos
    raise UnsafeInput("oversized protobuf varint")


def wire(data):
    fields = defaultdict(list)
    pos = 0
    while pos < len(data):
        tag, pos = varint(data, pos)
        number, kind = tag >> 3, tag & 7
        if not 0 < number < (1 << 29):
            raise UnsafeInput("invalid protobuf field number")
        if kind == 0:
            value, pos = varint(data, pos)
        elif kind in (1, 2, 5):
            if kind == 2:
                length, pos = varint(data, pos)
            else:
                length = 8 if kind == 1 else 4
            end = pos + length
            if end > len(data):
                raise UnsafeInput("truncated protobuf field")
            value, pos = data[pos:end], end
        else:
            raise UnsafeInput("unsupported protobuf wire type %d" % kind)
        fields[number].append((kind, value))
    return fields


def one(fields, number, kind, default=None, required=False):
    values = fields.get(number, [])
    if not values:
        if required:
            raise UnsafeInput("missing protobuf field %d" % number)
        return default
    if len(values) != 1 or values[0][0] != kind:
        raise UnsafeInput("duplicate or wrong-wire protobuf field %d" % number)
    return values[0][1]


def repeated(fields, number, kind):
    values = fields.get(number, [])
    if any(w != kind for w, _ in values):
        raise UnsafeInput("wrong repeated field wire type")
    return [v for _, v in values]


def positive(value):
    value = int(value)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return value


class PayloadRanges:
    """Virtual target partition; materializes only operations intersecting reads."""
    TYPES = {0: "REPLACE", 1: "REPLACE_BZ", 6: "ZERO", 8: "REPLACE_XZ"}

    def __init__(self, payload, partition, audit, *, max_target=8 << 20,
                 max_encoded=64 << 20, max_decoded=64 << 20,
                 max_operation=8 << 20, cache_bytes=16 << 20,
                 max_manifest=16 << 20):
        self.file = Path(payload).open("rb")
        self.audit = audit
        self.audit.update({"source": str(Path(payload).resolve()),
                           "source_role": "OnePlus donor reference, not Ace originals",
                           "partition": partition, "reads": [], "operations": [],
                           "limits": {"target_bytes": max_target,
                                      "encoded_bytes": max_encoded,
                                      "decoded_bytes": max_decoded,
                                      "operation_bytes": max_operation,
                                      "cache_bytes": cache_bytes}})
        self.max_target, self.max_encoded = max_target, max_encoded
        self.max_decoded, self.max_operation = max_decoded, max_operation
        self.cache_limit = cache_bytes
        self.target_bytes = self.encoded_bytes = self.decoded_bytes = 0
        self.cache = OrderedDict()
        self.cached_bytes = 0
        self.ops, self.extents = [], []
        try:
            self._manifest(partition, max_manifest)
        except Exception:
            self.file.close()
            raise

    def _read_input(self, offset, length, reason):
        self.file.seek(offset)
        value = self.file.read(length)
        self.audit.setdefault("input_reads", []).append(
            {"offset": offset, "length": length, "actual": len(value), "reason": reason})
        if len(value) != length:
            raise UnsafeInput("truncated payload read")
        return value

    def _manifest(self, partition, max_manifest):
        header = self._read_input(0, 20, "payload header")
        if header[:4] != b"CrAU":
            raise UnsafeInput("not a CrAU payload")
        major, length = struct.unpack(">QQ", header[4:])
        if major not in (1, 2) or length > max_manifest:
            raise UnsafeInput("unsupported major version or manifest limit")
        signature_bytes = 0
        if major == 2:
            extra = self._read_input(20, 4, "metadata signature length")
            signature_bytes = struct.unpack(">I", extra)[0]
            header += extra
        raw = self._read_input(len(header), length, "protobuf manifest")
        self.base = len(header) + length + signature_bytes
        self.file.seek(0, 2)
        self.file_size = self.file.tell()
        if self.base > self.file_size:
            raise UnsafeInput("metadata outside payload")
        manifest = wire(raw)
        self.block = one(manifest, 3, 0, 4096)
        if self.block < 512 or self.block > 65536 or self.block & (self.block - 1):
            raise UnsafeInput("invalid payload block size")
        signature_offset = one(manifest, 4, 0, self.file_size - self.base)
        signature_size = one(manifest, 5, 0, 0)
        if self.base + signature_offset + signature_size != self.file_size:
            raise UnsafeInput("payload signature bounds inconsistent")
        matches, inventory = [], []
        for raw_partition in repeated(manifest, 13, 2):
            part = wire(raw_partition)
            name = one(part, 1, 2, required=True).decode("utf-8", "strict")
            info = wire(one(part, 7, 2, required=True))
            inventory.append({"name": name, "size": one(info, 1, 0, required=True)})
            if name == partition:
                matches.append((part, info))
        if len(matches) != 1:
            raise UnsafeInput("partition not found uniquely")
        part, info = matches[0]
        self.size = one(info, 1, 0, required=True)
        if self.size <= 0:
            raise UnsafeInput("invalid partition size")
        # Deliberately refuse any source/base partition dependency.
        if 6 in part:
            raise UnsafeInput("old partition info present; not a self-contained target")
        for index, raw_op in enumerate(repeated(part, 8, 2)):
            fields = wire(raw_op)
            typ = one(fields, 1, 0, required=True)
            if typ not in self.TYPES or repeated(fields, 4, 2):
                raise UnsafeInput("source-dependent or unsupported operation")
            extents, decoded_offset = [], 0
            for raw_extent in repeated(fields, 6, 2):
                extent = wire(raw_extent)
                start = one(extent, 1, 0, required=True) * self.block
                length = one(extent, 2, 0, required=True) * self.block
                if length <= 0 or start + length > self.size:
                    raise UnsafeInput("destination extent outside partition")
                extents.append((start, start + length, decoded_offset, index))
                decoded_offset += length
            if not extents:
                raise UnsafeInput("operation without destination extents")
            length = one(fields, 3, 0, 0)
            offset = one(fields, 2, 0, 0)
            digest = one(fields, 8, 2)
            dst_length = one(fields, 7, 0)
            if dst_length is not None and dst_length != decoded_offset:
                raise UnsafeInput("destination length differs from extents")
            if typ == 6:
                if length or offset or digest is not None:
                    raise UnsafeInput("unexpected payload/hash on ZERO operation")
            elif length <= 0 or offset + length > signature_offset or digest is None or len(digest) != 32:
                raise UnsafeInput("operation data bounds or SHA-256 missing")
            self.ops.append({"index": index, "type": typ, "offset": offset,
                             "length": length, "decoded": decoded_offset,
                             "sha256": digest})
            self.extents.extend(extents)
        self.extents.sort()
        cursor = 0
        for start, end, _, _ in self.extents:
            if start != cursor:
                raise UnsafeInput("destination gap/overlap; range lookup refused")
            cursor = end
        if cursor != self.size:
            raise UnsafeInput("incomplete destination coverage")
        self.starts = [e[0] for e in self.extents]
        metadata_hash = base64.b64encode(hashlib.sha256(header + raw).digest()).decode()
        self.audit["manifest"] = {"major": major, "block_size": self.block,
                                  "data_offset": self.base, "metadata_sha256_base64": metadata_hash,
                                  "minor_version": one(manifest, 12, 0, 0),
                                  "partial_update": bool(one(manifest, 16, 0, 0)),
                                  "partitions": inventory, "target_size": self.size,
                                  "target_operation_count": len(self.ops),
                                  "full_partition_hash_verified": False,
                                  "cryptographic_signature_verified": False}
        props = Path(self.file.name).parent / "payload_properties.txt"
        if props.is_file():
            if props.stat().st_size > 65536:
                raise UnsafeInput("payload properties size limit")
            values = dict(line.split("=", 1) for line in props.read_text(encoding="utf-8").splitlines()
                          if "=" in line)
            if values.get("METADATA_HASH") != metadata_hash or int(values.get("FILE_SIZE", -1)) != self.file_size:
                raise UnsafeInput("metadata hash/file size mismatch with sidecar")
            if int(values.get("METADATA_SIZE", -1)) != len(header) + len(raw):
                raise UnsafeInput("metadata size sidecar mismatch")
            self.audit["manifest"]["sidecar_metadata_hash_verified"] = True
            self.audit["declared_target_version"] = values.get("ota_target_version")
            self.audit["declared_android_version"] = values.get("android_version")

    def _operation(self, index):
        if index in self.cache:
            self.cache.move_to_end(index)
            return self.cache[index]
        op = self.ops[index]
        if op["type"] == 6:
            self.audit["operations"].append({"index": index, "type": "ZERO",
                                             "sha256": "not applicable: no encoded bytes/hash in manifest"})
            return None
        if max(op["length"], op["decoded"]) > self.max_operation:
            raise UnsafeInput("single operation byte limit exceeded")
        if self.encoded_bytes + op["length"] > self.max_encoded:
            raise UnsafeInput("encoded read byte limit exceeded")
        if self.decoded_bytes + op["decoded"] > self.max_decoded:
            raise UnsafeInput("decoded operation byte limit exceeded")
        self.encoded_bytes += op["length"]
        raw = self._read_input(self.base + op["offset"], op["length"], "operation %d" % index)
        actual_hash = hashlib.sha256(raw).digest()
        record = {"index": index, "type": self.TYPES[op["type"]],
                  "payload_offset": self.base + op["offset"], "encoded_bytes": len(raw),
                  "expected_decoded_bytes": op["decoded"],
                  "expected_sha256": op["sha256"].hex(), "actual_sha256": actual_hash.hex(),
                  "sha256_verified": actual_hash == op["sha256"]}
        self.audit["operations"].append(record)
        if actual_hash != op["sha256"]:
            raise UnsafeInput("operation SHA-256 mismatch; not decoded")
        if op["type"] == 0:
            decoded = raw
        else:
            decoder = (bz2.BZ2Decompressor() if op["type"] == 1 else
                       lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=64 << 20))
            # max_length prevents decompression bombs; EOF and trailing data are strict.
            decoded = decoder.decompress(raw, max_length=op["decoded"] + 1)
            if not decoder.eof or decoder.unused_data:
                raise UnsafeInput("operation truncated, oversized or trailing compressed data")
        if len(decoded) != op["decoded"]:
            raise UnsafeInput("decoded length differs from destination extents")
        self.decoded_bytes += len(decoded)
        record["decoded_bytes"] = len(decoded)
        record["decoded_sha256"] = hashlib.sha256(decoded).hexdigest()
        if len(decoded) <= self.cache_limit:
            while self.cache and self.cached_bytes + len(decoded) > self.cache_limit:
                _, evicted = self.cache.popitem(last=False)
                self.cached_bytes -= len(evicted)
            self.cache[index] = decoded
            self.cached_bytes += len(decoded)
        return decoded

    def read_at(self, offset, length, reason="range"):
        if offset < 0 or length < 0 or offset + length > self.size:
            raise UnsafeInput("target range outside partition")
        if self.target_bytes + length > self.max_target:
            raise UnsafeInput("target read byte limit exceeded")
        self.target_bytes += length
        record = {"offset": offset, "length": length, "reason": reason, "operations": []}
        self.audit["reads"].append(record)
        result = bytearray()
        pos, end = offset, offset + length
        while pos < end:
            entry = bisect.bisect_right(self.starts, pos) - 1
            start, stop, decoded_offset, index = self.extents[entry]
            take = min(stop, end) - pos
            record["operations"].append(index)
            data = self._operation(index)
            if data is None:
                result.extend(bytes(take))
            else:
                local = decoded_offset + pos - start
                result.extend(data[local:local + take])
            pos += take
        record["complete"] = True
        return bytes(result)

    def close(self):
        self.audit["totals"] = {"target_bytes": self.target_bytes,
                                "encoded_operation_bytes": self.encoded_bytes,
                                "decoded_operation_bytes": self.decoded_bytes,
                                "resident_cache_bytes": self.cached_bytes}
        self.file.close()


class TargetedErofs:
    """Official flat EROFS inode/dirent formats; no recursive tree walk."""
    MAGIC = 0xE0F5E1E2

    def __init__(self, ranges, max_directory=256 << 10, max_entries=4096):
        self.ranges = ranges
        self.max_directory, self.max_entries = max_directory, max_entries
        sb = ranges.read_at(1024, 128, "EROFS superblock")
        if struct.unpack_from("<I", sb, 0)[0] != self.MAGIC:
            raise UnsafeInput("not EROFS; no filesystem blocks guessed")
        bits = sb[12]
        if not 9 <= bits <= 16:
            raise UnsafeInput("unsupported EROFS block size")
        self.block = 1 << bits
        self.root = struct.unpack_from("<H", sb, 14)[0]
        self.meta = struct.unpack_from("<I", sb, 40)[0] * self.block
        self.blocks = struct.unpack_from("<I", sb, 36)[0]
        if self.meta >= ranges.size or not 0 < self.blocks * self.block <= ranges.size:
            raise UnsafeInput("EROFS superblock bounds")
        self.incompat = struct.unpack_from("<I", sb, 80)[0]
        self.inodes, self.directories = {}, {}
        ranges.audit["erofs"] = {"block_size": self.block, "root_nid": self.root,
                                  "meta_offset": self.meta, "incompat": self.incompat,
                                  "compressed_inode_policy": "report only; no guessed blocks"}

    def inode(self, nid):
        if nid in self.inodes:
            return self.inodes[nid]
        off = self.meta + nid * 32
        raw = self.ranges.read_at(off, 32, "inode %d compact/header" % nid)
        fmt, xattrs, mode = struct.unpack_from("<HHH", raw, 0)
        if fmt & ~0xF:
            raise UnsafeInput("unsupported inode format bits")
        extended = bool(fmt & 1)
        if extended:
            raw += self.ranges.read_at(off + 32, 32, "inode %d extended" % nid)
        layout = (fmt >> 1) & 7
        size = struct.unpack_from("<Q" if extended else "<I", raw, 8)[0]
        xattr_size = 12 + (xattrs - 1) * 4 if xattrs else 0
        inode_size = 64 if extended else 32
        item = {"nid": nid, "inode_offset": off, "mode": mode,
                "type": "directory" if stat.S_ISDIR(mode) else "file" if stat.S_ISREG(mode) else "other",
                "size": size, "layout": layout,
                "data_address_raw": struct.unpack_from("<I", raw, 16)[0],
                "inline_offset": off + inode_size + xattr_size,
                "readable": layout in (0, 2),
                "status": "flat readable" if layout in (0, 2) else "unsupported layout; inode only"}
        self.inodes[nid] = item
        return item

    def flat(self, inode, maximum, reason):
        if inode["layout"] not in (0, 2):
            raise UnsupportedLayout("inode %d layout %d: no compressed index parsing" %
                                    (inode["nid"], inode["layout"]))
        size = inode["size"]
        if size > maximum:
            raise UnsafeInput("file/directory byte cap exceeded at inode %d" % inode["nid"])
        full, tail = divmod(size, self.block)
        address = inode["data_address_raw"]
        if address == 0xFFFFFFFF and (full or (tail and inode["layout"] == 0)):
            raise UnsafeInput("invalid flat block address")
        output = bytearray()
        if full:
            output.extend(self.ranges.read_at(address * self.block, full * self.block, reason + " flat"))
        if tail:
            offset = (inode["inline_offset"] if inode["layout"] == 2 else
                      (address + full) * self.block)
            output.extend(self.ranges.read_at(offset, tail, reason + " tail"))
        return bytes(output)

    def directory(self, inode):
        nid = inode["nid"]
        if nid in self.directories:
            return self.directories[nid]
        if inode["type"] != "directory":
            raise UnsafeInput("path component is not a directory; symlinks not followed")
        data = self.flat(inode, self.max_directory, "directory %d" % nid)
        entries = []
        names = set()
        for start in range(0, len(data), self.block):
            segment = data[start:start + self.block]
            if len(segment) < 12:
                raise UnsafeInput("directory segment too short")
            first_offset = struct.unpack_from("<H", segment, 8)[0]
            if first_offset < 12 or first_offset % 12 or first_offset > len(segment):
                raise UnsafeInput("invalid EROFS dirent name offset")
            count = first_offset // 12
            if len(entries) + count > self.max_entries + 2:
                raise UnsafeInput("directory entry limit")
            for i in range(count):
                child, name_start, ftype, reserved = struct.unpack_from("<QHBB", segment, i * 12)
                name_end = (struct.unpack_from("<H", segment, (i + 1) * 12 + 8)[0]
                            if i + 1 < count else len(segment))
                if reserved or ftype > 7 or not first_offset <= name_start < name_end <= len(segment):
                    raise UnsafeInput("invalid EROFS dirent bounds/type")
                raw_name = segment[name_start:name_end]
                if i + 1 == count:
                    name_bytes, _, padding = raw_name.partition(b"\0")
                    if padding and any(padding):
                        raise UnsafeInput("nonzero trailing directory padding")
                else:
                    name_bytes = raw_name
                    if b"\0" in name_bytes:
                        raise UnsafeInput("embedded NUL directory name")
                name = name_bytes.decode("utf-8", "strict")
                if not name or "/" in name or "\\" in name or len(name_bytes) > 255:
                    raise UnsafeInput("unsafe directory name")
                if name in (".", ".."):
                    continue
                if name in names:
                    raise UnsafeInput("duplicate directory name")
                names.add(name)
                entries.append({"name": name, "nid": child, "dirent_type": ftype})
        self.directories[nid] = entries
        return entries

    @staticmethod
    def path_parts(path):
        if not path.startswith("/") or "\\" in path:
            raise UnsafeInput("use absolute image paths, not host paths")
        parts = path.split("/")[1:]
        if path == "/":
            return []
        if len(parts) > 32 or any(p in ("", ".", "..") for p in parts):
            raise UnsafeInput("invalid target path")
        return parts

    def resolve(self, path):
        inode = self.inode(self.root)
        for component in self.path_parts(path):
            entries = self.directory(inode)
            found = [e for e in entries if e["name"] == component]
            if not found:
                raise FileNotFoundError("image path missing: " + path)
            inode = self.inode(found[0]["nid"])
        return inode

    def list_target(self, path):
        inode = self.resolve(path)
        result = {"path": path, "inode": inode}
        try:
            result["entries"] = [dict(e, inode=self.inode(e["nid"]))
                                  for e in self.directory(inode)]
        except UnsupportedLayout as exc:
            result["status"] = str(exc)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path)
    parser.add_argument("--partition", required=True)
    parser.add_argument("--list-dir", action="append", default=[], metavar="IMAGE_PATH")
    parser.add_argument("--inspect", action="append", default=[], metavar="IMAGE_PATH")
    parser.add_argument("--file", action="append", default=[], nargs=2,
                        metavar=("IMAGE_PATH", "HOST_OUTPUT"), help="explicit flat file only; no recursive dump")
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--recordreads", required=True, type=Path)
    parser.add_argument("--max-target-bytes", type=positive, default=8 << 20)
    parser.add_argument("--max-encoded-bytes", type=positive, default=64 << 20)
    parser.add_argument("--max-decoded-bytes", type=positive, default=64 << 20)
    parser.add_argument("--max-operation-bytes", type=positive, default=8 << 20)
    parser.add_argument("--cache-bytes", type=positive, default=16 << 20)
    parser.add_argument("--max-file-bytes", type=positive, default=512 << 10)
    args = parser.parse_args(argv)
    if not (args.list_dir or args.inspect or args.file):
        parser.error("select explicit directories/inodes/files")
    output_paths = [args.inventory, args.recordreads] + [Path(p) for _, p in args.file]
    if len({str(p.resolve()).casefold() for p in output_paths}) != len(output_paths):
        parser.error("outputs must be unique")
    # Exclusive creation avoids replacing existing code, data or a payload input.
    for p in output_paths:
        if p.exists():
            parser.error("output already exists: " + str(p))
    audit = {"mode": "read-only targeted ranges; no full partition image",
             "requested": {"directories": args.list_dir, "inodes": args.inspect,
                           "files": [p for p, _ in args.file]}}
    result = {"donor_reference_only": True, "directories": [], "inodes": [], "files": []}
    ranges, exit_code = None, 0
    try:
        ranges = PayloadRanges(args.payload, args.partition, audit,
                               max_target=args.max_target_bytes, max_encoded=args.max_encoded_bytes,
                               max_decoded=args.max_decoded_bytes, max_operation=args.max_operation_bytes,
                               cache_bytes=args.cache_bytes)
        fs = TargetedErofs(ranges)
        for path in args.list_dir:
            try:
                result["directories"].append(fs.list_target(path))
            except FileNotFoundError as exc:
                result["directories"].append({"path": path, "status": str(exc)})
        for path in args.inspect:
            try:
                result["inodes"].append({"path": path, "inode": fs.resolve(path)})
            except FileNotFoundError as exc:
                result["inodes"].append({"path": path, "status": str(exc)})
        for path, host in args.file:
            inode = fs.resolve(path)
            if inode["type"] != "file":
                raise UnsafeInput("only explicit regular files may be exported")
            if inode["layout"] not in (0, 2):
                result["files"].append({"path": path, "inode": inode,
                                         "status": "not exported: unsupported compressed layout"})
                continue
            data = fs.flat(inode, args.max_file_bytes, "selected file " + path)
            destination = Path(host)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as output:
                output.write(data)
            result["files"].append({"path": path, "host_output": str(destination.resolve()),
                                     "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    except (UnsafeInput, OSError, UnicodeError, lzma.LZMAError, EOFError) as exc:
        result["error"] = str(exc)
        audit["error"] = str(exc)
        exit_code = 2
    finally:
        if ranges is not None:
            ranges.close()
        for path, contents in ((args.inventory, result), (args.recordreads, audit)):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as output:
                json.dump(contents, output, ensure_ascii=False, indent=2)
                output.write("\n")
    print(json.dumps({"inventory": str(args.inventory.resolve()),
                      "recordreads": str(args.recordreads.resolve()),
                      "totals": audit.get("totals"), "error": result.get("error")}, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
