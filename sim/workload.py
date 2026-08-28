"""Workload traces: what a Windows 95 machine actually reads, and in what order.

Two independent draws are produced from the same generator:

  * a **training** workload, which is all the defrag95 planner is allowed to
    look at (it stands in for a background usage monitor of the kind Windows 98
    later shipped as Task Monitor / APPLOG), and
  * a **held-out evaluation** workload, which is what every layout is scored
    on.

They differ in which drivers load, which DLLs each app pulls in, which
documents are opened and in what order. A layout that only wins because it was
handed the answer key would not survive the held-out set.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .filesystem import Volume
from .image import Profiles

KB = 1024
MB = 1024 * 1024


@dataclass
class Access:
    """One read or write of a byte range in a file."""
    path: str
    offset: int
    length: int
    write: bool = False


@dataclass
class Scenario:
    """A named trace, and how often it happens in a working day."""
    name: str
    accesses: List[Access]
    per_day: float = 1.0        # how often this happens in a working day
    description: str = ""


Workload = List[Scenario]


def _load(vol: Volume, path: str, rng: random.Random,
          frac_lo: float = 0.55, frac_hi: float = 1.0) -> List[Access]:
    """An image load: header first, then the bulk of the sections."""
    size = vol.files[vol.by_path[path]].size
    frac = rng.uniform(frac_lo, frac_hi)
    body = int(size * frac)
    if size <= 8 * KB:
        return [Access(path, 0, size)]
    head = min(4 * KB, size)
    return [Access(path, 0, head), Access(path, head, max(1, body - head))]


def _read_all(vol: Volume, path: str) -> List[Access]:
    return [Access(path, 0, vol.files[vol.by_path[path]].size)]


def _chunks(vol: Volume, path: str, rng: random.Random, n: int, size: int,
            write: bool = False) -> List[Access]:
    """n scattered reads (or writes) of `size` bytes inside one file."""
    fsize = vol.files[vol.by_path[path]].size
    out = []
    for _ in range(n):
        off = rng.randrange(0, max(1, fsize - size)) if fsize > size else 0
        out.append(Access(path, off - off % 4096, size, write))
    return out


def _jitter(seq: Sequence[str], pool: Sequence[str], rng: random.Random,
            drop: float = 0.04, extras: int = 3, swap_frac: float = 0.06) -> List[str]:
    """Run-to-run variation around a fixed load set.

    Boots on unchanged hardware are highly repeatable but not identical: an
    optional component fails to load, a newly installed driver appears, two
    loads race and swap order. Everything a layout policy is scored on is drawn
    with this jitter, independently of what the monitor saw.
    """
    out = [p for p in seq if rng.random() >= drop]
    spare = [p for p in pool if p not in set(out)]
    if spare and extras:
        out += rng.sample(spare, min(extras, len(spare)))
        rng.shuffle(out) if False else None
    for i in range(len(out) - 1):
        if rng.random() < swap_frac:
            out[i], out[i + 1] = out[i + 1], out[i]
    return out


def boot_scenario(vol: Volume, prof: Profiles, rng: random.Random,
                  jitter: float = 1.0) -> Scenario:
    """Power-on to a usable desktop."""
    a: List[Access] = []
    # real mode: MBR, DOS kernel, config
    for p in ("C:\\IO.SYS", "C:\\MSDOS.SYS", "C:\\CONFIG.SYS", "C:\\AUTOEXEC.BAT"):
        a += _read_all(vol, p)
    a += _load(vol, "C:\\COMMAND.COM", rng, 0.45, 0.7)
    for p in ("C:\\WINDOWS\\HIMEM.SYS", "C:\\WINDOWS\\IFSHLP.SYS"):
        a += _read_all(vol, p)
    # WIN.COM -> VMM32 -> the ring 0 world
    a += _read_all(vol, "C:\\WINDOWS\\WIN.COM")
    a += _read_all(vol, "C:\\WINDOWS\\SYSTEM.INI")
    a += _load(vol, "C:\\WINDOWS\\SYSTEM\\VMM32.VXD", rng, 0.85, 1.0)
    # registry: the hive is walked, not streamed
    a += _chunks(vol, "C:\\WINDOWS\\SYSTEM.DAT", rng, 6, 48 * KB)
    # dynamically loaded VxDs and device drivers
    for p in _jitter(prof.boot_drivers, prof.driver_pool, rng,
                     drop=0.04 * jitter, extras=int(3 * jitter),
                     swap_frac=0.06 * jitter):
        a += _load(vol, p, rng, 0.6, 1.0)
    # the 16/32-bit core
    for p in (
        "C:\\WINDOWS\\SYSTEM\\KRNL386.EXE", "C:\\WINDOWS\\SYSTEM\\KERNEL32.DLL",
        "C:\\WINDOWS\\SYSTEM\\GDI.EXE", "C:\\WINDOWS\\SYSTEM\\GDI32.DLL",
        "C:\\WINDOWS\\SYSTEM\\USER.EXE", "C:\\WINDOWS\\SYSTEM\\USER32.DLL",
        "C:\\WINDOWS\\SYSTEM\\ADVAPI32.DLL", "C:\\WINDOWS\\SYSTEM\\MSGSRV32.EXE",
        "C:\\WINDOWS\\SYSTEM\\VGA.DRV", "C:\\WINDOWS\\SYSTEM\\MOUSE.DRV",
        "C:\\WINDOWS\\SYSTEM\\MMSYSTEM.DLL", "C:\\WINDOWS\\SYSTEM\\SYSTHUNK.DLL",
    ):
        a += _load(vol, p, rng, 0.7, 1.0)
    a += _chunks(vol, "C:\\WINDOWS\\USER.DAT", rng, 3, 32 * KB)
    a += _read_all(vol, "C:\\WINDOWS\\WIN.INI")
    # the shell
    for p in ("C:\\WINDOWS\\SYSTEM\\SHELL32.DLL", "C:\\WINDOWS\\SYSTEM\\SHELL.DLL",
              "C:\\WINDOWS\\SYSTEM\\COMCTL32.DLL", "C:\\WINDOWS\\EXPLORER.EXE"):
        a += _load(vol, p, rng, 0.6, 1.0)
    for p in _jitter(prof.boot_shell, prof.shell_files, rng,
                     drop=0.04 * jitter, extras=int(2 * jitter),
                     swap_frac=0.06 * jitter):
        a += _load(vol, p, rng, 0.5, 1.0)
    # font table
    for p in _jitter(prof.boot_fonts, prof.font_pool, rng,
                     drop=0.04 * jitter, extras=int(2 * jitter),
                     swap_frac=0.06 * jitter):
        a += _read_all(vol, p)
    # shell init hits the registry again
    a += _chunks(vol, "C:\\WINDOWS\\SYSTEM.DAT", rng, 8, 32 * KB)
    a += _chunks(vol, "C:\\WINDOWS\\USER.DAT", rng, 4, 16 * KB)
    return Scenario("boot", a, per_day=1.0,
                    description="cold boot: power-on to usable desktop")


def launch_scenario(vol: Volume, prof: Profiles, app: str, rng: random.Random,
                    jitter: float = 1.0) -> Scenario:
    """A cold launch of one application from the shell."""
    p = prof.apps[app]
    a: List[Access] = []
    a += _load(vol, p.exe, rng, 0.35, 0.6)
    for f in _jitter(p.load_order, p.own_files + p.sys_files, rng,
                     drop=0.04 * jitter, extras=int(2 * jitter),
                     swap_frac=0.06 * jitter):
        a += _load(vol, f, rng, 0.5, 1.0)
    a += _chunks(vol, "C:\\WINDOWS\\SYSTEM.DAT", rng, 3, 16 * KB)
    if p.docs:
        doc = rng.choice(p.docs)
        a += _read_all(vol, doc)
    return Scenario("launch_" + app, a, per_day=p.launches_per_day,
                    description="cold launch of %s from the shell" % app.upper())


def document_scenario(vol: Volume, prof: Profiles, rng: random.Random) -> Scenario:
    """Opening and saving eight documents."""
    a: List[Access] = []
    for doc in rng.sample(prof.docs, 8):
        a += _read_all(vol, doc)
        a += _chunks(vol, doc, rng, 2, 32 * KB, write=True)   # save in place
    return Scenario("documents", a, per_day=3.0,
                    description="open and save eight documents")


def browse_scenario(vol: Volume, prof: Profiles, rng: random.Random) -> Scenario:
    """A browsing session working against the disk cache."""
    a: List[Access] = []
    cache = prof.apps["netscape"].docs
    for f in rng.sample(cache, 40):
        a += _read_all(vol, f)
        if rng.random() < 0.5:
            a += _chunks(vol, f, rng, 1, 8 * KB, write=True)
    return Scenario("browse", a, per_day=2.0,
                    description="a browsing session against the disk cache")


def paging_scenario(vol: Volume, prof: Profiles, rng: random.Random) -> Scenario:
    """Alt-tabbing between two big apps on a 16 MB machine."""
    a: List[Access] = []
    swap = prof.swap
    for _ in range(140):
        a += _chunks(vol, swap, rng, 1, 4 * KB, write=rng.random() < 0.45)
        if rng.random() < 0.25:
            a += _chunks(vol, prof.apps["word"].exe, rng, 1, 8 * KB)
    return Scenario("paging", a, per_day=6.0,
                    description="page-in/page-out storm while task switching")


def make_workload(vol: Volume, prof: Profiles, seed: int, jitter: float = 1.0) -> Workload:
    """One day's traces. `jitter` scales run-to-run variation (1.0 = default)."""
    rng = random.Random(seed)
    return [
        boot_scenario(vol, prof, rng, jitter),
        launch_scenario(vol, prof, "word", rng, jitter),
        launch_scenario(vol, prof, "excel", rng, jitter),
        launch_scenario(vol, prof, "netscape", rng, jitter),
        document_scenario(vol, prof, rng),
        browse_scenario(vol, prof, rng),
        paging_scenario(vol, prof, rng),
    ]


# --- what the planner is allowed to know -------------------------------------

@dataclass
class AccessLog:
    """What a background usage monitor could have recorded over the training period."""

    counts: Dict[str, int] = field(default_factory=dict)
    bytes_read: Dict[str, int] = field(default_factory=dict)
    bytes_written: Dict[str, int] = field(default_factory=dict)
    rank: Dict[str, Dict[str, float]] = field(default_factory=dict)  # scenario -> mean touch rank
    scenario_weight: Dict[str, float] = field(default_factory=dict)
    days: int = 1

    @property
    def order(self) -> Dict[str, List[str]]:
        """Per scenario, the paths the monitor saw, in mean first-touch order."""
        return {sc: sorted(r, key=lambda p: r[p]) for sc, r in self.rank.items()}

    def touched(self) -> set:
        """Every path the monitor ever saw."""
        return set(self.counts)


def observe(days_of_traces: Sequence[Workload], days: int) -> AccessLog:
    """Aggregate what the monitor saw. Ordering uses each file's mean touch rank.

    A monitor watching for two weeks sees fourteen boots, not one, so its
    picture of the boot set is close to complete even though any single boot
    loads only part of it. Files it never saw are not treated as cold here --
    that decision is left to the layout policy.
    """
    log = AccessLog(days=days)
    ranks: Dict[str, Dict[str, List[float]]] = {}
    for workload in days_of_traces:
        for sc in workload:
            log.scenario_weight[sc.name] = sc.per_day
            seen: Dict[str, int] = {}
            for i, acc in enumerate(sc.accesses):
                log.counts[acc.path] = log.counts.get(acc.path, 0) + 1
                log.bytes_read[acc.path] = log.bytes_read.get(acc.path, 0) + acc.length
                if acc.write:
                    log.bytes_written[acc.path] = (
                        log.bytes_written.get(acc.path, 0) + acc.length)
                if acc.path not in seen:
                    seen[acc.path] = i
            n = max(1, len(sc.accesses))
            bucket = ranks.setdefault(sc.name, {})
            for path, pos in seen.items():
                bucket.setdefault(path, []).append(pos / n)
    log.rank = {
        sc: {p: sum(v) / len(v) - 0.001 * len(v) for p, v in paths.items()}
        for sc, paths in ranks.items()
    }
    return log


def observe_period(vol: Volume, prof: Profiles, days: int, seed: int,
                   jitter: float = 1.0) -> AccessLog:
    """Run the monitor for `days` days of ordinary use and return its log."""
    return observe(
        [make_workload(vol, prof, seed=seed * 1000 + d, jitter=jitter)
         for d in range(days)],
        days,
    )
