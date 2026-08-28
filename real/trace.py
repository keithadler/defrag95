"""Turn a QEMU block trace into filesystem-relative accesses.

QEMU records every read the guest actually issued, as a byte offset into the
disk image. Those offsets are only meaningful for the layout the image had at
the time, so to score a *different* layout each access has to be mapped back
through the filesystem -- offset -> cluster -> (file, index) -- and then
forward again into wherever the new layout puts that file.

Metadata (boot sector, FAT, root directory) sits at fixed addresses in every
layout, so it maps to itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .fat16 import Fat16

LINE = re.compile(r"^blk_co_p(read|write)v .*? offset (\d+) bytes (\d+)")


@dataclass
class RawAccess:
    """One I/O the guest actually issued, as a byte offset into the image."""

    offset: int
    length: int
    write: bool


@dataclass
class LogicalAccess:
    """The same I/O, expressed in terms the filesystem understands."""

    kind: str                 # "file" or "meta"
    write: bool
    sectors: int
    path: Optional[str] = None
    cluster_index: int = 0    # index within the file's chain
    sector_in_cluster: int = 0
    lba: int = 0              # for metadata, the fixed address


def parse(path: str) -> List[RawAccess]:
    """Read a QEMU trace log."""
    out: List[RawAccess] = []
    with open(path) as fh:
        for line in fh:
            m = LINE.match(line)
            if m:
                out.append(RawAccess(int(m.group(2)), int(m.group(3)),
                                     m.group(1) == "write"))
    return out


def to_logical(fs: Fat16, raw: List[RawAccess], part_offset: int) -> Tuple[List[LogicalAccess], Dict[str, int]]:
    """Map raw image offsets onto files, using the layout the trace was taken on."""
    owner = fs.owner_map()
    out: List[LogicalAccess] = []
    stats = {"file": 0, "meta": 0, "unowned": 0, "sectors": 0}
    for a in raw:
        lba = (a.offset - part_offset) // 512
        sectors = max(1, a.length // 512)
        stats["sectors"] += sectors
        if a.offset < part_offset:
            out.append(LogicalAccess("meta", a.write, sectors, lba=0))
            stats["meta"] += 1
            continue
        region = fs.region_of_lba(lba)
        if region != "data":
            out.append(LogicalAccess("meta", a.write, sectors, lba=lba))
            stats["meta"] += 1
            continue
        # a data-area access may span clusters; split it per cluster
        remaining = sectors
        pos = lba
        while remaining > 0:
            cluster = fs.cluster_of_lba(pos)
            if cluster is None:
                out.append(LogicalAccess("meta", a.write, remaining, lba=pos))
                stats["meta"] += 1
                break
            base = fs.lba_of_cluster(cluster)
            within = pos - base
            take = min(remaining, fs.sectors_per_cluster - within)
            hit = owner.get(cluster)
            if hit is None:
                # a cluster no file claims: free space the guest touched anyway
                out.append(LogicalAccess("meta", a.write, take, lba=pos))
                stats["unowned"] += 1
            else:
                path, index = hit
                out.append(LogicalAccess("file", a.write, take, path=path,
                                         cluster_index=index, sector_in_cluster=within))
                stats["file"] += 1
            remaining -= take
            pos += take
    return out, stats


def split_phases(logical: List[LogicalAccess], markers: List[str]) -> Dict[str, List[LogicalAccess]]:
    """Cut the trace at the marker files the workload reads between phases."""
    phases: Dict[str, List[LogicalAccess]] = {}
    current: List[LogicalAccess] = []
    name = "boot"
    order = ["boot", "launch", "binaries", "data", "tail"]
    at = 0
    for acc in logical:
        if acc.kind == "file" and acc.path in markers:
            phases[order[min(at, len(order) - 1)]] = current
            current = []
            at += 1
            continue
        current.append(acc)
    phases[order[min(at, len(order) - 1)]] = current
    return phases
