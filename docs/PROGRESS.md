# Verbose execution

Use `basis run -c study-isolate1/config.final.yaml -d -v` (or `--verbose`).
The environment variable `BASIS_VERBOSE=1` also enables progress for Python
helper scripts and worker processes. The consolidation helper accepts `-v`.

Timestamped, flushed messages on stderr report input planning, module stages,
resource fingerprinting, verified cache reuse, DeepSafe candidate processing,
reference searches, native commands, motifs/profiles and report generation.
Long tracked stages emit a heartbeat every 30 seconds with elapsed time.
A heartbeat means the stage has not returned; it is not an estimated percentage
or a guarantee that an external tool is making biological progress. Command-log
paths remain available for diagnosis. Native stdout files are unchanged.

Verbose mode is a runtime environment setting, not scientific configuration.
Toggling verbosity alone does not change cache keys or results. Installing the
source update still invalidates prior source-dependent caches as usual.
With the Snakemake engine, worker progress is saved in snakemake.log; the
launcher emits its own overall heartbeat. Local execution shows stages directly.
