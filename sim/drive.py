"""Physical drive model for mid-1990s IDE/EIDE hard disks.

Three mechanical costs dominate disk time on this hardware, and all three are
things a defragmenter can influence:

  1. seek       - moving the head assembly between cylinders
  2. rotation   - waiting for the sector to arrive under the head
  3. transfer   - the media rate, which on a zoned drive is ~1.7x faster on
                  the outer cylinders than the inner ones

They are modelled separately rather than being folded into one "MB/s" number,
because a layout policy trades them against each other.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass
from typing import List, Tuple

SECTOR_BYTES = 512


@dataclass(frozen=True)
class Zone:
    """One zoned-bit-recording band. Bands are listed outermost first."""

    cylinder_fraction: float
    sectors_per_track: int


@dataclass(frozen=True)
class DriveSpec:
    """One drive as a period spec sheet would describe it."""
    name: str
    year: int
    cylinders: int
    heads: int
    rpm: int
    zones: Tuple[Zone, ...]
    track_to_track_ms: float
    average_seek_ms: float       # vendor "average seek" == 1/3 full stroke
    head_switch_ms: float
    request_overhead_ms: float   # VFAT/IOS/port-driver cost per I/O request
    readahead_kb: int = 64       # on-drive segmented buffer read-ahead


class Drive:
    """Geometry + timing for one drive spec."""

    def __init__(self, spec: DriveSpec):
        self.spec = spec
        self._spt: List[int] = []
        total_frac = sum(z.cylinder_fraction for z in spec.zones)
        assigned = 0
        for i, z in enumerate(spec.zones):
            if i == len(spec.zones) - 1:
                n = spec.cylinders - assigned
            else:
                n = int(round(spec.cylinders * z.cylinder_fraction / total_frac))
            assigned += n
            self._spt.extend([z.sectors_per_track] * n)
        assert len(self._spt) == spec.cylinders

        # LBA 0 is the outermost cylinder, as on every drive of the era.
        self._cum: List[int] = [0]
        for c in range(spec.cylinders):
            self._cum.append(self._cum[-1] + self._spt[c] * spec.heads)
        self.total_sectors = self._cum[-1]

        # Seek curve  t(d) = a + b*sqrt(d), calibrated against the two numbers
        # a 1990s spec sheet actually published: track-to-track, and "average"
        # (which the industry defined as a 1/3-stroke seek).
        third_stroke = math.sqrt(spec.cylinders / 3.0)
        self._b = (spec.average_seek_ms - spec.track_to_track_ms) / (third_stroke - 1.0)
        self._a = spec.track_to_track_ms - self._b

        self.readahead_sectors = spec.readahead_kb * 1024 // SECTOR_BYTES
        self.revolution_ms = 60000.0 / spec.rpm
        self.avg_rotation_ms = self.revolution_ms / 2.0

    # --- geometry -----------------------------------------------------------

    def cylinder_of(self, lba: int) -> int:
        """Which cylinder an LBA lives on."""
        return bisect_right(self._cum, lba) - 1

    def sectors_per_track(self, cyl: int) -> int:
        """Sectors per track at this radius. Higher on the outer cylinders."""
        return self._spt[cyl]

    def cylinder_end_lba(self, cyl: int) -> int:
        """First LBA of the next cylinder out."""
        return self._cum[cyl + 1]

    def capacity_bytes(self) -> int:
        """Formatted capacity of the whole drive."""
        return self.total_sectors * SECTOR_BYTES

    # --- timing -------------------------------------------------------------

    def seek_ms(self, distance: int) -> float:
        """Time to move the heads `distance` cylinders. Zero if they are already there."""
        if distance <= 0:
            return 0.0
        return max(0.0, self._a + self._b * math.sqrt(distance))

    def full_stroke_ms(self) -> float:
        """Time to cross the entire platter.

        A prediction of the calibrated curve rather than an input to it, which
        is a small check on the curve being sane.
        """
        return self.seek_ms(self.spec.cylinders - 1)

    def sector_ms(self, cyl: int) -> float:
        """Time to stream one sector off the media at this radius."""
        sectors_per_second = self._spt[cyl] * self.spec.rpm / 60.0
        return 1000.0 / sectors_per_second

    def zone_rate_mb_s(self, cyl: int) -> float:
        """Raw media rate at this radius, before head switches and driver overhead."""
        return (self._spt[cyl] * self.spec.rpm / 60.0) * SECTOR_BYTES / 1e6


@dataclass
class ArmStats:
    """Where the time went, accumulated over a stream of requests."""
    requests: int = 0
    sectors: int = 0
    seek_ms: float = 0.0
    rotation_ms: float = 0.0
    transfer_ms: float = 0.0
    overhead_ms: float = 0.0
    seek_cylinders: int = 0

    @property
    def total_ms(self) -> float:
        """Everything the request stream cost."""
        return self.seek_ms + self.rotation_ms + self.transfer_ms + self.overhead_ms


class Arm:
    """Stateful head position; accumulates the cost of a request stream."""

    def __init__(self, drive: Drive):
        self.drive = drive
        self.cyl = 0
        self.next_lba = -1          # LBA immediately after the last transfer
        self.prefetch_end = -1      # how far the drive buffer has read ahead
        self.stats = ArmStats()

    def seek_to_park(self) -> None:
        """Park the heads and forget the read-ahead buffer, as a cold start would."""
        self.cyl = 0
        self.next_lba = -1
        self.prefetch_end = -1

    def transfer(self, lba: int, sectors: int) -> float:
        """Cost of one contiguous read or write. Returns milliseconds."""
        if sectors <= 0:
            return 0.0
        d = self.drive
        start_cyl = d.cylinder_of(lba)
        distance = abs(start_cyl - self.cyl)
        contiguous = lba == self.next_lba

        prefetched = (
            not contiguous
            and self.next_lba >= 0
            and self.next_lba <= lba < self.prefetch_end
        )
        if contiguous and distance <= 1:
            # Sustained sequential streaming: track/cylinder skew is laid out
            # so the next sector arrives without a full rotation.
            seek = 0.0
            rotation = d.spec.head_switch_ms if distance == 1 else 0.0
        elif prefetched:
            # The drive read ahead into its buffer after the previous request,
            # so this one is already on its way: no seek, no full rotation.
            # 1996 IDE drives shipped 128-256 KB segmented buffers doing exactly
            # this, which is why *near*-sequential layouts win, not just
            # perfectly sequential ones.
            seek = 0.0
            rotation = 0.0
        else:
            seek = d.seek_ms(distance)
            rotation = d.avg_rotation_ms

        transfer = 0.0
        remaining = sectors
        pos = lba
        cyl = start_cyl
        while remaining > 0:
            end = d.cylinder_end_lba(cyl)
            take = min(remaining, end - pos)
            spt = d.sectors_per_track(cyl)
            transfer += take * d.sector_ms(cyl)
            # head switches inside the cylinder
            transfer += d.spec.head_switch_ms * ((pos % spt + take - 1) // spt)
            remaining -= take
            pos += take
            if remaining > 0:
                cyl += 1
                transfer += d.spec.head_switch_ms

        cost = seek + rotation + transfer + d.spec.request_overhead_ms
        s = self.stats
        s.requests += 1
        s.sectors += sectors
        s.seek_ms += seek
        s.rotation_ms += rotation
        s.transfer_ms += transfer
        s.overhead_ms += d.spec.request_overhead_ms
        s.seek_cylinders += distance if seek > 0 else 0

        self.cyl = d.cylinder_of(lba + sectors - 1)
        self.next_lba = lba + sectors
        self.prefetch_end = self.next_lba + d.readahead_sectors
        return cost


# --- Drives used in the study -------------------------------------------------
# Specs follow published figures for representative consumer IDE drives of each
# year; see docs/METHODOLOGY.md for the sourcing and the tolerance we claim.

def _zones(outer: int, inner: int, bands: int = 8) -> Tuple[Zone, ...]:
    step = (outer - inner) / (bands - 1)
    return tuple(
        Zone(1.0 / bands, int(round(outer - step * i))) for i in range(bands)
    )


DRIVE_1994 = DriveSpec(
    name="1994 540MB IDE, 3600 RPM",
    year=1994,
    cylinders=1900,
    heads=8,
    rpm=3600,
    zones=_zones(90, 55),
    track_to_track_ms=4.0,
    average_seek_ms=14.0,
    head_switch_ms=1.2,
    request_overhead_ms=0.30,
    readahead_kb=32,
)

DRIVE_1996 = DriveSpec(
    name="1996 1.6GB EIDE, 5400 RPM",
    year=1996,
    cylinders=4000,
    heads=8,
    rpm=5400,
    zones=_zones(122, 74),
    track_to_track_ms=3.0,
    average_seek_ms=12.0,
    head_switch_ms=0.9,
    request_overhead_ms=0.25,
    readahead_kb=64,
)

DRIVE_1998 = DriveSpec(
    name="1998 4.0GB UDMA, 5400 RPM",
    year=1998,
    cylinders=6800,
    heads=8,
    rpm=5400,
    zones=_zones(180, 108),
    track_to_track_ms=2.5,
    average_seek_ms=10.5,
    head_switch_ms=0.8,
    request_overhead_ms=0.18,
    readahead_kb=128,
)

DRIVES = {"1994": DRIVE_1994, "1996": DRIVE_1996, "1998": DRIVE_1998}
