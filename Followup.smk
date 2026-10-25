"""Explicit follow-up; saved screening inputs have no producer rules."""
from pathlib import Path
import json
import os
import re
import sys

ROOT = Path(workflow.basedir).resolve()
PYTHON = sys.executable
CODE_PATH = os.pathsep.join(str(ROOT / part) for part in
                           ("workflow/lib", "workflow/vendor"))
for path in reversed(CODE_PATH.split(os.pathsep)):
    sys.path.insert(0, path)

from safety.followup import SavedFollowupContext
CTX = SavedFollowupContext(config, ROOT)
SAMPLES = CTX.samples
ISOLATE_PATTERN = re.escape(CTX.isolate)

wildcard_constraints:
    isolate=ISOLATE_PATTERN

shell.executable("/bin/bash")

rule reviewed_followup:
    input: CTX.followup_report_targets()
    default_target: True


rule capture_followup_review:
    input:
        saved=CTX.prerequisites,
        source=str(ROOT / "workflow/lib/safety/followup.py")
    output:
        config=CTX.followup_config_path(CTX.isolate),
        receipt=CTX.setup_target(CTX.isolate)[0]
    params:
        requested=json.dumps(CTX.supplied, sort_keys=True),
        root=str(ROOT)
    threads: 1
    resources: mem_mb=1000
    message: "Verify saved screening inputs and capture explicit follow-up configuration"
    shell:
        "PYTHONPATH={CODE_PATH:q} {PYTHON:q} -m safety.followup "
        "--requested-json {params.requested:q} --root {params.root:q}"


include: str(ROOT / "workflow/rules/deep.smk")
include: str(ROOT / "workflow/rules/report.smk")
