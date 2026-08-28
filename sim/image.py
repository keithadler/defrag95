"""Builds a synthetic but plausible Windows 95 volume, then ages it.

The install is generated from a directory manifest rather than hand-listing
8,000 files, but the named system files that matter (VMM32.VXD, SYSTEM.DAT,
SHELL32.DLL, WINWORD.EXE ...) are real names at close to their real sizes, and
the traces reference them by name.

`age()` is the important half. It replays the file churn of ordinary use --
browser cache, document saves, temp files, an install and an uninstall, swap
growth -- through the VFAT allocator. That is what produces the fragmented
"before" volume, and it is also what we replay *after* each defragmenter to
measure how long the layout survives.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .drive import Drive
from .filesystem import FileRec, OutOfSpace, Volume

KB = 1024
MB = 1024 * 1024

WINDOWS = "C:\\WINDOWS"
SYSTEM = "C:\\WINDOWS\\SYSTEM"
FONTS = "C:\\WINDOWS\\FONTS"
TEMP = "C:\\WINDOWS\\TEMP"
CACHE = "C:\\PROGRA~1\\NETSCAPE\\CACHE"
DOCS = "C:\\MY DOCU~1"
SWAP = "C:\\WINDOWS\\WIN386.SWP"

# name, bytes, kind
NAMED_FILES: List[Tuple[str, int, str]] = [
    ("C:\\IO.SYS", 223148, "boot"),
    ("C:\\MSDOS.SYS", 1676, "boot"),
    ("C:\\COMMAND.COM", 93812, "boot"),
    ("C:\\CONFIG.SYS", 512, "boot"),
    ("C:\\AUTOEXEC.BAT", 512, "boot"),
    (WINDOWS + "\\HIMEM.SYS", 33191, "boot"),
    (WINDOWS + "\\IFSHLP.SYS", 3708, "boot"),
    (WINDOWS + "\\WIN.COM", 22679, "boot"),
    (WINDOWS + "\\SYSTEM.INI", 6144, "boot"),
    (WINDOWS + "\\WIN.INI", 12288, "boot"),
    (WINDOWS + "\\SYSTEM.DAT", 712704, "boot"),
    (WINDOWS + "\\USER.DAT", 139264, "boot"),
    (WINDOWS + "\\EXPLORER.EXE", 204800, "sys"),
    (WINDOWS + "\\REGEDIT.EXE", 71680, "sys"),
    (WINDOWS + "\\NOTEPAD.EXE", 34304, "sys"),
    (SYSTEM + "\\VMM32.VXD", 733600, "boot"),
    (SYSTEM + "\\KRNL386.EXE", 124560, "boot"),
    (SYSTEM + "\\KERNEL32.DLL", 413696, "boot"),
    (SYSTEM + "\\GDI.EXE", 227824, "boot"),
    (SYSTEM + "\\GDI32.DLL", 155136, "boot"),
    (SYSTEM + "\\USER.EXE", 464560, "boot"),
    (SYSTEM + "\\USER32.DLL", 44544, "boot"),
    (SYSTEM + "\\SHELL32.DLL", 679424, "boot"),
    (SYSTEM + "\\SHELL.DLL", 41008, "boot"),
    (SYSTEM + "\\COMCTL32.DLL", 302592, "boot"),
    (SYSTEM + "\\COMDLG32.DLL", 90112, "sys"),
    (SYSTEM + "\\OLE32.DLL", 553984, "sys"),
    (SYSTEM + "\\OLEAUT32.DLL", 380416, "sys"),
    (SYSTEM + "\\MFC40.DLL", 924432, "sys"),
    (SYSTEM + "\\MSVCRT40.DLL", 326656, "sys"),
    (SYSTEM + "\\ADVAPI32.DLL", 66560, "boot"),
    (SYSTEM + "\\MSGSRV32.EXE", 21024, "boot"),
    (SYSTEM + "\\VGA.DRV", 51872, "boot"),
    (SYSTEM + "\\MOUSE.DRV", 12448, "boot"),
    (SYSTEM + "\\MMSYSTEM.DLL", 78848, "boot"),
    (SYSTEM + "\\SYSTHUNK.DLL", 20480, "boot"),
]

APP_DIRS = {
    "word": "C:\\MSOFFICE\\WINWORD",
    "excel": "C:\\MSOFFICE\\EXCEL",
    "netscape": "C:\\PROGRA~1\\NETSCAPE\\PROGRAM",
}

APP_EXES = {
    "word": (APP_DIRS["word"] + "\\WINWORD.EXE", 3897344),
    "excel": (APP_DIRS["excel"] + "\\EXCEL.EXE", 5214208),
    "netscape": (APP_DIRS["netscape"] + "\\NETSCAPE.EXE", 3170304),
}


@dataclass
class AppProfile:
    """What one application touches when it starts, and how often it is started."""
    name: str
    exe: str
    exe_size: int
    own_files: List[str] = field(default_factory=list)
    sys_files: List[str] = field(default_factory=list)
    load_order: List[str] = field(default_factory=list)   # fixed per install
    docs: List[str] = field(default_factory=list)
    launches_per_day: float = 1.0


@dataclass
class Profiles:
    """The parts of the install that traces and the layout planner refer to by name."""
    boot_core: List[str] = field(default_factory=list)
    driver_pool: List[str] = field(default_factory=list)
    font_pool: List[str] = field(default_factory=list)
    shell_files: List[str] = field(default_factory=list)
    # What *this* machine loads at boot. Fixed hardware loads a fixed driver
    # set in a near-fixed order on every boot; that repeatability is the whole
    # premise of laying the disk out by observed access order.
    boot_drivers: List[str] = field(default_factory=list)
    boot_fonts: List[str] = field(default_factory=list)
    boot_shell: List[str] = field(default_factory=list)
    apps: Dict[str, AppProfile] = field(default_factory=dict)
    docs: List[str] = field(default_factory=list)
    swap: str = SWAP


@dataclass
class Image:
    """A built volume together with the profiles describing what is on it."""
    volume: Volume
    profiles: Profiles
    seed: int


def _lognormal_size(rng: random.Random, median_kb: float, sigma: float,
                    lo_kb: float, hi_kb: float) -> int:
    import math

    v = rng.lognormvariate(math.log(median_kb), sigma)
    return int(min(max(v, lo_kb), hi_kb) * KB)


def _bulk(vol: Volume, rng: random.Random, directory: str, prefix: str, ext: str,
          count: int, median_kb: float, sigma: float, lo_kb: float, hi_kb: float,
          kind: str) -> List[str]:
    out = []
    for i in range(count):
        path = "%s\\%s%03d.%s" % (directory, prefix, i, ext)
        vol.create(path, _lognormal_size(rng, median_kb, sigma, lo_kb, hi_kb), kind)
        out.append(path)
    return out


def build_image(drive: Drive, seed: int = 1996, partition_sectors: Optional[int] = None,
                target_fill: float = 0.62) -> Image:
    """Generate a Windows 95 install on a fresh volume.

    Files are created in the order setup would have written them, so the
    fresh volume is perfectly ordered and perfectly unfragmented. Everything
    interesting happens in `age()`.
    """
    rng = random.Random(seed)
    vol = Volume(drive, partition_sectors=partition_sectors)
    prof = Profiles()

    # --- the install, in the order the setup program wrote it ---------------
    for path, size, kind in NAMED_FILES:
        vol.create(path, size, kind)
    prof.boot_core = [p for p, _, k in NAMED_FILES if k == "boot"]

    prof.driver_pool = _bulk(vol, rng, SYSTEM, "VXD", "VXD", 90, 24, 0.8, 4, 260, "sys")
    prof.driver_pool += _bulk(vol, rng, SYSTEM, "DRV", "DRV", 60, 18, 0.8, 3, 180, "sys")
    sys_dlls = _bulk(vol, rng, SYSTEM, "SYSDLL", "DLL", 300, 48, 1.0, 4, 900, "sys")
    prof.font_pool = _bulk(vol, rng, FONTS, "FONT", "FON", 70, 62, 0.6, 12, 340, "sys")
    prof.shell_files = _bulk(vol, rng, WINDOWS, "SHL", "DLL", 60, 40, 0.9, 5, 400, "sys")
    _bulk(vol, rng, WINDOWS + "\\HELP", "HLP", "HLP", 45, 320, 0.9, 20, 4200, "cold")
    _bulk(vol, rng, WINDOWS + "\\MEDIA", "SND", "WAV", 30, 180, 0.7, 20, 900, "cold")
    _bulk(vol, rng, WINDOWS + "\\SYSTEM\\VIEWERS", "VIEW", "DLL", 40, 70, 0.8, 8, 500, "cold")
    vol.create(TEMP + "\\PLACEHLD.TMP", 4 * KB, "temp")

    for app, directory in APP_DIRS.items():
        exe, size = APP_EXES[app]
        vol.create(exe, size, "app")
        own = _bulk(vol, rng, directory, app[:3].upper(), "DLL",
                    {"word": 55, "excel": 45, "netscape": 30}[app],
                    90, 1.0, 5, 1400, "app")
        prof.apps[app] = AppProfile(
            name=app,
            exe=exe,
            exe_size=size,
            own_files=own,
            sys_files=rng.sample(sys_dlls, {"word": 34, "excel": 30, "netscape": 22}[app]),
            launches_per_day={"word": 2.0, "excel": 1.0, "netscape": 2.0}[app],
        )
    # cold parts of the Office install nobody opens
    _bulk(vol, rng, "C:\\MSOFFICE\\POWERPNT", "PPT", "DLL", 70, 120, 1.0, 6, 2400, "cold")
    _bulk(vol, rng, "C:\\MSOFFICE\\CLIPART", "CLIP", "WMF", 220, 90, 0.8, 8, 600, "cold")

    # the fixed per-machine load sets
    prof.boot_drivers = rng.sample(prof.driver_pool, int(len(prof.driver_pool) * 0.62))
    prof.boot_fonts = rng.sample(prof.font_pool, int(len(prof.font_pool) * 0.5))
    prof.boot_shell = rng.sample(prof.shell_files, int(len(prof.shell_files) * 0.45))
    for app_prof in prof.apps.values():
        own = rng.sample(app_prof.own_files, max(1, int(len(app_prof.own_files) * 0.7)))
        shared = rng.sample(app_prof.sys_files, max(1, int(len(app_prof.sys_files) * 0.8)))
        order = own + shared
        rng.shuffle(order)
        app_prof.load_order = order

    prof.docs = _bulk(vol, rng, DOCS, "DOC", "DOC", 90, 90, 1.0, 8, 900, "data")
    prof.docs += _bulk(vol, rng, DOCS, "SHT", "XLS", 50, 110, 1.0, 10, 1200, "data")
    for app in ("word", "excel"):
        prof.apps[app].docs = rng.sample(prof.docs, 40)
    prof.apps["netscape"].docs = _bulk(vol, rng, CACHE, "CACHE", "DAT", 120, 22, 1.0, 2, 300, "cache")

    _bulk(vol, rng, "C:\\GAMES\\DOOM2", "WAD", "WAD", 24, 4200, 0.7, 200, 14000, "cold")

    # swap file, created by Windows on first boot after everything else
    vol.create(SWAP, 40 * MB, "swap")

    # filler to reach the target fill level
    filler = 0
    while vol.fill() < target_fill:
        size = _lognormal_size(rng, 700, 1.1, 20, 9000)
        if vol.free_count * vol.cluster_bytes < size + 8 * MB:
            break
        vol.create("C:\\ARCHIVE\\ARC%04d.ZIP" % filler, size, "cold")
        filler += 1

    return Image(volume=vol, profiles=prof, seed=seed)


# --- ageing -------------------------------------------------------------------

def age(vol: Volume, prof: Profiles, days: int, seed: int) -> None:
    """Replay `days` of ordinary use through the allocator, in place."""
    rng = random.Random(seed)
    tag = seed % 100          # keeps scratch names unique across ageing runs
    cache_seq = 0
    temp_seq = 0
    util_seq = 0
    installed: List[str] = []

    for day in range(days):
        # when the disk gets tight the user clears out old downloads
        if vol.free_count < vol.cluster_count * 0.04:
            spare = [p for p in vol.by_path if p.startswith("C:\\ARCHIVE")]
            rng.shuffle(spare)
            for path in spare[: max(1, len(spare) // 6)]:
                vol.delete(path)

        # web browsing: new cache entries, oldest evicted
        # only browser-generated entries are evicted; the seed set is left
        # alone so that traces built against the fresh image stay valid
        cache_files = [p for p in vol.by_path if p.startswith(CACHE + "\\NEW")]
        for _ in range(rng.randint(18, 34)):
            path = "%s\\N%02d%05d.DAT" % (CACHE, tag, cache_seq)
            cache_seq += 1
            try:
                vol.create(path, _lognormal_size(rng, 24, 1.0, 2, 400), "cache")
            except OutOfSpace:
                break
            cache_files.append(path)
        rng.shuffle(cache_files)
        for path in cache_files[: rng.randint(16, 30)]:
            if path in vol.by_path:
                vol.delete(path)

        # documents: open, edit, save (append), plus the editor's temp file
        for _ in range(rng.randint(2, 5)):
            doc = rng.choice(prof.docs)
            if doc in vol.by_path:
                try:
                    vol.append(doc, _lognormal_size(rng, 24, 1.0, 1, 400))
                except OutOfSpace:
                    pass
            tmp = "%s\\~W%02d%04d.TMP" % (TEMP, tag, temp_seq)
            temp_seq += 1
            try:
                vol.create(tmp, _lognormal_size(rng, 60, 1.0, 4, 900), "temp")
            except OutOfSpace:
                pass
            else:
                if rng.random() < 0.85:
                    vol.delete(tmp)

        # new documents
        if rng.random() < 0.45:
            path = "%s\\N%02d%04d.DOC" % (DOCS, tag, day)
            if path not in vol.by_path:
                try:
                    vol.create(path, _lognormal_size(rng, 70, 1.0, 8, 800), "data")
                except OutOfSpace:
                    pass
                else:
                    prof.docs.append(path)

        # scratch files from anything else running
        for _ in range(rng.randint(4, 12)):
            tmp = "%s\\T%02d%05d.TMP" % (TEMP, tag, temp_seq)
            temp_seq += 1
            try:
                vol.create(tmp, _lognormal_size(rng, 12, 1.2, 1, 600), "temp")
            except OutOfSpace:
                pass
            else:
                if rng.random() < 0.9:
                    vol.delete(tmp)

        # A service pack, driver update or application installer rewrites a
        # slice of the shared system files. This is what actually scattered
        # real machines: the boot set stops being where setup put it.
        if day % 31 == 17:
            pool = [p for p in vol.by_path
                    if p.startswith(SYSTEM + "\\") or p.startswith("C:\\MSOFFICE")]
            for path in rng.sample(pool, max(1, int(len(pool) * rng.uniform(0.03, 0.08)))):
                rec = vol.files[vol.by_path[path]]
                size = int(rec.size * rng.uniform(0.95, 1.25))
                kind = rec.kind
                vol.delete(path)
                try:
                    vol.create(path, size, kind)
                except OutOfSpace:
                    pass

        # a shareware utility gets installed, and later removed
        if day % 6 == 3:
            directory = "C:\\UTIL%02d%02d" % (tag, util_seq)
            util_seq += 1
            files = []
            for i in range(rng.randint(20, 45)):
                path = "%s\\U%03d.DAT" % (directory, i)
                try:
                    vol.create(path, _lognormal_size(rng, 80, 1.1, 4, 2400), "cold")
                except OutOfSpace:
                    break
                files.append(path)
            installed.append(directory)
        if day % 6 == 5 and installed:
            victim = installed.pop(0)
            for path in [p for p in list(vol.by_path) if p.startswith(victim + "\\")]:
                if rng.random() < 0.8:
                    vol.delete(path)

        # the swap file grows under memory pressure, up to what this machine
        # actually needs; Win95 grew it readily and shrank it only on reboot
        if SWAP in vol.by_path and rng.random() < 0.35:
            size = vol.files[vol.by_path[SWAP]].size
            if size < 72 * MB:
                try:
                    vol.append(SWAP, rng.choice([2, 4, 8]) * MB)
                except OutOfSpace:
                    pass
        # ... and is re-created at its default size after a reboot
        if day % 11 == 10 and SWAP in vol.by_path:
            vol.delete(SWAP)
            try:
                vol.create(SWAP, 40 * MB, "swap")
            except OutOfSpace:
                pass
