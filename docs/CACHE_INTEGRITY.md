# DeepSafe cache persistence

## Defect and repair

The previous checksum policy sorted Python dictionary keys before serializing them. In-memory PROSITE position maps had integer keys, whereas persisted JSON reloaded those keys as strings. Numeric and lexicographic ordering differ, producing a false cache miss even when the evidence was unchanged.

The `deep_cache_v2` payload checksum first converts values to their JSON representation, then sorts and hashes that representation. It matches the writer's conversion of tuples, paths and dictionary keys. Duplicate JSON key names and nonfinite values are rejected, so normalization cannot silently discard conflicting entries. The generic hash helper elsewhere in BaSIS is unchanged.

The payload excludes only `deep_cache`, `cache_hit`, and `cache_checked_at`, as before. Source/input/configuration cache identity, execution status, upstream status, failed-candidate checks and per-artifact SHA-256 checks remain required. Every declared artifact must have a checksum. The checksum detects inconsistency; it is not a digital signature or an authenticity guarantee against someone able to replace both data and manifests.

## Historical results

Version 1 seals are conservative misses. They are not automatically relabelled, repaired or promoted to current-source caches. The read-only verification helper can reconstruct the original integer-key representation strictly within saved `position_map` dictionaries to diagnose the known defect. This is a diagnostic of existing evidence, not a migration or authorization for reuse.

The uploaded rc3 pilot records for isolates 4 and 5 reproduced this defect exactly; isolate6 had no selected candidates and its version 1 payload already matched after reload. All three records pass the new representation roundtrip checks on the build host. Original native artifact paths are available only on the pilot PC; the supplied checker verifies those there before reporting a complete saved-artifact check.

## Tests and limits

Regression tests cover persisted reuse through the pipeline, integer/string map normalization, key ordering, tuples and paths, key collisions, nonfinite values, wrong identity, upstream failures, legacy seals, and altered or missing artifacts. The native analysis in the pipeline test is a temporary fixture, not a biological computation.

No change is made to biological methods, reference sets, interpretation thresholds, candidate selection or model outputs. Reproducibility of software and saved artifacts does not establish biological validity. See `RELEASE_CHECKS.json` for the actual checks completed for this release.
