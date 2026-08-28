# The real benchmark

The rest of this repository is a simulation. This part is not.

A real FreeDOS machine, running under QEMU, boots from a **real FAT16 volume**
holding 8,435 real files. Real programs run, real files are read, and QEMU
records **every block the guest actually asks the disk for**. Those traces are
then replayed against different layouts of that same real volume.

## What is real here, and what is still modelled

| | |
|---|---|
| The filesystem | **Real.** FAT16, 51,148 clusters of 32 KB on a 1.68 GB disk, formatted and populated on the host, mounted and used by a real DOS. |
| The files | **Real.** 8,435 files from the FreeDOS 1.4 distribution: real executables, libraries, documentation, FreeDOOM WADs. |
| The fragmentation | **Real.** Produced by 1,877 real file operations -- copies, deletions, appends, an "update" pattern that deletes and rewrites shared files -- executed by DOS itself on a 92%-full volume. |
| The access trace | **Real.** 9,000-269,000 block requests per run, captured with QEMU's `blk_co_preadv` tracepoint: exact byte offsets, exact lengths, in the order the guest issued them. |
| The layouts | **Real.** Computed against actual cluster chains parsed from the actual image. Verified: zero fragmented files, zero gaps, all 47,467 clusters placed, none lost or overlapping. |
| The drive timing | **Modelled.** No 1996 platter exists here. The same model is applied identically to every layout from the identical trace, so it cannot favour one. |

Emulators complete disk I/O at host speed with no seek and no rotational
latency. Timing a boot with a stopwatch inside QEMU would report that disk
layout does not matter, at any speed multiplier -- not because it doesn't, but
because the instrument has no opinion. Trace-driven replay is the standard way
around that, and it is what is done here.

## The volume

| | After install | After 1,877 real file operations |
|---|---:|---:|
| Files | 7,728 | 8,435 |
| Extents per file | 0.998 | 1.023 |
| Fragmented (of multi-cluster files) | 0.0% | 10.1% |
| Full | 38.9% | 92.8% |

**First finding, and it goes against the simulation.** The simulated volume
reached 1.34 extents per file and 18.3% fragmented after a modelled year. A
real allocator, given real churn, produced 1.02 and 10.1%. Real FAT16 volumes
in this experiment fragment *less* readily than the model assumed -- and they
only fragment at all once the volume is nearly full. At 39% full, 918 file
operations produced 4% fragmentation and just three free-space holes, because
there was always contiguous room to be had.

## Results

Three independent pairs of runs. In each pair, one run trains the layout
planner and a second, held-out run scores it. Every layout is charged for
byte-identical work: the sector totals match exactly, which the harness
asserts.

| Workload pair | No defrag | Conventional defrag | defrag95 | defrag95 vs conventional |
|---|---:|---:|---:|---:|
| A. Independent draws (247/540 files shared) | 13,451 ms | 13,439 ms | 12,047 ms | **+10.4%** |
| B. A daily routine, order shuffled (80% shared) | 24,139 ms | 24,109 ms | 21,740 ms | **+9.8%** |
| C. A daily routine, order stable (82% similar) | 12,885 ms | 12,869 ms | 10,838 ms | **+15.8%** |

**Second finding: the conventional defragmenter is worth +0.1%.** Not 2%, as
the simulation had it -- essentially nothing, in all three pairs. Making every
file contiguous and packing it against the front of a real volume changed the
disk time of a real workload by a tenth of a percent. On this volume only 10%
of multi-cluster files were fragmented in the first place, so there was very
little fragmentation for it to remove, and removing it did not move the number.

**Third finding: ordering by observed use is worth 10-16% -- real, repeatable,
and about half what the simulation claimed.** The simulation predicted 34% on a
cold boot. The real measurement is 15.8% under the most favourable realistic
conditions and 9.8% when the machine's habits are less regular.

## Why the real number is lower

The mechanism the design rests on is confirmed, and so is its sensitivity:

* Pair B and pair C use the *same files* with the *same 80% overlap*. The only
  difference is that B reads them in a shuffled order each run and C reads them
  in a near-stable order, as a machine with unchanging hardware and habits
  does. That single change takes the gain from 9.8% to 15.8%. Ordering by use
  is worth what the repeatability of use is worth -- exactly the relationship
  the simulation's sensitivity sweep predicted, at about half the magnitude.
* The real workload is more transfer-bound than a Windows 95 boot. It reads
  whole files end to end; a real boot reads *parts* of many more files, which
  is more seek-intensive per byte and leaves more for a layout policy to win.

## A hypothesis that turned out to be wrong

The head spends much of the trace bouncing between the data area and the FAT at
the front of the volume: 1,453 metadata accesses cause **567 data-to-metadata
switches**, each a full-width seek that no file layout can prevent. Windows 95
cached the FAT in RAM (VCACHE); this FreeDOS machine has no disk cache, so I
expected that traffic to be capping the gain.

It is not. Removing every metadata access from the trace and re-scoring leaves
the result unchanged at 9.9%. The cap is elsewhere, and the honest position is
that I have not fully explained the gap between 34% simulated and 16% measured.

## What I could not do

* **Run the period defragmenter.** `DEFRAG.EXE` ships with FreeDOS and its own
  help calls it "a clone of MSDOS defrag" -- the tool lineage Windows 95's
  defragmenter came from, and the baseline I wanted. It will not run on this
  volume: it reports "1 block = 0 clusters", sits at 0%, and ignores input.
  The conventional baseline here is therefore still my own implementation of
  directory-order packing, though it is applied to the real volume and verified
  to produce what such a tool produces: every file contiguous, free space
  consolidated into a single run.
* **Use Windows 95 itself.** Still copyrighted; abandonware sites are not a
  licence. FreeDOS is a real DOS on real FAT16, but it is not Windows 95, and
  its boot reads far less than a Windows 95 boot did.

## Reproducing

```bash
python3 -m real.bench --image hdd-aged.img \
    --train trace-train4.log --eval trace-eval4.log
```

`real/fat16.py` parses the volume, `real/trace.py` maps QEMU's byte offsets
onto files, `real/layouts.py` computes placements, `real/replay.py` charges
them to the drive model. Building the machine itself is documented in
[docs/REAL-BENCHMARK.md](../docs/REAL-BENCHMARK.md).

## What this does to the headline

The simulation's direction survives contact with a real filesystem and a real
trace: use-ordering beats directory-ordering, and directory-ordering is worth
almost nothing. Its magnitude does not. **34% was roughly twice too
optimistic**; the measured figure is 10-16%. The simulated numbers elsewhere in
this repository have not been changed -- they are what that model says -- but
this is the number I would stand behind.
