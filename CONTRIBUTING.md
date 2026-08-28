# Contributing

The most useful contributions to this project are attacks on it.

It is a simulation making a quantitative claim, so the interesting question is
never "does the code run" but "is the number real". If you can find a modelling
assumption that flatters defrag95, an unfair comparison, or a workload that is
convenient rather than representative, that is worth more than a feature.

## Things that would genuinely help

* **A better drive model.** The seek curve, zone table and read-ahead
  behaviour are built from representative period figures, not from a specific
  drive. Measurements from real hardware of the era, or from a datasheet you
  have, would improve the foundation everything else rests on.
* **A more faithful Windows 95 defragmenter.** `layout_win95_full` is my
  reading of what the shipped tool did. If you know it did something
  different — in what order it packed, what it refused to move, how it treated
  directories — that changes the baseline and therefore the headline.
* **A harder workload.** The traces are synthetic. A real boot trace, or an
  argument that the modelled one is unrepresentative, is very welcome.
* **An unfairness you can demonstrate.** Ideally as a failing test.

## Ground rules for changes that move the numbers

1. **Run the benchmark before and after** and put both numbers in the pull
   request. `make bench` is deterministic and takes about fifteen seconds.
2. **If a change makes defrag95 look better, be twice as suspicious of it.**
   Say in the pull request why it is not just tuning against the benchmark.
3. **The planner may not see the evaluation workload.** Anything that leaks
   held-out information into layout decisions is a bug, however good the
   resulting number looks.
4. **Keep `make test` passing.** The tests in `tests/test_claims.py` exist
   specifically to catch the ways this benchmark could start lying; if your
   change requires weakening one, explain why in the pull request rather than
   quietly loosening the bound.

## Style

* Python 3.9, standard library only, no third-party dependencies in `sim/`.
* The UI is C++17 against Turbo Vision and nothing else.
* Comments explain *why*, on the assumption the reader can see what the code
  does. Where a decision has an alternative, say what it was and why it lost.
* Every public function gets a docstring.

## Running things

```bash
make bench    # full benchmark, regenerates results/, ~15 s
make quick    # skips the sensitivity sweep
make test     # 42 checks
make ui       # builds Turbo Vision and the front end
```
