"""Focused compact EROFS regression tests; no adb or phone operations."""
import importlib.util
from pathlib import Path
import struct
import unittest
import lz4.block

SOURCE = Path(__file__).resolve().parents[1] / "tools" / "erofs_compressed.py"
spec = importlib.util.spec_from_file_location("exact_erofs_tested", SOURCE)
e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e)


def pack(values, base, shift=2):
    count = 2 if shift == 2 else 16
    bits = 16 if shift == 2 else 14
    value = sum((lo | (kind << 12)) << (bits * i)
                for i, (lo, kind) in enumerate(values))
    return value.to_bytes((count << shift) - 4, "little") + struct.pack("<I", base)


class MemoryRanges:
    def __init__(self):
        self.data = bytearray(64 * 4096)
        self.size = len(self.data)
        self.audit = {}
        self.reads = []

    def read_at(self, offset, length, reason="test"):
        self.reads.append((offset, length))
        return bytes(self.data[offset:offset + length])


class MemoryFs:
    block, root, meta, blocks, incompat = 4096, 1, 0, 64, 1

    def __init__(self, payload=b"camera config\n" * 1600, advise=1):
        self.ranges = MemoryRanges()
        data = self.ranges.data
        struct.pack_into("<I", data, 1024, 0xE0F5E1E2)
        data[1036] = 12
        struct.pack_into("<H", data, 1038, self.root)
        struct.pack_into("<I", data, 1104, self.incompat)
        struct.pack_into("<I", data, 1060, self.blocks)
        off = 4096
        size = len(payload)
        struct.pack_into("<HHHHIII", data, off, 6, 2, 0o100644, 1, size, 0, 1)
        # xattr_icount=2 -> 16-byte xattrs; header at inode+48; ebase+56.
        struct.pack_into("<HHHBB", data, off + 48, 0, 0, advise, 0, 0)
        total = (size + 4095) // 4096
        values = [(0, e.HEAD1)]
        for lcn in range(1, total):
            if lcn * 4096 <= size < (lcn + 1) * 4096 and size % 4096:
                # Explicit EOF sentinel in last cluster.
                values.append((size % 4096, e.PLAIN))
            else:
                values.append((lcn, e.NONHEAD))
        # Set pack-end NONHEAD lo to distance to sentinel, not d0.
        for lcn in range(1, total, 2):
            if values[lcn][1] == e.NONHEAD:
                values[lcn] = (total - 1 - lcn, e.NONHEAD)
        for lcn in range(0, total, 2):
            pair = values[lcn:lcn + 2]
            if len(pair) == 1:
                pair.append((0, e.PLAIN))
            data[off + 56 + lcn * 4:off + 64 + lcn * 4] = pack(pair, 9)
        compressed = lz4.block.compress(payload, store_size=False)
        assert len(compressed) <= 4096
        data[10 * 4096:11 * 4096] = bytes(4096 - len(compressed)) + compressed
        self.inode = {"nid": off // 32, "inode_offset": off,
                      "inline_offset": off + 48, "layout": 3,
                      "mode": 0o100644, "type": "file", "size": size,
                      "data_address_raw": 1}
        self.payload = payload


class BigMemoryFs:
    """Explicit on-disk compact fixtures; no neighbor/block-count inference."""
    block, root, meta, blocks = 4096, 1, 0, 64

    def __init__(self, values, size, compressed_blocks, *, bases=None, advise=7,
                 maximum=4, distance=65535, extensions=0, incompat=3):
        self.incompat = incompat
        self.ranges = MemoryRanges()
        data = self.ranges.data
        struct.pack_into("<I", data, 1024, 0xE0F5E1E2)
        data[1036], data[1037] = 12, extensions
        struct.pack_into("<H", data, 1038, self.root)
        struct.pack_into("<I", data, 1060, self.blocks)
        struct.pack_into("<IH", data, 1104, incompat, 1)
        self.config_offset = 1152 + extensions * 16
        struct.pack_into("<HHH", data, self.config_offset, 14, distance, maximum)
        off = 4096
        struct.pack_into("<HHHHIII", data, off, 6, 2, 0o100644, 1, size, 0, compressed_blocks)
        struct.pack_into("<HHHBB", data, off + 48, 0, 0, advise, 0, 0)
        self.inode = {"nid": off // 32, "inode_offset": off, "inline_offset": off + 48,
                      "layout": 3, "mode": 0o100644, "type": "file", "size": size,
                      "data_address_raw": compressed_blocks}
        assert len(values) == (size + 4095) // 4096
        c2 = (len(values) - 2) // 16 * 16 if advise & e.COMPACTED_2B and len(values) > 2 else 0
        groups = [(0, min(2, len(values)), 2)]
        if c2:
            groups.append((2, c2, 1))
        if len(values) > 2 + c2:
            groups.append((2 + c2, len(values) - 2 - c2, 2))
        self.pack_offsets = []
        pos = off + 56
        base_values = iter(bases or [10] * len(values))
        for start, count, shift in groups:
            vcnt = 16 if shift == 1 else 2
            for lcn in range(start, start + count, vcnt):
                items = values[lcn:min(lcn + vcnt, start + count)]
                items = items + [(0, e.PLAIN)] * (vcnt - len(items))
                encoded = pack(items, next(base_values), shift)
                self.pack_offsets.append(pos)
                data[pos:pos + len(encoded)] = encoded
                pos += len(encoded)

    def put_lz4(self, block, blocks, payload):
        encoded = lz4.block.compress(payload, store_size=False)
        self.put_encoded(block, blocks, encoded)
        return encoded

    def put_encoded(self, block, blocks, encoded):
        assert (blocks - 1) * 4096 < len(encoded) <= blocks * 4096
        start = block * 4096
        self.ranges.data[start:start + blocks * 4096] = bytes(blocks * 4096 - len(encoded)) + encoded

    def data_reads(self):
        return [item for item in self.ranges.reads if item[0] >= 10 * 4096]


def literal_lz4(payload):
    """One exact final-literal sequence, useful for multi-block test pclusters."""
    assert len(payload) >= 15
    extension = len(payload) - 15
    return b"\xf0" + b"\xff" * (extension // 255) + bytes([extension % 255]) + payload


class CompactIndexTests(unittest.TestCase):
    def test_first_head_uses_base_plus_one_not_base(self):
        decoded = e.unpack_compacted_index(pack([(0, e.HEAD1), (3, e.NONHEAD)], 100), 0, 2)
        self.assertEqual(decoded["physical_block"], 101)
        self.assertEqual(decoded["block_increment"], 1)

    def test_second_head_counts_prior_head(self):
        decoded = e.unpack_compacted_index(pack([(19, e.HEAD1), (30, e.HEAD1)], 100), 1, 2)
        self.assertEqual(decoded["physical_block"], 102)

    def test_pack_end_nonhead_lo_is_forward_distance(self):
        data = pack([(2, e.NONHEAD), (7, e.NONHEAD)], 100)
        decoded = e.unpack_compacted_index(data, 1, 2)
        self.assertEqual(decoded["delta0"], 3)
        self.assertEqual(decoded["delta1"], 7)

    def test_pack_end_nonhead_after_head_looks_back_one(self):
        data = pack([(0, e.HEAD1), (9, e.NONHEAD)], 100)
        self.assertEqual(e.unpack_compacted_index(data, 1, 2)["delta0"], 1)

    def test_compact_2byte_pack_exact_bit_decode(self):
        values = [(0, e.HEAD1), (1, e.NONHEAD), (2, e.NONHEAD), (3, e.NONHEAD),
                  (24, e.HEAD1)] + [(i - 4, e.NONHEAD) for i in range(5, 15)] + [(4, e.NONHEAD)]
        data = pack(values, 200, 1)
        decoded = e.unpack_compacted_index(data, 4, 1)
        self.assertEqual(decoded["cluster_offset"], 24)
        self.assertEqual(decoded["physical_block"], 202)
        self.assertEqual(e.unpack_compacted_index(data, 15, 1)["delta0"], 11)
        self.assertEqual(e.unpack_compacted_index(data, 15, 1)["delta1"], 4)

    def test_cblkcnt_without_big_pcluster_fails(self):
        with self.assertRaisesRegex(e.ExtractionError, "CBLKCNT"):
            e.unpack_compacted_index(pack([(0, e.HEAD1), (e.CBLKCNT | 2, e.NONHEAD)], 100), 1, 2)

    def test_truncated_pack_fails(self):
        with self.assertRaises(e.ExtractionError):
            e.unpack_compacted_index(b"\x00" * 7, 0, 2)

    def test_exact_zero_padded_lz4_and_eof_sentinel(self):
        fs = MemoryFs()
        data, proof = e.extract_file(fs, fs.inode)
        self.assertEqual(data, fs.payload)
        self.assertEqual(proof["compressed_blocks"], 1)
        self.assertEqual(len(proof["extents"]), 1)
        self.assertTrue(proof["indexes"][-1]["eof_sentinel"])
        self.assertEqual(proof["extents"][0]["physical_block"], 10)
        self.assertEqual([p for p in fs.ranges.reads if p[0] >= 10 * 4096], [(10 * 4096, 4096)])

    def test_final_odd_index_pack_and_initial_4byte_alignment(self):
        fs = MemoryFs(b"abc\n" * 2200)
        data, proof = e.extract_file(fs, fs.inode)
        self.assertEqual(data, fs.payload)
        self.assertEqual(proof["initial_4b_indexes"], 2)
        self.assertEqual(proof["total_indexes"], 3)
        self.assertEqual(proof["bytes"], 8800)

    def test_bad_lz4_never_reads_neighbors(self):
        fs = MemoryFs()
        fs.ranges.data[10 * 4096:11 * 4096] = b"x" * 4096
        neighbor = b"valid neighbor" * 100
        fs.ranges.data[11 * 4096:12 * 4096] = neighbor + bytes(4096 - len(neighbor))
        with self.assertRaisesRegex(e.ExtractionError, "no neighbor search"):
            e.extract_file(fs, fs.inode)
        self.assertEqual([p for p in fs.ranges.reads if p[0] >= 10 * 4096], [(10 * 4096, 4096)])

    def test_exact_decode_length_not_just_capacity(self):
        fs = MemoryFs()
        encoded = lz4.block.compress(b"short\n" * 100, store_size=False)
        fs.ranges.data[10 * 4096:11 * 4096] = bytes(4096 - len(encoded)) + encoded
        with self.assertRaisesRegex(e.ExtractionError, "decoded length"):
            e.extract_file(fs, fs.inode)

    def test_inode_compressed_blocks_is_count_not_address(self):
        fs = MemoryFs()
        struct.pack_into("<I", fs.ranges.data, 4096 + 16, 2)
        fs.inode["data_address_raw"] = 2
        with self.assertRaisesRegex(e.ExtractionError, "compressed_blocks"):
            e.extract_file(fs, fs.inode)
        self.assertFalse(any(offset >= 10 * 4096 for offset, _ in fs.ranges.reads))

    def test_unsupported_tail_fragment_and_invalid_big_flags_fail_before_data(self):
        for flag, message in ((e.INLINE, "ZTAILPACKING"), (e.FRAGMENT, "fragment"),
                              (e.BIG_1, "big pclusters"), (e.BIG_1 | e.BIG_2, "superblock")):
            with self.subTest(flag=flag):
                fs = MemoryFs(advise=flag)
                with self.assertRaisesRegex(e.ExtractionError, message):
                    e.extract_file(fs, fs.inode)
                self.assertFalse(any(offset >= 10 * 4096 for offset, _ in fs.ranges.reads))

    def test_physical_and_output_limits(self):
        fs = MemoryFs()
        with self.assertRaisesRegex(e.ExtractionError, "oversized"):
            e.extract_file(fs, fs.inode, max_file_bytes=100)
        with self.assertRaisesRegex(e.ExtractionError, "physical extent byte limit"):
            e.extract_file(fs, fs.inode, max_physical_bytes=100)
        self.assertFalse(any(offset >= 10 * 4096 for offset, _ in fs.ranges.reads))

    def test_oplus_plaintext_and_encrypted_envelope_are_distinguished(self):
        data = b'{"file_version":14.003,"file_data":[]}'
        validation = e.validate_config(data, "oplus_camera_config")
        self.assertEqual(validation["format"], "json")
        self.assertTrue(validation["plaintext_validated"])
        self.assertFalse(validation["encrypted_envelope"])
        envelope = bytes.fromhex("01013498") + bytes(16) + bytes.fromhex("606c3fff")
        validation = e.validate_config(envelope, "oplus_camera_config")
        self.assertTrue(validation["observed_shape_valid"])
        self.assertFalse(validation["plaintext_validated"])

    def test_all_zero_pcluster_fails(self):
        fs = MemoryFs()
        fs.ranges.data[10 * 4096:11 * 4096] = bytes(4096)
        with self.assertRaisesRegex(e.ExtractionError, "all-zero"):
            e.extract_file(fs, fs.inode)


class BigCompactTests(unittest.TestCase):
    def literal_fs(self, *, blocks=3):
        payload = bytes(range(251)) * 44
        values = [(0, e.HEAD1), (e.CBLKCNT | blocks, e.NONHEAD),
                  (len(payload) % 4096, e.PLAIN)]
        fs = BigMemoryFs(values, len(payload), blocks, bases=[10, 10 + blocks])
        if blocks == 3:
            fs.put_encoded(10, blocks, literal_lz4(payload))
        return fs, payload

    def test_big_first_head_uses_base_not_base_plus_one(self):
        raw = pack([(0, e.HEAD1), (e.CBLKCNT | 4, e.NONHEAD)], 100)
        head = e.unpack_compacted_index(raw, 0, 2, big_pcluster=True)
        count = e.unpack_compacted_index(raw, 1, 2, big_pcluster=True)
        self.assertEqual((head["physical_block"], head["block_increment"]), (100, 0))
        self.assertEqual((count["delta0"], count["delta1"], count["compressed_blocks"]), (1, 1, 4))

    def test_big_head_increment_uses_physical_counts_not_head_count(self):
        values = [(0, e.HEAD1), (e.CBLKCNT | 4, e.NONHEAD), (2, e.NONHEAD),
                  (0, e.HEAD1), (e.CBLKCNT | 2, e.NONHEAD), (19, e.HEAD1)]
        values += [(0, e.PLAIN)] * (16 - len(values))
        raw = pack(values, 100, 1)
        self.assertEqual(e.unpack_compacted_index(raw, 3, 1, big_pcluster=True)["physical_block"], 104)
        self.assertEqual(e.unpack_compacted_index(raw, 5, 1, big_pcluster=True)["physical_block"], 106)

    def test_big_pack_end_after_count_has_indirect_lookback(self):
        raw = pack([(e.CBLKCNT | 3, e.NONHEAD), (7, e.NONHEAD)], 100)
        item = e.unpack_compacted_index(raw, 1, 2, big_pcluster=True)
        self.assertEqual((item["delta0"], item["delta1"]), (2, 7))
        self.assertEqual(e.unpack_compacted_index(raw, 0, 2, big_pcluster=True)["delta1"], 8)

    def test_big_two_byte_pack_last_count_is_not_forward_count(self):
        values = [(0, e.PLAIN)] * 14 + [(0, e.HEAD1), (e.CBLKCNT | 4, e.NONHEAD)]
        item = e.unpack_compacted_index(pack(values, 100, 1), 15, 1, big_pcluster=True)
        self.assertEqual((item["delta0"], item["delta1"], item["compressed_blocks"]), (1, 1, 4))

    def test_exact_multiblock_decode_sum_and_no_neighbor_read(self):
        fs, payload = self.literal_fs()
        extractor = e.ExactCompressedErofs(fs)
        data, proof = extractor.extract(fs.inode)
        self.assertEqual(data, payload)
        self.assertEqual(proof["physical_blocks"], 3)
        self.assertEqual(len(proof["extents"]), 1)
        self.assertEqual(proof["extents"][0]["physical_bytes"], 3 * 4096)
        self.assertEqual(proof["extents"][0]["physical_count_source"], "CBLKCNT")
        self.assertLess(proof["extents"][0]["zero_padding_bytes"], 4096)
        self.assertEqual(fs.data_reads(), [(10 * 4096, 3 * 4096)])
        self.assertEqual(extractor.physical_bytes, 3 * 4096)

    def test_count_crosses_fourbyte_pack_boundary(self):
        size = 4 * 4096 + 113
        values = [(0, e.PLAIN), (0, e.HEAD1), (e.CBLKCNT | 3, e.NONHEAD),
                  (1, e.NONHEAD), (113, e.PLAIN)]
        fs = BigMemoryFs(values, size, 4, bases=[10, 11, 14])
        proof = e.ExactCompressedErofs(fs).plan(fs.inode)
        self.assertEqual([item["physical_blocks"] for item in proof["extents"]], [1, 3])
        self.assertEqual([item["physical_block"] for item in proof["extents"]], [10, 11])
        self.assertEqual(proof["indexes"][3]["delta0"], 2)
        self.assertEqual(proof["indexes"][3]["resolved_head_lcn"], 1)
        self.assertFalse(fs.data_reads())

    def test_two_byte_alignment_and_lookback_across_pack(self):
        values = [(0, e.PLAIN), (0, e.HEAD1), (e.CBLKCNT | 4, e.NONHEAD)]
        values += [(lcn - 1, e.NONHEAD) for lcn in range(3, 17)]
        values += [(1, e.NONHEAD), (29, e.PLAIN)]
        fs = BigMemoryFs(values, 18 * 4096 + 29, 5, bases=[10, 11, 15])
        proof = e.ExactCompressedErofs(fs).plan(fs.inode)
        self.assertEqual(proof["compacted_2b_indexes"], 16)
        self.assertEqual(proof["indexes"][17]["delta0"], 16)
        self.assertEqual(proof["indexes"][18]["physical_block"], 15)
        self.assertEqual([item["physical_blocks"] for item in proof["extents"]], [1, 4])

    def test_plain_extents_stay_one_block_and_batch_only_adjacency(self):
        for third in (12, 13):
            with self.subTest(third=third):
                fs = BigMemoryFs([(0, e.PLAIN)] * 3, 3 * 4096, 3, bases=[10, third])
                fs.ranges.data[10 * 4096:12 * 4096] = b"a" * 4096 + b"b" * 4096
                fs.ranges.data[third * 4096:(third + 1) * 4096] = b"c" * 4096
                data, proof = e.extract_file(fs, fs.inode)
                self.assertEqual(data, b"a" * 4096 + b"b" * 4096 + b"c" * 4096)
                expected = ([(10 * 4096, 3 * 4096)] if third == 12 else
                            [(10 * 4096, 8192), (13 * 4096, 4096)])
                self.assertEqual(fs.data_reads(), expected)
                self.assertTrue(all(item["physical_blocks"] == 1 for item in proof["extents"]))

    def test_lz4_config_after_superblock_extension_and_zero_defaults(self):
        fs = BigMemoryFs([(0, e.PLAIN)], 100, 1, maximum=0, distance=0, extensions=2)
        extractor = e.ExactCompressedErofs(fs)
        self.assertEqual(extractor.lz4_config["length_offset"], 1184)
        self.assertEqual(extractor.lz4_config["max_pclusterblks"], 1)
        self.assertEqual(extractor.lz4_config["max_distance"], 65535)
        self.assertEqual(extractor.lz4_config["max_distance_pages"], 17)
        self.assertIn((1184, 2), fs.ranges.reads)
        self.assertIn((1186, 14), fs.ranges.reads)

    def test_unknown_or_malformed_configs_fail_without_data(self):
        cases = [(1108, "H", 3, "compression algorithms"),
                 (1152, "H", 0, "config size"), (1152, "H", 13, "config size"),
                 (1156, "H", 257, "pcluster cap"), (1158, "B", 1, "reserved")]
        for offset, fmt, value, message in cases:
            with self.subTest(offset=offset, value=value):
                fs, _ = self.literal_fs()
                struct.pack_into("<" + fmt, fs.ranges.data, offset, value)
                with self.assertRaisesRegex(e.ExtractionError, message):
                    e.extract_file(fs, fs.inode)
                self.assertFalse(fs.data_reads())

    def test_missing_bonus_zero_count_and_bogus_lookback_fail(self):
        cases = [[(0, e.HEAD1), (1, e.NONHEAD), (113, e.PLAIN)],
                 [(0, e.HEAD1), (e.CBLKCNT, e.NONHEAD), (113, e.PLAIN)],
                 [(0, e.HEAD1), (e.CBLKCNT | 3, e.NONHEAD), (e.CBLKCNT | 3, e.NONHEAD), (113, e.PLAIN)],
                 [(0, e.HEAD1), (e.CBLKCNT | 3, e.NONHEAD), (8, e.NONHEAD), (113, e.PLAIN)]]
        for values in cases:
            with self.subTest(values=values):
                fs = BigMemoryFs(values, (len(values) - 1) * 4096 + 113, 3)
                with self.assertRaises(e.ExtractionError):
                    e.extract_file(fs, fs.inode)
                self.assertFalse(fs.data_reads())

    def test_configured_count_and_total_budget_checked_before_reads(self):
        fs, _ = self.literal_fs(blocks=5)
        with self.assertRaisesRegex(e.ExtractionError, "max_pclusterblks"):
            e.extract_file(fs, fs.inode)
        self.assertFalse(fs.data_reads())
        fs, _ = self.literal_fs()
        with self.assertRaisesRegex(e.ExtractionError, "physical extent byte limit"):
            e.extract_file(fs, fs.inode, max_physical_bytes=8192)
        self.assertFalse(fs.data_reads())

    def test_corrupt_big_lz4_no_neighbor_recovery(self):
        fs, payload = self.literal_fs()
        fs.ranges.data[10 * 4096:13 * 4096] = b"x" * (3 * 4096)
        encoded = lz4.block.compress(payload, store_size=False)
        fs.ranges.data[13 * 4096:14 * 4096] = bytes(4096 - len(encoded)) + encoded
        with self.assertRaisesRegex(e.ExtractionError, "no neighbor search"):
            e.extract_file(fs, fs.inode)
        self.assertEqual(fs.data_reads(), [(10 * 4096, 3 * 4096)])

    def test_padding_is_limited_to_first_block(self):
        fs, payload = self.literal_fs()
        encoded = lz4.block.compress(payload, store_size=False)
        fs.ranges.data[10 * 4096:13 * 4096] = bytes(3 * 4096 - len(encoded)) + encoded
        with self.assertRaisesRegex(e.ExtractionError, "all-zero first"):
            e.extract_file(fs, fs.inode)
        self.assertEqual(fs.data_reads(), [(10 * 4096, 3 * 4096)])

    def test_physical_overlap_and_filesystem_bounds_fail_before_data(self):
        values = [(0, e.HEAD1), (e.CBLKCNT | 3, e.NONHEAD),
                  (0, e.HEAD1), (e.CBLKCNT | 3, e.NONHEAD), (113, e.PLAIN)]
        for bases, message in (([10, 12, 15], "overlapping"),
                               ([10, 63, 15], "outside EROFS"),
                               ([1, 10, 13], "metadata"),
                               ([0xFFFFFFFF, 10, 13], "physical block address")):
            with self.subTest(bases=bases):
                fs = BigMemoryFs(values, 4 * 4096 + 113, 6, bases=bases)
                with self.assertRaisesRegex(e.ExtractionError, message):
                    e.extract_file(fs, fs.inode)
                self.assertFalse(fs.data_reads())

    def test_metadata_budget_truncated_ranges_unknown_flags_and_head2(self):
        fs, _ = self.literal_fs()
        with self.assertRaisesRegex(e.ExtractionError, "metadata byte limit"):
            e.extract_file(fs, fs.inode, max_metadata_bytes=143)
        self.assertFalse(fs.data_reads())
        fs, _ = self.literal_fs()
        read = fs.ranges.read_at
        fs.ranges.read_at = lambda offset, length, reason: read(offset, length, reason)[:-1]
        with self.assertRaisesRegex(e.ExtractionError, "truncated range"):
            e.extract_file(fs, fs.inode)
        for advise, incompat in ((0x40, 3), (7, 0x43)):
            fs = BigMemoryFs([(0, e.PLAIN)], 100, 1, advise=advise, incompat=incompat)
            with self.assertRaises(e.ExtractionError):
                e.extract_file(fs, fs.inode)
        fs = BigMemoryFs([(0, e.HEAD2), (e.CBLKCNT | 3, e.NONHEAD), (113, e.PLAIN)],
                         8192 + 113, 3)
        with self.assertRaisesRegex(e.ExtractionError, "HEAD2"):
            e.extract_file(fs, fs.inode)

    def test_device_ceiling_remains_four_mib_and_local_is_explicit(self):
        fs, _ = self.literal_fs()
        with self.assertRaisesRegex(e.ExtractionError, "file limit"):
            e.extract_file(fs, fs.inode, max_file_bytes=8 << 20)
        extractor = e.ExactCompressedErofs(fs, max_file_bytes=e.MAX_LOCAL_BUDGET,
                                         max_physical_bytes=e.MAX_LOCAL_BUDGET,
                                         budget_ceiling=e.MAX_LOCAL_BUDGET)
        self.assertEqual(extractor.plan(fs.inode)["physical_blocks"], 3)
        with self.assertRaisesRegex(e.ExtractionError, "ceilings"):
            e.extract_file(fs, fs.inode, budget_ceiling=256 << 20)


if __name__ == "__main__":
    unittest.main(verbosity=2)
