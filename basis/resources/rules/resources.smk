"""Administrative resource targets; never selected by a normal analysis run."""
import json
from pathlib import Path

from basis.resource_setup import RECIPES
from basis.workflow_environments import environment_for_recipe

RESOURCE_PLAN_PATH = str(Path(config["resource_plan"]).resolve())
RESOURCE_PLAN = json.loads(Path(RESOURCE_PLAN_PATH).read_text(encoding="utf-8"))
RESOURCE_ENTRIES = {row["name"]: row for row in RESOURCE_PLAN["resources"]}
RESOURCE_DIRECTORY = RESOURCE_PLAN["directory"]
RESOURCE_PYTHON = RESOURCE_PLAN["python"]
RESOURCE_SOURCE = str(Path(__import__("basis").__file__).resolve().parent.parent)


def resource_environment(wildcards):
    row = RESOURCE_ENTRIES[wildcards.resource]
    if row["backend"] == "pinned_files" or row.get("environment_prefix"):
        return None
    return environment_for_recipe(RECIPES[row["backend"]])


rule all:
    default_target: True
    input:
        RESOURCE_PLAN["summary_path"]


rule prepare_resource:
    input:
        plan=RESOURCE_PLAN_PATH
    output:
        RESOURCE_DIRECTORY + "/resources/{resource}.json"
    wildcard_constraints:
        resource="|".join(RESOURCE_ENTRIES)
    threads:
        lambda wc: RESOURCE_ENTRIES[wc.resource]["threads"]
    conda:
        resource_environment
    params:
        python=RESOURCE_PYTHON,
        source=RESOURCE_SOURCE
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.resource_setup worker --plan {input.plan:q} --resource {wildcards.resource:q} --threads {threads}"


rule resource_summary:
    input:
        plan=RESOURCE_PLAN_PATH,
        receipts=expand(RESOURCE_DIRECTORY + "/resources/{resource}.json", resource=list(RESOURCE_ENTRIES))
    output:
        RESOURCE_PLAN["summary_path"]
    params:
        python=RESOURCE_PYTHON,
        source=RESOURCE_SOURCE
    shell:
        "PYTHONPATH={params.source:q} {params.python:q} -m basis.resource_setup summary --plan {input.plan:q}"
