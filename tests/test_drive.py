"""The drive model has to behave like a drive before anything else means much."""

import random
import unittest

from tests.common import *      # noqa: F401,F403
from sim.drive import DRIVES, Arm, Drive


class TestSeekCurve(unittest.TestCase):
    def setUp(self):
        self.spec = DRIVES["1996"]
        self.drive = Drive(self.spec)

    def test_curve_hits_the_published_anchors(self):
        self.assertAlmostEqual(self.drive.seek_ms(1), self.spec.track_to_track_ms, places=6)
        third = self.spec.cylinders // 3
        self.assertAlmostEqual(self.drive.seek_ms(third), self.spec.average_seek_ms,
                               delta=0.05)

    def test_seek_is_monotone_and_sublinear(self):
        prev = 0.0
        for d in (1, 10, 100, 1000, 3999):
            t = self.drive.seek_ms(d)
            self.assertGreater(t, prev)
            prev = t
        # doubling the distance must cost less than double the time
        self.assertLess(self.drive.seek_ms(2000), 2 * self.drive.seek_ms(1000))

    def test_no_seek_for_no_movement(self):
        self.assertEqual(self.drive.seek_ms(0), 0.0)


class TestZoning(unittest.TestCase):
    def setUp(self):
        self.drive = Drive(DRIVES["1996"])

    def test_outer_cylinders_are_faster(self):
        outer = self.drive.zone_rate_mb_s(0)
        inner = self.drive.zone_rate_mb_s(self.drive.spec.cylinders - 1)
        self.assertGreater(outer, inner * 1.4)

    def test_sequential_read_approaches_the_zone_rate(self):
        """A long streaming read should come out just under the raw media rate.

        Just under, not equal: crossing a track costs a head switch and every
        32 KB request costs the driver something, which is exactly why the
        sustained figure on a 1990s spec sheet was below the raw one.
        """
        for cyl in (0, self.drive.spec.cylinders - 60):
            arm = Arm(self.drive)
            lba = self.drive.cylinder_end_lba(cyl) - self.drive.sectors_per_track(cyl)
            sectors = 4096                      # 2 MB
            ms = 0.0
            pos = lba
            for _ in range(sectors // 64):      # 32 KB at a time, as the FS would
                ms += arm.transfer(pos, 64)
                pos += 64
            mb = sectors * 512 / 1e6
            measured = mb / (ms / 1000)
            expected = self.drive.zone_rate_mb_s(self.drive.cylinder_of(lba))
            self.assertGreater(measured / expected, 0.82)
            self.assertLessEqual(measured / expected, 1.0)


class TestRandomAccess(unittest.TestCase):
    def test_random_reads_cost_seek_plus_latency(self):
        drive = Drive(DRIVES["1996"])
        arm = Arm(drive)
        rng = random.Random(4)
        n = 4000
        for _ in range(n):
            arm.transfer(rng.randrange(0, drive.total_sectors - 8), 8)
        per_request = arm.stats.total_ms / n
        floor = drive.avg_rotation_ms + drive.spec.request_overhead_ms
        self.assertGreater(per_request, floor)
        self.assertLess(per_request, floor + drive.full_stroke_ms())
        # and the average seek should land near the drive's rated average
        self.assertAlmostEqual(arm.stats.seek_ms / n, drive.spec.average_seek_ms,
                               delta=4.0)


class TestReadAhead(unittest.TestCase):
    def setUp(self):
        self.drive = Drive(DRIVES["1996"])

    def test_streaming_pays_no_rotational_latency(self):
        arm = Arm(self.drive)
        arm.transfer(100000, 64)
        before = arm.stats.rotation_ms
        arm.transfer(100064, 64)
        self.assertEqual(arm.stats.rotation_ms, before)

    def test_a_gap_inside_the_buffer_is_still_free(self):
        arm = Arm(self.drive)
        arm.transfer(100000, 64)
        before = arm.stats.rotation_ms + arm.stats.seek_ms
        arm.transfer(100064 + 32, 64)           # skipped 16 KB, still prefetched
        self.assertEqual(arm.stats.rotation_ms + arm.stats.seek_ms, before)

    def test_a_gap_beyond_the_buffer_is_not(self):
        arm = Arm(self.drive)
        arm.transfer(100000, 64)
        before = arm.stats.rotation_ms + arm.stats.seek_ms
        arm.transfer(100064 + self.drive.readahead_sectors + 64, 64)
        self.assertGreater(arm.stats.rotation_ms + arm.stats.seek_ms, before)


if __name__ == "__main__":
    unittest.main()
