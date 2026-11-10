"""Retain generated artifacts before replacement; never prune automatically."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid


def preserve_existing(path):
    """Move existing bytes or a link into unique history; fail before replacement."""
    path = Path(path).absolute()
    if not os.path.lexists(path):
        return None
    history = path.parent / ".previous-results" / (path.name + "-" + uuid.uuid4().hex)
    history.mkdir(parents=True)
    manifest = {"schema": "basis-retained-artifact-v1", "original_path": str(path),
                "retained_at": datetime.now(timezone.utc).isoformat(), "artifact": "data"}
    (history / "retention.json").write_text(json.dumps(manifest, indent=2) + "\n")
    path.rename(history / "data")
    return history / "data"


def publish_link(destination, source):
    """Publish a link while retaining any existing output and its history."""
    destination = Path(destination).absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = Path(source).resolve(strict=True)
    preserve_existing(destination)
    destination.symlink_to(source, target_is_directory=source.is_dir())


def new_attempt(destination):
    """Allocate durable work outside the scheduler-owned destination."""
    destination = Path(destination).absolute()
    attempt = destination.parent / ".attempts" / destination.name / ("attempt-" + uuid.uuid4().hex)
    attempt.mkdir(parents=True)
    (attempt / "attempt.json").write_text(json.dumps({
        "schema": "basis-retained-attempt-v1", "destination": str(destination),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "note": "Retained work; directory existence does not mean successful completion."
    }, indent=2) + "\n")
    return attempt
