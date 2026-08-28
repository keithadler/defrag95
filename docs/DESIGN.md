# defrag95: the design

How this would have been built in 1995, on the hardware it is about.

## The observation

Windows 95's Disk Defragmenter offered three operations: full defragmentation
of files and free space, files only, and free-space consolidation only. The
full pass walks the directory tree, makes each file contiguous, and packs the
files against the front of the volume in the order that walk meets them.

That ordering carries no information about use. C:\WINDOWS\SYSTEM held several
hundred files on a typical install; a boot touched perhaps a third of them,
and they were scattered through the rest in whatever order setup wrote them.
After a full defragmentation the boot set is unfragmented and still spread over
hundreds of megabytes. The head still crosses the platter to assemble it.

Two things follow. The first is that the remaining cost is dominated by seeks
and rotations rather than transfer — the measurement bears this out: a full
Win95 pass removes 710 ms of seek from the modelled boot and leaves 1,063 ms
of rotational latency exactly where it was. The second is that this is fixable
with information the machine already has.

## The passes

**Pass 0 — monitor.** A small resident driver records, per file: an access
count, the first-touch position within each recognised activity (boot,
application launch), and bytes written. Windows 98 shipped this idea as Task
Monitor, writing `.LGC` logs used to accelerate application launches, so the
feasibility question is settled by history rather than by argument. Fourteen
days of logging is enough; the sensitivity sweep shows one day already gets
most of the benefit.

**Pass 1 — analyse.** Read the FAT and the directory tree, build the cluster
allocation bitmap, and join it to the monitor's log. Classify every file into
a region:

| Region | Contents | Placement |
|---|---|---|
| 0 | boot set | outermost cylinders, in first-touch order |
| 1 | application launch sets | next, hottest application first, each in launch order |
| 2 | paging file | contiguous, adjacent to region 1, with 8 MB of headroom |
| 3 | warm files | grouped by directory, hottest directory first |
| 4 | unobserved files in otherwise-hot directories | beside their siblings |
| 5 | temp and cache directories | one arena, sized at 1.6x current use |
| 6 | never touched | innermost cylinders |

Region 4 matters more than it looks. A monitor's picture is always incomplete,
and a file it happened not to see is not cold — it is unknown. Exiling unknown
files to the slow edge of the disk was the first version of this design and it
was *slower than doing nothing*, because held-out boots kept reaching for files
that had been moved 40,000 clusters away. Directory affinity fixes it.

**Pass 2 — place.** Walk the regions outward-in, assigning each file a
contiguous run, with growth headroom after files the monitor saw being
rewritten. Reservations are scaled down automatically on a volume with little
free space.

**Pass 3 — move.** Standard cluster shuffling through a scratch region, FAT
updated after each move so an interrupted run leaves a consistent volume.
Nothing here is different from what the shipped tool did; the difference is
entirely in where things are told to go.

**Pass 4 — maintain.** The pass that runs nightly. Recompute the ideal
placement, then leave a file alone if it is still in one piece and still where
it belongs — exactly, for the ordered regions; anywhere inside the right
region, for the rest. Only the drift moves. On the test volume, restoring the
layout after 90 days costs 204 MB of movement against 1,077 MB for a full pass.

## Cost on 1995 hardware

The analysis is one pass over the FAT plus a sort. On a 1.6 GB volume with
32 KB clusters there are about 49,000 clusters and a few thousand files:

| Structure | Size |
|---|---|
| cluster allocation bitmap | 6 KB |
| FAT16 image | 96 KB |
| per-file record (order, region, size, first cluster) | ~16 bytes x 5,000 = 80 KB |
| monitor log, 14 days | ~64 KB on disk |

Under 200 KB of working set, well inside what a 486 with 8 MB could give a
disk utility, and the sort is over a few thousand records. The expensive part
is the movement, which is I/O-bound and identical in kind to what Defrag
already did. The first full pass moves slightly more data than a Win95 full
pass — it is relocating by use, not only removing fragmentation — and that is
a one-off overnight cost.

## Why the ordering pays

Three mechanisms, in the order of how much they contribute:

1. **The drive's read-ahead buffer.** 1996 IDE drives carried segmented
   buffers of 128-256 KB and read ahead after every request. If the next file
   the boot asks for is the next file on the platter, it is already in the
   buffer: no seek, no rotation. This is why laying files down in read order
   beats merely putting them near each other, and why the gain scales with
   buffer size in the sensitivity table (14% with read-ahead disabled, 37%
   with a 256 KB buffer).
2. **Seek distance.** On the aged volume the boot set is spread across a
   1,605 MB span. A Win95 full pass compacts it to 59 MB, which is most of
   what removing fragmentation can do. Ordering by use compacts it to 20 MB,
   and takes the boot's seek time from 1,381 ms to 109 ms.
3. **Zoned recording.** The outer cylinders stream about 1.7x faster. This is
   the smallest of the three, and a Win95 full pass already gets much of it by
   packing everything forwards.

## What would have made it better still

* **Allocation hints.** The layout decays because VFAT keeps handing out the
  next free cluster with no idea that a file belongs in a region. A filesystem
  driver hooked into allocation would keep new files in the right neighbourhood
  and largely remove the need for the nightly pass.
* **Ordering within a file.** This design places whole files. A boot loads
  parts of files, and ordering by section would tighten the stream further.
* **Directory-entry placement.** Directory clusters are treated as ordinary
  data here; putting a directory next to the files it names would save the
  head another trip.
