#!/usr/bin/env python3
"""The real benchmark.

Takes a real FAT16 image, a real block trace recorded while a real DOS
machine did real work, and scores the layout policies against it. Nothing here
invents a file, a fragment, or an access; the only modelled quantity is what a
1996 platter would have charged for the resulting seek pattern, and that model
is applied identically to every layout from the identical trace.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from real.fat16 import Fat16
from real import layouts as L
from real import replay as R
from real import trace as T

MARKERS = {"MARK1.TXT", "MARK2.TXT", "MARK3.TXT", "MARK4.TXT"}


def touch_order(logical: Sequence[T.LogicalAccess]) -> List[str]:
    """First-touch order, which is all the layout planner is allowed to know."""
    seen = set()
    out: List[str] = []
    for a in logical:
        if a.kind == "file" and a.path and a.path not in MARKERS and a.path not in seen:
            seen.add(a.path)
            out.append(a.path)
    return out


def placement_from_image(image: str, offset: int, reference: Fat16) -> Dict[str, List[int]]:
    """Read a layout back out of an image some other defragmenter produced."""
    other = Fat16(image, offset)
    out: Dict[str, List[int]] = {}
    missing = 0
    for path, e in reference.entries.items():
        got = other.entries.get(path)
        if got is None or len(got.chain) != len(e.chain):
            missing += 1
            out[path] = list(e.chain)          # fall back to where it was
        else:
            out[path] = list(got.chain)
    if missing:
        print("  note: %d entries did not match between images" % missing)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="defrag95 real benchmark")
    ap.add_argument("--image", required=True, help="the aged volume the traces were taken on")
    ap.add_argument("--offset", type=int, default=32256)
    ap.add_argument("--train", required=True, help="QEMU trace of the training run")
    ap.add_argument("--eval", required=True, help="QEMU trace of the held-out run")
    ap.add_argument("--defragged", help="image produced by a real defragmenter")
    ap.add_argument("--out", default="results/REAL-RESULTS.md")
    args = ap.parse_args(argv)

    fs = Fat16(args.image, args.offset)
    report = fs.fragmentation_report()
    print("volume: %d clusters of %d KB, %d files, %.1f%% full"
          % (fs.cluster_count, fs.cluster_bytes // 1024, report["files"], report["fill_pct"]))
    print("        %.2f extents/file, %.1f%% of multi-cluster files fragmented, %d free holes"
          % (report["extents_per_file"], report["pct_fragmented"], report["free_holes"]))

    train_raw = T.parse(args.train)
    train_log, _ = T.to_logical(fs, train_raw, args.offset)
    order = touch_order(train_log)
    print("training run: %d accesses, %d distinct files observed" % (len(train_log), len(order)))

    eval_raw = T.parse(args.eval)
    eval_log, stats = T.to_logical(fs, eval_raw, args.offset)
    eval_reads = [a for a in eval_log if not a.write]
    print("held-out run: %d accesses, %.1f MB read"
          % (len(eval_log), sum(a.sectors for a in eval_reads) * 512 / 1e6))
    overlap = len(set(order) & set(a.path for a in eval_log if a.kind == "file"))
    print("             %d of its files were also seen during training" % overlap)

    placements: Dict[str, Dict[str, List[int]]] = {
        "none": L.current(fs),
        "directory order": L.directory_order(fs),
        "defrag95": L.use_order(fs, order),
    }
    if args.defragged and os.path.exists(args.defragged):
        placements["FreeDOS defrag"] = placement_from_image(args.defragged, args.offset, fs)

    drive = R.drive_for(args.offset // 512 + fs.total_sectors)
    print("drive model: %s, %d cylinders\n" % (drive.spec.name, drive.spec.cylinders))

    order_names = ["none", "FreeDOS defrag", "directory order", "defrag95"]
    results = {}
    for name in order_names:
        if name not in placements:
            continue
        results[name] = R.score(fs, placements[name], eval_log, drive)

    sectors = {r.sectors for r in results.values()}
    print("every layout read %s sectors -- %s\n"
          % (sectors, "identical" if len(sectors) == 1 else "MISMATCH"))

    base = results.get("FreeDOS defrag") or results.get("directory order")
    lines = []
    w = lines.append
    w("| Layout | Disk time | Seek | Rotation | Transfer | vs untouched | vs conventional |")
    w("|---|---:|---:|---:|---:|---:|---:|")
    for name in order_names:
        r = results.get(name)
        if r is None:
            continue
        vs_none = (results["none"].ms - r.ms) / results["none"].ms * 100
        vs_base = (base.ms - r.ms) / base.ms * 100 if base else 0.0
        w("| %s | %.0f ms | %.0f | %.0f | %.0f | %+.1f%% | %+.1f%% |"
          % (name, r.ms, r.seek_ms, r.rotation_ms, r.transfer_ms, vs_none, vs_base))
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
