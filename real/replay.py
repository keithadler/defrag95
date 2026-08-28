"""Score a real trace against a real layout, using the drive model.

This is the only modelled step left in the real benchmark. The filesystem, the
fragmentation, the file set and the access sequence all come from a real
machine; what a 1996 platter would have charged for that sequence is the one
thing no emulator can tell us, so it is computed here -- identically for every
layout, from the same trace.

No cache is modelled: the trace already records only what actually reached the
disk, so the guest's own buffering is baked into it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.drive import DRIVES, Arm, Drive, DriveSpec
from .fat16 import Fat16
from .trace import LogicalAccess


def drive_for(sectors_needed: int, base: str = "1996") -> Drive:
    """A modelled drive big enough to hold this image, with the era's timings.

    Only the cylinder count changes; the seek curve is calibrated from
    track-to-track and average seek, so it stays the drive it was.
    """
    spec = DRIVES[base]
    drive = Drive(spec)
    if drive.total_sectors >= sectors_needed:
        return drive
    grown = DriveSpec(**{**spec.__dict__,
                         "cylinders": int(spec.cylinders * sectors_needed
                                          / drive.total_sectors) + 8})
    return Drive(grown)


@dataclass
class Replay:
    """What one layout cost for one trace."""

    ms: float
    requests: int
    sectors: int
    seek_ms: float
    rotation_ms: float
    transfer_ms: float
    overhead_ms: float
    unmapped: int


def score(fs: Fat16, placement: Dict[str, List[int]],
          accesses: Sequence[LogicalAccess], drive: Drive,
          partition_lba: int = 63) -> Replay:
    """Replay one phase of a trace against one layout."""
    arm = Arm(drive)
    total = 0.0
    unmapped = 0
    pending_lba = -1
    pending_sectors = 0

    def flush() -> float:
        nonlocal pending_lba, pending_sectors
        if pending_sectors <= 0:
            return 0.0
        cost = arm.transfer(pending_lba, pending_sectors)
        pending_lba, pending_sectors = -1, 0
        return cost

    for a in accesses:
        if a.kind == "meta":
            lba = partition_lba + a.lba
        else:
            chain = placement.get(a.path)
            if not chain or a.cluster_index >= len(chain):
                unmapped += 1
                continue
            lba = (partition_lba + fs.lba_of_cluster(chain[a.cluster_index])
                   + a.sector_in_cluster)
        if pending_sectors and lba == pending_lba + pending_sectors:
            pending_sectors += a.sectors          # coalesce, as the guest would
        else:
            total += flush()
            pending_lba, pending_sectors = lba, a.sectors
    total += flush()

    s = arm.stats
    return Replay(total, s.requests, s.sectors, s.seek_ms, s.rotation_ms,
                  s.transfer_ms, s.overhead_ms, unmapped)
