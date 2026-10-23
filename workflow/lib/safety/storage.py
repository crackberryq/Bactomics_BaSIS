"""Resolve optional native-result storage without changing execution settings."""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
from pathlib import Path
import re


def native_result_store(root, config, isolate, module):
    """Return a store path; do not create, move, or inspect result contents.

    An explicit shared root separates large retained payloads from sample-facing
    receipts and links. Canonical isolate paths namespace it even when samples
    have the same basename. Storage placement is not a scientific setting.
    """
    if not isinstance(config, Mapping):
        raise ValueError("Workflow configuration must be a mapping")
    storage = config.get("storage", {})
    if not isinstance(storage, Mapping):
        raise ValueError("storage must be a mapping")
    unknown = set(storage) - {"native_results_directory"}
    if unknown:
        raise ValueError("Unknown storage options: " + ", ".join(sorted(map(str, unknown))))
    if not isinstance(module, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", module):
        raise ValueError("Native result module must be a simple module name")
    if "native_results_directory" not in storage:
        return Path(isolate) / "safety/results-cache" / module
    selected = storage["native_results_directory"]
    if not isinstance(selected, str) or not selected.strip() or "\x00" in selected:
        raise ValueError("storage.native_results_directory must be a nonempty path string")
    try:
        base = Path(root).expanduser().resolve()
        directory = Path(selected).expanduser()
        directory = (directory if directory.is_absolute() else base / directory).resolve()
        sample = Path(isolate).expanduser()
        sample = (sample if sample.is_absolute() else base / sample).resolve()
    except (OSError, RuntimeError, ValueError) as error:
        raise ValueError(f"Cannot resolve native result storage paths: {error}") from error
    sample_key = hashlib.sha256(str(sample).encode("utf-8")).hexdigest()
    return directory / sample_key / module
