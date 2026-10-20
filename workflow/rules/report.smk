"""Reports depend on normalized evidence, never execute analysis tools."""

from pathlib import Path

REPORTING_SOURCE = [str(Path(ROOT) / "workflow/lib/safety/reporting.py")] + [
    str(path) for path in sorted((Path(ROOT) / "workflow/vendor/basis").glob("report*.py"))
] + [str(Path(ROOT) / "workflow/vendor/basis/run_summary.py")]
REPORTING_SOURCE.append(str(Path(ROOT) / "workflow/vendor/basis/deep_workflow.py"))


def safety_report_evidence(wildcards):
    paths = list(CTX.evidence_targets(wildcards.isolate))
    if CTX.deep_enabled:
        paths.append(f"{wildcards.isolate}/safety/evidence/deepsafe.json")
    return paths


rule safety_report:
    input:
        sample="{isolate}/safety/metadata/sample.json",
        config="{isolate}/safety/metadata/config.json",
        evidence=safety_report_evidence,
        source=REPORTING_SOURCE,
    output:
        html="{isolate}/safety/report.html",
        evidence="{isolate}/safety/evidence.json",
        findings="{isolate}/safety/tables/findings.tsv",
        recommendations="{isolate}/safety/tables/recommendations.tsv",
        scope="{isolate}/safety/tables/module-scope.tsv",
        deferred="{isolate}/safety/tables/deferred-candidates.tsv",
        summary_tsv="{isolate}/safety/tables/run-summary.tsv",
        summary_json="{isolate}/safety/metadata/run-summary.json",
        methods="{isolate}/safety/metadata/methods.txt",
        navigation="{isolate}/safety/metadata/report-navigation.json",
        artifacts="{isolate}/safety/metadata/report-artifacts.json",
        files_html="{isolate}/safety/metadata/files.html",
        files_json="{isolate}/safety/metadata/files.json",
        deep_workflow_json="{isolate}/safety/metadata/deep-workflow.json",
        deep_workflow_tsv="{isolate}/safety/metadata/deep-workflow.tsv",
        cff="{isolate}/safety/metadata/citations/CITATION.cff",
        bib="{isolate}/safety/metadata/citations/BaSIS.bib",
        ris="{isolate}/safety/metadata/citations/BaSIS.ris",
        citation="{isolate}/safety/metadata/citations/CITATION.txt",
        data=directory("{isolate}/safety/report-data"),
    params:
        python=PYTHON,
        code=CODE_PATH,
        root=lambda wc: f"{wc.isolate}/safety",
    threads: 1
    resources:
        mem_mb=2000
    log:
        "{isolate}/safety/logs/report.log"
    benchmark:
        "{isolate}/safety/benchmarks/report.tsv"
    message:
        "Render {wildcards.isolate}/safety/report.html from completed saved evidence"
    shell:
        "PYTHONPATH={params.code:q} {params.python:q} -m safety.reporting "
        "--sample {input.sample:q} --config {input.config:q} "
        "--evidence {input.evidence:q} --outdir {params.root:q} > {log:q} 2>&1"
