"""Guards on the headline claim.

A benchmark that shows what you hoped it would show is worth very little, so
these check the ways this one could be lying: that the planner was handed the
answer key, that the layouts are being charged for different amounts of work,
or that any reshuffle at all would have produced the same win.
"""

import random
import unittest

from tests.common import apply_layout, case, measure
from sim.engine import run_scenario
from sim.layouts import pack


def boot(vol, evals):
    return measure(vol, evals).per_scenario["boot"][0]


class TestTheExperimentIsFair(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = case()
        cls.layouts = {
            name: apply_layout(name, cls.c.aged, cls.c.log)
            for name in ("none", "win95_full", "win95_full_offline", "defrag95")
        }

    def test_the_planner_never_saw_the_evaluation_workload(self):
        """The monitor's traces and the scored traces are different draws."""
        from sim.workload import make_workload
        from sim.bench import EVAL_SEEDS, MONITOR_SEED, MONITOR_DAYS

        monitored = set()
        for d in range(MONITOR_DAYS):
            w = make_workload(self.c.aged, self.c.profiles, seed=MONITOR_SEED * 1000 + d)
            monitored.add(tuple(a.path for a in w[0].accesses))
        for w in self.c.evals:
            self.assertNotIn(tuple(a.path for a in w[0].accesses), monitored)

    def test_every_layout_is_charged_for_the_same_work(self):
        """Same trace, same bytes off the platter. Only the geometry differs."""
        sectors = set()
        for name, vol in self.layouts.items():
            r = run_scenario(vol, self.c.evals[0][0])
            sectors.add(r.stats.sectors)
            self.assertEqual(r.missing_paths, 0, name)
        self.assertEqual(len(sectors), 1, "layouts read different amounts of data")

    def test_measurement_is_deterministic(self):
        vol = self.layouts["defrag95"]
        self.assertEqual(boot(vol, self.c.evals), boot(vol, self.c.evals))


class TestTheClaimHolds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = case()
        cls.aged = apply_layout("none", cls.c.aged, cls.c.log)
        cls.win95 = apply_layout("win95_full", cls.c.aged, cls.c.log)
        cls.new = apply_layout("defrag95", cls.c.aged, cls.c.log)

    def test_the_shipped_defragmenter_helps(self):
        """If it did not, the baseline would be the suspicious thing."""
        self.assertLess(boot(self.win95, self.c.evals), boot(self.aged, self.c.evals))

    def test_defrag95_beats_it_on_a_held_out_boot(self):
        gain = 1 - boot(self.new, self.c.evals) / boot(self.win95, self.c.evals)
        self.assertGreater(gain, 0.15)

    def test_the_gain_is_seek_and_rotation_not_transfer(self):
        a = run_scenario(self.win95, self.c.evals[0][0]).stats
        b = run_scenario(self.new, self.c.evals[0][0]).stats
        self.assertLess(b.seek_ms, a.seek_ms * 0.5)
        self.assertLess(b.rotation_ms, a.rotation_ms * 0.8)
        # the same bytes come off the platter, so transfer barely moves
        self.assertAlmostEqual(b.transfer_ms / a.transfer_ms, 1.0, delta=0.1)

    def test_packing_in_an_arbitrary_order_is_not_enough(self):
        """The win is the ordering, not merely the absence of fragmentation."""
        rng = random.Random(11)
        order = list(self.c.aged.files)
        rng.shuffle(order)
        scrambled = self.c.aged.rebuild(pack(self.c.aged, order))
        shuffled_ms = boot(scrambled, self.c.evals)
        self.assertGreater(shuffled_ms, boot(self.new, self.c.evals) * 1.2)


if __name__ == "__main__":
    unittest.main()
