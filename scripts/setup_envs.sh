#!/usr/bin/env bash
# Works from a source checkout; pip installations can use
# python -m basis.environment_setup with the same arguments.
set -euo pipefail
script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
if [[ -f "$source_root/basis/environment_setup.py" ]]; then
    export PYTHONPATH="$source_root${PYTHONPATH:+:$PYTHONPATH}"
fi
exec "${BASIS_PYTHON:-python3}" -m basis.environment_setup "$@"
