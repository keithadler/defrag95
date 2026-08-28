"""Tests for the real-volume tooling.

The benchmark itself needs a 1.6 GB disk image and a QEMU run, neither of which
belongs in a test suite. What can be tested here is the part everything else
depends on: that the FAT16 parser reads a real on-disk structure correctly, and
that a trace maps onto it and back out again without losing anything. So these
build an actual FAT16 filesystem, byte by byte, and parse it.
"""

import os
import struct
import tempfile
import unittest

from tests.common import *      # noqa: F401,F403
from real.fat16 import Fat16
from real import trace as T


SECTOR = 512
CLUSTERS = 5000                 # comfortably inside FAT16's 4085..65524
FAT_SECTORS = ((CLUSTERS + 2) * 2 + SECTOR - 1) // SECTOR
ROOT_SECTORS = 32
DATA_START = 1 + 2 * FAT_SECTORS + ROOT_SECTORS


def build_image(path, files):
    """Write a real FAT16 volume containing `files`: {name: [cluster, ...]}."""
    total = DATA_START + CLUSTERS
    img = bytearray(total * SECTOR)

    bpb = bytearray(SECTOR)
    bpb[0:3] = b"\xeb\x3c\x90"
    bpb[3:11] = b"DEFRAG95"
    struct.pack_into("<H", bpb, 11, SECTOR)      # bytes per sector
    bpb[13] = 1                                  # sectors per cluster
    struct.pack_into("<H", bpb, 14, 1)           # reserved
    bpb[16] = 2                                  # FATs
    struct.pack_into("<H", bpb, 17, 512)         # root entries
    struct.pack_into("<H", bpb, 19, 0)           # small total (use large)
    bpb[21] = 0xF8
    struct.pack_into("<H", bpb, 22, FAT_SECTORS)
    struct.pack_into("<H", bpb, 24, 63)
    struct.pack_into("<H", bpb, 26, 255)
    struct.pack_into("<I", bpb, 32, total)
    bpb[510:512] = b"\x55\xaa"
    img[0:SECTOR] = bpb

    fat = bytearray(FAT_SECTORS * SECTOR)
    struct.pack_into("<HH", fat, 0, 0xFFF8, 0xFFFF)
    root = bytearray(ROOT_SECTORS * SECTOR)
    for i, (name, chain) in enumerate(files.items()):
        for j, c in enumerate(chain):
            nxt = chain[j + 1] if j + 1 < len(chain) else 0xFFFF
            struct.pack_into("<H", fat, c * 2, nxt)
        stem, _, ext = name.partition(".")
        entry = bytearray(32)
        entry[0:8] = stem.ljust(8).encode()[:8]
        entry[8:11] = ext.ljust(3).encode()[:3]
        entry[11] = 0x20
        struct.pack_into("<H", entry, 26, chain[0])
        struct.pack_into("<I", entry, 28, len(chain) * SECTOR)
        root[i * 32:(i + 1) * 32] = entry

    img[1 * SECTOR:(1 + FAT_SECTORS) * SECTOR] = fat
    img[(1 + FAT_SECTORS) * SECTOR:(1 + 2 * FAT_SECTORS) * SECTOR] = fat
    start = (1 + 2 * FAT_SECTORS) * SECTOR
    img[start:start + len(root)] = root
    with open(path, "wb") as fh:
        fh.write(img)


class TestFat16Parser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.path = os.path.join(cls.dir, "test.img")
        cls.files = {
            "WHOLE.BIN": [2, 3, 4, 5],                 # contiguous
            "SPLIT.BIN": [10, 11, 40, 41, 90],         # three extents
            "ONE.BIN": [200],
        }
        build_image(cls.path, cls.files)
        cls.fs = Fat16(cls.path)

    def test_geometry_is_read_from_the_volume(self):
        self.assertEqual(self.fs.cluster_count, CLUSTERS)
        self.assertEqual(self.fs.sectors_per_cluster, 1)
        self.assertEqual(self.fs.data_start, DATA_START)

    def test_every_file_is_found_with_its_chain(self):
        self.assertEqual(set(self.fs.entries), set(self.files))
        for name, chain in self.files.items():
            self.assertEqual(self.fs.entries[name].chain, chain, name)

    def test_extents_are_counted_correctly(self):
        self.assertEqual(self.fs._extent_count(self.files["WHOLE.BIN"]), 1)
        self.assertEqual(self.fs._extent_count(self.files["SPLIT.BIN"]), 3)

    def test_cluster_and_lba_round_trip(self):
        for c in (2, 100, CLUSTERS + 1):
            self.assertEqual(self.fs.cluster_of_lba(self.fs.lba_of_cluster(c)), c)

    def test_metadata_regions_are_recognised(self):
        self.assertEqual(self.fs.region_of_lba(0), "boot")
        self.assertEqual(self.fs.region_of_lba(1), "fat")
        self.assertEqual(self.fs.region_of_lba(1 + 2 * FAT_SECTORS), "root")
        self.assertEqual(self.fs.region_of_lba(DATA_START), "data")

    def test_the_report_matches_what_was_written(self):
        r = self.fs.fragmentation_report()
        self.assertEqual(r["files"], 3)
        self.assertEqual(r["used_clusters"], 10)
        # of the two multi-cluster files, one is fragmented
        self.assertAlmostEqual(r["pct_fragmented"], 50.0)

    def test_owner_map_is_one_to_one(self):
        owner = self.fs.owner_map()
        self.assertEqual(len(owner), sum(len(c) for c in self.files.values()))
        self.assertEqual(owner[40], ("SPLIT.BIN", 2))


class TestTraceMapping(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.path = os.path.join(cls.dir, "t.img")
        build_image(cls.path, {"A.BIN": [2, 3], "B.BIN": [500]})
        cls.fs = Fat16(cls.path)

    def test_trace_lines_are_parsed(self):
        log = os.path.join(self.dir, "trace.log")
        with open(log, "w") as fh:
            fh.write("blk_co_preadv blk 0x1 bs 0x2 offset 1024 bytes 512 flags 0x0\n")
            fh.write("unrelated trace line that must be ignored\n")
            fh.write("blk_co_pwritev blk 0x1 bs 0x2 offset 2048 bytes 1024 flags 0x0\n")
        raw = T.parse(log)
        self.assertEqual(len(raw), 2)
        self.assertEqual((raw[0].offset, raw[0].length, raw[0].write), (1024, 512, False))
        self.assertTrue(raw[1].write)

    def test_a_data_access_maps_to_the_file_that_owns_it(self):
        lba = self.fs.lba_of_cluster(500)
        raw = [T.RawAccess(lba * SECTOR, SECTOR, False)]
        logical, stats = T.to_logical(self.fs, raw, 0)
        self.assertEqual(stats["file"], 1)
        self.assertEqual(stats["unowned"], 0)
        self.assertEqual(logical[0].path, "B.BIN")
        self.assertEqual(logical[0].cluster_index, 0)

    def test_an_access_spanning_two_clusters_is_split(self):
        lba = self.fs.lba_of_cluster(2)
        raw = [T.RawAccess(lba * SECTOR, 2 * SECTOR, False)]
        logical, _ = T.to_logical(self.fs, raw, 0)
        self.assertEqual([a.cluster_index for a in logical], [0, 1])
        self.assertTrue(all(a.path == "A.BIN" for a in logical))

    def test_metadata_stays_metadata(self):
        raw = [T.RawAccess(SECTOR, SECTOR, False)]          # inside the FAT
        logical, stats = T.to_logical(self.fs, raw, 0)
        self.assertEqual(stats["meta"], 1)
        self.assertEqual(logical[0].kind, "meta")


if __name__ == "__main__":
    unittest.main()
