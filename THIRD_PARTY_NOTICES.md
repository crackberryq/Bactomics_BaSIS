# Third-party notices

BaSIS software and original documentation are licensed under the MIT license in
`LICENSE`, copyright (c) 2026 Jimi Goldstein. Reference data and separately
installed tools retain their own terms; MIT does not replace them.

## Included reference data

UniProt-derived comparator sequences and annotations in
`resources/comparators/reviewed-v1/`, `resources/placement/`, and
`basis/resources/placement/`, and `basis/resources/comparators/reviewed-v1/`
are attributed to the **UniProt Consortium** under
[Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/).
See [UniProt's licensing policy](https://www.uniprot.org/help/license).
Individual accessions, source URLs, retrieval information and sequence hashes
are retained in the accompanying TSV/JSON records. BaSIS selects and repackages
records, assigns local identifiers, deduplicates where documented, and adds
evidence-review metadata. These changes do not imply endorsement by UniProt.

NCBI-derived sequences retain accession-level provenance. See
[NCBI's molecular-data usage and disclaimer policies](https://www.ncbi.nlm.nih.gov/home/about/policies/).
NCBI places no restrictions of its own on use or distribution of its molecular
data, but identifies possible rights asserted by original submitters. NCBI does
not warrant the data or endorse BaSIS. MIT does not grant rights in third-party
material beyond those provided by its original terms.

## External software, databases and models

Native analysis tools, full reference databases and model weights are installed
or downloaded separately. Their licenses and citation requirements remain
applicable. Cite the tools and databases actually used, as recorded in BaSIS's
methods and provenance outputs, in addition to citing BaSIS.

Automatic CRISPR/Cas preparation downloads the pinned CRISPRCasFinder 4.3.2
source under GPL-3.0-or-later and CASFinder 3.1.0 models under
CC BY-NC-SA 4.0. Their license files are preserved in the resource snapshot;
the model terms include a noncommercial restriction. Vmatch is installed as
a separate dependency with its own terms. The BaSIS MIT license does not
grant rights to relicense these tools or models. See the official
[CRISPRCasFinder repository](https://github.com/dcouvin/CRISPRCasFinder) and
[CASFinder repository](https://github.com/macsy-models/CASFinder).

Public reference seed panels are development resources. Their inclusion does
not establish a validated classifier or replace independent biological validation.
