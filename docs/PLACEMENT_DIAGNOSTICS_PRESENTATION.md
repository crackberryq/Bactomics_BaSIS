# Update 35: placement-weight presentation

The v2 internal benchmark exposed a presentation problem: a diagnostic can record
nonzero competing-boundary weight even when almost all weight lies within a
qualified group. The previous blanket boundary banner could therefore look like
an overall abstention, including for an accepted assignment. ClPa also has tiny
boundary weight while its substantial unassigned weight lies on context edges.

This reporting-only correction identifies the largest placement-weight category
and quantifies the boundary component separately. The saved diagnostic reason
describes category presence; it does not replace the recorded decision.
No materiality threshold or new classification rule is introduced.

Native outputs, group support values, candidate assessments, benchmark outcomes,
and the existing reference-panel defaults remain unchanged. Regenerate a saved
benchmark report with:

```bash
python BaSIS_recover_benchmark.py --run-dir PATH_TO_SAVED_BENCHMARK
```

The helper verifies saved evidence and writes a new report directory without
rerunning BLAST, MAFFT, IQ-TREE, EPA-ng, or GPU inference. Historical isolate
family-placement reports can use `BaSIS_refresh_placement_report.py`.
