#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Exact, bounded extraction of selected compact-index EROFS LZ4 files.

Run with Python -I. Imports the existing range reader / TargetedErofs by file
location, without invoking either main(). No image, mount, recursive walk,
physical-block guessing, phone write, or partial/truncated decode fallback.

The legacy device CLI remains capped at 4 MiB. The separate
``extract_payload_camera.py`` CLI accepts explicit local file/range budgets
(at most 192 MiB), compares selected inodes with an existing inventory, and
can report an exact metadata-only plan.

Mapping reference: Linux v6.1 fs/erofs/zmap.c, compacted_load_cluster_from_disk,
unpack_compacted_index, get_compacted_la_distance and extent length routines.
On-disk definitions: Linux v6.1 fs/erofs/erofs_fs.h.
Compression config: Linux v6.1 fs/erofs/super.c:erofs_load_compr_cfgs and
fs/erofs/decompressor.c:z_erofs_load_lz4_config / z_erofs_fixup_insize.
Only 4 KiB logical clusters and exact compact LZ4/plain pclusters are supported,
including configured big pclusters. Fragments, inline tails, interlacing,
unknown algorithms/configuration fields, and partial decoding fail closed.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import struct
import sys

BLOCK = 4096
MAX_BUDGET = 4 << 20
MAX_LOCAL_BUDGET = 192 << 20
INODE_PROOF_KEYS = ("nid", "inode_offset", "mode", "size", "layout",
                    "data_address_raw", "inline_offset")
PLAIN, HEAD1, NONHEAD, HEAD2 = range(4)
COMPACTED_2B = 1
BIG_1, BIG_2, INLINE, INTERLACED, FRAGMENT = 2, 4, 8, 16, 32
CBLKCNT = 1 << 11
COMPR_CFGS, COMPR_HEAD2 = 2, 8
MAX_PCLUSTER_BYTES = 1 << 20  # Linux v6.1 Z_EROFS_PCLUSTER_MAX_SIZE.
MAX_READ_BATCH = 1 << 20  # Join only adjacent proved extents, never read gaps.
REFERENCES = [
    "https://github.com/torvalds/linux/blob/v6.1/fs/erofs/zmap.c",
    "https://github.com/torvalds/linux/blob/v6.1/fs/erofs/erofs_fs.h",
    "https://github.com/torvalds/linux/blob/v6.1/fs/erofs/decompressor.c",
    "https://github.com/torvalds/linux/blob/v6.1/fs/erofs/super.c",
]
# Raw upstream files verified against the saved v6.1 source, not a donor binary.
REFERENCE_SHA256 = {
    "fs/erofs/zmap.c": "e43f0742f898c44cab04d24edb603824767b32059cc72ef904bd1b91b648d383",
    "fs/erofs/erofs_fs.h": "528299b3bb25b55c8b11e536d3cd9d703ccc6669eec8c12915091ebb5c0ad9b8",
    "fs/erofs/decompressor.c": "ed126b0b0e4daa98b17af658e4b02763269ce4cdf1f3efb4cd9b4549949e45ad",
    "fs/erofs/super.c": "84ef8377d9f3ee846b1dbd53b7806060dc8ae4f71b1f7e0fba8b63f0404fe50b",
}


class ExtractionError(ValueError):
    pass


def load_module(path, name):
    """Absolute trusted source path; register dataclasses-compatible imports."""
    spec = importlib.util.spec_from_file_location(name, Path(path).resolve())
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def unpack_compacted_index(pack, index, amortizedshift, lclusterbits=12, *,
                           big_pcluster=False):
    """Linux v6.1 compact unpack, with exact BIG/CBLKCNT pack semantics.

    Ordinary packs start nblk at ONE; big packs start at ZERO and add the
    physical count from the first NONHEAD. A last NONHEAD's lo is d1 unless
    CBLKCNT is set. Never search neighboring blocks or infer counts from them.
    """
    if lclusterbits != 12 or amortizedshift not in (1, 2):
        raise ExtractionError("unsupported compact index cluster/entry width")
    vcnt = 16 if amortizedshift == 1 else 2
    packsize = vcnt << amortizedshift
    if len(pack) != packsize or not 0 <= index < vcnt:
        raise ExtractionError("truncated compact index pack or invalid index")
    encodebits = (packsize - 4) * 8 // vcnt
    lomask = (1 << lclusterbits) - 1

    def decode(i):
        pos = encodebits * i
        value = struct.unpack_from("<I", pack, pos // 8)[0] >> (pos & 7)
        return value & lomask, (value >> lclusterbits) & 3

    def block_count(lo):
        if not big_pcluster:
            raise ExtractionError("CBLKCNT in ordinary pcluster; big-pcluster format required")
        count = lo & ~CBLKCNT
        if not count:
            raise ExtractionError("zero CBLKCNT physical block count")
        return count

    lo, kind = decode(index)
    item = {"type": kind, "lo": lo, "pack_index": index,
            "pack_base_block": struct.unpack_from("<I", pack, packsize - 4)[0]}
    if kind == NONHEAD:
        # Kernel get_compacted_la_distance scans to a HEAD or the end of pack.
        distance = 0
        cursor = index
        while cursor < vcnt:
            last_lo, last_kind = decode(cursor)
            if last_kind != NONHEAD:
                break
            if last_lo & CBLKCNT:
                block_count(last_lo)
            distance += 1
            cursor += 1
        if cursor == vcnt and not last_lo & CBLKCNT:
            distance += last_lo - 1
        if lo & CBLKCNT:
            item["compressed_blocks"] = block_count(lo)
            d0 = 1
        elif index + 1 != vcnt:
            d0 = lo
            if big_pcluster and lo <= 1:
                raise ExtractionError("big pcluster has ordinary NONHEAD d0 <= 1 instead of CBLKCNT")
        else:
            previous_lo, previous_kind = decode(index - 1)
            if previous_kind != NONHEAD:
                previous_lo = 0
            elif previous_lo & CBLKCNT:
                block_count(previous_lo)
                previous_lo = 1
            d0 = previous_lo + 1
        if d0 <= 0 or distance <= 0:
            raise ExtractionError("zero/bogus NONHEAD lookback/lookahead distance")
        item.update(cluster_offset=1 << lclusterbits, delta0=d0, delta1=distance)
        return item

    nblk, cursor = (0 if big_pcluster else 1), index
    while cursor > 0:
        cursor -= 1
        previous_lo, previous_kind = decode(cursor)
        if previous_kind == NONHEAD:
            if big_pcluster:
                if previous_lo & CBLKCNT:
                    cursor -= 1
                    nblk += block_count(previous_lo)
                else:
                    if previous_lo <= 1:
                        raise ExtractionError("big pcluster has ordinary NONHEAD d0 <= 1 instead of CBLKCNT")
                    cursor -= previous_lo - 2
                continue
            if previous_lo & CBLKCNT:
                block_count(previous_lo)  # Fail closed even while mapping a HEAD.
            cursor -= previous_lo
        if cursor >= 0:
            nblk += 1
    pblk = item["pack_base_block"] + nblk
    if pblk >= 0xFFFFFFFF:
        raise ExtractionError("invalid/overflowing compact physical block address")
    item.update(cluster_offset=lo, delta0=0, delta1=1,
                block_increment=nblk, physical_block=pblk)
    return item


class ExactCompressedErofs:
    """Use a parent's TargetedErofs and cached ranges.read_at directly."""
    def __init__(self, fs, *, max_file_bytes=512 << 10,
                 max_physical_bytes=MAX_BUDGET, max_metadata_bytes=256 << 10,
                 budget_ceiling=MAX_BUDGET):
        if budget_ceiling not in (MAX_BUDGET, MAX_LOCAL_BUDGET):
            raise ExtractionError("only 4 MiB device or 192 MiB local payload ceilings are accepted")
        if not 0 < max_file_bytes <= budget_ceiling:
            raise ExtractionError("file limit must be in 1..%d bytes" % budget_ceiling)
        if not 0 < max_physical_bytes <= budget_ceiling:
            raise ExtractionError("physical read limit must be in 1..%d bytes" % budget_ceiling)
        if not 0 < max_metadata_bytes <= MAX_BUDGET:
            raise ExtractionError("metadata read limit must be in 1..4 MiB")
        self.fs, self.ranges = fs, fs.ranges
        self.max_file_bytes = max_file_bytes
        self.max_physical_bytes = max_physical_bytes
        self.max_metadata_bytes = max_metadata_bytes
        self.physical_bytes = self.metadata_bytes = 0
        self._metadata_ranges = []
        if fs.block != BLOCK:
            raise ExtractionError("Linux v6.1 compact mapper requires 4096-byte blocks")
        self.fs_size = fs.blocks * BLOCK
        if not 0 < self.fs_size <= self.ranges.size:
            raise ExtractionError("EROFS filesystem bounds differ from range source")
        sb = self._read(1024, 128, "compressed extraction superblock proof")
        if (struct.unpack_from("<I", sb)[0] != 0xE0F5E1E2 or sb[12] != 12 or
                struct.unpack_from("<H", sb, 14)[0] != fs.root or
                struct.unpack_from("<I", sb, 36)[0] != fs.blocks or
                struct.unpack_from("<I", sb, 40)[0] * BLOCK != fs.meta):
            raise ExtractionError("raw EROFS superblock differs from resolved filesystem")
        incompat = struct.unpack_from("<I", sb, 80)[0]
        if incompat != fs.incompat or incompat & ~0x3F:
            raise ExtractionError("unknown/inconsistent EROFS superblock incompat bits")
        if struct.unpack_from("<H", sb, 86)[0]:
            raise ExtractionError("multi-device EROFS unsupported; no external device reads")
        sb_size = 128 + sb[13] * 16
        if sb_size > BLOCK - 1024:
            raise ExtractionError("superblock extension exceeds first filesystem block")
        extension = (self._read(1152, sb_size - 128, "superblock extension proof")
                     if sb_size > 128 else b"")
        if any(extension):
            raise ExtractionError("unknown nonzero superblock extension fields")
        self.superblock = {
            "raw_hex": sb.hex(), "sha256": hashlib.sha256(sb).hexdigest(),
            "feature_compat": struct.unpack_from("<I", sb, 8)[0],
            "feature_incompat": incompat, "blkszbits": sb[12],
            "sb_extslots": sb[13], "superblock_bytes": sb_size,
            "extension_raw_hex": extension.hex(), "root_nid": fs.root,
            "meta_offset": fs.meta, "filesystem_bytes": self.fs_size,
            "uuid_hex": sb[48:64].hex(),
            "zero_padding": bool(incompat & 1),
            "big_pcluster": bool(incompat & COMPR_CFGS),
            "compression_configs": bool(incompat & COMPR_CFGS),
            "compr_head2": bool(incompat & COMPR_HEAD2),
            "ztailpacking": bool(incompat & 16),
            "fragments": bool(incompat & 32),
        }
        self.lz4_config = self._load_lz4_config(sb, sb_size)
        self.superblock.update(available_compr_algs=1, lz4_config=self.lz4_config)

    def _load_lz4_config(self, sb, sb_size):
        """v6.1 super.c metadata length/alignment + decompressor.c LZ4 config.

        The u1 field is a bitmap with COMPR_CFGS, otherwise a legacy distance.
        Reject unknown records before using their lengths or reading payloads.
        """
        u1 = struct.unpack_from("<H", sb, 84)[0]
        if self.fs.incompat & COMPR_CFGS:
            if u1 != 1:
                raise ExtractionError("unsupported available compression algorithms %#x; only LZ4 configs accepted" % u1)
            offset = (1024 + sb_size + 3) & ~3
            length_raw = self._read(offset, 2, "LZ4 compression config length")
            length = struct.unpack("<H", length_raw)[0] or 65536
            if length != 14:
                raise ExtractionError("unsupported LZ4 compression config size %d; expected 14" % length)
            raw = self._read(offset + 2, length, "LZ4 compression config raw proof")
            distance, maximum = struct.unpack_from("<HH", raw)
            if any(raw[4:]):
                raise ExtractionError("unknown nonzero LZ4 compression config reserved fields")
            if maximum > MAX_PCLUSTER_BYTES // BLOCK:
                raise ExtractionError("LZ4 max_pclusterblks exceeds Linux 1 MiB pcluster cap")
            config = {"source": "COMPR_CFGS", "length_offset": offset,
                      "data_offset": offset + 2, "length": length,
                      "length_raw_hex": length_raw.hex(), "raw_hex": raw.hex(),
                      "sha256": hashlib.sha256(raw).hexdigest(),
                      "next_record_offset": (offset + 2 + length + 3) & ~3}
        else:
            distance, maximum = u1, 1
            config = {"source": "legacy superblock lz4_max_distance", "data_offset": 1108,
                      "length": 2, "raw_hex": sb[84:86].hex()}
        maximum = maximum or 1  # Kernel reserved-zero max_pclusterblks case.
        config.update(max_distance_raw=distance, max_distance=distance or 65535,
                      max_distance_pages=((distance or 65535) + BLOCK - 1) // BLOCK + 1,
                      max_pclusterblks_raw=struct.unpack_from("<H", raw, 2)[0]
                      if self.fs.incompat & COMPR_CFGS else 1,
                      max_pclusterblks=maximum, max_pcluster_bytes=maximum * BLOCK)
        return config

    def _read(self, offset, length, reason, *, physical=False):
        if offset < 0 or length <= 0 or offset + length > self.fs_size:
            raise ExtractionError("range outside EROFS filesystem: %d+%d" % (offset, length))
        if physical:
            if self.physical_bytes + length > self.max_physical_bytes:
                raise ExtractionError("total selected physical extent byte limit exceeded")
            self.physical_bytes += length
        else:
            if self.metadata_bytes + length > self.max_metadata_bytes:
                raise ExtractionError("total selected metadata byte limit exceeded")
            self.metadata_bytes += length
            self._metadata_ranges.append((offset, offset + length))
        data = self.ranges.read_at(offset, length, reason)
        if len(data) != length:
            raise ExtractionError("truncated range; no inferred bytes")
        return data

    def plan(self, inode):
        if inode["layout"] != 3 or not stat.S_ISREG(inode["mode"]):
            raise ExtractionError("only explicit regular compact-compressed inodes are accepted")
        size = inode["size"]
        if not 0 < size <= self.max_file_bytes:
            raise ExtractionError("empty/oversized selected compressed file")
        off = self.fs.meta + inode["nid"] * 32
        if off != inode["inode_offset"]:
            raise ExtractionError("inode location inconsistent with superblock/nid")
        raw = self._read(off, 32, "inode %d raw proof" % inode["nid"])
        fmt, xattrs, mode = struct.unpack_from("<HHH", raw)
        if fmt & ~15 or ((fmt >> 1) & 7) != 3:
            raise ExtractionError("unsupported/inconsistent raw inode format")
        inode_size = 64 if fmt & 1 else 32
        if inode_size == 64:
            raw += self._read(off + 32, 32, "extended inode proof")
        raw_size = struct.unpack_from("<Q" if fmt & 1 else "<I", raw, 8)[0]
        compressed_blocks = struct.unpack_from("<I", raw, 16)[0]
        xattr_size = 12 + (xattrs - 1) * 4 if xattrs else 0
        inline_offset = off + inode_size + xattr_size
        if (raw_size != size or mode != inode["mode"] or
                inline_offset != inode["inline_offset"] or
                compressed_blocks != inode["data_address_raw"]):
            raise ExtractionError("raw inode differs from resolved/inventory metadata")
        header_offset = (inline_offset + 7) & ~7
        header = self._read(header_offset, 8, "inode %d compression map header" % inode["nid"])
        reserved, idata_size, advise, algorithms, cbits = struct.unpack("<HHHBB", header)
        if cbits & 128 or advise & FRAGMENT:
            raise ExtractionError("fragment/packed inode requires packed-inode mapping; not read")
        if advise & INLINE:
            raise ExtractionError("ZTAILPACKING inline compressed extent unsupported; not expanded")
        if advise & INTERLACED:
            raise ExtractionError("interlaced plain pclusters unsupported")
        if advise & ~(COMPACTED_2B | BIG_1 | BIG_2) or cbits != 0:
            raise ExtractionError("unsupported advise/clusterbits: %#x/%#x" % (advise, cbits))
        big_pcluster = bool(advise & BIG_1)
        if bool(advise & BIG_2) != big_pcluster:
            raise ExtractionError("compact big pclusters HEAD1/HEAD2 flags must be consistent")
        if big_pcluster and not self.fs.incompat & COMPR_CFGS:
            raise ExtractionError("big pclusters without superblock COMPR_CFGS/BIG feature")
        if reserved or idata_size:
            raise ExtractionError("nonzero reserved map header fields without inline/fragment format")
        if algorithms != 0:
            raise ExtractionError("only LZ4 algorithms for both HEAD types are supported")
        totalidx = (size + BLOCK - 1) // BLOCK
        ebase = header_offset + 8
        c4 = (32 - ebase % 32) // 4
        if c4 == 8:
            c4 = 0
        c2 = ((totalidx - c4) // 16) * 16 if advise & COMPACTED_2B and c4 < totalidx else 0
        entries, packs = [], {}
        for lcn in range(totalidx):
            if lcn < c4:
                pos, shift = ebase + lcn * 4, 2
            elif lcn < c4 + c2:
                pos, shift = ebase + c4 * 4 + (lcn - c4) * 2, 1
            else:
                pos, shift = ebase + c4 * 4 + c2 * 2 + (lcn - c4 - c2) * 4, 2
            packsize = 32 if shift == 1 else 8
            start = pos // packsize * packsize
            if start not in packs:
                packs[start] = self._read(start, packsize, "inode %d exact compact index pack" % inode["nid"])
            entry = unpack_compacted_index(packs[start], (pos - start) >> shift, shift,
                                           big_pcluster=big_pcluster)
            entry.update(lcn=lcn, entry_offset=pos, pack_offset=start,
                         pack_bytes=packsize, next_pack_offset=start + packsize)
            if entry["type"] == HEAD2 and not self.fs.incompat & 8:
                raise ExtractionError("HEAD2 index without superblock COMPR_HEAD2 feature")
            entries.append(entry)

        # Validate NONHEAD lookback against the last HEAD, including pack-end d0.
        head = None
        heads = []
        for entry in entries:
            if entry["type"] != NONHEAD:
                logical = entry["lcn"] * BLOCK + entry["cluster_offset"]
                if logical > size or (heads and logical <= heads[-1]["logical_offset"]):
                    raise ExtractionError("unordered/beyond-EOF compact extent boundary")
                entry["logical_offset"] = logical
                entry["resolved_head_lcn"] = entry["lcn"]
                heads.append(entry)
                head = entry
            else:
                if head is None:
                    raise ExtractionError("NONHEAD at start of file")
                previous = entry["lcn"] - entry["delta0"]
                if previous < head["lcn"] or previous < 0:
                    raise ExtractionError("NONHEAD lookback crosses its extent head")
                # Inductive kernel lookback: entries only point backwards, and
                # the destination was already validated. No quadratic chain walk.
                if entries[previous]["resolved_head_lcn"] != head["lcn"]:
                    raise ExtractionError("NONHEAD resolves to a different extent head")
                entry["resolved_head_lcn"] = head["lcn"]
                if big_pcluster:
                    first = entry["lcn"] == head["lcn"] + 1
                    has_count = "compressed_blocks" in entry
                    if has_count != first or head["type"] == PLAIN:
                        raise ExtractionError("CBLKCNT must occur only in first NONHEAD of a compressed big pcluster")
        if not heads or heads[0]["logical_offset"] != 0:
            raise ExtractionError("first compact extent must start at file offset zero")
        extents = []
        for i, head in enumerate(heads):
            logical = head["logical_offset"]
            if logical == size:
                # EOF PLAIN markers contain no file data and consume NO blocks.
                if head["type"] != PLAIN or i != len(heads) - 1:
                    raise ExtractionError("invalid compact EOF sentinel")
                head["eof_sentinel"] = True
                continue
            end = heads[i + 1]["logical_offset"] if i + 1 < len(heads) else size
            # Validate kernel get_extent_decompressedlen lookahead traversal.
            cursor = head["lcn"] + 1
            while cursor < totalidx and entries[cursor]["type"] == NONHEAD:
                cursor += entries[cursor]["delta1"]
            mapped_end = size if cursor >= totalidx else entries[cursor]["logical_offset"]
            if mapped_end != end or end <= logical:
                raise ExtractionError("compact lookahead disagrees with exact extent length")
            outlen = end - logical
            if head["type"] == PLAIN and outlen > BLOCK:
                raise ExtractionError("plain compact extent exceeds one physical block")
            physical_blocks, count_lcn = 1, None
            if big_pcluster and head["type"] != PLAIN:
                successor = head["lcn"] + 1
                if successor >= totalidx:
                    raise ExtractionError("big compressed extent has no next index for CBLKCNT/one-block proof")
                first = entries[successor]
                if first["type"] == NONHEAD:
                    if first["delta0"] != 1 or not first.get("compressed_blocks"):
                        raise ExtractionError("missing/bogus first-NONHEAD CBLKCNT")
                    physical_blocks = first["compressed_blocks"]
                    count_lcn = successor
                # Otherwise the next HEAD/PLAIN explicitly proves one block.
            if physical_blocks > self.lz4_config["max_pclusterblks"]:
                raise ExtractionError("CBLKCNT physical count exceeds LZ4 max_pclusterblks")
            pblk = head["physical_block"]
            physical_end = (pblk + physical_blocks) * BLOCK
            if pblk <= 0 or pblk + physical_blocks > 0xFFFFFFFF or physical_end > self.fs_size:
                raise ExtractionError("exact physical extent outside EROFS")
            extents.append({"head_lcn": head["lcn"], "type": head["type"],
                            "logical_offset": logical, "decoded_bytes": outlen,
                            "physical_block": pblk, "physical_offset": pblk * BLOCK,
                            "physical_blocks": physical_blocks,
                            "physical_bytes": physical_blocks * BLOCK,
                            "physical_count_source": "CBLKCNT" if count_lcn is not None else
                            "plain one block" if head["type"] == PLAIN else
                            "next HEAD/PLAIN one block" if big_pcluster else "ordinary one block",
                            "cblkcnt_lcn": count_lcn,
                            "compression": "plain" if head["type"] == PLAIN else "lz4"})
        physical_blocks = sum(extent["physical_blocks"] for extent in extents)
        if physical_blocks != compressed_blocks:
            raise ExtractionError("inode compressed_blocks %d != exact physical blocks %d" %
                                  (compressed_blocks, physical_blocks))
        if sum(e["decoded_bytes"] for e in extents) != size:
            raise ExtractionError("extent coverage differs from inode byte count")
        # Check ALL physical ranges before any selected data read. Dedup/partial
        # references and overlaps are not silently accepted as ordinary extents.
        previous_end = 0
        for extent in sorted(extents, key=lambda item: item["physical_offset"]):
            start = extent["physical_offset"]
            stop = start + extent["physical_bytes"]
            if start < previous_end:
                raise ExtractionError("overlapping exact physical extents")
            if start < BLOCK or any(start < hi and stop > lo for lo, hi in self._metadata_ranges):
                raise ExtractionError("physical extent overlaps selected EROFS metadata")
            previous_end = stop
        if any(extent["compression"] == "lz4" for extent in extents) and not self.superblock["zero_padding"]:
            raise ExtractionError("non-ZERO_PADDING LZ4 needs partial decoding; no fallback")
        if self.physical_bytes + physical_blocks * BLOCK > self.max_physical_bytes:
            raise ExtractionError("total selected physical extent byte limit exceeded")
        return {"inode": dict(inode), "raw_inode_hex": raw.hex(),
                "i_ino": struct.unpack_from("<I", raw, 20)[0],
                "inode_bytes": inode_size, "xattr_icount": xattrs,
                "xattr_bytes": xattr_size, "compressed_blocks": compressed_blocks,
                "physical_blocks": physical_blocks, "physical_bytes": physical_blocks * BLOCK,
                "physical_ranges_verified": True, "big_pcluster": big_pcluster,
                "lz4_config": dict(self.lz4_config), "linux_source_sha256": REFERENCE_SHA256,
                "compressed_blocks_verified": True, "map_header_offset": header_offset,
                "map_header_hex": header.hex(), "advise": advise,
                "algorithms": algorithms, "h_clusterbits": cbits,
                "logical_clusterbits": 12, "index_base": ebase,
                "total_indexes": totalidx, "initial_4b_indexes": c4,
                "compacted_2b_indexes": c2,
                "index_packs": [{"offset": o, "length": len(p), "raw_hex": p.hex()}
                                for o, p in packs.items()],
                "indexes": entries, "extents": extents,
                "exact_index_verified": True, "candidate_search_used": False}

    def _extent_data(self, extents, nid):
        """Batch only adjacent exact extents, bounded to 1 MiB per read_at.

        OTA operation caching remains the parent's responsibility. No gap,
        neighbor candidate, or whole partition is read to improve throughput.
        """
        cursor = 0
        while cursor < len(extents):
            first = extents[cursor]
            start, length = first["physical_offset"], first["physical_bytes"]
            end = cursor + 1
            while end < len(extents):
                following = extents[end]
                if (following["physical_offset"] != start + length or
                        length + following["physical_bytes"] > MAX_READ_BATCH):
                    break
                length += following["physical_bytes"]
                end += 1
            batch = self._read(start, length,
                               "inode %d exact physical extents lcn %d..%d" %
                               (nid, first["head_lcn"], extents[end - 1]["head_lcn"]), physical=True)
            local = 0
            for index in range(cursor, end):
                extent = extents[index]
                stop = local + extent["physical_bytes"]
                yield extent, batch[local:stop]
                local = stop
            cursor = end

    def extract(self, inode):
        proof = self.plan(inode)
        output = bytearray()
        for extent, raw in self._extent_data(proof["extents"], inode["nid"]):
            expected = extent["decoded_bytes"]
            if extent["type"] == PLAIN:
                decoded = raw[:expected]
                padding = 0
            else:
                if not self.superblock["zero_padding"]:
                    raise ExtractionError("non-ZERO_PADDING LZ4 needs partial decoding; no fallback")
                padding = 0
                while padding < min(BLOCK, len(raw)) and raw[padding] == 0:
                    padding += 1
                if padding == min(BLOCK, len(raw)):
                    raise ExtractionError("all-zero first compressed block; corrupt pcluster")
                import lz4.block
                try:
                    decoded = lz4.block.decompress(raw[padding:], uncompressed_size=expected)
                except lz4.block.LZ4BlockError as exc:
                    raise ExtractionError("exact physical LZ4 extent failed (no neighbor search): " + str(exc)) from exc
                if len(decoded) != expected:
                    raise ExtractionError("LZ4 decoded length %d != exact extent length %d" % (len(decoded), expected))
            if extent["logical_offset"] != len(output):
                raise ExtractionError("noncontiguous decoded output")
            output.extend(decoded)
            extent.update(zero_padding_bytes=padding,
                          raw_sha256=hashlib.sha256(raw).hexdigest(),
                          decoded_sha256=hashlib.sha256(decoded).hexdigest(),
                          decoded_length_verified=True)
        data = bytes(output)
        if len(data) != inode["size"]:
            raise ExtractionError("output length differs from inode")
        proof.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                     byte_count_verified=True)
        return data, proof


def extract_file(fs, inode, **limits):
    """Import-safe parent API. Reuses fs.ranges including its cached blocks."""
    return ExactCompressedErofs(fs, **limits).extract(inode)


def validate_config(data, name):
    """Static validation only; never decrypt encrypted envelopes or run input."""
    config = load_module(Path(__file__).with_name("config_patch.py"), "erofs_config_validation")
    result = {"c16_reference_sha256": config.C16_STOCK_SHA256.get(name),
              "equals_c16_reference_bytes": hashlib.sha256(data).hexdigest() == config.C16_STOCK_SHA256.get(name)}
    if name == "camera_unit_config":
        obj = config.JsonDocument(data).obj
        result.update(format="json", valid=True, root_keys=list(obj),
                      native_xpan_operation_mode=obj.get("mode_operation_mode", {}).get("xpan_mode"),
                      rear_main_modes=obj.get("mode_type_list", {}).get("rear_main"))
    elif name == "camera_unit_feature_config.protobuf":
        fields = config.parse_wire(data)
        if config.serialize_wire(fields) != data:
            raise ExtractionError("protobuf wire round trip changed bytes")
        pool = [f.value.decode("utf-8") for f in fields if f.number == 1 and f.wire_type == 2]
        # Existing descriptor-derived schema: validation, not mutation.
        obj = config.decode_message(data, "FeatureTable", pool)
        result.update(format="indexed-protobuf", valid=True,
                      wire_roundtrip_exact=True, top_level_fields=len(fields),
                      string_pool_count=len(pool),
                      schema_basis="existing config_patch.PB_SCHEMA; descriptor not newly acquired",
                      version=obj["version"],
                      native_modes=[m["key"] for m in obj["camera_feature_table"]["modes"]])
    elif name == "oplus_camera_config":
        if data.lstrip().startswith(b"{"):
            obj = config.JsonDocument(data).obj
            records, tags = config.vendor_records(obj)
            result.update(format="json", valid=True, plaintext_validated=True,
                          file_version=str(obj.get("file_version")),
                          vendor_record_count=len(records),
                          xpan_records={tag: [record for _, record in matches]
                                        for tag, matches in tags.items() if "xpan" in tag},
                          encrypted_envelope=False)
            return result
        shape = (len(data) >= 24 and (len(data) - 8) % 16 == 0 and
                 data[:4] == bytes.fromhex("01013498") and data[-4:] == bytes.fromhex("606c3fff"))
        result.update(format="encrypted-envelope" if shape else "opaque-envelope",
                      observed_shape_valid=shape,
                      header_hex=data[:4].hex(), trailer_hex=data[-4:].hex(),
                      ciphertext_bytes=len(data) - 8,
                      ciphertext_block_aligned=(len(data) - 8) % 16 == 0,
                      plaintext_validated=False,
                      qualification="shape and exact raw hash only; no key, decryption or envelope-semantic claim")
    else:
        result.update(format="raw", semantic_validation_performed=False)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", required=True, type=Path)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--budget", type=int, default=MAX_BUDGET)
    args = parser.parse_args(argv)
    if not 0 < args.budget <= MAX_BUDGET:
        parser.error("total device transfer budget must not exceed 4 MiB")
    if args.output.exists():
        parser.error("output must be a new unused directory")
    if args.inventory.stat().st_size > 64 << 10:
        parser.error("inventory byte cap exceeded")
    targets = json.loads(args.inventory.read_text(encoding="utf-8"))
    if not isinstance(targets, list) or not 1 <= len(targets) <= 3:
        parser.error("inventory must select one to three explicit files")
    names = [Path(t["path"]).name for t in targets]
    allowed = {"camera_unit_config", "camera_unit_feature_config.protobuf", "oplus_camera_config"}
    if len(set(names)) != len(names) or any(n not in allowed for n in names):
        parser.error("only the three specified camera config names are accepted")
    module = load_module(Path(__file__).with_name("read_device_erofs.py"), "erofs_device_ranges")
    args.output.mkdir(parents=True)
    ranges = None
    report = {"status": "incomplete", "source": "direct dm EROFS; not overlaid pathname view",
              "device": args.device, "serial": args.serial,
              "source_inventory": str(args.inventory.resolve()),
              "linux_references": REFERENCES, "files": [],
              "phone_writes": False, "full_image": False, "candidate_search": False}
    try:
        ranges = module.DeviceRanges(args.adb, args.serial, args.device, args.budget)
        fs = module.reader.TargetedErofs(ranges)
        extractor = ExactCompressedErofs(fs)
        report["superblock"] = extractor.superblock
        for target, name in zip(targets, names):
            module.reader.TargetedErofs.path_parts(target["path"])
            inode = fs.resolve(target["path"])
            expected = target["inode"]
            for key in ("nid", "inode_offset", "mode", "size", "layout", "data_address_raw", "inline_offset"):
                if inode[key] != expected[key]:
                    raise ExtractionError("device directory/inode differs from input inventory: " + key)
            data, proof = extractor.extract(inode)
            proof.update(path=target["path"], path_resolved_from_dm=True,
                         input_inventory_metadata_verified=True)
            proof["validation"] = validate_config(data, name)
            destination = args.output / name
            with destination.open("xb") as stream:
                stream.write(data)
            proof["host_output"] = str(destination.resolve())
            report["files"].append(proof)
            print(json.dumps({"path": target["path"], "bytes": len(data),
                              "sha256": proof["sha256"], "extents": proof["extents"]}, ensure_ascii=False))
        report.update(status="complete", selected_metadata_bytes=extractor.metadata_bytes,
                      selected_physical_extent_bytes=extractor.physical_bytes)
    except Exception as exc:
        report.update(status="failed", error=type(exc).__name__ + ": " + str(exc))
    finally:
        if ranges is not None:
            ranges.audit["transferred_bytes"] = ranges.transferred
            ranges.audit["budget_bytes"] = args.budget
            ranges.audit.setdefault("erofs", {})["compressed_inode_policy"] = "exact compact index; no candidate search"
            report["transferred_device_bytes"] = ranges.transferred
            with (args.output / "read-audit.json").open("x", encoding="utf-8") as stream:
                json.dump(ranges.audit, stream, indent=2)
                stream.write("\n")
        with (args.output / "extraction.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    print(json.dumps({"status": report["status"], "error": report.get("error"),
                      "output": str(args.output.resolve()),
                      "transferred_device_bytes": report.get("transferred_device_bytes")}, ensure_ascii=False))
    return 0 if report["status"] == "complete" else 2


if __name__ == "__main__":
    sys.exit(main())
