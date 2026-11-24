# Researcher-curated reference catalogs

A catalog packages local files a researcher has reviewed. Each approved record carries its citation, source release, license, reviewer, review date and SHA256. Pending and excluded records remain visible in the manifest. Only approved files enter the package.

## Prepare the review record

Copy [the JSON template](../config/reference-catalog.example.json) beside your local reference files. Give the catalog a name and a version. Add one record per file. Use short, distinct record IDs.

| Field | What to enter |
| --- | --- |
| `status` | `pending`, `approved` or `excluded`. Record your decision in `notes` if helpful. |
| `kind` | `reference` for a supporting file. Use `placement_registry` only for an existing registry authored to the native BaSIS format. A package accepts one approved registry. |
| `path` | A local path, relative to the specification or absolute on the computer running the builder. |
| `source`, `source_version`, `citation`, `license` | Identify the source and release, the supporting publication or notebook record, and the terms that apply to this file. |
| `reviewer`, `reviewed_on` | The researcher who approved these bytes and the review date in YYYY-MM-DD format. |
| `sha256` | Lowercase SHA256 of the exact file the researcher approved. In Ubuntu, run `sha256sum -- reference-files/calibration-notes.tsv` for the example path. |

## Start a bibliography entry from a DOI

With an internet connection, look up one DOI that you supply. The command also accepts a doi.org link.

```bash
python3 scripts/reference_catalog.py draft-doi 10.1038/nphys1170
```

The command prints an editable one-record JSON specification. Add its record to your existing catalog or save the result as a new specification. The record identifies the DOI, citation and UTC metadata-retrieval time. Its status is `pending`. Read the work, review the local resource, and document its source release, license and digest before approving it. A DOI lookup retrieves bibliographic metadata only. The check, build and verify commands operate offline.

Metadata service: [Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/).

## Check, build and verify

Run from your BaSIS checkout. These commands use the Python standard library and the checkout's native registry validator. They operate on local files.

```bash
python3 scripts/reference_catalog.py check /path/to/review.json
python3 scripts/reference_catalog.py build /path/to/review.json /path/to/catalog-v1
python3 scripts/reference_catalog.py verify /path/to/catalog-v1
```

Check lists counts and validates every approved file. Build requires at least one approved file and a new output directory. A change to reviewed bytes stops the build. The output contains `catalog.json`, `catalog.sha256` and the approved files under `files/`. The manifest also records the original specification's SHA256. Keep the original specification for the mapping back to your local paths. Save the reported `catalog_sha256` in the study record and compare it after transferring the package. Digests verify content integrity. Review decisions and license information are the researcher's declarations.

For an ordinary catalog, the result is a portable evidence collection. If it includes an approved native placement registry, the command returns the absolute `placement_registry` path for the existing `deep.placement_registry` setting. The registry bytes and all of its original eligibility rules are preserved. Selecting one explicit registry replaces the configured registry for that follow-up. Supporting documents remain evidence attachments. The builder performs no sequence retrieval, assigns no functional labels and initiates no screening or follow-up.

For a revision, increment the catalog version, review the changed files, update their checksums and build into a new directory. Keep the previous package with the runs that used it.
