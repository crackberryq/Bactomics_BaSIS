# BaSIS AWARE 0.4.0a7

This alpha update preserves generated files across retries, failures, and reruns. It does not change biological algorithms, evidence interpretation, analysis thresholds, or model parameters.

- Before Snakemake replaces a job's outputs, logs, or benchmark, it moves existing files into a unique `retained/` history under the isolate's `safety/` directory or configured resource directory. A manifest records original paths, the owning rule, and preservation status. Unsupported scheduler versions fail before work starts; the adapter targets Snakemake 9.6.2 and 9.27.1.
- Failed and interrupted products and temporary scheduler outputs are kept. Native result-store attempts are never automatically pruned, including when older configuration supplies a failed-attempt retention cap.
- DeepSafe uses durable native attempts. PF2 retains complete embedding and prediction work directories, including extra maps and sidecars; current output paths link to validated retained products.
- Native database working copies, generated indexes, provider logs, partial downloads, retrieved responses, and failed placement builds remain available for inspection.

Histories are evidence of generated files, not successful execution. Validated completion records still establish completion. This policy increases storage use. Cleanup requires an explicit user decision. Files already deleted by older versions cannot be recovered by this update.

Distribution remains a GitHub source archive at `v0.4.0a7`; `basis-aware` and `development` use the same release source. The earlier RC `main` branch is unchanged. Independent native and biological validation remain separate from storage regression testing.
