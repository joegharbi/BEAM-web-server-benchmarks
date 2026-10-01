# Advanced outlier methods (documented, not implemented)

Status: **not implemented, and not needed for now.** The framework currently uses
two simple, robust outlier rules, IQR and Hampel, in `tools/aggregate_repeats.py`.
They are enough for our case (one quantity, energy, with a handful of repeats per
configuration). This file records the heavier options so we can add them later if
the simple rules ever prove insufficient. They would add a machine-learning
dependency (scikit-learn) and more moving parts, so we keep them parked.

## What we use now

- **IQR** drops runs outside the middle-half range (the box-plot rule).
- **Hampel** drops runs far from the median, measured by the typical wobble (MAD).

Both look at **one number** (energy) at a time and are a few lines of plain Python.

## The heavier rules (future)

These are smarter in two ways: they can judge a run on **several measurements at
once** (energy and CPU and memory and runtime together), and they handle messier
data shapes. Plain descriptions:

- **Isolation Forest** — splits the data at random over and over. A weird run gets
  cut off from the crowd in just a few splits, so "easy to isolate" means outlier.
- **Local Outlier Factor (LOF)** — checks how crowded each run's neighbourhood is.
  A run sitting alone, far from the others, stands out.
- **DBSCAN** — groups runs that are close together into clusters and calls anything
  left over "noise", that is, an outlier.
- **Elliptic Envelope** — draws a balloon around the normal cloud of runs and flags
  anything outside it.

## The automatic selector (future)

No single rule is best on every machine. One machine's noise may suit IQR, another's
may suit Isolation Forest. The selector would **try the rules on our own repeated
data, check which one separates the obviously bad runs from the good ones most
cleanly, and pick that one for the machine**, then record the choice in the config.
This validates the choice on real data instead of guessing.

## When to revisit this

Implement these only if one of the following becomes true:

- The simple rules visibly miss bad runs on real measurements.
- We decide to judge a run on several measurements together, not energy alone.
- We want the selector to choose and justify the filter automatically.

Until then, IQR and Hampel stay the working rules.
