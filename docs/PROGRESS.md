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
Snakemake and worker progress are forwarded live and saved in each launch's
snakemake.log. Verbose mode includes native command diagnostics. The launcher
does not add a duplicate overall heartbeat. GUI percentages come from observed
Snakemake job counts; checkpoint expansion can change the number of pending jobs.
