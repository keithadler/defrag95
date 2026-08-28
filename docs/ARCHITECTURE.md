# Architecture

How the pieces fit, and where to change things.

```
   sim/image.py            sim/workload.py
   builds a Win95          boot / launch / document / browse / paging traces,
   install, then ages      in two independent draws: one the planner may see,
   it a year               one it may not
        |                          |
        |  Volume (sim/filesystem.py)          AccessLog
        |  FAT16, 32 KB clusters, VFAT's       counts, mean first-touch rank,
        |  next-fit allocator, extents         bytes written
        |                          |
        +----------> sim/layouts.py <----------+
                     none / win95 x3 / defrag95
                                |
                          a new Volume
                                |
                     sim/engine.py    replays a trace against the volume
                                |
                     sim/drive.py     charges it: seek, rotation, transfer,
                                |     read-ahead, per-request overhead
                     sim/bench.py     the experiment, the ablation, the sweep
                                |
                     results/         RESULTS.md, CSVs, clustermap.json
                                |
                     ui/              Turbo Vision front end
```

The important separation is that **a layout policy only ever produces a
placement** — a map from file to cluster list — and `Volume.rebuild` applies
it. A policy cannot influence how it is measured, and every policy is measured
by the same code against the same traces.

## The modules

| Module | Responsibility |
|---|---|
| `sim/drive.py` | `Drive` (geometry, seek curve, zones) and `Arm` (head position, cost of a transfer). Nothing above this layer knows about milliseconds. |
| `sim/filesystem.py` | `Volume`: allocation, deletion, growth, extents, and turning a byte range into sector runs. |
| `sim/cache.py` | VCACHE as an LRU over 4 KB blocks. |
| `sim/image.py` | `build_image` writes an install; `age` replays a year of use through the allocator. |
| `sim/workload.py` | Traces, and `observe_period`, which is the only channel by which information reaches the planner. |
| `sim/layouts.py` | `pack` (shared placement machinery) plus one function per policy. |
| `sim/engine.py` | `run_scenario` replays a trace; `defrag_cost_ms` prices a reorganisation. |
| `sim/bench.py` | Builds the case, runs everything, renders the report. All seeds live here. |

## Extending it

### Add a drive

Append a `DriveSpec` to `sim/drive.py` and register it in `DRIVES`. Seek is
calibrated from `track_to_track_ms` and `average_seek_ms` alone, so those two
numbers plus the zone table and RPM are all you need. It will appear in the
sensitivity sweep automatically if you add its key to `run_sensitivity`.

### Add a layout policy

Write `layout_yours(vol, log) -> Volume`. Build an ordered list of file ids,
optionally a `{fid: gap_clusters}` dict, and hand both to `pack`; return
`vol.rebuild(placement)`. Register it in `LAYOUTS`, then add it to
`LAYOUT_ORDER` and `LAYOUT_LABEL` in `sim/bench.py` to have it measured and
reported. If your policy has regions whose internal order matters, pass
`region` and `ordered_regions` so the maintenance pass knows what "in the
right place" means for it.

### Add a workload

Write a function returning a `Scenario` and add it to `make_workload`. It will
be included in the monitor log, the per-scenario table and the weighted day
automatically. Give it an honest `per_day`: that weight is what decides how
much it moves the headline.

### Change what the planner is allowed to know

`observe` in `sim/workload.py` is deliberately the only path from workload to
planner. If you widen it, say so loudly — that is the assumption the whole
result depends on.

## Invariants worth preserving

* A placement covers every file, allocates each exactly the clusters it needs,
  and overlaps nothing. `Volume.rebuild` enforces all three.
* Every layout reads the same bytes for the same trace.
  `tests/test_claims.py` asserts it.
* Nothing in `sim/` uses wall-clock time, randomness without a seed, or the
  filesystem outside `results/`. The benchmark is reproducible byte for byte.
