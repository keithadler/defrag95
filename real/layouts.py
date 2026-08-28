"""Layout policies applied to a real FAT16 volume.

These are the same policies as `sim/layouts.py`, but they operate on cluster
chains parsed out of an actual disk image rather than on a model of one. A
policy returns {path: [cluster, ...]}; nothing is written to the image, because
scoring a layout only needs to know where the clusters would be.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Set

from .fat16 import Entry, Fat16


def _pack(fs: Fat16, order: Sequence[Entry], gaps: Optional[Dict[str, int]] = None
          ) -> Dict[str, List[int]]:
    """Lay files down contiguously from the outer edge, in the given order."""
    gaps = gaps or {}
    placement: Dict[str, List[int]] = {}
    cursor = 2                                   # cluster numbering starts at 2
    limit = fs.cluster_count + 2
    for e in order:
        n = len(e.chain)
        if n == 0:
            placement[e.path] = []
            continue
        if cursor + n > limit:
            raise RuntimeError("ran off the end of the volume placing " + e.path)
        placement[e.path] = list(range(cursor, cursor + n))
        cursor += n + gaps.get(e.path, 0)
    return placement


def current(fs: Fat16) -> Dict[str, List[int]]:
    """The volume exactly as it is: the untouched baseline."""
    return {e.path: list(e.chain) for e in fs.entries.values()}


def directory_order(fs: Fat16) -> Dict[str, List[int]]:
    """The conventional defragmenter.

    Every file made contiguous and packed against the front of the volume in
    directory-walk order, all free space consolidated at the end. This is what
    Windows 95's Disk Defragmenter did, and what the GPL FreeDOS defrag does.
    `fs.entries` is populated by a depth-first walk of the real directory tree,
    so its natural order *is* the walk order.
    """
    return _pack(fs, list(fs.entries.values()))


def use_order(fs: Fat16, touch_order: Sequence[str],
              hot_first: bool = True) -> Dict[str, List[int]]:
    """defrag95: ordered by when the machine was observed to read things.

    `touch_order` is the first-touch sequence recorded from a *training* run.
    Files the monitor never saw are not treated as cold: they are placed beside
    their directory's observed siblings, and only files in directories that
    were never touched at all go to the slow inner edge.
    """
    by_path = fs.entries
    placed: Set[str] = set()
    order: List[Entry] = []

    def add(path: str) -> None:
        e = by_path.get(path)
        if e is not None and path not in placed:
            placed.add(path)
            order.append(e)

    # 1. everything the monitor saw, in the order it saw it
    if hot_first:
        for path in touch_order:
            add(path)
    else:                                        # ablation: hot set, but unordered
        for path in sorted(set(touch_order)):
            add(path)

    # 2. the directories containing hot files, and their unobserved siblings
    hot_dirs = [e.directory for e in order]
    seen_dirs: List[str] = []
    for d in hot_dirs:
        if d not in seen_dirs:
            seen_dirs.append(d)
    for d in seen_dirs:
        for e in by_path.values():
            if e.directory == d and e.path not in placed:
                add(e.path)

    # 3. directories nobody touched, innermost
    for e in by_path.values():
        add(e.path)
    return _pack(fs, order)
