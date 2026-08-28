# defrag95 UI

A Turbo Vision front end for the cluster maps and timings the simulator
produces, by Keith Adler.

It reads `results/clustermap.json` — written by `python3 -m sim.bench` — and
shows, for each layout policy, where every file ended up on the platter and
what that cost. Tab cycles the layouts, F9 animates a full pass, F10 a
maintenance pass.

## Building

```bash
make            # fetches magiblot/tvision into third_party/ and builds it
../ui/defrag95  # run from the repository root so it finds results/
```

Needs a C++17 compiler and ncurses. There is no dependency on CMake: the
Makefile compiles Turbo Vision itself. If you would rather use CMake,
`CMakeLists.txt` does the same job with `FetchContent`.

## Why Turbo Vision

Because the subject is a 1995 disk utility, and because
[magiblot's fork](https://github.com/magiblot/tvision) of Borland's library is
open source and has been brought forward to true colour, Unicode and a modern
terminal — so the thing can look like what it is about without being
unpleasant to use.
