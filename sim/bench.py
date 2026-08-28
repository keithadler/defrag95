#!/usr/bin/env python3
"""defrag95 benchmark harness.

Builds an aged Windows 95 volume, applies each layout policy to the *same*
volume, and replays a held-out workload against each. Everything is seeded, so
`python3 -m sim.bench` reproduces the published numbers exactly.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .drive import DRIVES, Drive, DriveSpec
from .engine import defrag_cost_ms, run_session, run_workload, weighted_day_ms
from .filesystem import Volume
from .image import Profiles, age, build_image
from .layouts import (
    Defrag95Options,
    layout_defrag95,
    layout_none,
    layout_win95_files_only,
    layout_win95_full,
    layout_win95_offline,
)
from .workload import AccessLog, Workload, make_workload, observe_period

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(os.path.dirname(HERE), "results")

INSTALL_SEED = 1996
MONITOR_SEED = 101
AGE_SEED = 7
EVAL_SEEDS = (9001, 9002, 9003, 9004, 9005)
AGE_DAYS = 365
DURABILITY_DAYS = 90
MONITOR_DAYS = 14
CACHE_BLOCKS = 768              # 3 MB of VCACHE on a 16 MB machine
TOUCHUP_SLACK = 512             # 16 MB of tolerance in a maintenance pass
FAT16_MAX_SECTORS = (2 * 1024 ** 3) // 512 - 64

LAYOUT_ORDER = ["none", "win95_files_only", "win95_full", "win95_full_offline", "defrag95"]
LAYOUT_LABEL = {
    "none": "No defrag (aged volume)",
    "win95_files_only": "Win95 Defrag - files only",
    "win95_full": "Win95 Defrag - full (as shipped)",
    "win95_full_offline": "Win95 Defrag - full, DOS mode",
    "defrag95": "defrag95",
}
LAYOUT_FN: Dict[str, Callable[[Volume, AccessLog], Volume]] = {
    "none": layout_none,
    "win95_files_only": layout_win95_files_only,
    "win95_full": layout_win95_full,
    "win95_full_offline": layout_win95_offline,
    "defrag95": layout_defrag95,
}

SCENARIO_LABEL = {
    "boot": "Cold boot",
    "launch_word": "Launch Word",
    "launch_excel": "Launch Excel",
    "launch_netscape": "Launch Netscape",
    "documents": "Open/save 8 documents",
    "browse": "Browsing session",
    "paging": "Paging storm",
}


@dataclass
class Case:
    """One experiment: an aged volume, the monitor log, and the held-out workloads."""
    drive_key: str
    aged: Volume
    fresh_report: Dict[str, float]
    profiles: Profiles
    log: AccessLog
    evals: List[Workload]
    target_fill: float


def partition_sectors_for(drive: Drive) -> int:
    """FAT16 with 32 KB clusters tops out at 2 GB, so a bigger drive is split."""
    return min(drive.total_sectors, FAT16_MAX_SECTORS)


def make_case(drive_key: str = "1996", target_fill: float = 0.62, jitter: float = 1.0,
              monitor_days: int = MONITOR_DAYS, age_days: int = AGE_DAYS) -> Case:
    """Build a volume, monitor it, then age it a year.

    The evaluation workloads are drawn against the fresh volume and are
    independent of the monitor log, which is what makes them held out.
    """
    drive = Drive(DRIVES[drive_key])
    img = build_image(drive, seed=INSTALL_SEED,
                      partition_sectors=partition_sectors_for(drive),
                      target_fill=target_fill)
    fresh_report = img.volume.fragmentation_report()
    log = observe_period(img.volume, img.profiles, monitor_days, seed=MONITOR_SEED,
                         jitter=jitter)
    evals = [make_workload(img.volume, img.profiles, seed=s, jitter=jitter)
             for s in EVAL_SEEDS]
    age(img.volume, img.profiles, age_days, seed=AGE_SEED)
    return Case(drive_key, img.volume, fresh_report, img.profiles, log, evals, target_fill)


def apply_layout(name: str, vol: Volume, log: AccessLog,
                 opts: Optional[Defrag95Options] = None, slack: int = 0) -> Volume:
    """Run one layout policy over a volume and return the result."""
    if name == "defrag95":
        return layout_defrag95(vol, log, opts, slack=slack)
    if name in ("win95_full", "win95_full_offline"):
        return LAYOUT_FN[name](vol, log, slack=slack)
    return LAYOUT_FN[name](vol, log)


@dataclass
class Measurement:
    """Scenario timings and the boot breakdown, averaged over the eval draws."""
    per_scenario: Dict[str, Tuple[float, float]]     # name -> (mean ms, stdev)
    day: Tuple[float, float]
    boot_breakdown: Dict[str, float]


def measure(vol: Volume, evals: Sequence[Workload],
            cache_blocks: int = CACHE_BLOCKS) -> Measurement:
    """Replay every evaluation workload and average the result."""
    samples: Dict[str, List[float]] = {}
    days: List[float] = []
    breakdown: Dict[str, float] = {}
    for w in evals:
        res = run_workload(vol, w, cache_blocks)
        for name, r in res.items():
            samples.setdefault(name, []).append(r.ms)
        days.append(weighted_day_ms(res, w))
        st = res["boot"].stats
        for k, v in (("seek", st.seek_ms), ("rotation", st.rotation_ms),
                     ("transfer", st.transfer_ms), ("overhead", st.overhead_ms)):
            breakdown[k] = breakdown.get(k, 0.0) + v / len(evals)
    per = {
        k: (statistics.mean(v), statistics.pstdev(v) if len(v) > 1 else 0.0)
        for k, v in samples.items()
    }
    day = (statistics.mean(days), statistics.pstdev(days) if len(days) > 1 else 0.0)
    return Measurement(per, day, breakdown)


def pct_faster(new: float, old: float) -> float:
    """How much less time `new` takes than `old`, as a percentage."""
    return (old - new) / old * 100.0 if old else 0.0


# --- report sections ----------------------------------------------------------

def run_main(case: Case) -> Dict[str, object]:
    """Apply every layout to the same aged volume and measure each one."""
    out: Dict[str, object] = {}
    volumes: Dict[str, Volume] = {}
    measures: Dict[str, Measurement] = {}
    defrag: Dict[str, Tuple[float, int]] = {}
    for name in LAYOUT_ORDER:
        v = apply_layout(name, case.aged, case.log)
        volumes[name] = v
        measures[name] = measure(v, case.evals)
        defrag[name] = defrag_cost_ms(case.aged, v) if name != "none" else (0.0, 0)
    out["volumes"] = volumes
    out["measures"] = measures
    out["defrag"] = defrag
    out["fragmentation"] = {n: volumes[n].fragmentation_report() for n in LAYOUT_ORDER}
    return out


@dataclass
class Durability:
    """What became of a layout after further use, and what a re-run costs."""
    after: Measurement
    report: Dict[str, float]
    rerun: Measurement
    rerun_cost_ms: float
    rerun_clusters: int


def run_durability(case: Case, volumes: Dict[str, Volume],
                   days: int = DURABILITY_DAYS) -> Dict[str, Durability]:
    """Age each layout by another `days`, then let each defragmenter run again.

    The re-run is the fair comparison for upkeep: a Win95 user reaches for the
    same Defrag they ran before, and a defrag95 user re-runs defrag95, which by
    then only has to move what has drifted out of place.
    """
    out: Dict[str, Durability] = {}
    for name, vol in volumes.items():
        v = vol.copy()
        prof = copy.deepcopy(case.profiles)
        age(v, prof, days, seed=AGE_SEED + 1)
        after = measure(v, case.evals)
        again = apply_layout(name, v, case.log, slack=TOUCHUP_SLACK)
        cost, moved = (0.0, 0) if name == "none" else defrag_cost_ms(v, again)
        out[name] = Durability(after, v.fragmentation_report(),
                               measure(again, case.evals), cost, moved)
    return out


ABLATIONS: List[Tuple[str, Defrag95Options]] = [
    ("full policy", Defrag95Options()),
    ("without growth gaps", Defrag95Options(use_gaps=False)),
    ("without churn arena", Defrag95Options(use_arenas=False)),
    ("without swap placement", Defrag95Options(place_swap=False)),
    ("without access ordering", Defrag95Options(use_access_order=False)),
    ("ordering only", Defrag95Options(use_gaps=False, use_arenas=False, place_swap=False)),
]


def run_ablation(case: Case) -> List[Tuple[str, Measurement, Measurement]]:
    """Measure the policy with each of its parts turned off in turn."""
    rows = []
    for label, opts in ABLATIONS:
        v = apply_layout("defrag95", case.aged, case.log, opts)
        before = measure(v, case.evals)
        w = v.copy()
        prof = copy.deepcopy(case.profiles)
        age(w, prof, DURABILITY_DAYS, seed=AGE_SEED + 1)
        rows.append((label, before, measure(w, case.evals)))
    return rows


@dataclass
class SensitivityRow:
    """One row of the sweep: one assumption changed, both layouts remeasured."""
    axis: str
    setting: str
    boot_win95: float
    boot_defrag95: float
    day_win95: float
    day_defrag95: float

    @property
    def boot_gain(self) -> float:
        """How much less boot time defrag95 takes in this configuration."""
        return pct_faster(self.boot_defrag95, self.boot_win95)

    @property
    def day_gain(self) -> float:
        """How much less disk time defrag95 takes over a working day here."""
        return pct_faster(self.day_defrag95, self.day_win95)


def _pair(case: Case, cache_blocks: int = CACHE_BLOCKS,
          readahead_kb: Optional[int] = None) -> Tuple[Measurement, Measurement]:
    aged = case.aged
    if readahead_kb is not None:
        spec = aged.drive.spec
        newspec = DriveSpec(**{**spec.__dict__, "readahead_kb": readahead_kb})
        aged = aged.copy()
        aged.drive = Drive(newspec)
    a = apply_layout("win95_full", aged, case.log)
    b = apply_layout("defrag95", aged, case.log)
    return measure(a, case.evals, cache_blocks), measure(b, case.evals, cache_blocks)


def session_row(case: Case) -> SensitivityRow:
    """One row measured with the cache and head carried across the whole day."""
    a = apply_layout("win95_full", case.aged, case.log)
    b = apply_layout("defrag95", case.aged, case.log)
    days_a = [run_session(a, w) for w in case.evals]
    days_b = [run_session(b, w) for w in case.evals]
    boot_a = [run_workload(a, w)["boot"].ms for w in case.evals]
    boot_b = [run_workload(b, w)["boot"].ms for w in case.evals]
    return SensitivityRow("Cache and head state", "shared across one session",
                          statistics.mean(boot_a), statistics.mean(boot_b),
                          statistics.mean(days_a), statistics.mean(days_b))


FILL_STATS: Dict[float, Dict[str, float]] = {}


def run_sensitivity(quick: bool = False) -> List[SensitivityRow]:
    """Re-run the whole experiment with one assumption changed at a time."""
    rows: List[SensitivityRow] = []

    def add(axis: str, setting: str, pair: Tuple[Measurement, Measurement]) -> None:
        a, b = pair
        rows.append(SensitivityRow(axis, setting,
                                   a.per_scenario["boot"][0], b.per_scenario["boot"][0],
                                   a.day[0], b.day[0]))

    for key in (["1996"] if quick else ["1994", "1996", "1998"]):
        add("Drive", DRIVES[key].name, _pair(make_case(drive_key=key)))
    if not quick:
        for fill in (0.50, 0.62, 0.75):
            c = make_case(target_fill=fill)
            FILL_STATS[fill] = c.aged.fragmentation_report()
            add("Volume fill after install", "%d%%" % int(fill * 100), _pair(c))
        base = make_case()
        for blocks, label in ((256, "1 MB"), (2048, "8 MB")):
            add("VCACHE size", label, _pair(base, cache_blocks=blocks))
        rows.append(session_row(base))
        for kb, label in ((0, "none"), (32, "32 KB"), (64, "64 KB"), (256, "256 KB")):
            add("Drive read-ahead buffer", label, _pair(base, readahead_kb=kb))
        for j, label in ((0.0, "identical every run"), (1.0, "default"),
                         (2.5, "2.5x more variable"), (5.0, "5x more variable")):
            add("Run-to-run workload variation", label, _pair(make_case(jitter=j)))
        for days in (1, 3, 14, 30):
            add("Monitoring period before layout", "%d day(s)" % days,
                _pair(make_case(monitor_days=days)))
    return rows


# --- rendering ----------------------------------------------------------------

def render(case: Case, main: Dict[str, object], after: Dict[str, "Durability"],
           ablation: List[Tuple[str, Measurement, Measurement]],
           sens: List[SensitivityRow], elapsed: float) -> str:
    """Turn the measurements into the published report."""
    measures: Dict[str, Measurement] = main["measures"]      # type: ignore
    frag: Dict[str, Dict[str, float]] = main["fragmentation"]  # type: ignore
    defrag: Dict[str, Tuple[float, int]] = main["defrag"]     # type: ignore
    base = measures["win95_full"]
    none = measures["none"]
    new = measures["defrag95"]
    L: List[str] = []
    w = L.append

    drive = case.aged.drive
    w("# defrag95 benchmark results")
    w("")
    w("Generated by `python3 -m sim.bench` in %.1f s. Every number below is "
      "reproducible from the seeds in `sim/bench.py`." % elapsed)
    w("")
    w("## The machine under test")
    w("")
    w("| | |")
    w("|---|---|")
    w("| Drive | %s |" % drive.spec.name)
    w("| Geometry | %d cylinders, %d heads, %d-%d sectors/track (zoned) |" % (
        drive.spec.cylinders, drive.spec.heads,
        drive.sectors_per_track(drive.spec.cylinders - 1), drive.sectors_per_track(0)))
    w("| Media rate (raw) | %.2f MB/s outer, %.2f MB/s inner |" % (
        drive.zone_rate_mb_s(0), drive.zone_rate_mb_s(drive.spec.cylinders - 1)))
    w("| Seek | %.1f ms track-to-track, %.1f ms average, %.1f ms full stroke |" % (
        drive.spec.track_to_track_ms, drive.spec.average_seek_ms, drive.full_stroke_ms()))
    w("| Rotation | %d RPM, %.2f ms average latency |" % (
        drive.spec.rpm, drive.avg_rotation_ms))
    w("| Volume | FAT16, %d clusters of %d KB, %.2f GB |" % (
        case.aged.cluster_count, case.aged.cluster_bytes // 1024,
        case.aged.capacity_bytes() / 1e9))
    w("| VCACHE | %d KB |" % (CACHE_BLOCKS * 4))
    w("| History | fresh install, then %d days of ordinary use |" % AGE_DAYS)
    w("")
    fresh = case.fresh_report
    aged = frag["none"]
    w("| Volume state | Files | Extents/file | Fragmented files | Free-space holes | Full |")
    w("|---|---:|---:|---:|---:|---:|")
    w("| After install | %d | %.2f | %.1f%% | %d | %.1f%% |" % (
        fresh["files"], fresh["extents_per_file"], fresh["pct_fragmented"],
        fresh["free_holes"], fresh["fill_pct"]))
    w("| After %d days of use | %d | %.2f | %.1f%% | %d | %.1f%% |" % (
        AGE_DAYS, aged["files"], aged["extents_per_file"], aged["pct_fragmented"],
        aged["free_holes"], aged["fill_pct"]))
    w("")

    w("## Headline")
    w("")
    boot_gain = pct_faster(new.per_scenario["boot"][0], base.per_scenario["boot"][0])
    day_gain = pct_faster(new.day[0], base.day[0])
    boot_vs_none = pct_faster(new.per_scenario["boot"][0], none.per_scenario["boot"][0])
    w95_vs_none = pct_faster(base.per_scenario["boot"][0], none.per_scenario["boot"][0])
    w("Against the defragmenter Windows 95 actually shipped, on the same volume, "
      "the same drive and a held-out workload:")
    w("")
    w("* **cold boot %.1f%% less disk time** (%.0f ms -> %.0f ms)" % (
        boot_gain, base.per_scenario["boot"][0], new.per_scenario["boot"][0]))
    w("* **a modelled working day %.1f%% less disk time** (%.1f s -> %.1f s)" % (
        day_gain, base.day[0] / 1000, new.day[0] / 1000))
    w("")
    w("For scale: the shipped defragmenter itself only bought %.1f%% on boot over "
      "not defragmenting at all, and defrag95 buys %.1f%%." % (w95_vs_none, boot_vs_none))
    w("")

    w("## Per scenario")
    w("")
    header = "| Scenario | " + " | ".join(LAYOUT_LABEL[n] for n in LAYOUT_ORDER) + \
             " | shipped vs no defrag | defrag95 vs no defrag | defrag95 vs shipped |"
    w(header)
    w("|" + "---|" * (len(LAYOUT_ORDER) + 4))
    for sc in SCENARIO_LABEL:
        cells = []
        for n in LAYOUT_ORDER:
            m, sd = measures[n].per_scenario[sc]
            cells.append("%.0f ms ±%.0f" % (m, sd))
        raw = none.per_scenario[sc][0]
        w("| %s | %s | %+.1f%% | %+.1f%% | **%+.1f%%** |" % (
            SCENARIO_LABEL[sc], " | ".join(cells),
            pct_faster(base.per_scenario[sc][0], raw),
            pct_faster(new.per_scenario[sc][0], raw),
            pct_faster(new.per_scenario[sc][0], base.per_scenario[sc][0])))
    cells = ["%.2f s ±%.2f" % (measures[n].day[0] / 1000, measures[n].day[1] / 1000)
             for n in LAYOUT_ORDER]
    w("| **Weighted working day** | %s | %+.1f%% | %+.1f%% | **%+.1f%%** |" % (
        " | ".join(cells), pct_faster(base.day[0], none.day[0]),
        pct_faster(new.day[0], none.day[0]), day_gain))
    w("")
    w("Mean ± population standard deviation over %d independent evaluation "
      "workloads, none of which the layout planner was allowed to see." % len(EVAL_SEEDS))
    w("")
    w("The two middle columns are the ones to read if the question is whether "
      "defragmenting was worth doing at all. Over a whole working day the "
      "shipped defragmenter is %.1f%% better than leaving the volume alone, "
      "because its boot and launch wins are largely cancelled by the paging "
      "storm, which it makes **%.1f%% worse**: it packs every file against the "
      "front of the volume but cannot move the in-use swap file, so it drags "
      "application code away from the %d-piece swap file it pages against. Run "
      "from DOS, where the swap file can be moved, that regression disappears." % (
        pct_faster(base.day[0], none.day[0]),
        -pct_faster(base.per_scenario["paging"][0], none.per_scenario["paging"][0]),
        len(case.aged.extents(case.aged.by_path["C:\\WINDOWS\\WIN386.SWP"]))))
    w("")

    w("## Where the boot time goes")
    w("")
    w("| Layout | Seek | Rotational latency | Transfer | Per-request overhead | Total |")
    w("|---|---:|---:|---:|---:|---:|")
    for n in LAYOUT_ORDER:
        b = measures[n].boot_breakdown
        w("| %s | %.0f ms | %.0f ms | %.0f ms | %.0f ms | %.0f ms |" % (
            LAYOUT_LABEL[n], b["seek"], b["rotation"], b["transfer"], b["overhead"],
            sum(b.values())))
    w("")
    w("The shipped defragmenter attacks fragmentation, which is mostly a *seek* "
      "problem. Ordering by observed access turns unrelated reads into "
      "near-sequential ones, which additionally collapses rotational latency, "
      "because the drive's own read-ahead buffer has already fetched the next "
      "file by the time it is asked for.")
    w("")

    w("## Does the layout survive use?")
    w("")
    w("Each layout is then put through another %d days of the same simulated "
      "use -- same seed, same operations, so every layout faces identical "
      "churn -- and then each defragmenter is run again." % DURABILITY_DAYS)
    w("")
    w("| Layout | Boot, fresh | Boot, +%dd | Decay | Extents/file, +%dd | Re-run moves | Boot after re-run |" % (
        DURABILITY_DAYS, DURABILITY_DAYS))
    w("|---|---:|---:|---:|---:|---:|---:|")
    for n in LAYOUT_ORDER:
        d = after[n]
        b0 = measures[n].per_scenario["boot"][0]
        b1 = d.after.per_scenario["boot"][0]
        moved_mb = d.rerun_clusters * case.aged.cluster_bytes / 1e6
        w("| %s | %.0f ms | %.0f ms | %+.1f%% | %.2f | %.0f MB | %.0f ms |" % (
            LAYOUT_LABEL[n], b0, b1, -pct_faster(b1, b0), d.report["extents_per_file"],
            moved_mb, d.rerun.per_scenario["boot"][0]))
    w("")
    d95, dw95 = after["defrag95"], after["win95_full"]
    w("defrag95's layout decays faster in relative terms, and that is the honest "
      "shape of the result: it starts from a much better place, and the same "
      "churn costs it more. In absolute terms it is still %.0f ms ahead of a "
      "freshly defragmented Win95 volume after %d days without maintenance "
      "(%.0f ms vs %.0f ms), and re-running it costs %.0f MB of movement "
      "against %.0f MB for a full Win95 pass." % (
        dw95.after.per_scenario["boot"][0] - d95.after.per_scenario["boot"][0],
        DURABILITY_DAYS,
        d95.after.per_scenario["boot"][0], dw95.after.per_scenario["boot"][0],
        d95.rerun_clusters * case.aged.cluster_bytes / 1e6,
        dw95.rerun_clusters * case.aged.cluster_bytes / 1e6))
    w("")
    w("The re-run is a maintenance pass: both defragmenters leave a file alone "
      "if it is still contiguous and still within %d MB of where the pass would "
      "put it, and move only what has drifted. The design's answer to decay is "
      "that pass running nightly on idle, not a cleverer one-shot -- which is "
      "what Windows 98 eventually shipped as a scheduled task." % (
        TOUCHUP_SLACK * case.aged.cluster_bytes // (1024 * 1024)))
    w("")

    w("## Cost of running the defragmenter")
    w("")
    w("| Layout | Clusters relocated | Data moved | Lower-bound run time |")
    w("|---|---:|---:|---:|")
    for n in LAYOUT_ORDER[1:]:
        ms, moved = defrag[n]
        w("| %s | %d | %.0f MB | %.1f min |" % (
            LAYOUT_LABEL[n], moved, moved * case.aged.cluster_bytes / 1e6, ms / 60000))
    w("")
    w("defrag95 moves slightly more data than a full Win95 pass on its first "
      "run, because it is not only removing fragmentation but relocating by "
      "use. That is a one-off overnight cost; the maintenance passes after it "
      "are far cheaper (see above).")
    w("")

    w("## Which part of the policy earns the win")
    w("")
    w("| Variant | Boot | vs shipped | Launch Word | Paging | Working day | Boot after %d days |"
      % DURABILITY_DAYS)
    w("|---|---:|---:|---:|---:|---:|---:|")
    for label, m, m_after in ablation:
        w("| %s | %.0f ms | %+.1f%% | %.0f ms | %.0f ms | %.2f s | %.0f ms |" % (
            label, m.per_scenario["boot"][0],
            pct_faster(m.per_scenario["boot"][0], base.per_scenario["boot"][0]),
            m.per_scenario["launch_word"][0], m.per_scenario["paging"][0],
            m.day[0] / 1000, m_after.per_scenario["boot"][0]))
    w("")

    w("## Sensitivity")
    w("")
    w("Does the result depend on the assumptions? Each row re-runs the whole "
      "experiment with one assumption changed.")
    w("")
    w("| Assumption | Setting | Boot: shipped | Boot: defrag95 | Gain | Working-day gain |")
    w("|---|---|---:|---:|---:|---:|")
    last_axis = None
    for r in sens:
        axis = r.axis if r.axis != last_axis else ""
        last_axis = r.axis
        w("| %s | %s | %.0f ms | %.0f ms | **%+.1f%%** | %+.1f%% |" % (
            axis, r.setting, r.boot_win95, r.boot_defrag95, r.boot_gain, r.day_gain))
    w("")
    hit = 0.0
    probe = apply_layout("defrag95", case.aged, case.log)
    from .engine import run_scenario
    r0 = run_scenario(probe, case.evals[0][0])
    hit = 100.0 * r0.cache_hits / max(1, r0.cache_hits + r0.cache_misses)
    caveat = ("Two of those rows deserve a caveat rather than a claim. "
              "**Volume fill** barely moves the boot number because both "
              "defragmenters put the boot set at the front of the volume "
              "whatever else is on it. ")
    if FILL_STATS:
        lo = FILL_STATS[min(FILL_STATS)]
        hi = FILL_STATS[max(FILL_STATS)]
        caveat += ("It does change the volume underneath -- %.0f%% versus "
                   "%.0f%% full after ageing, %.2f versus %.2f extents per "
                   "file -- it just does not change this measurement. " % (
                       lo["fill_pct"], hi["fill_pct"],
                       lo["extents_per_file"], hi["extents_per_file"]))
    caveat += ("**Cache size** barely moves anything because a cold boot reads "
               "mostly distinct blocks: the measured VCACHE hit rate during "
               "boot is %.1f%%. That is a consequence of measuring cold "
               "starts, which is the conservative choice here, not a claim "
               "that VCACHE did not matter in 1996." % hit)
    w(caveat)
    w("")
    gains = [r.boot_gain for r in sens]
    if gains:
        w("Boot gain across every configuration tested: %.1f%% to %.1f%% "
          "(median %.1f%%)." % (min(gains), max(gains), statistics.median(gains)))
    w("")
    w("---")
    w("")
    w("defrag95 - Keith Adler. Model, workload and analysis in `sim/`.")
    return "\n".join(L) + "\n"


# --- cluster map export for the Turbo Vision UI -------------------------------

CATEGORY = {
    "free": 0, "boot": 1, "app": 2, "swap": 3, "warm": 4,
    "churn": 5, "cold": 6, "fragmented": 7,
}


def classify(vol: Volume, log: AccessLog) -> Dict[int, str]:
    """Assign every file the category the cluster map colours it by."""
    boot = set(log.order.get("boot", []))
    appset = set()
    for sc, paths in log.order.items():
        if sc.startswith("launch_"):
            appset |= set(paths)
    warm = set(log.counts)
    out: Dict[int, str] = {}
    for fid, rec in vol.files.items():
        if rec.kind == "swap":
            cat = "swap"
        elif rec.path in boot:
            cat = "boot"
        elif rec.path in appset:
            cat = "app"
        elif rec.kind in ("temp", "cache"):
            cat = "churn"
        elif rec.path in warm:
            cat = "warm"
        else:
            cat = "cold"
        out[fid] = cat
    return out


def export_map(case: Case, volumes: Dict[str, Volume], measures: Dict[str, Measurement],
               path: str, cells: int = 4096) -> None:
    """Write the cluster map the Turbo Vision front end reads."""
    cats = classify(case.aged, case.log)
    payload = {
        "drive": case.aged.drive.spec.name,
        "clusters": case.aged.cluster_count,
        "cluster_kb": case.aged.cluster_bytes // 1024,
        "cells": cells,
        "legend": list(CATEGORY),
        "layouts": [],
    }
    per_cell = max(1, case.aged.cluster_count // cells)
    for name in LAYOUT_ORDER:
        vol = volumes[name]
        frag_files = {fid for fid in vol.files if vol.fragments(fid) > 1}
        row = []
        for i in range(cells):
            lo = i * per_cell
            hi = min(case.aged.cluster_count, lo + per_cell)
            counts: Dict[str, int] = {}
            for c in range(lo, hi):
                fid = vol.owner[c]
                if fid is None:
                    key = "free"
                elif fid in frag_files:
                    key = "fragmented"
                else:
                    key = cats.get(fid, "cold")
                counts[key] = counts.get(key, 0) + 1
            best = max(counts, key=lambda k: (counts[k], k != "free")) if counts else "free"
            row.append(CATEGORY[best])
        rep = vol.fragmentation_report()
        payload["layouts"].append({
            "name": name,
            "label": LAYOUT_LABEL[name],
            "cells": row,
            "boot_ms": measures[name].per_scenario["boot"][0],
            "word_ms": measures[name].per_scenario["launch_word"][0],
            "paging_ms": measures[name].per_scenario["paging"][0],
            "day_ms": measures[name].day[0],
            "extents_per_file": rep["extents_per_file"],
            "pct_fragmented": rep["pct_fragmented"],
            "free_holes": rep["free_holes"],
        })
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=1)


def write_csv(path: str, rows: Sequence[Dict[str, object]], columns: Sequence[str]) -> None:
    """Write one of the machine-readable result files."""
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(columns))
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the experiment and write everything in results/."""
    ap = argparse.ArgumentParser(description="defrag95 benchmark")
    ap.add_argument("--quick", action="store_true", help="skip the slow sensitivity sweep")
    ap.add_argument("--out", default=RESULTS)
    args = ap.parse_args(argv)

    t0 = time.time()
    os.makedirs(args.out, exist_ok=True)
    print("building the aged volume ...", flush=True)
    case = make_case()
    print("applying layouts and replaying %d held-out workloads ..." % len(EVAL_SEEDS),
          flush=True)
    main_res = run_main(case)
    volumes: Dict[str, Volume] = main_res["volumes"]        # type: ignore
    measures: Dict[str, Measurement] = main_res["measures"]  # type: ignore
    print("ageing each layout %d more days ..." % DURABILITY_DAYS, flush=True)
    after = run_durability(case, volumes)
    print("running the ablation ...", flush=True)
    ablation = run_ablation(case)
    print("running the sensitivity sweep ...", flush=True)
    sens = run_sensitivity(quick=args.quick)
    elapsed = time.time() - t0

    report = render(case, main_res, after, ablation, sens, elapsed)
    with open(os.path.join(args.out, "RESULTS.md"), "w") as fh:
        fh.write(report)

    rows = []
    for name in LAYOUT_ORDER:
        row: Dict[str, object] = {"layout": name}
        for sc in SCENARIO_LABEL:
            row[sc + "_ms"] = round(measures[name].per_scenario[sc][0], 1)
            row[sc + "_sd"] = round(measures[name].per_scenario[sc][1], 1)
        row["day_ms"] = round(measures[name].day[0], 1)
        row["boot_after_%dd_ms" % DURABILITY_DAYS] = round(
            after[name].after.per_scenario["boot"][0], 1)
        row["boot_after_rerun_ms"] = round(after[name].rerun.per_scenario["boot"][0], 1)
        row["rerun_mb_moved"] = round(
            after[name].rerun_clusters * case.aged.cluster_bytes / 1e6, 1)
        rows.append(row)
    write_csv(os.path.join(args.out, "summary.csv"), rows, list(rows[0]))
    write_csv(
        os.path.join(args.out, "sensitivity.csv"),
        [{"axis": r.axis, "setting": r.setting, "boot_win95_ms": round(r.boot_win95, 1),
          "boot_defrag95_ms": round(r.boot_defrag95, 1),
          "boot_gain_pct": round(r.boot_gain, 2),
          "day_gain_pct": round(r.day_gain, 2)} for r in sens],
        ["axis", "setting", "boot_win95_ms", "boot_defrag95_ms", "boot_gain_pct",
         "day_gain_pct"],
    )
    export_map(case, volumes, measures, os.path.join(args.out, "clustermap.json"))

    base = measures["win95_full"]
    new = measures["defrag95"]
    print()
    print("boot: %.0f ms -> %.0f ms  (%+.1f%%)" % (
        base.per_scenario["boot"][0], new.per_scenario["boot"][0],
        pct_faster(new.per_scenario["boot"][0], base.per_scenario["boot"][0])))
    print("day : %.2f s -> %.2f s  (%+.1f%%)" % (
        base.day[0] / 1000, new.day[0] / 1000, pct_faster(new.day[0], base.day[0])))
    print("wrote %s" % os.path.join(args.out, "RESULTS.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
