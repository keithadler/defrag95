"""A real FAT16 reader and re-writer.

Everything in `sim/` is a model. This is not: it parses an actual disk image
produced by an actual operating system, reports where the actual clusters of
every actual file are, and can write the image back out with those files
relocated. It is what lets a layout policy be applied to a real filesystem
rather than to a simulation of one.

Only the parts of FAT16 this experiment needs are implemented: no FAT32, no
fragmented-directory edge cases beyond what a real volume produces, no repair.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

SECTOR = 512
FREE, EOC_MIN, BAD = 0x0000, 0xFFF8, 0xFFF7
ATTR_DIR, ATTR_VOLUME, ATTR_LFN = 0x10, 0x08, 0x0F


@dataclass
class Entry:
    """One directory entry: a real file or directory on the volume."""

    path: str
    name: str
    size: int
    first_cluster: int
    attrs: int
    chain: List[int] = field(default_factory=list)
    # where this entry's 32 bytes live, so its first-cluster field can be patched
    entry_dir: Optional[str] = None      # containing directory path, "" for root
    entry_offset: int = 0                # byte offset within that directory's data

    @property
    def is_dir(self) -> bool:
        return bool(self.attrs & ATTR_DIR)

    @property
    def directory(self) -> str:
        return self.path.rsplit("\\", 1)[0] if "\\" in self.path else ""


class Fat16:
    """A FAT16 volume inside a disk image, at `offset` bytes."""

    def __init__(self, image: str, offset: int = 0):
        self.image = image
        self.offset = offset
        with open(image, "rb") as fh:
            fh.seek(offset)
            bpb = fh.read(512)
        self.bytes_per_sector = struct.unpack("<H", bpb[11:13])[0]
        self.sectors_per_cluster = bpb[13]
        self.reserved_sectors = struct.unpack("<H", bpb[14:16])[0]
        self.num_fats = bpb[16]
        self.root_entries = struct.unpack("<H", bpb[17:19])[0]
        total16 = struct.unpack("<H", bpb[19:21])[0]
        self.sectors_per_fat = struct.unpack("<H", bpb[22:24])[0]
        total32 = struct.unpack("<I", bpb[32:36])[0]
        self.total_sectors = total16 or total32

        self.cluster_bytes = self.bytes_per_sector * self.sectors_per_cluster
        self.fat_start = self.reserved_sectors
        self.root_start = self.fat_start + self.num_fats * self.sectors_per_fat
        self.root_sectors = (self.root_entries * 32 + self.bytes_per_sector - 1) // self.bytes_per_sector
        self.data_start = self.root_start + self.root_sectors
        self.cluster_count = (self.total_sectors - self.data_start) // self.sectors_per_cluster
        if not 4085 < self.cluster_count < 65525:
            raise ValueError("not FAT16: %d clusters" % self.cluster_count)

        self.fat: List[int] = []
        self.entries: Dict[str, Entry] = {}
        self._read_fat()
        self._read_tree()

    # --- addressing ---------------------------------------------------------

    def lba_of_cluster(self, cluster: int) -> int:
        """First LBA (within the partition) of a cluster."""
        return self.data_start + (cluster - 2) * self.sectors_per_cluster

    def cluster_of_lba(self, lba: int) -> Optional[int]:
        """Which cluster an LBA falls in, or None if it is metadata."""
        if lba < self.data_start:
            return None
        c = (lba - self.data_start) // self.sectors_per_cluster + 2
        return c if c < self.cluster_count + 2 else None

    def region_of_lba(self, lba: int) -> str:
        """Name the structure an LBA belongs to."""
        if lba < self.fat_start:
            return "boot"
        if lba < self.root_start:
            return "fat"
        if lba < self.data_start:
            return "root"
        return "data"

    # --- reading ------------------------------------------------------------

    def _read(self, lba: int, sectors: int) -> bytes:
        with open(self.image, "rb") as fh:
            fh.seek(self.offset + lba * self.bytes_per_sector)
            return fh.read(sectors * self.bytes_per_sector)

    def _read_fat(self) -> None:
        raw = self._read(self.fat_start, self.sectors_per_fat)
        self.fat = list(struct.unpack("<%dH" % (len(raw) // 2), raw))

    def chain_of(self, first: int) -> List[int]:
        """Follow a cluster chain, defensively."""
        out: List[int] = []
        c = first
        seen = set()
        while 2 <= c < min(len(self.fat), self.cluster_count + 2) and c < BAD:
            if c in seen:
                break
            seen.add(c)
            out.append(c)
            c = self.fat[c]
        return out

    def _read_chain_data(self, chain: List[int]) -> bytes:
        return b"".join(
            self._read(self.lba_of_cluster(c), self.sectors_per_cluster) for c in chain
        )

    def _read_tree(self) -> None:
        root = self._read(self.root_start, self.root_sectors)
        self._parse_dir(root, "", None)

    def _parse_dir(self, data: bytes, path: str, chain: Optional[List[int]]) -> None:
        for off in range(0, len(data), 32):
            e = data[off:off + 32]
            if len(e) < 32 or e[0] == 0x00:
                break
            if e[0] == 0xE5:                       # deleted
                continue
            attrs = e[11]
            if attrs & ATTR_LFN == ATTR_LFN:       # long-name fragment
                continue
            if attrs & ATTR_VOLUME:
                continue
            name = e[0:8].decode("latin-1").rstrip()
            ext = e[8:11].decode("latin-1").rstrip()
            short = name + ("." + ext if ext else "")
            if short in (".", ".."):
                continue
            first = struct.unpack("<H", e[26:28])[0]
            size = struct.unpack("<I", e[28:32])[0]
            full = (path + "\\" + short) if path else short
            entry = Entry(full, short, size, first, attrs,
                          entry_dir=path, entry_offset=off)
            entry.chain = self.chain_of(first) if first >= 2 else []
            self.entries[full] = entry
            if entry.is_dir and entry.chain:
                self._parse_dir(self._read_chain_data(entry.chain), full, entry.chain)

    # --- reporting ----------------------------------------------------------

    def files(self) -> List[Entry]:
        """Every real file on the volume, directories excluded."""
        return [e for e in self.entries.values() if not e.is_dir]

    def directories(self) -> List[Entry]:
        """Every subdirectory. Directories are files too, and they move."""
        return [e for e in self.entries.values() if e.is_dir]

    def owner_map(self) -> Dict[int, Tuple[str, int]]:
        """cluster -> (path, index within that file). The heart of trace mapping."""
        out: Dict[int, Tuple[str, int]] = {}
        for e in self.entries.values():
            for i, c in enumerate(e.chain):
                out[c] = (e.path, i)
        return out

    def used_clusters(self) -> int:
        return sum(len(e.chain) for e in self.entries.values())

    def fragmentation_report(self) -> Dict[str, float]:
        """The same summary the simulator produces, computed from a real volume."""
        files = self.files()
        multi = [e for e in files if len(e.chain) > 1]
        frag = [e for e in multi if not self._contiguous(e.chain)]
        extents = sum(self._extent_count(e.chain) for e in files)
        holes = 0
        prev_free = False
        for c in range(2, self.cluster_count + 2):
            free = self.fat[c] == FREE
            if free and not prev_free:
                holes += 1
            prev_free = free
        used = self.used_clusters()
        return {
            "files": len(files),
            "extents_per_file": extents / max(1, len(files)),
            "pct_fragmented": 100.0 * len(frag) / max(1, len(multi)),
            "free_holes": holes,
            "fill_pct": 100.0 * used / self.cluster_count,
            "clusters": self.cluster_count,
            "used_clusters": used,
        }

    @staticmethod
    def _contiguous(chain: List[int]) -> bool:
        return all(chain[i] + 1 == chain[i + 1] for i in range(len(chain) - 1))

    @staticmethod
    def _extent_count(chain: List[int]) -> int:
        if not chain:
            return 0
        return 1 + sum(1 for i in range(len(chain) - 1) if chain[i] + 1 != chain[i + 1])
