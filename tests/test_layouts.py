"""Each layout policy must actually do what it says on the tin."""

import unittest

from tests.common import apply_layout, case
from sim.engine import defrag_cost_ms
from sim.layouts import (
    R_APP, R_BOOT, R_COLD, Defrag95Options, SWAP, layout_defrag95, layout_win95_full,
)


def contiguous(vol, fid):
    chain = vol.chain[fid]
    return all(chain[i] + 1 == chain[i + 1] for i in range(len(chain) - 1))


def free_runs(vol):
    runs = 0
    prev = False
    for o in vol.owner:
        free = o is None
        if free and not prev:
            runs += 1
        prev = free
    return runs


class TestWin95Defrag(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = case()
        cls.full = apply_layout("win95_full", cls.c.aged, cls.c.log)
        cls.offline = apply_layout("win95_full_offline", cls.c.aged, cls.c.log)

    def test_a_full_pass_leaves_no_fragmented_files(self):
        swap_fid = self.full.by_path[SWAP]
        for fid in self.full.files:
            if fid == swap_fid:
                continue
            self.assertTrue(contiguous(self.full, fid), self.full.files[fid].path)

    def test_a_full_pass_consolidates_free_space(self):
        # Free space ends up in one run at the end -- except that the swap file
        # cannot be moved, and this one is itself in many pieces, so the packed
        # region is perforated by exactly the holes it left behind.
        swap_extents = len(self.c.aged.extents(self.c.aged.by_path[SWAP]))
        self.assertGreater(swap_extents, 1)
        self.assertLessEqual(free_runs(self.full), swap_extents + 1)
        self.assertEqual(free_runs(self.offline), 1)

    def test_the_swap_file_cannot_be_moved_while_windows_is_running(self):
        fid = self.c.aged.by_path[SWAP]
        self.assertEqual(self.full.chain[fid], self.c.aged.chain[fid])

    def test_dos_mode_can_move_it(self):
        fid = self.c.aged.by_path[SWAP]
        self.assertNotEqual(self.offline.chain[fid], self.c.aged.chain[fid])
        self.assertTrue(contiguous(self.offline, fid))

    def test_files_are_packed_in_directory_walk_order(self):
        """Not by use: a directory's files land as one uninterrupted group."""
        order = sorted(
            (fid for fid, r in self.offline.files.items() if r.path != SWAP),
            key=lambda fid: self.offline.chain[fid][0],
        )
        seen = set()
        previous = None
        for fid in order:
            d = self.offline.files[fid].directory
            if d != previous:
                self.assertNotIn(d, seen,
                                 "%s was interrupted by another directory" % d)
                seen.add(d)
                previous = d


class TestDefrag95(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = case()
        cls.v = apply_layout("defrag95", cls.c.aged, cls.c.log)

    def test_every_file_is_contiguous(self):
        for fid in self.v.files:
            self.assertTrue(contiguous(self.v, fid), self.v.files[fid].path)

    def test_no_file_is_lost_or_resized(self):
        self.assertEqual(set(self.v.by_path), set(self.c.aged.by_path))
        for fid in self.c.aged.files:
            self.assertEqual(len(self.v.chain[fid]), len(self.c.aged.chain[fid]))

    def test_the_boot_set_lands_on_the_outer_cylinders(self):
        boot = [p for p in self.c.log.order["boot"] if p in self.v.by_path]
        worst = max(self.v.chain[self.v.by_path[p]][-1] for p in boot)
        self.assertLess(worst, self.v.cluster_count * 0.15)

    def test_boot_comes_before_applications_which_come_before_cold(self):
        boot = set(self.c.log.order["boot"])
        app = set()
        for sc, paths in self.c.log.order.items():
            if sc.startswith("launch_"):
                app |= set(paths)
        app -= boot
        cold = [p for p in self.v.by_path if p.startswith("C:\\ARCHIVE")]
        boot_end = max(self.v.chain[self.v.by_path[p]][-1] for p in boot if p in self.v.by_path)
        app_start = min(self.v.chain[self.v.by_path[p]][0] for p in app if p in self.v.by_path)
        cold_start = min(self.v.chain[self.v.by_path[p]][0] for p in cold)
        self.assertLess(boot_end, app_start)
        self.assertLess(app_start, cold_start)

    def test_boot_files_are_laid_out_in_the_order_they_are_read(self):
        boot = [p for p in self.c.log.order["boot"] if p in self.v.by_path]
        starts = [self.v.chain[self.v.by_path[p]][0] for p in boot]
        self.assertEqual(starts, sorted(starts))

    def test_the_swap_file_is_one_extent(self):
        self.assertTrue(contiguous(self.v, self.v.by_path[SWAP]))

    def test_growth_headroom_only_appears_when_asked_for(self):
        without = layout_defrag95(self.c.aged, self.c.log,
                                  Defrag95Options(use_gaps=False))
        packed = max(max(without.chain[f]) for f in without.files)
        spaced = max(max(self.v.chain[f]) for f in self.v.files)
        self.assertLess(packed, spaced)

    def test_dropping_access_order_changes_the_layout(self):
        unordered = layout_defrag95(self.c.aged, self.c.log,
                                    Defrag95Options(use_access_order=False))
        boot = [p for p in self.c.log.order["boot"] if p in unordered.by_path]
        starts = [unordered.chain[unordered.by_path[p]][0] for p in boot]
        self.assertNotEqual(starts, sorted(starts))


class TestMaintenancePass(unittest.TestCase):
    def test_a_pass_over_an_undisturbed_volume_moves_nothing(self):
        c = case()
        for name in ("win95_full", "defrag95"):
            v = apply_layout(name, c.aged, c.log)
            again = apply_layout(name, v, c.log, slack=512)
            _, moved = defrag_cost_ms(v, again)
            self.assertEqual(moved, 0, name)

    def test_a_full_pass_over_the_same_volume_is_also_stable(self):
        c = case()
        v = apply_layout("defrag95", c.aged, c.log)
        again = apply_layout("defrag95", v, c.log)
        _, moved = defrag_cost_ms(v, again)
        self.assertEqual(moved, 0)


if __name__ == "__main__":
    unittest.main()
