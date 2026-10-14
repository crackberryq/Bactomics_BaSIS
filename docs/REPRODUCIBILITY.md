## rc13 normalization and restored-pilot review

Repeated normalization preserves the first captured native-coordinate provenance and stable generated finding IDs. Canonical coordinates continue to be validated against the saved annotation context. Legacy provenance is retained as recorded; missing or previously overwritten native values cannot be reconstructed.

The October 14 review of the rc12 restored pilot matched all 18,867 serialized screening findings by biological identity, with no changed retained calls or numeric scores. The selected protein sets and decisions matched (35, 28 and zero selected for isolates4,5,6). PathogenFinder2's four ordered member predictions matched exactly for isolates4/5. Isolate6 remained outside the requested Extend scope.

The two inspected isolate4 baseline trees have identical topology and internal support labels. Four ClpP terminal branches differ by at most 3.8e-9, and two qac terminal branches differ by 1e-10. Their alignment and reference-label bytes match exactly. These are recorded numerical differences, not byte-identical trees. No equivalence tolerance was introduced after observing them, and these observations do not validate a protein's function.

See the delivered `validation/restored-pilot-review.json` and run `BaSIS_check_normalization.py` with the installed rc13 launcher for the distinct saved-evidence replay check. Independent biological validation and replication on other machines remain outstanding.

## rc12 environment handling

The rc11 launcher passed nine top-level checks on the target PC using Python 3.11.17; all command log hashes and 97 installed package identities were verified. Four historical snapshots, sixteen inputs and sixty-six resources were captured separately. Those records remain historical acceptance evidence, not independent biological validation.

rc12 corrects the ordinary-venv Conda metadata warning and avoids native fingerprint probes for synthetic fixtures. The focused environment checker validates this software behavior using the installed package. Native Conda fingerprints remain required for native cache reuse; an unsupported environment disables reuse instead of authorizing it from version strings alone.

# Reproducibility capture

`basis audit-reproducibility` records a local study and its dependency identities without running scientific analyses, installation, downloads or environment activation. Use a new output directory for every capture. Its manifest always retains the distinction between a completed capture and a verified restoration.

```bash
basis audit-reproducibility \
  --config study/config.yaml \
  --evidence study/results/evidence.json \
  --environment /absolute/path/to/tool-environment \
  --outdir release-audit
```

Configuration, evidence and environment arguments can be repeated. Explicit Conda `-p`/`--prefix` environments in declared command prefixes are discovered from configurations and saved evidence. Named environments are not guessed: supply their actual prefixes. Evidence-derived environment/resource declarations describe the saved run; inspecting their current paths does not update or validate historical results.

The audit creates:

- `manifest.json`, with BaSIS version, source/input/resource identities, environment metadata, issue categories and explicit limits.
- `frozen/`, containing byte-for-byte copies of supplied configurations, their TSV sample sheets and supplied evidence. Their hashes are checked against the sources before finalization; their contents and interpretations are not edited.
- `report.html`, a local summary with downloads for the manifest and checksum list.
- `SHA256SUMS`, covering all generated files except the checksum list itself.

The frozen files can contain local paths or sensitive study content. Review them before sharing. Python direct-source inventories remove credentials, query strings, local build paths and Conda token path segments. Exact study source copies are deliberately not redacted because redaction would break byte identity.

## Inputs and resources

Assembly, protein, annotation and declared phenotype files are hashed but not copied. Resource paths declared in configurations or saved effective configurations are recorded, including databases, models, reference registries and manifests. This audit does not discover undeclared dependencies by searching the computer. Mutable placement-cache contents are not treated as immutable reference resources.

Configured module paths containing `{sample_id}` are expanded for the validated sample IDs in the frozen TSV, using the same substitution supported by module execution. Unknown placeholders are reported as `unresolved_template`; they are not treated as literal missing filenames. DeepSafe resource paths are global and do not receive module sample-template substitution.

Disabled modules, explicit per-sample exclusions and saved decisions with `requested: false` remain visible as inactive declarations. Their absent resources or environments do not become missing-required-dependency gaps. Configurations alone establish enabled/disabled declarations, not whether optional `-d`/`-e` execution was requested; this distinction is retained in `module_scopes`. Saved effective configurations and explicit recorded decisions provide the historical scope when evidence is supplied. Exact frozen source bytes remain unchanged.

Use `--hash-resources` to additionally hash the current contents of each declared resource. Large database/model directories can take time. Directory hashes cover the sorted file inventory. Symlinked directories and file links outside the declared resource root are recorded as unresolved gaps rather than silently traversed. A directory with such gaps is not reported as completely hashed. No resource contents are archived by this command.

Absent inputs/resources and unreadable environment metadata remain visible gaps. Missing or malformed explicitly supplied configuration/evidence files stop the command. If a source changes while being frozen, the capture does not produce a finalized manifest; keep the partial directory for diagnosis and rerun into a new directory after the source is stable.

## Conda and Python metadata

The audit reads `conda-meta/*.json` directly, with a **128 MiB per-file bound**. It records each metadata file's size and SHA256 when it is read, and extracts narrow package identities. Size-limit failures, filesystem read failures and malformed JSON/identity records are separate categories.

This addresses the earlier capture's inability to diagnose the missing `tensorflow-base` metadata in `basis-context`: that capture used a 16 MiB bound and combined several error types. The new audit can read a valid record larger than 16 MiB, but does not invent a missing record or alter Conda metadata. The October 13 target-PC capture read all 228 records and matched all 228 explicit-export digests. The previously omitted TensorFlow metadata was 29,214,494 bytes, larger than the old bound. The original audit is preserved in `reproducibility/rc10-context-audit-original.json`; this resolves the metadata capture discrepancy, not package-archive or restoration verification.

The Python API accepts an optional `conda_explicit_paths` mapping from environment prefix to a previously saved `@EXPLICIT` export. It compares package filenames and supplied MD5/SHA256 fields with installed metadata and preserves mismatches. The CLI exposes the same comparison through repeatable `--conda-explicit PREFIX EXPORT` arguments. Matching metadata does not verify the downloaded package archive bytes or demonstrate a successful restoration.

Python distributions are inventoried using metadata files in each prefix's `site-packages`, without importing those packages or executing `.pth` startup code. `.pth` filenames, hashes and presence of startup code are reported separately. Distribution versions, installers and sanitized source commit identities are retained. Pip inventories do not preserve exact wheel builds and are not installable, artifact-hashed locks.

## What still needs independent verification

A reproducible deployment requires retention of installation artifacts, input snapshots, database/model snapshots and their licenses, plus a successful installation and native-tool run in a fresh target environment. Conda package inventories can be platform and hardware specific; the existing context environment includes CPU/GPU-specific dependencies. This command neither replaces nor repairs installed environments.

Independent biological validation is a separate project using held-out data and predefined assessment criteria. A successful audit, software smoke test, report replay or pilot rerun does not establish function, pathogenicity, clinical validity or organism safety. This release makes no new scientific classification decisions.

## rc11 filesystem aliases

Python inventory counts a physical site-packages root, distribution metadata container or `.pth` file once. Resolved paths and available device/inode identities distinguish aliases from separate installations. Alias paths remain recorded in `scan_aliases`; separate physical installations remain visible even when package names and versions are identical. Version conflicts are not hidden by name-only deduplication. The original rc10 audit retains its original duplicate rows as a historical artifact.

The supplied `BaSIS_freeze_validation.py` wraps the capture for the four explicitly pinned pilot snapshots and verifies source and release identities. It does not turn historical pilot evidence into independent validation or verify that current mutable resources are identical to every historical run's resources. Use repeatable `--config` for explicitly chosen future-run configurations.
