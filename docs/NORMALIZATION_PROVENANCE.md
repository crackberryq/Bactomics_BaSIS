# Stable normalization provenance in rc13

The restored-pilot review found matching scientific calls but differing generated finding IDs. The discrepancy was traced to repeated normalization of saved findings, not a new native-tool call.

For a protein-only native hit, BaSIS records the tool's missing coordinates as four null fields, then attaches the validated annotation coordinates. Before rc13, a later import overwrote that original snapshot with the mapped coordinates. A contig self-alias could also be added on the second pass. The attributes contribute to the finding hash, so these changes regenerated the finding ID and its downstream references.

rc13 preserves a previously recorded coordinate snapshot and native alias, suppresses newly introduced self-alias entries, and continues to validate canonical locations. Repeated normalization must produce exactly the same result. For legacy findings with a coherent existing ID triplet but missing provenance, missing native values remain missing. The ID triplet is a format marker; source authentication remains the import layer's responsibility.

The fix does not reconstruct information that was already overwritten in an old import. Historical evidence remains unchanged. Fresh native findings that formerly acquired a redundant self-alias may have different generated IDs under rc13; compare their scientific identity and evidence rather than treating the generated string as a cross-version biological identifier.

The installed replay checker verifies the pinned saved evidence, runs normalization in memory, requires the saved findings to remain identical and subsequent normalization to be stable, and verifies that original source bytes remain unchanged. This is a software regression and saved-evidence test. It does not rerun native tools, fit trees, infer new phenotypes or perform independent biological validation.

The rc12 restored tree review is separately recorded in the release validation directory. Two raw tree files differed only in six terminal branch lengths; topology and internal support labels were identical. Raw hashes remain distinct, and no post hoc acceptance tolerance or scientific threshold was introduced.
