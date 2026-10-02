# Busy-wait experiment

In the pilot (results/2026-10-01_203846), six BEAM servers used up to 3-4 times more CPU from one run
to the next for the same work (container energy CV up to 56%), while the whole machine's energy varied
by less than 3%. Hypothesis: BEAM scheduler busy-waiting (schedulers spin on the CPU for a while when they
run out of work, instead of sleeping), which differs from run to run at this load.

Test: the same servers with busy-waiting switched off (`+sbwt none +sbwtdcpu none +sbwtdio none`,
passed through `ERL_FLAGS`). The rebar3 release template (`vm.args`) itself suggests `+sbwt none` when
running in a container.

```bash
bash experiments/busy-wait/build.sh                              # the -nobw images (originals unchanged)
make run CONFIG=experiments/busy-wait/busywait.config            # 3 servers x 2 variants x 2 levels x 5 = 60 runs
```
