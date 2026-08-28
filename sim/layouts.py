"""Layout policies: what the shipped Win95 defragmenter did, and what defrag95 does."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from .filesystem import Volume
from .workload import AccessLog

SWAP = "C:\\WINDOWS\\WIN386.SWP"

# Placement regions, outermost first. Order matters inside the first three,
# because those are streamed; elsewhere only locality matters.
R_BOOT, R_APP, R_SWAP, R_WARM, R_ARENA, R_COLD = range(6)
VOLATILE_DIRS = ("C:\\WINDOWS\\TEMP", "C:\\PROGRA~1\\NETSCAPE\\CACHE")


class Packer:
    """Hands out clusters from the outer edge inwards, skipping immovable ones."""

    def __init__(self, vol: Volume, blocked: Optional[Set[int]] = None):
        self.n = vol.cluster_count
        self.blocked = blocked or set()
        self.taken: Set[int] = set()
        self.cursor = 0

    def _free(self, c: int) -> bool:
        return c not in self.blocked and c not in self.taken

    def _advance(self) -> None:
        while self.cursor < self.n and not self._free(self.cursor):
            self.cursor += 1

    def take(self, count: int) -> List[int]:
        """Claim `count` clusters, contiguously if the volume allows it.

        A defragmenter that split a file around an immovable block would be
        defeating its own purpose, so the search skips forward to the first run
        long enough. Splitting is the last resort, on a volume with no run left
        that will hold the file.
        """
        start = self.cursor
        run_start = None
        run = 0
        c = start
        while c < self.n:
            if not self._free(c):
                run_start = None
                run = 0
            else:
                if run_start is None:
                    run_start = c
                    run = 1
                else:
                    run += 1
                if run >= count:
                    out = list(range(run_start, run_start + count))
                    self.taken.update(out)
                    self.cursor = run_start + count
                    return out
            c += 1
        # Nothing long enough is left. Take whatever is free, including the
        # holes an earlier contiguous placement stepped over -- a full volume
        # is exactly when a defragmenter has to accept a split.
        out = []
        c = 0
        while len(out) < count and c < self.n:
            if self._free(c):
                out.append(c)
            c += 1
        if len(out) < count:
            raise RuntimeError("packer ran off the end of the volume")
        self.taken.update(out)
        return out

    def reserve(self, count: int) -> None:
        """Leave a hole (growth headroom / arena padding)."""
        for _ in range(count):
            self._advance()
            if self.cursor >= self.n:
                return
            self.cursor += 1

    def remaining(self) -> int:
        """Clusters left between the cursor and the end of the volume."""
        return self.n - self.cursor


def pack(vol: Volume, order: Sequence[int], blocked: Optional[Set[int]] = None,
         gaps: Optional[Dict[int, int]] = None, slack: int = 0,
         pinned: Optional[Dict[int, List[int]]] = None,
         region: Optional[Dict[int, int]] = None,
         ordered_regions: Optional[Set[int]] = None) -> Dict[int, List[int]]:
    """Lay `order` down from the outer edge of the volume.

    With ``slack == 0`` this is a full pass: every file is rewritten into its
    ideal position. With ``slack > 0`` it is a maintenance pass -- the ideal
    positions are computed anyway, but a file is left alone if it is still in
    one piece and already within `slack` clusters of where it belongs. Only
    files that are fragmented or genuinely displaced are moved, and they are
    moved in ideal order into the space left over.

    `region` and `ordered_regions` refine what "where it belongs" means during
    a maintenance pass. Inside a region whose internal order is exploited --
    the boot stream, an application's launch set -- a file has to be near its
    exact slot. Elsewhere only locality matters, so being anywhere inside the
    right region is good enough, and adding a file in the middle of the volume
    does not cascade into rewriting everything behind it.
    """
    gaps = dict(gaps or {})
    pinned = pinned or {}
    blocked_set: Set[int] = set(blocked or ())
    movable = [fid for fid in order if fid not in pinned]

    # Headroom is a luxury: on a nearly full volume, shrink every reservation
    # rather than running off the end.
    used = sum(len(vol.chain[fid]) for fid in vol.files)
    free = vol.cluster_count - used
    wanted = sum(gaps.values())
    if wanted > free * 0.5 and wanted > 0:
        scale = (free * 0.5) / wanted
        gaps = {fid: int(g * scale) for fid, g in gaps.items()}

    def lay_out(fids: Sequence[int], reserved: Set[int]) -> Dict[int, List[int]]:
        packer = Packer(vol, reserved)
        out: Dict[int, List[int]] = {}
        for fid in fids:
            out[fid] = packer.take(len(vol.chain[fid]))
            if gaps.get(fid):
                packer.reserve(gaps[fid])
        return out

    if slack <= 0:
        placement = lay_out(movable, blocked_set)
        placement.update({fid: list(cl) for fid, cl in pinned.items()})
        return placement

    target = lay_out(movable, blocked_set)
    region = region or {}
    ordered_regions = ordered_regions if ordered_regions is not None else set()
    bounds: Dict[int, Tuple[int, int]] = {}
    for fid in movable:
        r = region.get(fid, 0)
        lo, hi = bounds.get(r, (vol.cluster_count, 0))
        bounds[r] = (min(lo, target[fid][0]), max(hi, target[fid][-1]))
    if bounds:
        last = max(bounds, key=lambda r: bounds[r][1])
        bounds[last] = (bounds[last][0], vol.cluster_count - 1)

    keep: Dict[int, List[int]] = {}
    move: List[int] = []
    for fid in movable:
        chain = vol.chain[fid]
        contiguous = all(chain[i] + 1 == chain[i + 1] for i in range(len(chain) - 1))
        r = region.get(fid, 0)
        if region and r not in ordered_regions:
            lo, hi = bounds[r]
            near = lo - slack <= chain[0] and chain[-1] <= hi + slack
        else:
            near = abs(chain[0] - target[fid][0]) <= slack
        clear = not any(c in blocked_set for c in chain)
        if contiguous and near and clear:
            keep[fid] = list(chain)
            blocked_set.update(chain)
        else:
            move.append(fid)

    placement = lay_out(move, blocked_set)
    placement.update(keep)
    placement.update({fid: list(cl) for fid, cl in pinned.items()})
    return placement


def _directory_walk_order(vol: Volume) -> List[int]:
    """Order in which a FAT directory-tree walk meets the files.

    Directories are visited in the order their first entry was created, and
    files within a directory in directory-entry order. This is what the shipped
    defragmenter packed by: it had no other information.
    """
    dirs: Dict[str, int] = {}
    for fid, rec in vol.files.items():
        d = rec.directory
        if d not in dirs or rec.created < dirs[d]:
            dirs[d] = rec.created
    return sorted(
        vol.files,
        key=lambda fid: (dirs[vol.files[fid].directory], vol.files[fid].created),
    )


# --- baseline ----------------------------------------------------------------

def layout_none(vol: Volume, log: Optional[AccessLog] = None) -> Volume:
    """The aged volume, untouched. The baseline every policy is measured against."""
    return vol


# --- Windows 95 Disk Defragmenter -------------------------------------------

def layout_win95_full(vol: Volume, log: Optional[AccessLog] = None,
                      move_swap: bool = False, slack: int = 0) -> Volume:
    """"Full defragmentation (both files and free space)".

    Files are made contiguous and packed against the front of the volume in
    directory-walk order; all free space ends up in one run at the end. The
    Windows swap file is in use while Windows is running and could not be
    moved, so by default it stays where it is and everything else is packed
    around it.
    """
    blocked: Set[int] = set()
    swap_fid = vol.by_path.get(SWAP)
    if swap_fid is not None and not move_swap:
        blocked = set(vol.chain[swap_fid])

    pinned = {}
    if swap_fid is not None and not move_swap:
        pinned[swap_fid] = list(vol.chain[swap_fid])
    placement = pack(vol, _directory_walk_order(vol), blocked=blocked,
                     slack=slack, pinned=pinned)
    return vol.rebuild(placement)


def layout_win95_offline(vol: Volume, log: Optional[AccessLog] = None,
                         slack: int = 0) -> Volume:
    """The same policy run with no swap file in the way (DOS-mode defrag)."""
    return layout_win95_full(vol, log, move_swap=True, slack=slack)


def layout_win95_files_only(vol: Volume, log: Optional[AccessLog] = None) -> Volume:
    """"Defragment files only": make each file contiguous, leave free space alone."""
    owner: List[Optional[int]] = list(vol.owner)
    placement: Dict[int, List[int]] = {}
    order = sorted(vol.files, key=lambda fid: vol.chain[fid][0])
    for fid in order:
        chain = vol.chain[fid]
        need = len(chain)
        if all(chain[i] + 1 == chain[i + 1] for i in range(need - 1)):
            placement[fid] = list(chain)
            continue
        for c in chain:
            owner[c] = None
        # first hole big enough
        run_start = None
        run_len = 0
        chosen = None
        for c in range(vol.cluster_count):
            if owner[c] is None:
                if run_start is None:
                    run_start = c
                    run_len = 1
                else:
                    run_len += 1
                if run_len >= need:
                    chosen = run_start
                    break
            else:
                run_start = None
                run_len = 0
        if chosen is None:
            chosen = chain[0]
            placement[fid] = list(chain)
            for c in chain:
                owner[c] = fid
            continue
        new = list(range(chosen, chosen + need))
        for c in new:
            owner[c] = fid
        placement[fid] = new
    return vol.rebuild(placement)


# --- defrag95 ----------------------------------------------------------------

@dataclass
class Defrag95Options:
    """Switches for the ablation: each one turns off a part of the policy."""
    use_access_order: bool = True     # place in observed first-touch order
    use_arenas: bool = True           # churn gets its own region
    use_gaps: bool = True             # growth headroom after volatile files
    place_swap: bool = True           # contiguous swap next to the hot region
    growth_horizon_days: int = 90
    max_gap_clusters: int = 8
    arena_headroom: float = 1.6


def _gap_for(vol: Volume, log: AccessLog, path: str, opts: Defrag95Options) -> int:
    if not opts.use_gaps:
        return 0
    written_per_day = log.bytes_written.get(path, 0) / max(1, 14)
    # half of what a save rewrites turns into net growth, empirically
    grow = 0.5 * written_per_day * opts.growth_horizon_days
    return int(min(opts.max_gap_clusters, math.ceil(grow / vol.cluster_bytes)))


def layout_defrag95(vol: Volume, log: AccessLog,
                    opts: Optional[Defrag95Options] = None,
                    slack: int = 0) -> Volume:
    """Reorganise by observed use rather than by directory order.

    Regions, outermost (fastest) cylinders first:

      1. boot set, in the order the boot actually touches it
      2. per-application launch sets, hottest application first, each in
         launch-touch order
      3. the paging file, contiguous, adjacent to the application code that
         pages against it
      4. warm files, grouped by directory
      5. a churn arena for temp and cache directories, with headroom
      6. everything never touched, innermost
    """
    opts = opts or Defrag95Options()
    order: List[int] = []
    gaps: Dict[int, int] = {}
    done: Set[int] = set()
    region: Dict[int, int] = {}
    current_region = [R_BOOT]

    class _Cursor:
        """Collects the ideal order; pack() decides what actually moves."""

        def reserve(self, n: int) -> None:
            if order and n:
                gaps[order[-1]] = gaps.get(order[-1], 0) + n

    packer = _Cursor()

    def place(path: str, gap: int = 0) -> None:
        fid = vol.by_path.get(path)
        if fid is None or fid in done:
            return
        order.append(fid)
        region[fid] = current_region[0]
        done.add(fid)
        if gap:
            gaps[fid] = gaps.get(fid, 0) + gap

    ordering = log.order

    def order_for(scenario: str) -> List[str]:
        paths = ordering.get(scenario, [])
        if opts.use_access_order:
            return paths
        # ablation: same hot/cold split, but no attempt to order within it
        return sorted(paths)

    swap_fid = vol.by_path.get(SWAP)

    # 1. boot set
    for path in order_for("boot"):
        if path != SWAP:
            place(path, _gap_for(vol, log, path, opts))

    # 2. applications, hottest first
    current_region[0] = R_APP
    launches = [s for s in log.order if s.startswith("launch_")]
    launches.sort(key=lambda s: -log.scenario_weight.get(s, 0.0))
    for scenario in launches:
        for path in order_for(scenario):
            if path != SWAP:
                place(path, _gap_for(vol, log, path, opts))
        packer.reserve(4)               # room for an app update

    # 3. paging file, contiguous, next to the code that pages
    if swap_fid is not None and opts.place_swap:
        current_region[0] = R_SWAP
        place(SWAP)
        packer.reserve(int(8 * 1024 * 1024 / vol.cluster_bytes))   # room to grow

    # 4. warm files: everything else that was touched, grouped by directory
    current_region[0] = R_WARM
    warm: Dict[str, List[int]] = {}
    for path in log.counts:
        fid = vol.by_path.get(path)
        if fid is None or fid in done:
            continue
        warm.setdefault(vol.files[fid].directory, []).append(fid)
    dir_heat = {
        d: sum(log.counts.get(vol.files[f].path, 0) for f in fids)
        for d, fids in warm.items()
    }
    for d in sorted(warm, key=lambda d: -dir_heat[d]):
        for fid in sorted(warm[d], key=lambda f: -log.counts.get(vol.files[f].path, 0)):
            place(vol.files[fid].path, _gap_for(vol, log, vol.files[fid].path, opts))
        packer.reserve(2)

    # 5b. files the monitor never saw, but which live in a directory that is
    #     otherwise hot: keep them beside their siblings rather than exiling
    #     them, because the monitor's view is necessarily incomplete
    hot_dirs = {
        vol.files[vol.by_path[p]].directory
        for p in log.counts if p in vol.by_path
    }
    neighbours: Dict[str, List[int]] = {}
    for fid, rec in vol.files.items():
        if fid in done or rec.directory.startswith(VOLATILE_DIRS):
            continue
        if rec.directory in hot_dirs:
            neighbours.setdefault(rec.directory, []).append(fid)
    for d in sorted(neighbours, key=lambda d: -dir_heat.get(d, 0)):
        for fid in sorted(neighbours[d], key=lambda f: vol.files[f].created):
            place(vol.files[fid].path)
        packer.reserve(2)

    # 5. churn arena
    volatile = [
        fid for fid, rec in vol.files.items()
        if fid not in done and rec.directory.startswith(VOLATILE_DIRS)
    ]
    current_region[0] = R_ARENA
    if opts.use_arenas and volatile:
        arena_clusters = sum(len(vol.chain[f]) for f in volatile)
        for fid in sorted(volatile, key=lambda f: vol.files[f].created):
            place(vol.files[fid].path)
        packer.reserve(int(arena_clusters * (opts.arena_headroom - 1.0)))

    # 6. cold: nothing in this directory was ever touched
    current_region[0] = R_COLD
    cold = [fid for fid in vol.files if fid not in done]
    cold.sort(key=lambda f: (vol.files[f].directory, vol.files[f].created))
    for fid in cold:
        place(vol.files[fid].path)

    assert len(order) == len(vol.files)
    placement = pack(vol, order, gaps=gaps, slack=slack, region=region,
                     ordered_regions={R_BOOT, R_APP, R_SWAP})
    return vol.rebuild(placement)


LAYOUTS: Dict[str, Callable[..., Volume]] = {
    "none": layout_none,
    "win95_files_only": layout_win95_files_only,
    "win95_full": layout_win95_full,
    "win95_full_offline": layout_win95_offline,
    "defrag95": layout_defrag95,
}
