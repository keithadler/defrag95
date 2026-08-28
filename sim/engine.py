"""Replays a trace against a volume and charges it to the drive model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .cache import ClusterCache
from .drive import Arm, ArmStats, Drive
from .filesystem import Volume
from .workload import Access, Scenario

BLOCK_SECTORS = 8       # VCACHE tracks 4 KB blocks


@dataclass
class Result:
    """What one scenario cost, and which of the three mechanics it went on."""
    scenario: str
    ms: float
    stats: ArmStats
    cache_hits: int
    cache_misses: int
    missing_paths: int = 0


def run_scenario(vol: Volume, scenario: Scenario, cache_blocks: int = 768) -> Result:
    """Cold-cache replay of one scenario. Returns wall-clock disk time."""
    arm = Arm(vol.drive)
    cache = ClusterCache(cache_blocks)
    total = _replay(vol, scenario, arm, cache)
    return Result(scenario.name, total, arm.stats, cache.hits, cache.misses,
                  _replay.missing)


def _replay(vol: Volume, scenario: Scenario, arm: Arm, cache: ClusterCache) -> float:
    total = 0.0
    missing = 0

    for acc in scenario.accesses:
        if acc.path not in vol.by_path:
            missing += 1
            continue
        runs = vol.read_runs(acc.path, acc.offset, acc.length)
        pending: List[Tuple[int, int]] = []      # (lba, sectors), merged

        def emit(lba: int, sectors: int) -> None:
            if pending and pending[-1][0] + pending[-1][1] == lba:
                pending[-1] = (pending[-1][0], pending[-1][1] + sectors)
            else:
                pending.append((lba, sectors))

        for cluster, first, count in runs:
            base = vol.lba_of(cluster)
            b0 = first // BLOCK_SECTORS
            b1 = (first + count - 1) // BLOCK_SECTORS
            for b in range(b0, b1 + 1):
                lo = max(first, b * BLOCK_SECTORS)
                hi = min(first + count, (b + 1) * BLOCK_SECTORS)
                key = cluster * 64 + b
                if acc.write:
                    # write-through: the platter is touched either way
                    cache.insert(key)
                    emit(base + lo, hi - lo)
                elif not cache.probe(key):
                    emit(base + lo, hi - lo)
        for lba, sectors in pending:
            total += arm.transfer(lba, sectors)

    _replay.missing = missing
    return total


_replay.missing = 0


def run_workload(vol: Volume, workload: Sequence[Scenario],
                 cache_blocks: int = 768) -> Dict[str, Result]:
    """Replay every scenario in the workload, each with its own cold cache."""
    return {sc.name: run_scenario(vol, sc, cache_blocks) for sc in workload}


def run_session(vol: Volume, workload: Sequence[Scenario],
                cache_blocks: int = 768) -> float:
    """Replay the whole day back-to-back with one cache and one head position.

    The default methodology gives every scenario a cold cache and a parked
    head, which is the conservative choice -- it is also the pessimistic one
    for a layout that wins by keeping related things together. This is the
    other end: nothing is flushed between a boot and the application launches
    that follow it.
    """
    total = 0.0
    cache = ClusterCache(cache_blocks)
    arm = Arm(vol.drive)
    for sc in workload:
        total += _replay(vol, sc, arm, cache) * sc.per_day
    return total


def weighted_day_ms(results: Dict[str, Result], workload: Sequence[Scenario]) -> float:
    """Disk time for one modelled working day."""
    per_day = {sc.name: sc.per_day for sc in workload}
    return sum(r.ms * per_day[name] for name, r in results.items())


def defrag_cost_ms(before: Volume, after: Volume) -> Tuple[float, int]:
    """Lower-bound cost of performing the reorganisation itself.

    Every cluster that changes address is read once from its old location and
    written once to its new one. A real defragmenter also shuffles clusters
    through a scratch area and rewrites the FAT, so this is optimistic for all
    policies equally.
    """
    arm = Arm(before.drive)
    moved = 0
    total = 0.0
    for fid, rec in before.files.items():
        old = before.chain[fid]
        new = after.chain[fid]
        if old == new:
            continue
        moved += len(old)
        for start, length in before.extents(fid):
            total += arm.transfer(before.lba_of(start), length * before.cluster_sectors)
        for start, length in after.extents(fid):
            total += arm.transfer(after.lba_of(start), length * after.cluster_sectors)
    return total, moved
