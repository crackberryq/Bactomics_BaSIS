# Sharing results through Teams, SharePoint and OneDrive

Use a sharing ZIP made by `scripts/export_share_bundle.py`. It works with saved
results from existing runs and future runs. It does not launch analyses, rename
the live results, change completion records or change cache identities.

The exporter addresses both the ZIP filename and its extracted contents:

- Every member uses a portable name and a relative path of at most 64 UTF-16
  units, with no deep directory nesting. Case differences, reserved Windows
  names and unsupported filename characters cannot collide in the export.
- Every original regular file is retained byte-for-byte. `manifest.json` maps
  original paths to the short names and records full SHA-256 checksums. The
  short identifier is only a filename; it does not replace the full checksum.
- Internal symlinks are materialized into regular files. Multiple paths to the
  same resolved file share a single copy. Original link targets are recorded.
- `index.html` searches the original paths and downloads original files. The
  screening and follow-up reports have separate derived HTML copies with
  adjusted local links; original HTML and provenance JSON remain unchanged.
- Native HTML, scripts and checkpoints are retained as original downloads.
  They can require their original folder layout. This is a sharing copy for
  review, not a relocated restart directory. Native HTML links in the derived
  report lead to the file index; they are not advertised as relocated tool UIs.

## Export one isolate

Stop writers to the selected results first. From the BaSIS checkout in its
controller environment, prepare a short output directory and preview:

```bash
mkdir -p /mnt/c/TB-share
python scripts/export_share_bundle.py \
  --isolate /mnt/c/TB/isolate007TB \
  --output /mnt/c/TB-share/007.zip
```

The preview is read-only. It reports the selected file count, uncompressed size,
link count, examples of problematic source paths, and destination path checks.
To create the same ZIP, repeat the command with `--apply`:

```bash
python scripts/export_share_bundle.py \
  --isolate /mnt/c/TB/isolate007TB \
  --output /mnt/c/TB-share/007.zip --apply
```

Upload or sync `007.zip` to Teams. Colleagues download it, extract it and open
`index.html` in their browser. The report and its saved file links work after
the original Linux source directory is unavailable. Existing ZIPs are never
overwritten; choose a new output name for another snapshot.

For an explicit saved directory, use `--source /path/to/safety` instead of
`--isolate`. Only that directory and permitted link targets are selected; input
assemblies, external databases and neighboring runs are not searched for or
automatically added. Large selected files still contribute to ZIP size and
export time, even though their paths are short.

## Check the actual sync destination

The full destination prefix matters. Supply the Windows extraction folder when
known; this is the folder containing the extracted `index.html`, not the ZIP:

```bash
python scripts/export_share_bundle.py \
  --isolate /mnt/c/TB/isolate007TB \
  --output /mnt/c/TB-share/007.zip \
  --windows-root 'C:\Users\Jimi\OneDrive - Lab\Results\007' \
  --cloud-root '/sites/Lab/Shared Documents/Results/007' \
  --apply
```

Replace both destination examples with the real folders. `--cloud-root` is a
decoded server-relative path, not a browser URL. The default Windows budget is
240 UTF-16 units including the filename, leaving headroom below legacy Windows
limits. The optional cloud check applies a conservative 400-unit budget to the
decoded SharePoint path. Non-BMP characters count as two units in these checks.

Without `--windows-root`, the preview explicitly assumes a 180-unit destination
prefix. Without `--cloud-root`, the cloud prefix is not checked. This is not a
guarantee for arbitrarily deep folders or organization-specific restrictions.
Moving an extracted copy deeper requires checking the new destination again.

## Linked result stores and preservation

Links inside the selected source are followed, copied once, and recorded.
External link targets are refused unless their parent result directory is
explicitly permitted with `--allow-external-root /path/to/result-store`. The
exporter follows only links encountered in the selected source; it does not
scan unrelated contents of that external directory. Use the narrowest relevant
directory, and inspect the preview before creating the ZIP.

Broken links, cycles, special files, short-name collisions and detected changes
to source files or directory membership fail the export. An incomplete ZIP is
not published. Failed export attempts remove their own temporary file and leave
original files and existing exports intact. The preview and manifest record
source paths for traceability; this command does not upload to Teams.

Do not shorten identity hashes or rename folders inside a live result cache to
solve sync errors. Completion receipts and restart checks refer to that layout.
Keep the active Linux workspace outside the synchronized sharing directory and
share the portable ZIP instead. No analysis code or source fingerprints change
as part of this export feature.

## Microsoft limits

Microsoft documents a 400-character decoded cloud path limit, lower Windows
application limits, invalid names, and lack of OneDrive support for symbolic
links. The exporter checks its own portable paths; Microsoft tenant file-type,
quota and other policies still apply.

- [File path limits](https://support.microsoft.com/en-us/onedrive/what-are-file-path-length-limits)
- [OneDrive and SharePoint restrictions](https://support.microsoft.com/en-us/onedrive/restrictions-and-limitations-in-onedrive-and-sharepoint)

Regression tests cover long and invalid paths, byte preservation, Unicode,
collisions, internal/external links, changing sources, no-overwrite publication
and report links. The browser CI job extracts and moves a synthetic export,
makes the source unavailable, and checks report search, file navigation and a
download while offline.
