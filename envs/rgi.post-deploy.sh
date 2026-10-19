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

probe rgi '[0-9]+\.[0-9]+\.[0-9]+' 0 main --version
