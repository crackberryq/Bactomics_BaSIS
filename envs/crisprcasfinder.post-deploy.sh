#!/usr/bin/env bash
# Snakemake executes this before marking the Conda environment complete.
# Failure is an environment deployment failure; Snakemake owns cleanup/rebuild.
set -euo pipefail
: "${CONDA_PREFIX:?Snakemake must activate this environment before deployment checks}"

probe() {
    local names="$1" pattern="$2" allowed="$3" binary="" candidate output status
    shift 3
    for candidate in $names; do
        if binary=$(command -v "$candidate"); then break; fi
        binary=""
    done
    if [[ -z "$binary" || "$binary" != "$CONDA_PREFIX"/bin/* ]]; then
        printf 'Missing executable in activated environment: %s\n' "$names" >&2
        return 1
    fi
    if output=$("$binary" "$@" 2>&1); then status=0; else status=$?; fi
    if [[ " $allowed " != *" $status "* ]] || ! grep -Eiq -- "$pattern" <<< "$output"; then
        printf 'Deployment probe failed: %s (exit %s)\n%s\n' "$binary" "$status" "$output" >&2
        return 1
    fi
    printf 'Verified %s\n' "${binary##*/}"
}

probe perl 'BaSIS BioPerl probe OK' 0 -e 'use Bio::AlignIO; use Bio::SeqIO; use Bio::DB::Fasta; print qq(BaSIS BioPerl probe OK\n)'
probe macsyfinder 'macsyfinder.*2\.0' 0 --version
probe vmatch 'vmatch|2\.3\.1' 0 -version
probe mkvtree 'mkvtree|2\.3\.1' 0 -version
probe vsubseqselect 'vsubseqselect|2\.3\.1' 0 -version
probe muscle 'muscle.*5\.' 0 -version
probe prodigal prodigal 0 -v
probe fuzznuc EMBOSS 0 -version
probe needle EMBOSS 0 -version
probe hmmsearch HMMER 0 -h
