# Extend header mapping correction

The pinned PathogenFinder2 embedder reads header lines using
`line.replace('>', '').strip().replace('/', '_')`. BaSIS previously removed
only the leading FASTA delimiter. Headers with embedded closing brackets could
therefore fail attention mapping after successful native inference.

The adapter now mirrors the pinned reader exactly. IDs still map back to the
original FASTA identifiers. No arbitrary prefix or first-word fallback is used.
Duplicate normalized headers, unknown tokens, missing proteins, invalid score
matrices and incomplete proteome coverage remain errors. Long-protein chunk
suffixes are handled only after checking exact normalized header identity.

`BaSIS_recover_extend.py` is restricted to the observed header-mapping failure.
It verifies the original proteome hash, recorded successful native command,
embedding provenance and hash, then reparses predictions and exports attention
from saved numeric arrays using the existing environment. It writes a separate
recovered result and HTML report; earlier failed evidence and native outputs
are preserved. No model inference, training or embedding calculation is run.
The source result retains its historical code provenance; recovery is explicit.

The corrected parser is used automatically in subsequent native `-e` runs.
The recovered result is not automatically substituted into earlier reports or
claimed as a verified native cache.
