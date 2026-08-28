"""FAT16 volume model: cluster allocation, fragmentation, and read planning.

The allocator reproduces the behaviour that made Windows 95 volumes fragment in
the first place: VFAT hands out the next free cluster at or after the last one
it allocated, wrapping at the end of the volume. Interleave a few hundred file
creations, deletions and appends and files stop being contiguous.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .drive import SECTOR_BYTES, Drive


class OutOfSpace(Exception):
    pass


@dataclass
class FileRec:
    fid: int
    path: str
    kind: str
    size: int
    created: int
    growth_per_day: int = 0

    @property
    def directory(self) -> str:
        return self.path.rsplit("\\", 1)[0]


class Volume:
    """A FAT16 partition living on the outer cylinders of `drive`."""

    def __init__(
        self,
        drive: Drive,
        partition_sectors: Optional[int] = None,
        cluster_sectors: int = 64,      # 32 KB clusters: FAT16, 1-2 GB partition
        start_lba: int = 0,
        alloc_policy: str = "next-fit",
    ):
        self.drive = drive
        self.start_lba = start_lba
        self.cluster_sectors = cluster_sectors
        self.cluster_bytes = cluster_sectors * SECTOR_BYTES
        self.alloc_policy = alloc_policy

        avail = partition_sectors if partition_sectors is not None else drive.total_sectors
        avail = min(avail, drive.total_sectors - start_lba)
        reserved = 1
        root_sectors = 32                      # 512 root directory entries
        n = (avail - reserved - root_sectors) // cluster_sectors
        for _ in range(3):                     # FAT size depends on cluster count
            fat_sectors = math.ceil(n * 2 / SECTOR_BYTES)
            n = (avail - reserved - root_sectors - 2 * fat_sectors) // cluster_sectors
        self.fat_sectors = math.ceil(n * 2 / SECTOR_BYTES)
        self.cluster_count = int(n)
        self.data_start_lba = (
            start_lba + reserved + 2 * self.fat_sectors + root_sectors
        )

        self.owner: List[Optional[int]] = [None] * self.cluster_count
        self.chain: Dict[int, List[int]] = {}
        self.files: Dict[int, FileRec] = {}
        self.by_path: Dict[str, int] = {}
        self._next_free = 0
        self.free_count = self.cluster_count
        self._seq = 0
        self._next_fid = 1

    # --- addressing ---------------------------------------------------------

    def lba_of(self, cluster: int) -> int:
        return self.data_start_lba + cluster * self.cluster_sectors

    def capacity_bytes(self) -> int:
        return self.cluster_count * self.cluster_bytes

    def used_bytes(self) -> int:
        return (self.cluster_count - self.free_count) * self.cluster_bytes

    def fill(self) -> float:
        return 1.0 - self.free_count / self.cluster_count

    def clusters_for(self, size: int) -> int:
        return max(1, math.ceil(size / self.cluster_bytes))

    # --- allocation ---------------------------------------------------------

    def _find_free(self, start: int) -> int:
        n = self.cluster_count
        owner = self.owner
        i = start
        for _ in range(n):
            if owner[i] is None:
                return i
            i += 1
            if i == n:
                i = 0
        raise OutOfSpace("volume full")

    def _alloc(self, fid: int, count: int) -> List[int]:
        if count > self.free_count:
            raise OutOfSpace("volume full")
        out = []
        cursor = 0 if self.alloc_policy == "first-fit" else self._next_free
        for _ in range(count):
            c = self._find_free(cursor)
            self.owner[c] = fid
            out.append(c)
            self.free_count -= 1
            cursor = 0 if self.alloc_policy == "first-fit" else (c + 1) % self.cluster_count
        if self.alloc_policy != "first-fit":
            self._next_free = cursor
        return out

    def create(self, path: str, size: int, kind: str, growth_per_day: int = 0) -> FileRec:
        if path in self.by_path:
            raise ValueError("exists: " + path)
        fid = self._next_fid
        rec = FileRec(fid, path, kind, size, self._seq, growth_per_day)
        # allocate first: a failed create must not leave a half-registered file
        chain = self._alloc(fid, self.clusters_for(size))
        self._next_fid += 1
        self._seq += 1
        self.files[fid] = rec
        self.by_path[path] = fid
        self.chain[fid] = chain
        return rec

    def append(self, path: str, extra_bytes: int) -> None:
        fid = self.by_path[path]
        rec = self.files[fid]
        old = len(self.chain[fid])
        rec.size += extra_bytes
        need = self.clusters_for(rec.size) - old
        if need > 0:
            self.chain[fid].extend(self._alloc(fid, need))

    def delete(self, path: str) -> None:
        fid = self.by_path.pop(path)
        for c in self.chain.pop(fid):
            self.owner[c] = None
            self.free_count += 1
        del self.files[fid]

    # --- geometry of a file -------------------------------------------------

    def extents(self, fid: int) -> List[Tuple[int, int]]:
        """Physically contiguous runs, in file order."""
        runs: List[Tuple[int, int]] = []
        for c in self.chain[fid]:
            if runs and runs[-1][0] + runs[-1][1] == c:
                runs[-1] = (runs[-1][0], runs[-1][1] + 1)
            else:
                runs.append((c, 1))
        return runs

    def fragments(self, fid: int) -> int:
        return len(self.extents(fid))

    def read_plan(self, path: str, offset: int, length: int) -> List[Tuple[int, int]]:
        """Byte range -> list of (first_cluster, cluster_count) physical runs."""
        fid = self.by_path[path]
        chain = self.chain[fid]
        first = offset // self.cluster_bytes
        last = max(first, (offset + max(1, length) - 1) // self.cluster_bytes)
        last = min(last, len(chain) - 1)
        runs: List[Tuple[int, int]] = []
        for i in range(first, last + 1):
            c = chain[i]
            if runs and runs[-1][0] + runs[-1][1] == c:
                runs[-1] = (runs[-1][0], runs[-1][1] + 1)
            else:
                runs.append((c, 1))
        return runs

    def read_runs(self, path: str, offset: int, length: int) -> List[Tuple[int, int, int]]:
        """Byte range -> [(cluster, first_sector_in_cluster, sectors)], file order.

        Sector-accurate: reading 4 KB out of a 32 KB cluster costs 8 sectors,
        not 64. That matters for the paging traces.
        """
        fid = self.by_path[path]
        chain = self.chain[fid]
        cs = self.cluster_sectors
        file_sectors = len(chain) * cs
        first = offset // 512
        last = (offset + max(1, length) + 511) // 512
        first = max(0, min(first, file_sectors - 1))
        last = max(first + 1, min(last, file_sectors))
        out: List[Tuple[int, int, int]] = []
        ci = first // cs
        while ci * cs < last:
            lo = max(first, ci * cs) - ci * cs
            hi = min(last, (ci + 1) * cs) - ci * cs
            out.append((chain[ci], lo, hi - lo))
            ci += 1
        return out

    def clusters_of_range(self, path: str, offset: int, length: int) -> List[int]:
        fid = self.by_path[path]
        chain = self.chain[fid]
        first = offset // self.cluster_bytes
        last = max(first, (offset + max(1, length) - 1) // self.cluster_bytes)
        last = min(last, len(chain) - 1)
        return chain[first : last + 1]

    # --- whole-volume statistics -------------------------------------------

    def fragmentation_report(self) -> Dict[str, float]:
        total_files = len(self.files)
        frags = 0
        fragmented = 0
        multi = 0
        for fid in self.files:
            f = self.fragments(fid)
            frags += f
            if len(self.chain[fid]) > 1:
                multi += 1
                if f > 1:
                    fragmented += 1
        # free space: how many separate holes
        holes = 0
        prev = True
        for o in self.owner:
            free = o is None
            if free and not prev:
                holes += 1
            prev = free
        if self.owner and self.owner[0] is None:
            holes += 1
        return {
            "files": total_files,
            "extents_per_file": frags / max(1, total_files),
            "pct_fragmented": 100.0 * fragmented / max(1, multi),
            "free_holes": holes,
            "fill_pct": 100.0 * self.fill(),
        }

    # --- rebuilding under a layout policy ----------------------------------

    def clone_empty(self) -> "Volume":
        v = Volume.__new__(Volume)
        v.__dict__.update(
            {
                k: self.__dict__[k]
                for k in (
                    "drive",
                    "start_lba",
                    "cluster_sectors",
                    "cluster_bytes",
                    "alloc_policy",
                    "fat_sectors",
                    "cluster_count",
                    "data_start_lba",
                )
            }
        )
        v.owner = [None] * v.cluster_count
        v.chain = {}
        v.files = {}
        v.by_path = {}
        v._next_free = 0
        v.free_count = v.cluster_count
        v._seq = self._seq
        v._next_fid = self._next_fid
        return v

    def copy(self) -> "Volume":
        v = self.clone_empty()
        v.owner = list(self.owner)
        v.chain = {fid: list(ch) for fid, ch in self.chain.items()}
        v.files = {
            fid: FileRec(r.fid, r.path, r.kind, r.size, r.created, r.growth_per_day)
            for fid, r in self.files.items()
        }
        v.by_path = dict(self.by_path)
        v.free_count = self.free_count
        v._next_free = self._next_free
        v._next_fid = self._next_fid
        return v

    def rebuild(self, placement: Dict[int, List[int]]) -> "Volume":
        """Return a copy of this volume with files placed at given clusters."""
        v = self.clone_empty()
        for fid, rec in self.files.items():
            clusters = placement[fid]
            assert len(clusters) == len(self.chain[fid]), rec.path
            v.files[fid] = FileRec(
                rec.fid, rec.path, rec.kind, rec.size, rec.created, rec.growth_per_day
            )
            v.by_path[rec.path] = fid
            v.chain[fid] = list(clusters)
            for c in clusters:
                if v.owner[c] is not None:
                    raise ValueError("overlapping placement at cluster %d" % c)
                v.owner[c] = fid
                v.free_count -= 1
        v._next_free = 0
        return v
