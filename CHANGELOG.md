# 0.3.0rc1 — Snakemake-owned workflow

- Use one packaged Snakefile entry point with analysis, explicit environment setup and explicit resource preparation modes.
- Move input/resource checks, named native module jobs, nine DeepSafe stages per sample, consolidation, reporting and provenance into the analysis DAG. Deep stages process the selected candidates within each sample; candidate-level scheduler fan-out is not included.
- Use native Snakemake `conda:` deployment for supported modules without an explicit command prefix. Preserve existing external prefixes and the legacy local execution option.
- Add explicit, versioned resource preparation for supported upstream databases and SHA256-pinned files. Preparation writes provenance and never silently refreshes resources during analysis.
- Preserve scientific thresholds, candidate selection and interpretation policies. The preceding `0.2.0rc16` release remains a separate baseline. Independent biological validation is pending.

# 0.2.0rc16 — workflow orchestration

- Snakemake is now a base dependency and the default CLI/GUI analysis scheduler. Serial local execution remains available.
- Verified environment installation is coordinated by a setup workflow, with explicit retries and per-attempt records. Existing directories are never treated as proof of installation.
- Guided workspace navigation and live scheduler output improve setup/run visibility.
- Source and resource cache validation, scientific thresholds and interpretation policies remain unchanged.
- Database/model auto-provisioning and independent biological validation remain pending.

# 0.2.0rc15 — GitHub distribution preparation

- Add the user-selected MIT license, author ORCID, and repository metadata.
- Add public-source installation instructions and continuous integration.
- Exclude generated build copies and local pilot audit/results from public source.
- Preserve rc14 scientific analysis code and thresholds. Native biological validation remains pending.

# 0.2.0rc14

- Optional Streamlit workspace, batch preview/import, configuration review,
  persistent single-host jobs, progress logs and interactive report access.
- A background worker calls the existing CLI; refresh/reconnect does not launch
  analyses. A new attempt is explicit. Requested-scope incompleteness remains
  distinct from process failure.
- Source checksums, private job configs/results and recorded command lines.
- Scoped loopback artifact serving, including explicitly selected sibling runs.
- RGI, antiSMASH and MOB-suite recipes included in pip distributions.
- Author ORCID 0000-0002-7829-0090 in CFF, BibTeX, RIS and text exports.
- Clean local installer and synthetic interface acceptance helper.
- Prepared TestPyPI workflow; publication requires repository/license decisions.
- No changes to scientific scoring, thresholds, native adapters or normalization.

# 0.2.0rc13 — Stable native-coordinate provenance on import

- Preserve the first captured native coordinates and contig alias when normalizing saved findings again.
- Avoid adding a native alias for a canonical contig self-mapping.
- Revalidate canonical coordinates while keeping generated finding IDs stable under repeated normalization.
- Add installed-package acceptance against the three pinned restored pilot evidence files; all replay is in memory.
- Document the restored-pilot review, including exact topology/support agreement and measured branch-length differences.

Historical evidence is preserved as recorded. This fix cannot reconstruct native provenance already overwritten in an old imported result. Scientific thresholds, reference data, tool recipes and candidate interpretation policies are unchanged. Independent biological validation remains outstanding.

# 0.2.0rc12 — Python virtual-environment classification

- Recognize valid Python virtual environments in the read-only dependency audit and explain when Conda metadata is not applicable.
- Skip native-engine fingerprint probes and native cache reuse for synthetic fixtures.
- Treat unsupported non-Conda engine fingerprints as unavailable while retaining strict verified Conda cache checks.
- Add a focused installed-environment acceptance check that reuses verified synthetic output and performs no native analyses.
- Preserve the accepted rc11 PC installation and historical-pilot capture records separately.
- Scientific algorithms, candidate selection, references, thresholds and interpretations remain unchanged.

## 0.2.0rc11 — inventory identity and reproducibility preparation

- Deduplicate Python metadata reached through filesystem aliases while preserving distinct installations and their versions.
- Preserve the rc10 PC audit establishing agreement between 228 Conda records and 228 export entries.
- Add a pinned historical-pilot capture helper and an isolated Python-launcher restoration check with retained wheel checksums.
- Keep scientific analyses, thresholds, references, selection and interpretation unchanged.
- Full native environment restoration and independent biological validation remain separate acceptance gates.

## 0.2.0rc10 — reporting consolidation and reproducibility capture

- Standard `basis report` supports explicit source checksum pins and a batch `--sources` manifest, producing one control panel for the selected snapshots.
- Reports and panels link exact recorded artifacts within explicitly selected roots. Directories, absent files, excluded locations and errors are distinct. Existing results and scientific records remain unchanged.
- `basis audit-reproducibility` freezes supplied configurations, execution sample sheets and evidence; hashes inputs and optionally resources; inventories local Conda/Python metadata without executing those environments. The context metadata reader now handles valid records up to 128 MiB and distinguishes failure causes.
- Release includes installed reporting acceptance, collaborator validation guidance and separately recorded historical PC checks.
- No biological methods, thresholds, reference resources, native execution, candidate selection or cache policies change. Full environment restoration and independent biological validation remain unperformed.

# Changelog

## 0.2.0rc9 — integrated evidence navigation

- Add a searchable selected-candidate navigator and stable sample-scoped section links.
- Expose motifs/patterns, domains, trees/alignments, placement, comparator gaps and saved files from each candidate.
- Validate report/evidence hashes and candidate exports before detailed control-panel links are used; show fallback warnings for stale indexes.
- Withhold accepted feature maps and tree displays for incomplete execution while retaining raw evidence.
- Preserve native analyses, reference resources, thresholds, candidate selection and interpretation rules.
- Add synthetic installed-package navigation acceptance checks. Independent biological validation remains separate.

## 0.2.0rc8 — unified run controls and batch summaries

- Accept run shorthand consistently across flag order, combined short flags and long-option values.
- Add verbose synthetic-demo support and restore prior verbosity settings after each CLI invocation.
- Keep run JSON stdout separate from progress; add explicit human summary output.
- Record compact per-sample run summaries in JSON/TSV and run history, with requested scope separated from selected-candidate completion and deferred work.
- Label dry-run output as a plan and distinguish imported/reused evidence from execution outcomes.
- Retain native algorithms, module selection, thresholds, resources and cache/exit policies.
- Record rc7 target-PC preflight acceptance as passed; rc8 target-PC acceptance is a separate synthetic check.

## 0.2.0rc7 — per-sample preflight and scope reporting

- Add structured sample input outcomes and module resource-check outcomes to validate/doctor.
- Continue preflight diagnostics for independent samples after a row resolution or sequence-consistency failure, while keeping structural errors global and execution loading strict.
- Add optional offline HTML/JSON/TSV preflight reports in a new directory.
- Distinguish explicit exclusions, disabled modules, omitted extension scope and requested prerequisite holds in reports.
- Preserve inactive exclusion declarations and show sample-specific input warnings.
- Keep native algorithms, thresholds, resource definitions and declared-scope policy unchanged.
- Retain rc6 and earlier software/PC checks as versioned historical evidence; rc7 target-PC acceptance is separate.

## 0.2.0rc6 — external-input provenance and validation

- Expose per-sample input mode, taxonomy context and HybAs metadata consultation status in import preview and `validate`/`doctor` JSON.
- Reject an external input claiming an imported HybAs taxonomy source even when its taxon is blank, for both importer and direct TSV loading.
- Recognize missing-taxonomy markers consistently for the existing advisory; preserve raw declarations and module planning actions.
- Give assembly-only TSV inputs an actionable annotation-required error. No annotation engine is added.
- Add verbose preflight stages/heartbeats and a target-PC external-mode checker using preserved source annotations and isolated copies.
- Retain native algorithms, detection thresholds, reference/model resources and cache policy.

## 0.2.0rc5 — batch preview and import acceptance

- Add `basis import-samples --dry-run` with structured per-row readiness, resolved input paths and aggregated file errors. It creates no study; `--directory` is optional for preview.
- Use the same file resolver for preview and import, retaining strict sheet validation, shared-input rejection and transactional study creation. Shared-file diagnostics are bounded per row.
- Add `-v` import/preview progress on stderr while retaining machine-readable JSON on stdout.
- Supply equivalent Excel/CSV pilot sheets and a separate installed-command checker for isolates 4, 5 and 6. It checks manifest handling without native analysis or GPU inference.
- Retain scientific algorithms, resources, interpretation rules and rc4 cache behavior unchanged. A ready import is not a passed analysis preflight.

## 0.2.0rc4 — persistence-stable DeepSafe cache

- Normalize cache payloads to their persisted JSON representation before hashing, so numeric position-map keys retain the same checksum after reloading.
- Reject JSON key collisions and nonfinite values; retain failed-execution, upstream, configuration/source-key and native-artifact checks.
- Use the versioned `deep_cache_v2` policy. Historical seals remain unchanged and are conservative cache misses.
- Include a read-only saved-pilot cache check and regression coverage for persisted reuse and tampering. No scientific methods or resources changed.


## 0.2.0rc3 — native runtime and evidence integration candidate

Explicitly targeted Conda subprocesses now receive an isolated copy of their environment: launcher virtualenv paths and Python overrides are removed, while Conda, CUDA and offline settings are retained. Parent process settings and unprefixed commands are unchanged. Per-command routing decisions are recorded without environment variable values.

DeepSafe retains execution/provenance warnings and finalizes its completion summary after upstream status is known. Imported records retain a source checksum and historical provenance separately from the new import execution.

Includes the rc2 batch-input and external-annotation features. A separate verification launcher imports checked recovered upstream evidence and runs the existing DeepSafe analysis with frozen scientific settings in new output directories. Original failed acceptance records remain historical failures; the new check is an integration checkpoint, not a fresh run of every native tool or independent biological validation.

## 0.2.0rc2 — batch-input candidate

Adds strict Excel/CSV/TSV import into a new study, exact sample-name discovery, original-sheet snapshots and input hashes. External annotated inputs can explicitly bypass HybAs metadata discovery. Assembly-only input returns an annotation-required error.

Adds per-sample exclusion declarations and prerequisites, shared by doctor, local/Snakemake execution and reporting. Requested holds remain incomplete; deliberate exclusions change the requested scope and retain their reason. Native ICEscreen now needs matching GenBank plus an explicit sample phylum declaration; old configs must be reviewed when adopting this candidate. No lineage is inferred from a genus name.

Adds input/scope reporting and module-scope.tsv. Existing scientific thresholds, reference labels and analysis algorithms are unchanged. The rc1 acceptance run and its installation are separate. Software verification is recorded in this candidate's release checks; independent biological validation remains pending.

## 0.2.0rc1 — deployment candidate

This candidate consolidates the captured pilot implementation previously labelled 0.1.0. It is a packaging and reproducibility checkpoint, not a biological validation release.

- Added an immutable package manifest and `basis verify-install` for Python source and runtime resources.
- Added installed `basis report`, `basis control-panel` and `basis setup-environments` commands.
- Bundled the existing environment recipes and moved setup destinations to an explicit workspace or the current directory.
- Added saved-report/control-panel acceptance checks and corrected Overview navigation and report classification.
- Included bundled runtime assets in the source fingerprint used for provenance and cache identity.
- Preserved exact captured source lineage, eleven environment inventories and the unresolved context metadata discrepancy.
- Consolidated release version and citation metadata; retained historical run evidence separately.

No biological detection thresholds, family decision rules or comparator labels were changed for this consolidation. The earlier native pilot results remain historical execution evidence. Independent biological performance validation and a complete offline environment/data restore remain outstanding.
