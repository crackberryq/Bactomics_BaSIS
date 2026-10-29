"""Reports depend on normalized evidence, never execute analysis tools."""

from pathlib import Path

REPORTING_SOURCE = [str(Path(ROOT) / "workflow/lib/safety" / name)
                    for name in ("reporting.py", "report_links.py")] + [
    str(path) for path in sorted((Path(ROOT) / "workflow/vendor/basis").glob("report*.py"))
] + [str(Path(ROOT) / "workflow/vendor/basis/run_summary.py")]
REPORTING_SOURCE.append(str(Path(ROOT) / "workflow/vendor/basis/deep_workflow.py"))
REPORTING_SOURCE.append(str(Path(ROOT) / "workflow/vendor/basis/deep_reporting.py"))


def safety_report_evidence(wildcards):
    # The screening report has no dependency on follow-up evidence. It can be
    # rendered as soon as the recorded screening scope is available.
    return list(CTX.evidence_targets(wildcards.isolate))


if not getattr(CTX, "followup_only", False):
    rule screening_reports:
        input: lambda w: CTX.screening_report_targets()
        message: "Screening reports from saved native evidence"

rule followup_reports:
    input: lambda w: CTX.followup_report_targets()
    message: "Separate DeepSafe reports from saved follow-up evidence"


if not getattr(CTX, "followup_only", False):
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
            readable_findings="{isolate}/safety/tables/screening-findings.tsv",
            recommendations="{isolate}/safety/tables/recommendations.tsv",
            scope="{isolate}/safety/tables/module-scope.tsv",
            summary_tsv="{isolate}/safety/tables/run-summary.tsv",
            summary_json="{isolate}/safety/metadata/run-summary.json",
            methods="{isolate}/safety/metadata/methods.txt",
            navigation="{isolate}/safety/metadata/report-navigation.json",
            artifacts="{isolate}/safety/metadata/report-artifacts.json",
            files_html="{isolate}/safety/metadata/files.html",
            files_json="{isolate}/safety/metadata/files.json",
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
        priority: 20
        resources:
            mem_mb=2000
        log:
            "{isolate}/safety/logs/report.log"
        benchmark:
            "{isolate}/safety/benchmarks/report.tsv"
        message:
            "Render screening report: {wildcards.isolate}/safety/report.html"
        shell:
            "PYTHONPATH={params.code:q} {params.python:q} -m safety.reporting "
            "--sample {input.sample:q} --config {input.config:q} "
            "--evidence {input.evidence:q} --outdir {params.root:q} "
            "--view screening --evidence-root {params.root:q} > {log:q} 2>&1"

if CTX.deep_enabled:
    rule safety_deep_report:
        input:
            sample="{isolate}/safety/metadata/sample.json",
            config=lambda w: CTX.followup_config_path(w.isolate) if hasattr(CTX, "followup_config_path")
                else w.isolate + "/safety/metadata/config.json",
            evidence=lambda w: list(CTX.evidence_targets(w.isolate)) + [
                f"{w.isolate}/safety/evidence/deepsafe.json"],
            source=REPORTING_SOURCE,
        output:
            html="{isolate}/safety/deepsafe-report/report.html",
            evidence="{isolate}/safety/deepsafe-report/evidence.json",
            findings="{isolate}/safety/deepsafe-report/tables/findings.tsv",
            readable_findings="{isolate}/safety/deepsafe-report/tables/screening-findings.tsv",
            recommendations="{isolate}/safety/deepsafe-report/tables/recommendations.tsv",
            scope="{isolate}/safety/deepsafe-report/tables/module-scope.tsv",
            deferred="{isolate}/safety/deepsafe-report/tables/deferred-candidates.tsv",
            summary_tsv="{isolate}/safety/deepsafe-report/tables/run-summary.tsv",
            summary_json="{isolate}/safety/deepsafe-report/metadata/run-summary.json",
            methods="{isolate}/safety/deepsafe-report/metadata/methods.txt",
            navigation="{isolate}/safety/deepsafe-report/metadata/report-navigation.json",
            artifacts="{isolate}/safety/deepsafe-report/metadata/report-artifacts.json",
            files_html="{isolate}/safety/deepsafe-report/metadata/files.html",
            files_json="{isolate}/safety/deepsafe-report/metadata/files.json",
            deep_workflow_json="{isolate}/safety/deepsafe-report/metadata/deep-workflow.json",
            deep_workflow_tsv="{isolate}/safety/deepsafe-report/metadata/deep-workflow.tsv",
            cff="{isolate}/safety/deepsafe-report/metadata/citations/CITATION.cff",
            bib="{isolate}/safety/deepsafe-report/metadata/citations/BaSIS.bib",
            ris="{isolate}/safety/deepsafe-report/metadata/citations/BaSIS.ris",
            citation="{isolate}/safety/deepsafe-report/metadata/citations/CITATION.txt",
            data=directory("{isolate}/safety/deepsafe-report/report-data"),
        params:
            python=PYTHON,
            code=CODE_PATH,
            root=lambda wc: f"{wc.isolate}/safety/deepsafe-report",
            evidence_root=lambda wc: f"{wc.isolate}/safety",
        threads: 1
        resources:
            mem_mb=2000
        log:
            "{isolate}/safety/logs/deepsafe-report.log"
        benchmark:
            "{isolate}/safety/benchmarks/deepsafe-report.tsv"
        message:
            "Render separate DeepSafe report: {wildcards.isolate}/safety/deepsafe-report/report.html"
        shell:
            "PYTHONPATH={params.code:q} {params.python:q} -m safety.reporting "
            "--sample {input.sample:q} --config {input.config:q} "
            "--evidence {input.evidence:q} --outdir {params.root:q} "
            "--view deep --evidence-root {params.evidence_root:q} > {log:q} 2>&1"
