# Building the real benchmark machine

How to rebuild the real FAT16 volume, capture traces from it, and score
layouts against them. Everything here uses freely redistributable software.

## Requirements

```bash
brew install qemu mtools dosfstools     # or your platform's equivalent
```

## 1. A real FAT16 volume

```bash
qemu-img create -f raw hdd.img 1600M
```

Boot the [FreeDOS 1.4 LiveCD](https://www.freedos.org/download/) in the live
environment and partition it:

```
fdisk /auto
```

FDISK will mark the partition FAT32 on a disk this size. A period machine with
a 1.6 GB disk ran FAT16 with 32 KB clusters, so set the type byte and format
from the host, which also gives exact control over cluster size:

```bash
python3 -c "f=open('hdd.img','r+b'); f.seek(450); f.write(bytes([0x06]))"
mformat -i hdd.img@@32256 -c 64 -T 3273921 -h 255 -s 63 -H 63 -v DEFRAG95 ::
```

`-c 64` is 64 sectors per cluster: 32 KB, which is what FAT16 uses on a
1-2 GB partition. Boot the LiveCD again and run `sys c:` to write the boot
sector and system files.

## 2. Real files

Extract the FreeDOS packages and copy them in:

```bash
for z in packages/**/*.zip; do unzip -oq "$z" -d staging/$(basename $z .zip); done
mcopy -s -b -o -i hdd.img@@32256 staging/FDOS staging/APPS staging/GAMES ::/
```

Fill the volume to about 90%. This matters more than it looks: at 39% full,
almost nothing fragments, because there is always contiguous room.

## 3. Real fragmentation

Generate a batch file of real DOS file operations -- copies, deletions,
appends, and an "update" pattern that deletes and rewrites shared files -- and
let DOS execute it. The allocator doing the work is the real one.

## 4. Real traces

QEMU records every block the guest asks for:

```bash
qemu-system-i386 -M pc -m 16 -hda hdd-aged.img -fda floppy-TRAIN.img -boot c \
  -snapshot -display none \
  -trace enable=blk_co_preadv -trace enable=blk_co_pwritev -D trace-train.log
```

`-snapshot` sends all writes to a temporary overlay, so the image is identical
for every run. The workload comes from a floppy so that switching workloads
never changes the hard disk. `AUTOEXEC.BAT` calls `A:\RUN.BAT` and brackets it
with reads of marker files, which is how phases are found in the trace later.

Two runs are needed: one to train the layout planner, one held out to score it.

## 5. Scoring

```bash
python3 -m real.bench --image hdd-aged.img \
    --train trace-train.log --eval trace-eval.log
```

Each traced byte offset is mapped back through the filesystem to (file,
offset), then forward into whatever layout is being scored, then charged to the
drive model. The harness asserts that every layout is charged for an identical
number of sectors.

## Traps worth knowing about

* **An emulator has no platter.** QEMU completes I/O at host speed with no seek
  or rotational latency, so timing a boot inside it with a stopwatch reports
  that layout does not matter. Replay the trace against a drive model instead.
* **Programs do surprising things.** `DU.EXE /?` ignores the switch and walks
  the entire filesystem; it produced 94% of the first trace captured here.
  Another utility crashed the machine with an invalid opcode. Curate the
  workload and check what dominates the trace before trusting it.
* **Markers must be unique.** The end-of-run marker is detected by its byte
  offset in the trace. Verify that offset on the image the run will actually
  use, not on an earlier copy.
* **Fill the disk before churning**, or the allocator will never have to
  fragment anything.
