# PathogenFinder2 extension

BaSIS runs pretrained PathogenFinder2 on the **full supplied proteome**. It
retains each ensemble member's score, native mean, native population standard
deviation, binary model label, and phenotype string. These are model outputs,
not the probability of illness from a construction application. The module
always returns an inconclusive organism-safety interpretation. It never adds
a safe/unsafe verdict, trains a model, or describes ensemble disagreement as
Monte Carlo dropout or a confidence interval.

## Supported native contract

Source inspected: PathogenFinder2 version 0.7.1, commit
`3837caf6710da5da3038f5d1246dd82ab26e003a`.

- [Output writer](https://github.com/genomicepidemiology/PathogenFinder2/blob/3837caf6710da5da3038f5d1246dd82ab26e003a/src/pathogenfinder2/utils/output.py): `PredictionReport.get_predictions` and `save_report`.
- [Path layout](https://github.com/genomicepidemiology/PathogenFinder2/blob/3837caf6710da5da3038f5d1246dd82ab26e003a/src/pathogenfinder2/utils/configuration.py): `FilesModule` single-input key is `PF2`.
- [Input embedding order](https://github.com/genomicepidemiology/PathogenFinder2/blob/3837caf6710da5da3038f5d1246dd82ab26e003a/src/pathogenfinder2/dl/utils/data.py): `ProteomeDataset.open_embedfile`.
- [Long-protein splitting](https://github.com/genomicepidemiology/PathogenFinder2/blob/3837caf6710da5da3038f5d1246dd82ab26e003a/src/pathogenfinder2/preprocessing/embedder.py).

The default command is:

```text
pathogenfinder2 predict -i FULL_PROTEOME.faa -o OUTPUT/native -f proteome --attProteins report
```

The adapter reads the exact nested path
`native/predictionPF2/results_PF2/predictions.tsv`. It ignores native comment
lines and requires `File Name`, `Prediction_0` through `Prediction_3`,
`Prediction Mean`, `Prediction STD`, `Binary Prediction Mean`, and `Phenotype`.
It rejects missing/empty/malformed output, a wrong sample key, wrong ensemble
size, nonfinite values, inconsistent summary statistics, and contradictory
labels. A process returning zero without valid outputs is still a failed
module. Native files remain intact.

## Configuration

A fresh full-preset study requested with `-e` automatically prepares the pinned
local ProtT5 model and classifier resources before inference. Existing explicit
paths are preserved. With `execution.device: auto`, the workflow probes NVIDIA
capacity and verifies CUDA inside the inference environment, recording a CPU
fallback when unavailable. Drivers remain an external prerequisite; BaSIS does
not install them. `metrics.inference_device` and command logs retain the device
check. The table below describes adapter keys and manual overrides; automatic
resource preparation supplies the local model paths and identities.

Use these keys inside the `pathogenfinder2` module configuration:

| Key | Default | Meaning |
| --- | --- | --- |
| `attention` | `true` | Request and export per-protein attention. |
| `attention_top_n` | `20` | Number of exploratory `ml_attention` findings; `0` keeps all scores but adds no candidate findings. |
| `map` | `false` | Add `--embedProteome map`; preserve optional BPL products. |
| `expected_models` | `4` | Strict expected member count; change only for a reviewed custom ensemble. |
| `validate_score_ranges` | `true` | Validate finite native scores in [0, 1]; finite/structural checks always remain active. |
| `prot_t5_path` | unset | Existing local ProtT5 model, passed to `--protT5Path`. |
| `prot_t5_identity` | upstream default plus PF2 source commit | Reviewed model version/digest; required with `prot_t5_path`. |
| `weights_model` | unset | Optional native `--weightsModel` override; root provenance must fingerprint it. |
| `embeddings` | unset | Reuse a previously generated `ProteinEmbeddings.h5`. |
| `embeddings_manifest` | unset | Required provenance manifest for reused embeddings. |
| `python_executable` | `python` | Python in the same PF2 environment, for the small NumPy/HDF5 export step. |

Standard module `executable`, `command_prefix`, timeout, and resource settings
are provided by the main workflow. The adapter does not prepend
`command_prefix` itself; the runner does. There is no invented PF2 `--threads`
flag. Scheduler resource declarations and GPU availability do not prove which
device the model used; inspect its logs. Do not launch concurrent GPU-heavy
jobs without sufficient GPU memory.

## Attention interpretation and integrity

PF2 exports `attentions.npz` with `protIDs` and `attentions`. The IDs are a
pickled NumPy object array at the pinned revision. BaSIS never unpickles them.
It reads only the numeric attention matrix with `allow_pickle=False`, and
reads the exact ordered names from the HDF5 `Names` dataset that PF2 itself
uses for inference. This tiny conversion executes in the PF2 environment;
BaSIS's main Python environment does not need NumPy, HDF5, or PyTorch.

Every token must map to the full input proteome; unknown, duplicate, missing,
or dimensionally inconsistent data cause failure. Source FASTA IDs and full
descriptions are retained during matching, including PF2's slash-to-underscore
header transformation. For long proteins, upstream can create multiple
`_Kmer-N` tokens. BaSIS sums those token weights **within each model and
protein**, retains all token scores, and ranks proteins by the largest of
their per-model sums. This is an explicit BaSIS ranking convention; it is not
a calibrated score of virulence and can be influenced by chunking/length.
With score-range validation enabled, per-model token weights must also sum
to one within a 0.02 tolerance for reduced-precision output.

`attention_scores.tsv` contains **all proteins**, per-model summed weights,
means, maxima, token counts, and ranking. `attention_native.json` retains the
token matrix. Only the configured top N become `ml_attention` findings, with
`exploratory` evidence and unknown genomic coordinates left null for the
shared coordinate resolver. Attention indicates where to look; it does not
demonstrate a protein's biological function or a causal pathogenic mechanism.

## Reusing embeddings

The expensive reusable product is
`native/predictionPF2/preprocess_PF2/ProteinEmbeddings.h5`, not the optional
whole-genome `results_PF2/embeddings.npz` landscape representation.

Every successful run writes `embeddings_manifest.json`. Reuse requires exact
matches for the complete proteome SHA-256, HDF5 SHA-256, supported PF2 source
commit, and declared ProtT5 identity. The next command uses `-f embeddings`.
Keep the original full FASTA for attention-ID validation and coordinate
resolution. A changed FASTA, corrupt HDF5, missing manifest, or changed model
identity blocks reuse. The main workflow should additionally fingerprint
these paths and any local model/weight artifacts in its run provenance.

For a manual configuration that permits upstream-default model fetching, the
ProtT5 model is not fully fixed by a PF2 source commit. The automatic full preset
instead supplies a pinned local model snapshot. For manual long-term reuse, use a
reviewed local model and an immutable model identity/digest; the manifest records
the identity available for that run.
Supplying an identity string alone does not verify the local model's bytes;
the outer workflow must record their digest. The source revision in the
manifest describes the supported adapter contract; use the pinned environment
and retain actual installed package metadata and model provenance.

## Environment and validation status

`envs/pathogenfinder2.yml` installs the canonical repository at the inspected
commit using Python 3.11. Upstream dependencies include PyTorch, Transformers,
NumPy, h5py, and related scientific packages. Mapping extras are installed to
support the optional BPL feature; inference does not execute mapping unless
requested. Prodigal is included as an upstream external dependency even though
this adapter uses existing proteomes. DIAMOND/UniRef and GSEA are not requested
by the adapter.

The source pin is reproducible, but this file is **not a solved platform lock**.
Native dependency solving, CPU/GPU execution, model downloads, resource sizing,
and a biological validation panel still require validation on the target
machine. Contract fixtures test malformed/missing output, statistics and
labels, complete-proteome attention matching, chunk aggregation, and reuse
integrity. Where NumPy/h5py are available, an additional small format test
executes the numeric NPZ/HDF5 conversion; it does not load or run the model.
