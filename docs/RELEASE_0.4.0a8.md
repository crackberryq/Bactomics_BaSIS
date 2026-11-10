# BaSIS AWARE 0.4.0a8

This controller compatibility patch restores retained-output execution in Conda environments where `snakemake-minimal` provides Snakemake 9.6.2 but Python distribution metadata reports `0.0.0`.

The retention adapter checks the imported engine's `snakemake.__version__`. Only the supported 9.6.2 and 9.27.1 runtimes are accepted. Missing distribution metadata or its known `0.0.0` placeholder is tolerated for those supported runtimes; a meaningful version conflict is rejected. The scheduler API-shape guard remains required. Unknown runtimes cannot be authorized by distribution metadata.

Output retention, retry behavior, scientific algorithms, model parameters and interpretation are unchanged from 0.4.0a7. This remains an alpha release, distributed as the GitHub source archive `v0.4.0a8`.

Publication now also requires a native Conda controller CI matrix: `snakemake-minimal` 9.6.2 and 9.27.1, with no pip replacement of the scheduler. Each job records the runtime/distribution versions and installed packages and exercises real scheduler failure, retry, and forced-rerun retention. Local pip-based checks alone cannot satisfy this gate.
