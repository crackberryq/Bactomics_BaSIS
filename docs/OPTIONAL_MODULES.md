# Optional native screens — update 25

This chapter records the historical isolate1 setup workflow. Native pilot runs
and subsequent adapter fixes are documented separately in the release provenance.
For installed-package deployment and current acceptance boundaries, use
[DEPLOYMENT.md](DEPLOYMENT.md). The root helper scripts are retained in the source
distribution for earlier studies; they are not general installed CLI commands.

The isolate1 setup helper adds four native screens to the existing core, DeepSafe
and Extend workflow. Existing input files, environments for core analyses and
results directories are preserved. New installations use isolated prefixes.

| Module | Added scope | Interpretation limit |
| --- | --- | --- |
| BacMet 2.0 experimental proteins + DIAMOND | Protein homologues of biocide/metal-associated reference genes | Experimental evidence for a reference does not establish the isolate's phenotype. Reported separately from antibiotic resistance. |
| ISEScan 1.7.3 | Insertion-sequence predictions | Proximity to a candidate does not demonstrate transfer. |
| ICEscreen 1.3.3 | ICE/IME and related signature-protein structures in Bacillota | Signature extremes are not established element boundaries; unknown bounds remain unmapped. |
| MinCED 0.4.2 | CRISPR arrays | Cas genes, subtype and activity are not assessed. |

CRISPRCasFinder remains an unselected alternative, with its additional Cas scope
explicitly unassessed. No applicable Lysinibacillus MLST scheme was identified in
the checked PubMLST and mlst catalogues; a Bacillus scheme is not substituted.
This is a study-specific applicability statement, not a universal assertion
about all possible typing methods or future catalogue releases.

## Use

From the project folder and the `basis` environment:

```bash
python BaSIS_setup_optional.py --install --run
```

The helper uses `study-isolate1/config.fresh.yaml` when present, otherwise
`config.combined.yaml`. A different native config can be supplied with `--config`.
It expects the current isolate1 input set and its saved completed core/Extend
results. It verifies matching input hashes and recorded native artifact hashes
before preparing imports. No GPU inference is repeated in this supplemental run.

The helper streams setup progress, emits periodic heartbeats and writes a setup
summary under `study-isolate1/optional-setup/`. Each stage's native command logs
remain under its result attempt directory. On a download/install failure, rerun
the same command: successful environments are probed and retained, while
incomplete environments are updated. No SSL-verification bypass is used.

The following are produced:

- `config.optional-TIMESTAMP.yaml`: new optional tools native; prior core/Extend
  imported and explicitly labelled; DeepSafe runs against the combined evidence.
- `results-optional/report.html`: combined report, including the additional tracks.
- `config.expanded-native-TIMESTAMP.yaml`: all configured tools native, ready for a
  future `basis run -c CONFIG -d -e -v`. The helper prints the exact command.

`--install` without `--run` prepares resources/configs only. `--run` without
`--install` verifies and uses installed resources without downloading BacMet.
`--modules bacmet isescan` limits this activation to the named optional screens.
Existing results are never deleted. Repeated execution of the same output scope
uses valid caches where allowed, retains attempt history and refreshes its report.

## Provenance and completion

Tool versions, installed package inventories, executables and required bundled
resource hashes are captured. ICEscreen uses `--galaxy` to prevent its shell
wrapper from switching to another globally named environment. Its bundled
database and Bacillota parameter profile are identified explicitly.

BacMet's official 753-protein experimental release is fetched directly on the
user's PC and checked against the reviewed SHA256
`e62756aafc1c653cdd1408fdbf558fd396ce1ef7ad99f2af50b0a6b67d00dd45`.
The original FASTA, headers, download metadata and DIAMOND version/build command
are retained. HTTPS is attempted first; the historical official HTTP endpoint is
accepted only with the exact content pin. No database is redistributed in this
update. Five short BAC labels are duplicated in the source, so both search and
annotation retain the complete unique FASTA identifiers.

The study preset uses 80% identity, 70% reference coverage and 70% query coverage
for BacMet report qualification. These are screening thresholds, not calibrated
phenotype classifiers. `deep.include_stress: true` admits qualifying stress
homologues into the bounded native-screen follow-up queue; source category remains
`stress`. BacMet references are appended to a separate retrieval catalogue as
**unreviewed comparators**. Existing reviewed anchor metadata remains intact.
No new reviewed family classifier or automatic functional verdict is invented.
Below-threshold exploratory candidates retain their configured deferral policy.

ISEScan can omit its table in a successful no-hit run. BaSIS requires the exact
input-specific completion message, generated proteins and both completed HMMER
tables to accept that case. Missing output alone is never a negative call.
ICEscreen table counts must agree with all six native summary categories and
the GenBank record inventory. MinCED text/GFF, per-contig writer completion,
repeat intervals and representative repeat sequence must agree with the assembly;
logged Java errors fail even if upstream returns exit code zero.

Source/fixture validation was performed locally. MinCED 0.4.2 was also run on
positive and all-negative multi-contig fixtures. ISEScan/ICEscreen full execution
and Conda dependency resolution on the user's PC still require the first native
run. The helper records actual outcomes; setup readiness is not native acceptance.

## Sources checked 2026-10-09

- https://github.com/xiezhq/ISEScan
- https://bioconda.github.io/recipes/isescan/README.html
- https://icescreen.migale.inrae.fr/run_icescreen/install_run_locally/install_from_conda/
- https://icescreen.migale.inrae.fr/run_icescreen/install_run_locally/troubleshooting_installation/
- https://icescreen.migale.inrae.fr/interpret_results/description_output_files/
- http://bacmet.biomedicine.gu.se/download_temporary.html
- http://bacmet.biomedicine.gu.se/download/BacMet2_EXP_database.fasta
- https://github.com/bbuchfink/diamond/wiki/3.-Command-line-options
- https://github.com/ctSkennerton/minced
- https://github.com/dcouvin/CRISPRCasFinder
- https://pubmlst.org/organisms/l
- https://github.com/tseemann/mlst/blob/master/db/scheme_species_map.tab
