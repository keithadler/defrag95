# defrag95 - Keith Adler

PYTHON ?= python3

.PHONY: bench quick test ui run clean

bench:            ## run the full benchmark and regenerate results/
	$(PYTHON) -m sim.bench

quick:            ## the same, without the sensitivity sweep
	$(PYTHON) -m sim.bench --quick

test:             ## check the model behaves the way the write-up claims
	$(PYTHON) -m unittest discover -s tests -v

ui:               ## build the Turbo Vision front end
	$(MAKE) -C ui

run: ui           ## build it and look at the results
	ui/defrag95 results/clustermap.json

clean:
	rm -f results/RESULTS.md results/summary.csv results/sensitivity.csv \
	      results/clustermap.json
	$(MAKE) -C ui clean
