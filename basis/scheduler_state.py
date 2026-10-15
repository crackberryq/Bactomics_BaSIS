"""Current-run worker start evidence, independent of Snakemake output cleanup."""
from pathlib import Path

from .util import atomic_json, hash_object, now, read_json


def started_path(root, sample_id, module):
    return Path(root) / sample_id / "started" / (module + ".json")


def mark_started(plan, root, sample_id, module, stage=None):
    value = {"schema": "basis-scheduler-start-v1", "run_id": Path(root).name,
             "plan_hash": plan["plan_hash"], "source_hash": plan["source_hash"],
             "sample_id": sample_id, "module": module, "status": "started",
             "started_at": now(), "stage": stage}
    value["content_sha256"] = hash_object(value)
    atomic_json(started_path(root, sample_id, module), value)
    return value


def read_started(plan, root, sample_id, module):
    path = started_path(root, sample_id, module)
    if not path.exists():
        return None
    value = read_json(path)
    if not isinstance(value, dict):
        raise ValueError("Worker start receipt is not a mapping: " + str(path))
    digest = value.pop("content_sha256", None)
    expected = {"schema": "basis-scheduler-start-v1", "run_id": Path(root).name,
                "plan_hash": plan["plan_hash"], "source_hash": plan["source_hash"],
                "sample_id": sample_id, "module": module, "status": "started"}
    if digest != hash_object(value) or any(value.get(k) != v for k, v in expected.items()):
        raise ValueError("Worker start receipt identity or checksum mismatch: " + str(path))
    value["content_sha256"] = digest
    return value


def failed_gate(plan, root, sample_id, module):
    """Find an attested failed prerequisite without inferring worker execution."""
    from .workflow_runtime import _read_gate
    for relative, reason in (("input-validation.json", "input_validation_failed"),
                             ("checks/" + module + ".json", "resource_check_failed")):
        path = Path(root) / sample_id / relative
        if path.exists():
            gate = _read_gate(path, plan, Path(root), sample_id)
            if relative.startswith("checks/") and gate.get("module") != module:
                raise ValueError("Resource gate belongs to another module")
            if gate.get("status") == "failed":
                return {"reason": reason, "error": gate.get("error") or reason,
                        "blocked_by": [str(path)]}
    return None
