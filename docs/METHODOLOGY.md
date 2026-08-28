# Methodology

What is modelled, what the numbers rest on, and where the model is wrong.

## The drive

Three costs are modelled separately, because a layout policy trades them
against each other.

**Seek.** `t(d) = a + b*sqrt(d)`, calibrated to the two figures a period spec
sheet published: track-to-track, and "average", which the industry defined as
a one-third-stroke seek. Full-stroke time is then a prediction of the curve,
not an input — for the 1996 drive it comes out at 18.8 ms, in the right
neighbourhood for the era.

**Rotation.** Half a revolution on average. Charged in full when a request
neither continues the previous one nor falls inside the drive's read-ahead
buffer; not charged at all when it does.

**Transfer.** Zoned: sectors per track falls from the outer bands to the inner
ones, so the media rate falls with it. A head switch is charged per track
boundary crossed. Sustained sequential throughput therefore comes out about
12% below the raw media rate, which is roughly the gap those two figures had
in period specifications.

**Read-ahead.** After each request the drive is assumed to keep reading
forward into its buffer. A following request that starts inside that window
costs neither a seek nor a rotation. This mechanism matters a great deal to
the result, so it is a sensitivity axis: the headline gain runs from 14% with
read-ahead disabled entirely to 37% with a 256 KB buffer.

Three drives are modelled — 1994, 1996 and 1998, with the 1996 one as the
default. The parameters are representative of consumer IDE drives of each
year. They are not measurements of a specific drive, and nothing here should
be read as a claim about one.

| | 1994 | 1996 | 1998 |
|---|---|---|---|
| Capacity | 540 MB | 1.6 GB | 4.0 GB |
| Spindle | 3,600 RPM | 5,400 RPM | 5,400 RPM |
| Track-to-track / average seek | 4.0 / 14.0 ms | 3.0 / 12.0 ms | 2.5 / 10.5 ms |
| Read-ahead buffer | 32 KB | 64 KB | 128 KB |
| Per-request driver overhead | 0.30 ms | 0.25 ms | 0.18 ms |

## The volume

FAT16, 32 KB clusters, which is what a 1-2 GB partition got. The allocator
reproduces VFAT's behaviour: the next free cluster at or after the last one
allocated, wrapping at the end. First-fit is implemented too, and a test
demonstrates the two differ, because that allocator is the mechanism that
fragments the volume in the first place.

On the 1998 drive the partition is capped at 2 GB, since that is where FAT16
with 32 KB clusters stops.

## The machine

A generated Windows 95 install: the named system files that matter at close to
their real sizes (VMM32.VXD, SYSTEM.DAT, SHELL32.DLL, KERNEL32.DLL, WINWORD.EXE,
EXCEL.EXE, NETSCAPE.EXE and so on), plus procedurally generated bulk — 450 files
in SYSTEM, fonts, help files, clip art, documents, a browser cache, games and
archives. About 1,900 files and 62% full after install.

Then a year of use is replayed through the allocator: browser cache churn,
document edits and saves, temp files, an install and an uninstall every six
days, swap-file growth and reboot re-creation, and a service-pack-style event
that rewrites 3-8% of the shared system files every month. That last one is
what actually scatters a real machine, and without it the model is far too
tidy. After 365 days: 4,700 files, 80% full, 1.34 extents per file, 18% of
multi-cluster files fragmented, 1,900 separate free-space holes, and a swap
file in 197 pieces.

## The workload

Seven traces: cold boot, three application launches, a document session, a
browsing session, and a paging storm. Reads are modelled at byte granularity
and mapped to sectors, so reading 4 KB out of a 32 KB cluster costs 8 sectors
rather than 64.

A machine with unchanging hardware loads a near-identical driver set in a
near-identical order on every boot, and that repeatability is the premise of
the whole design — so it is modelled explicitly, and so is its absence. Each
run drops about 4% of the fixed set, adds a few files that were not in it, and
swaps the order of about 6% of adjacent loads. How much this matters is a
sensitivity axis: with perfectly identical runs the gain is 41%, with five
times the variation it is 16%.

**What the planner is allowed to see.** A 14-day monitor log: access counts,
mean first-touch rank per activity, and bytes written. Nothing else. Every
number in the results is measured on five evaluation workloads drawn
independently of those 14 days, and a test asserts that no evaluation trace
matches any training trace.

## Fairness controls

* One aged volume, built once, handed to every layout policy.
* Every layout is charged for the same bytes off the platter — asserted by a
  test, and it holds exactly.
* The Windows 95 defragmenter is modelled in three modes, including the
  DOS-mode run in which it can relocate the swap file.
* Both defragmenters get the same maintenance-pass machinery, with the same
  tolerance.
* A control policy that packs every file contiguously in random order is
  measured too. It performs like Windows 95's, which is what tells us the
  ordering rather than the packing is doing the work.
* Five evaluation draws, reported as mean and standard deviation.

## Known limitations

* **Writes do not allocate during evaluation.** Document saves and page-outs
  overwrite existing clusters. Allocation behaviour is covered separately, by
  the ageing model and the durability test, but a single trace that both reads
  and grows files would be more faithful.
* **VCACHE is nearly inert here.** Measured hit rate during a cold boot is
  0.7%, so cache size barely registers. Cold starts are the conservative thing
  to measure, but it does mean this study says nothing about how caching and
  layout interact during a long session.
* **Whole-file placement.** Real loads read parts of files; ordering by
  section would be better and is not modelled.
* **The install is synthetic.** File sizes come from log-normal distributions
  fitted by eye to what these directories looked like, not from an inventory of
  a real disk.
* **Directory clusters are ordinary data.** Directory-entry locality is not
  modelled, which if anything understates what a use-ordered layout could do.
* **The defragmenter's own run time is a lower bound**: one read and one write
  per relocated cluster, no scratch-area shuffling, no FAT rewrites.

## Reproducing

Every seed is fixed in `sim/bench.py`. `make bench` regenerates
`results/RESULTS.md`, the CSVs and the cluster map in about 15 seconds, and
produces identical numbers on every run. `make test` checks the model against
42 assertions, including the fairness controls above.
