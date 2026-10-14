# Native context adapters

These adapters report predictions and genomic context. They do not classify an
organism as safe. Software dependencies and databases are external; this release
contains parser and failure-path tests. Update 25 also checks live MinCED test inputs;
native acceptance on each user installation and biological validation remain separate.

## Supported interfaces

| Module | Native engine and result | Additional configuration |
|---|---|---|
| `antismash` | antiSMASH 6+ JSON `records[].areas` | Optional `database`; supplied GFF is used, otherwise Prodigal is requested |
| `genomad` | `genomad end-to-end`; both `*_virus_summary.tsv` and `*_plasmid_summary.tsv` | Required prepared `database` directory |
| `mobsuite` | `mob_recon`; `contig_report.txt` | Required prepared `database` directory |
| `integronfinder` | `integron_finder`; `.integrons` plus `.summary` | `topology: linear` (default) or `circular`; `local_max: true` (default) |
| `isescan` | `isescan.py`; input-basename `.tsv` | Uses the engine's installed databases |
| `icescreen` | ICEscreen 1.3.3 CLI; `*_detected_ME.tsv` plus `.summary` | Required `sample.genbank` and explicit `phylum: bacillota` |
| `minced` | MinCED 0.4.2 `-gffFull`; text and GFF | No database; Java runtime |

Every module accepts `executable` and `threads`; the shared runner handles
`command_prefix`, timeout, commands, stderr and nonzero exits. There is no shell
interpolation. Native outputs are preserved under the module's `native/`
directory. An existing directory is rejected to prevent a failed rerun reading
stale results. Use a fresh run directory.

ICEscreen requires an annotated GenBank record including nucleotide ORIGIN.
The adapter copies it into an isolated input folder and runs the documented
`icescreen --galaxy -g INPUT_DIR -o OUTPUT_DIR -j JOBS --phylum bacillota` interface. Its
signature-protein reference resources must be prepared by the tool's installer.
The taxonomic restriction is not inferred from a string species name.

Optional CRISPRCasFinder and MLST native execution is **not implemented**
in this release. These are characterization analyses, not required safety
screens. If needed, use the application's validated normalized-result import
with tool/database provenance. No dummy success or negative output is generated.

## Interpretation and coordinate rules

* Missing files, empty files, HTML, unsupported headers, truncated rows, invalid
  values and inconsistent paired summaries raise errors. A header-only native
  result is accepted only where it is an explicit zero-result format. MOB-recon
  and IntegronFinder summary tables must contain assessed contigs/replicons.
* Header-only outputs do not prove that the upstream search ran; native execution
  must also finish successfully. For an imported result the importer must validate
  provenance and completed execution separately.
* antiSMASH JSON uses zero-based half-open coordinates. The adapter converts to
  one-based inclusive coordinates. Origin-spanning regions become two linked
  segment findings using the record sequence length, with one native region ID;
  `metrics.region_count` counts the biological region once. If the required
  sequence length is missing, parsing fails rather than guessing a location.
  Multiple products in one region do not become multiple BGC counts. BGC classes
  do not establish production of a specific molecule or toxic effect.
* geNomad's integrated-virus ID must agree with its one-based coordinate column
  and reported length. `attributes.integrated` distinguishes proviruses from
  complete viral contigs. Its native scores are retained without treating them
  as biosafety probabilities. Both virus and plasmid tables are mandatory.
* MOB-recon provides a contig-level plasmid classification. Reconstruction group,
  replicons, relaxases and predicted mobility are retained in native attributes.
  Chromosome calls are counted, not converted to hazard findings. Taxonomic
  representation and assembly recovery limit negative plasmid claims.
* IntegronFinder component rows are grouped by replicon and integron ID, and
  observed element counts must equal the native summary. Canonical coordinates
  describe the detected component span, not verified element boundaries. A
  circular replicon's element boundaries are left null because simple min/max
  spans can include most of the chromosome. Native component intervals are
  retained separately and require review before inferring cargo linkage.
* ISEScan TSV coordinates are one-based inclusive; strand refers to its
  transposase. Proximity to an insertion sequence does not demonstrate transfer.
  For the verified 1.7.3 zero-result format, the exact input-specific terminal
  message, nonempty generated proteins and both completed HMMER outputs are
  required when no TSV is emitted. Missing output alone remains a failure.
* ICEscreen's `Start_of_most_upstream_SP` and `Stop_of_most_downstream_SP` are
  signature-protein extremes, **not mobile-element boundaries**. Therefore
  canonical element coordinates are null. Native spans, signature-protein IDs,
  structural categories and curation comments are retained. These calls identify
  structures needing examination, but automated full-element cargo overlap is
  unavailable without a reviewed boundary annotation.

Native-schema changes fail closed and require a parser update. Unknown numerical
values are null, never zero. Genomic findings are hypotheses requiring appropriate
sequence and contextual review; the module count is not a risk score.

## Upstream interface sources checked

Accessed 2026-10-07. Sources establish interfaces; they do not imply live testing
of the user's installation.

* antiSMASH commands: https://docs.antismash.secondarymetabolites.org/command_line/
  and maintainer CLI listing https://github.com/antismash/antismash/discussions/728
* antiSMASH area schema: https://github.com/antismash/antismash/discussions/484
  and https://github.com/antismash/antismash/blob/master/antismash/common/serialiser.py
* geNomad command, tables and coordinates: https://portal.nersc.gov/genomad/quickstart.html
* MOB-suite native headers and CLI:
  https://github.com/phac-nml/mob-suite/blob/master/mob_suite/constants.py
  and https://github.com/phac-nml/mob-suite/blob/master/mob_suite/mob_recon.py
* IntegronFinder files, topology and CLI:
  https://integronfinder.readthedocs.io/en/stable/user_guide/tutorial.html
  and https://github.com/gem-pasteur/Integron_Finder/blob/master/integron_finder/results.py
* ISEScan invocation and output columns: https://github.com/xiezhq/ISEScan
  and https://github.com/xiezhq/ISEScan/blob/master/pred.py
* ICEscreen CLI: https://icescreen.migale.inrae.fr/run_icescreen/install_run_locally/run_locally/
* ICEscreen result semantics:
  https://icescreen.migale.inrae.fr/interpret_results/description_output_files/

Test command: `python -m unittest discover -s tests -p test_context.py -v`.

Update 25 adds resource setup, summary validation and MinCED native execution;
see [OPTIONAL_MODULES.md](OPTIONAL_MODULES.md) for current configuration and validation scope.
