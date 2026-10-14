# Report navigation

The main report has an Evidence navigator for displayed selected candidates. Its rows follow the candidate search, sample, origin and interpretation filters. Search includes recorded motif labels, source labels, review reasons and missing comparator groups. ML attention remains exploratory prioritization.

Each card has stable, sample-scoped sections: Overview, Domains, Motifs & patterns, Trees & alignments, Placement, Comparators and Files. Links reveal a filtered or collapsed card. A copied section URL opens the same section. Explicitly deferred candidates remain in the deferred TSV, with no fabricated card links.

Files includes exact saved candidate, analysis, comparator and consolidated records. Additional recorded files are linked only if they exist inside the report bundle; otherwise the saved location is shown as unavailable in that bundle. These basic local-file links do not claim content verification. Existing tree and placement views enforce their recorded checksum checks before exporting visualizations. Missing or corrupt artifacts are not reconstructed during reporting.

The report-navigation.json index records exact report.html and evidence.json hashes, candidate fingerprints, section anchors and raw candidate export paths. A study control panel uses detailed navigation only after checking that index against the actual report and evidence. Missing indexes use basic legacy navigation; stale or invalid indexes show a warning and withhold unverifiable links. Keep linked report folders in place or rebuild the panel after moving them.

Candidate/parent execution and per-layer completion gate accepted feature displays. Incomplete execution can still have retained raw evidence; it is not promoted by rendering. Source review status, missing comparators, unresolved interpretations and scope remain as recorded. No new analysis, candidate selection, functional scoring or biological interpretation is performed.

## Synthetic PC acceptance

Run `python -I BaSIS_check_report_navigation.py` from the installed release. It verifies installation and renders artificial evidence, checks local links and sample identity, tests stale/missing index fallback and preserves source records. The printed ZIP is a software audit, not biological validation. The PC helper checks HTML structure and links without launching a browser. Build-host interaction checks exercise the navigation script with a simulated document; graphical browser validation was unavailable on the build host.
