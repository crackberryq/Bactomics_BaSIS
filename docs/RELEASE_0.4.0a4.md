# BaSIS AWARE 0.4.0a4

**Alpha prerelease.** Tag: `v0.4.0a4`.

Includes the focused AMRFinder review policy and report/export improvements in
[v0.4.0a3](RELEASE_0.4.0a3.md), plus a provenance correction found by the Bactologix
cross-version acceptance test. The separate DeepSafe report now exports the
original screening version alongside the executing follow-up version. The
previous condition used an unsupported view name, so the original version was
retained in reviewed configuration but omitted from the report's run record.

Direct aggregation and the actual saved-follow-up DAG both verify the two version
fields. The original screening version and files remain unchanged. This patch
does not change candidate selection, thresholds, tool execution or interpretation.

Publication automatically waits for Python 3.11/3.12, offline browser and real
Conda deployment/recovery checks. Native AFB01/isolate1 acceptance and scientific
review remain required before beta. No real isolate analysis was run for this fix.
