"""BaSIS standalone workflow. Snakemake is the sole workflow engine."""
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

configfile: str(ROOT / "config.yaml")

from safety.context import SafetyContext
CTX = SafetyContext(config, ROOT)
SAMPLES = CTX.samples
ISOLATE_PATTERN = "|".join(re.escape(path) for path in SAMPLES) or "(?!)"

wildcard_constraints:
    isolate=ISOLATE_PATTERN

shell.executable("/bin/bash")

# Default terminal output is Snakemake's own readable job progress. Tool output
# stays in per-rule logs. Add -p explicitly when debugging command lines.
rule all:
    input: lambda w: CTX.report_targets() + native_completion_targets()
    default_target: True

include: str(ROOT / "workflow/rules/setup.smk")
include: str(ROOT / "workflow/rules/resources.smk")
include: str(ROOT / "workflow/rules/native.smk")
include: str(ROOT / "workflow/rules/extend.smk")
include: str(ROOT / "workflow/rules/deep.smk")
include: str(ROOT / "workflow/rules/report.smk")
