"""The volume model: allocation, fragmentation, and turning byte ranges into I/O."""

import unittest

from tests.common import *      # noqa: F401,F403
from sim.drive import DRIVES, Drive
from sim.filesystem import OutOfSpace, Volume


def small_volume(policy="next-fit"):
    # a deliberately tiny partition so the allocator's behaviour is visible
    return Volume(Drive(DRIVES["1996"]), partition_sectors=64 * 1024,
                  alloc_policy=policy)


class TestAllocation(unittest.TestCase):
    def test_fresh_files_are_contiguous(self):
        v = small_volume()
        v.create("C:\\A", 200 * 1024, "data")
        v.create("C:\\B", 200 * 1024, "data")
        self.assertEqual(v.fragments(v.by_path["C:\\A"]), 1)
        self.assertEqual(v.fragments(v.by_path["C:\\B"]), 1)

    def test_interleaved_churn_fragments_files(self):
        """This is the whole premise: next-fit plus churn produces fragments."""
        v = small_volume()
        for i in range(6):
            v.create("C:\\PAD%d" % i, 32 * 1024, "temp")
        v.create("C:\\DOC", 32 * 1024, "data")
        for i in range(0, 6, 2):
            v.delete("C:\\PAD%d" % i)
        for i in range(4):
            v.create("C:\\NEW%d" % i, 32 * 1024, "temp")
            v.append("C:\\DOC", 32 * 1024)
        self.assertGreater(v.fragments(v.by_path["C:\\DOC"]), 1)

    def test_delete_returns_the_space(self):
        v = small_volume()
        before = v.free_count
        v.create("C:\\A", 320 * 1024, "data")
        self.assertLess(v.free_count, before)
        v.delete("C:\\A")
        self.assertEqual(v.free_count, before)

    def test_a_failed_create_leaves_nothing_behind(self):
        v = small_volume()
        with self.assertRaises(OutOfSpace):
            v.create("C:\\HUGE", v.capacity_bytes() * 2, "cold")
        self.assertNotIn("C:\\HUGE", v.by_path)
        self.assertEqual(v.free_count, v.cluster_count)

    def test_first_fit_and_next_fit_differ(self):
        placements = {}
        for policy in ("first-fit", "next-fit"):
            v = small_volume(policy)
            for i in range(4):
                v.create("C:\\P%d" % i, 32 * 1024, "temp")
            v.delete("C:\\P0")
            v.create("C:\\X", 32 * 1024, "temp")
            placements[policy] = v.chain[v.by_path["C:\\X"]][0]
        self.assertEqual(placements["first-fit"], 0)      # reuses the hole
        self.assertNotEqual(placements["next-fit"], 0)    # carries on past it


class TestReadPlanning(unittest.TestCase):
    def setUp(self):
        self.v = small_volume()
        self.v.create("C:\\BIG", 320 * 1024, "data")      # 10 clusters

    def test_a_small_read_costs_a_small_number_of_sectors(self):
        runs = self.v.read_runs("C:\\BIG", 0, 4096)
        self.assertEqual(sum(n for _, _, n in runs), 8)

    def test_a_whole_file_read_covers_every_sector(self):
        runs = self.v.read_runs("C:\\BIG", 0, 320 * 1024)
        self.assertEqual(sum(n for _, _, n in runs), 10 * self.v.cluster_sectors)

    def test_an_offset_read_starts_in_the_right_cluster(self):
        runs = self.v.read_runs("C:\\BIG", 96 * 1024, 4096)
        chain = self.v.chain[self.v.by_path["C:\\BIG"]]
        self.assertEqual(runs[0][0], chain[3])
        self.assertEqual(runs[0][1], 0)

    def test_reads_never_run_past_the_end_of_the_file(self):
        runs = self.v.read_runs("C:\\BIG", 300 * 1024, 1024 * 1024)
        last = self.v.chain[self.v.by_path["C:\\BIG"]][-1]
        self.assertEqual(runs[-1][0], last)
        self.assertLessEqual(runs[-1][1] + runs[-1][2], self.v.cluster_sectors)


class TestRebuild(unittest.TestCase):
    def test_rebuild_preserves_every_file(self):
        v = small_volume()
        for i in range(5):
            v.create("C:\\F%d" % i, 64 * 1024, "data")
        placement = {}
        cursor = 0
        for fid in v.files:
            n = len(v.chain[fid])
            placement[fid] = list(range(cursor, cursor + n))
            cursor += n
        w = v.rebuild(placement)
        self.assertEqual(set(w.by_path), set(v.by_path))
        for fid in v.files:
            self.assertEqual(len(w.chain[fid]), len(v.chain[fid]))
            self.assertEqual(w.files[fid].size, v.files[fid].size)

    def test_rebuild_refuses_to_overlap_files(self):
        v = small_volume()
        v.create("C:\\A", 64 * 1024, "data")
        v.create("C:\\B", 64 * 1024, "data")
        placement = {fid: [0, 1] for fid in v.files}
        with self.assertRaises(ValueError):
            v.rebuild(placement)


if __name__ == "__main__":
    unittest.main()
