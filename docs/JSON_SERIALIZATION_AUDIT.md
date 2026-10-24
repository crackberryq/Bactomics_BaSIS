# JSON checkpoint correction and related audit

Audit date: 2026-10-24. Baseline: `f66dfedd46da05ae79bf769e7e6fada91270d4d1`
on `snakemake-standalone`. This is a software serialization audit using synthetic
fixtures, not a validation of native analyses or biological interpretation.

## Failure and correction

The reported profile subprocess returned zero in 0.2449 seconds. The wrapper
then raised `Compact workflow state failed lossless roundtrip` while writing
the checkpoint. The log does not identify the mismatching field.

The exact exception is reproducible with an inline mapping such as
`{2: "two", 10: "ten"}`. Python sorts these keys numerically before dumping JSON;
reading the JSON produces string keys, which sort in a different order. The
old checksum therefore changes even though both values survive. Mixed key
types can also fail during sorting, and keys such as `1` and `"1"` can collide.
This reproduces a concrete serializer defect; it does not prove which field
triggered the user's particular run.

The checkpoint writer and logical digest now use the same JSON representation:

- Convert supported dictionary keys to their JSON string spelling before sorting.
- Retain the existing tuple/list and fallback-string value conventions.
- Reject colliding keys, unsupported keys, cycles and nonfinite numbers.
- Validate the full document before publishing shared objects.
- Retain checksum verification, immutable shared objects and atomic replacement.
- Normalize once per checkpoint write; internal hash/dump operations reuse that
  representation instead of copying large state repeatedly.

The standalone DeepSafe JSON writer uses the same normalization. The report
normalizer also rejects key collisions instead of silently overwriting values;
its existing display-key spellings are preserved.

Existing valid v1/v2 JSON states have string keys. Their logical hashes,
context hashes and shared-object names remain unchanged. No schema bump or
offline migration is required. The patch does not change native commands,
thresholds, database resources or scientific decisions.

## Related audit findings

Nine hash/canonicalization helpers were checked. The checkpoint helper is fixed
above. The following eight still sort Python dictionaries directly:

| Helper | Persisted use |
| --- | --- |
| `safety.result_store.digest` | Native execution identities and receipts |
| `safety.resources._digest` | Resource preparation identities |
| `safety.deep_resources._digest` | Resource snapshot manifests |
| `safety.ccf_batches.digest` | Compatibility batch receipts |
| `basis.util.hash_object` | Object and snapshot identifiers |
| `basis.deep_stages._digest` | Stage identities |
| `basis.placement_native._digest` | Cache identities |
| `basis.placement_panels._canonical` | Registry and panel bytes |

All eight reproduce numeric-key ordering drift when passed the synthetic mapping
above. Their usual callers use JSON-loaded metadata or construct string-key
mappings; this audit did not demonstrate those eight failures in a production
run. They remain latent input-contract risks, not eight additional diagnosed
failures. They are not silently described as repaired by this patch.

`safety.runtime.write_json` also permits colliding Python keys to become duplicate
JSON keys; the ordinary reader then keeps only the last value. This generic
writer remains a separate hardening item. No affected user record was observed.

A follow-up shared-serializer change must cover those writers and hashes
together, preserve valid existing identities, and explicitly handle source-bound
cache compatibility. Editing `runtime.py`, `result_store.py` or `basis/util.py`
changes native source identities and can cause expensive recomputation even if
the JSON semantics are unchanged for ordinary inputs. This focused correction
leaves those native identity source files unchanged.

## Validation and resuming

Regression tests cover nested numeric keys, mixed supported key types, every
shared checkpoint location, unknown metadata, unchanged caller data, repeated
aliases, cycles, collisions, invalid numbers, legacy hashes, object reuse,
corruption detection and preservation of existing files on failed writes.
Separate report tests cover its collision guard and existing display behavior.

Wait for workflow workers to stop before pulling this code into an active
checkout. Keep completed outputs and databases. Resume with the existing
configuration and `--rerun-incomplete`; do not force or delete all results.
Snakemake's normal source/input invalidation still applies: because storage
code is a declared DeepSafe input, completed DeepSafe jobs may be scheduled
again after this update. Inspect a dry run before launching. This patch does
not promise a resume that schedules only the failed candidate and does not
mark incomplete work complete or bypass provenance checks.
