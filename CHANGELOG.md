# Changelog

## 1.0.0 — 2026-08-27

First public release.

### The claim

On a simulated 1996 EIDE drive with an aged FAT16 volume, laying the disk out
by observed use rather than by directory order costs **33.8% less disk time on
a cold boot** than the defragmenter Windows 95 shipped, and **24.5% less over a
modelled working day**. Measured against leaving the volume alone, defrag95 is
46.3% and 26.1% better; the shipped defragmenter is 19.0% and 2.0%.

Across every drive, fill level and modelling assumption in the sensitivity
sweep the boot gain ranges from 14.2% to 42.2%, median 33.8%.

### What is in it

* `sim/` — a drive model (seek curve, rotational latency, zoned transfer,
  on-drive read-ahead), a FAT16 volume with VFAT's next-fit allocator, a
  generated Windows 95 install aged by a year of use, seven workload traces,
  five layout policies and the benchmark harness.
* `ui/` — a Turbo Vision front end showing the cluster map for each policy,
  with animated full and maintenance passes.
* `tests/` — 42 checks, including the fairness controls: held-out evaluation
  workloads, identical bytes read across every layout, and a random-order
  control layout that performs like the shipped defragmenter rather than like
  defrag95.
* `docs/` — the design as it would have been built in 1995, and a methodology
  document that is candid about the model's limits.

### Notable findings that were not the point

* The shipped defragmenter makes the paging workload **20.7% worse** than not
  defragmenting, because it packs every file against the front of the volume
  and cannot move the in-use swap file.
* Ordering does nearly all the work: with access ordering ablated, the gain
  over the shipped defragmenter falls from 33.8% to 2.6%.
* An early version of the planner that treated files its monitor had not seen
  as cold was *slower than not defragmenting at all*.
