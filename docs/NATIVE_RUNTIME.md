# Native subprocess routing and recovered evidence

When CommandRunner uses an explicit `conda run` target, BaSIS prepares a copied environment for that subprocess. It removes the launcher's virtualenv bin directory and Python override variables, sets `PYTHONNOUSERSITE=1`, and leaves Conda, CUDA and offline settings intact. This prevents a bare child `python` or `python3` from resolving to the launcher environment after Conda activation. The parent process environment is unchanged. Direct commands and arbitrary wrappers are not reinterpreted.

Command metadata and logs record whether this policy applied, the removed variable names and the removed PATH directories. Values of removed variables are not recorded. Target interpreter probes in the recovered-pilot verification run test this policy through CommandRunner itself.

Imported evidence is historical. Its source path and file checksum bind the imported record; source provenance, configuration, input hashes and artifact checksums remain separate from the new import operation. A source checksum is an integrity identifier, not a digital signature or evidence of the source's correctness.

DeepSafe completion JSON is finalized after upstream statuses are known. Selected execution, deferred scope and biological interpretation are separate fields. Cache/provenance warnings remain visible when analysis output is merged. This release does not relax cache identity checks or scientific thresholds.
